import ctypes
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from teams_lite.audio import Recorder, soundcard_source
from teams_lite.storage import Storage

def wait_until(condition, timeout=5.0):
    """Poll instead of sleeping a fixed time: capture threads run late on a loaded machine."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


class FakeSource:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def record(self, numframes):
        time.sleep(0.005)
        return np.ones((numframes, 1), dtype=np.float32) * 0.25

class FakeStereoSource(FakeSource):
    def record(self, numframes):
        time.sleep(0.005)
        return np.column_stack((np.ones(numframes), np.zeros(numframes))).astype(np.float32)

class AudioTests(unittest.TestCase):
    def test_separate_streams_are_segmented_and_valid_wav(self):
        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting('test')
            recorder = Recorder(store, meeting, sample_rate=8000, segment_seconds=0.02,
                                source_factory=lambda kind, rate: FakeSource())
            recorder.start()
            time.sleep(0.06)
            recorder.stop()
            segments = store.list_segments(meeting)
            self.assertEqual({s['kind'] for s in segments}, {'microphone', 'loopback'})
            self.assertFalse(recorder.errors)
            self.assertTrue(all(s['status'] == 'finalized' for s in segments))
            for segment in segments:
                with wave.open(segment['path'], 'rb') as wav:
                    self.assertEqual(wav.getframerate(), 8000)
                    self.assertGreater(wav.getnframes(), 0)

    def test_source_error_stops_and_reports_failure(self):
        def fail(kind, rate):
            raise RuntimeError('device unavailable')
        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting()
            recorder = Recorder(store, meeting, source_factory=fail)
            recorder.start()
            recorder.stop()
            self.assertTrue(recorder.errors)
            self.assertEqual(store.list_meetings()[0]['status'], 'failed')
            self.assertTrue(store.list_events(meeting))

    def test_lost_device_does_not_stop_the_other_stream_and_reconnects(self):
        # A Bluetooth headset dies mid-meeting: the loopback device is invalidated, then the
        # new default output is opened. The microphone must never stop recording.
        opens = {'microphone': 0, 'loopback': 0}

        class DyingSource(FakeSource):
            def __init__(self):
                self.calls = 0

            def record(self, numframes):
                self.calls += 1
                if self.calls > 3:
                    raise RuntimeError('AUDCLNT_E_DEVICE_INVALIDATED')
                return super().record(numframes)

        def factory(kind, rate):
            opens[kind] += 1
            return DyingSource() if kind == 'loopback' and opens[kind] == 1 else FakeSource()

        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting()
            recorder = Recorder(store, meeting, sample_rate=8000, segment_seconds=0.02,
                                source_factory=factory, retry_seconds=0.01)
            recorder.start()
            wait_until(lambda: opens['loopback'] == 2 and 'capture_resumed' in
                       [event['kind'] for event in store.list_events(meeting)])
            time.sleep(0.05)  # let both streams write a few more segments
            recorder.stop()

            self.assertEqual([], recorder.errors)
            self.assertEqual('completed', store.list_meetings()[0]['status'])
            self.assertEqual(1, opens['microphone'], recorder.warnings)
            self.assertEqual(2, opens['loopback'], recorder.warnings)
            kinds = [event['kind'] for event in store.list_events(meeting)]
            self.assertIn('capture_interrupted', kinds)
            self.assertIn('capture_resumed', kinds)
            self.assertTrue(any('loopback' in warning for warning in recorder.warnings))
            segments = store.list_segments(meeting)
            for kind in ('microphone', 'loopback'):
                sequences = [s['sequence'] for s in segments if s['kind'] == kind]
                self.assertEqual(len(sequences), len(set(sequences)), f'{kind} reused a sequence number')
            loopback = sorted(s['started_monotonic'] for s in segments if s['kind'] == 'loopback')
            microphone = sorted(s['started_monotonic'] for s in segments if s['kind'] == 'microphone')
            # Timing-independent: the microphone kept producing segments across the whole run,
            # including after the loopback failed (its last segment starts after loopback's first).
            self.assertGreaterEqual(len(microphone), 2)
            self.assertGreater(microphone[-1], loopback[0])
            self.assertGreater(loopback[-1], loopback[0])  # loopback resumed after the failure

    def test_default_device_change_switches_to_the_new_device(self):
        # Headphones are plugged in: Windows moves Teams audio to them while the old speakers
        # still exist, so the old loopback would silently record silence.
        current = {'microphone': 'built-in-mic', 'loopback': 'speakers'}
        opened = []

        def factory(kind, rate):
            opened.append((kind, current[kind]))
            return FakeSource()

        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting()
            recorder = Recorder(store, meeting, sample_rate=8000, segment_seconds=0.02,
                                source_factory=factory, device_id=lambda kind: current[kind],
                                device_check_seconds=0.01)
            recorder.start()
            wait_until(lambda: ('loopback', 'speakers') in opened)
            time.sleep(0.03)  # a few device checks pass on the old default first
            current['loopback'] = 'headphones'
            wait_until(lambda: ('loopback', 'headphones') in opened and 'device_changed' in
                       [event['kind'] for event in store.list_events(meeting)])
            recorder.stop()

            self.assertIn(('loopback', 'speakers'), opened)
            self.assertIn(('loopback', 'headphones'), opened)
            self.assertEqual(1, sum(1 for kind, _ in opened if kind == 'microphone'))
            self.assertIn('device_changed', [event['kind'] for event in store.list_events(meeting)])
            self.assertEqual([], recorder.errors)
            self.assertEqual('completed', store.list_meetings()[0]['status'])

    @unittest.skipUnless(sys.platform == 'win32', 'COM apartments are Windows specific')
    def test_capture_threads_initialize_com_themselves(self):
        # soundcard initializes COM only once, in the thread that imports it. When that first
        # recording's thread exits, later recordings got CO_E_NOTINITIALIZED (0x800401F0) and
        # captured nothing. Each capture thread must own an explicit COM initialization.
        apartments = []

        def apartment_of_current_thread():
            kind, qualifier = ctypes.c_int(), ctypes.c_int()
            hr = ctypes.windll.ole32.CoGetApartmentType(ctypes.byref(kind), ctypes.byref(qualifier))
            return hr & 0xFFFFFFFF, kind.value, qualifier.value

        def factory(kind, rate):
            apartments.append(apartment_of_current_thread())
            return FakeSource()

        APTTYPE_MTA, APTTYPEQUALIFIER_IMPLICIT_MTA = 1, 1
        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            for _ in range(2):  # a second recording in the same process must behave the same
                meeting = store.create_meeting()
                recorder = Recorder(store, meeting, sample_rate=8000, segment_seconds=0.02, source_factory=factory)
                recorder.start()
                time.sleep(0.05)
                recorder.stop()
        self.assertEqual(4, len(apartments))
        for hr, kind, qualifier in apartments:
            self.assertEqual(0, hr, f'COM not initialized in the capture thread (hr=0x{hr:08X})')
            self.assertEqual(APTTYPE_MTA, kind)
            self.assertNotEqual(APTTYPEQUALIFIER_IMPLICIT_MTA, qualifier)

    def test_startup_metadata_failure_does_not_leak_finalizer(self):
        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting()
            recorder = Recorder(store, meeting, source_factory=lambda kind, rate: FakeSource())
            with patch.object(store, 'create_stream', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    recorder.start()
            self.assertFalse(any(t.name == 'audio-finalizer' and t.is_alive() for t in threading.enumerate()))
            self.assertEqual(store.list_meetings()[0]['status'], 'failed')

    def test_native_channel_capture_is_downmixed_to_mono(self):
        calls = []
        device = SimpleNamespace(recorder=lambda **kwargs: calls.append(kwargs) or FakeStereoSource())
        fake_soundcard = SimpleNamespace(default_microphone=lambda: device)
        with patch.dict('sys.modules', {'soundcard': fake_soundcard}):
            soundcard_source('microphone', 48_000)
        self.assertIsNone(calls[0]['channels'])

        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            meeting = store.create_meeting()
            recorder = Recorder(store, meeting, sample_rate=8000, segment_seconds=0.02,
                                source_factory=lambda kind, rate: FakeStereoSource())
            recorder.start()
            time.sleep(0.04)
            recorder.stop()
            for segment in store.list_segments(meeting):
                with wave.open(segment['path'], 'rb') as wav:
                    self.assertEqual(wav.getnchannels(), 1)
                    samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
                    self.assertTrue(np.allclose(samples, 16383, atol=1))

if __name__ == '__main__': unittest.main()

