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

