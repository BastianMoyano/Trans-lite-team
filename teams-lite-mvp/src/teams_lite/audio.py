"""Concurrent microphone and system-loopback recording into recoverable PCM segments."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import os
import queue
import threading
import time
import numpy as np
from .storage import utc_now


def soundcard_source(kind, sample_rate):
    # Imported lazily so metadata and tests do not require an audio device.
    import soundcard as sc
    if kind == 'microphone':
        device = sc.default_microphone()
    elif kind == 'loopback':
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError('No hay un dispositivo de salida predeterminado disponible')
        device = sc.get_microphone(id=speaker.id, include_loopback=True)
    else:
        raise ValueError('Tipo de fuente desconocido')
    if device is None:
        raise RuntimeError(f'No hay un dispositivo disponible para {kind}')
    # SoundCard documents broken mono recording on Windows/WASAPI. Capture the
    # device's native channel layout and downmix explicitly in _capture().
    return device.recorder(samplerate=sample_rate, channels=None)


@contextmanager
def _com_initialized():
    """Initialize COM (MTA) in the calling thread for the duration of a capture.

    soundcard calls CoInitializeEx only once, in the thread that imports it. When that first
    recording's capture thread exits, Windows tears that initialization down and every later
    recording failed with CO_E_NOTINITIALIZED (0x800401F0), capturing no audio at all.
    """
    if os.name != 'nt':
        yield
        return
    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
    try:
        yield
    finally:
        if hr in (0, 1):  # S_OK / S_FALSE must be balanced; RPC_E_CHANGED_MODE must not
            ole32.CoUninitialize()


def soundcard_device_id(kind):
    """Id of the current Windows default device for a stream, or None when it cannot be read."""
    import soundcard as sc
    device = sc.default_microphone() if kind == 'microphone' else sc.default_speaker()
    return getattr(device, 'id', None)


_MAX_RETRY_SECONDS = 5.0


class Recorder:
    """Records microphone and system audio independently.

    A stream that fails (device unplugged, Bluetooth dropped) is reopened on the current
    default device while the other stream keeps recording. When the default device changes
    without failing (headphones plugged in), the stream switches to the new device, because
    the old loopback would otherwise record silence without any error.
    """

    def __init__(self, storage, meeting_id, sample_rate=48000, segment_seconds=30,
                 source_factory=soundcard_source, device_id=None, retry_seconds=1.0,
                 device_check_seconds=2.0):
        if sample_rate <= 0 or segment_seconds <= 0:
            raise ValueError('La frecuencia de muestreo y la duración del segmento deben ser positivas')
        self.storage, self.meeting_id = storage, meeting_id
        self.sample_rate, self.segment_seconds = sample_rate, segment_seconds
        self.source_factory = source_factory
        if device_id is None and source_factory is soundcard_source:
            device_id = soundcard_device_id
        self.device_id = device_id
        self.retry_seconds = retry_seconds
        self.device_check_seconds = device_check_seconds
        self.errors = []
        self.warnings = []  # recovered problems, shown live; errors are unrecoverable
        self._stop = threading.Event()
        self._threads = []
        self._started = False
        self._pending = queue.Queue()
        self._finalizer = None

    def start(self):
        if self._started:
            raise RuntimeError('La grabadora ya se inició')
        if self.source_factory is soundcard_source:
            # Import soundcard here, before any capture thread initializes COM: its import-time
            # CoInitializeEx treats S_FALSE ("already initialized") as a fatal error.
            import soundcard  # noqa: F401
        self._started = True
        try:
            streams = [
                (kind, self.storage.create_stream(self.meeting_id, kind, self.sample_rate, 1))
                for kind in ('microphone', 'loopback')
            ]
            self._finalizer = threading.Thread(target=self._finalize, name='audio-finalizer')
            self._threads = [
                threading.Thread(target=self._capture, args=(kind, stream_id), name=f'audio-{kind}')
                for kind, stream_id in streams
            ]
            self._finalizer.start()
            for thread in self._threads:
                thread.start()
        except Exception as exc:
            self.errors.append(f'inicio de la grabación: {exc}')
            self._stop.set()
            for thread in self._threads:
                if thread.is_alive():
                    thread.join()
            if self._finalizer is not None and self._finalizer.is_alive():
                self._pending.put(None)
                self._finalizer.join()
            try:
                self.storage.add_event(self.meeting_id, 'startup_error', str(exc))
                self.storage.finish_meeting(self.meeting_id, 'failed')
            finally:
                self._started = False
            raise

    def stop(self):
        if not self._started:
            raise RuntimeError('La grabadora no se ha iniciado')
        self._stop.set()
        for thread in self._threads:
            thread.join()
        self._pending.put(None)
        self._finalizer.join()
        self.storage.finish_meeting(self.meeting_id, 'failed' if self.errors else 'completed')

    def _finalize(self):
        while True:
            segment = self._pending.get()
            if segment is None:
                return
            try:
                self.storage.finalize_segment(segment['id'])
            except Exception as exc:
                self.errors.append(f"falló la finalización del segmento: {exc}")
                self._stop.set()
                try:
                    self.storage.add_event(self.meeting_id, 'finalization_error', str(exc))
                except Exception:
                    pass

    def _warn(self, event, message):
        self.warnings.append(message)
        try:
            self.storage.add_event(self.meeting_id, event, message)
        except Exception:
            pass

    def _current_device(self, kind):
        if self.device_id is None:
            return None
        try:
            return self.device_id(kind)
        except Exception:
            return None

    def _capture(self, kind, stream_id):
        with _com_initialized():
            self._capture_with_retries(kind, stream_id)

    def _capture_with_retries(self, kind, stream_id):
        state = {'sequence': 0, 'captured': False, 'failures': 0}
        last_error = None
        while not self._stop.is_set():
            try:
                self._capture_session(kind, stream_id, state)
            except Exception as exc:
                last_error = exc
                state['failures'] += 1
                if state['failures'] == 1:
                    self._warn('capture_interrupted', f'{kind}: se interrumpió la captura ({exc}); reintentando.')
                # The other stream keeps recording; retry this one on the current default device.
                self._stop.wait(min(_MAX_RETRY_SECONDS, self.retry_seconds * state['failures']))
        if last_error is not None and not state['captured']:
            self.errors.append(f'{kind}: {last_error}')

    def _capture_session(self, kind, stream_id, state):
        """Record from one opened device until stop, a device error, or a default-device change."""
        segment = None
        handle = None
        frames_in_segment = 0
        max_frames = max(1, round(self.sample_rate * self.segment_seconds))
        block_frames = max(1, min(max_frames, self.sample_rate // 10))
        opened_device = self._current_device(kind)
        next_check = time.monotonic() + self.device_check_seconds
        try:
            with self.source_factory(kind, self.sample_rate) as source:
                while not self._stop.is_set():
                    data = np.asarray(source.record(numframes=min(block_frames, max_frames - frames_in_segment)))
                    if data.ndim == 1:
                        data = data[:, None]
                    if data.ndim != 2 or data.shape[1] < 1 or not len(data):
                        raise RuntimeError(f'{kind} devolvió datos de audio no válidos')
                    if len(data) > max_frames - frames_in_segment:
                        raise RuntimeError(f'{kind} devolvió más muestras de las solicitadas')
                    if segment is None:
                        segment = self.storage.begin_segment(stream_id, state['sequence'], time.monotonic(), utc_now())
                        handle = open(segment['temp_path'], 'xb', buffering=0)
                    mono = data.mean(axis=1, keepdims=True)
                    pcm = (np.clip(np.nan_to_num(mono), -1, 1) * 32767).astype('<i2')
                    handle.write(pcm.tobytes())
                    frames_in_segment += len(data)
                    state['captured'] = True
                    if state['failures']:
                        state['failures'] = 0
                        self._warn('capture_resumed', f'{kind}: la captura se recuperó.')
                    if frames_in_segment == max_frames:
                        handle.flush()
                        os.fsync(handle.fileno())
                        handle.close()
                        handle = None
                        self._pending.put(segment)
                        segment = None
                        frames_in_segment = 0
                        state['sequence'] += 1
                    if self.device_id is not None and time.monotonic() >= next_check:
                        next_check = time.monotonic() + self.device_check_seconds
                        current = self._current_device(kind)
                        if opened_device is None:
                            opened_device = current
                        elif current is not None and current != opened_device:
                            self._warn('device_changed',
                                       f'{kind}: cambió el dispositivo predeterminado; se sigue grabando en el nuevo.')
                            return
        finally:
            if handle is not None:
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
                except OSError as exc:
                    self.errors.append(f'{kind}: {exc}')
            if segment is not None:
                self._pending.put(segment)
                state['sequence'] += 1  # the partial segment used this number; never reuse it
