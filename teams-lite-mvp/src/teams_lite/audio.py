"""Concurrent microphone and system-loopback recording into recoverable PCM segments."""
from __future__ import annotations

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


class Recorder:
    def __init__(self, storage, meeting_id, sample_rate=48000, segment_seconds=30,
                 source_factory=soundcard_source):
        if sample_rate <= 0 or segment_seconds <= 0:
            raise ValueError('La frecuencia de muestreo y la duración del segmento deben ser positivas')
        self.storage, self.meeting_id = storage, meeting_id
        self.sample_rate, self.segment_seconds = sample_rate, segment_seconds
        self.source_factory = source_factory
        self.errors = []
        self._stop = threading.Event()
        self._threads = []
        self._started = False
        self._pending = queue.Queue()
        self._finalizer = None

    def start(self):
        if self._started:
            raise RuntimeError('La grabadora ya se inició')
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

    def _capture(self, kind, stream_id):
        segment = None
        handle = None
        try:
            max_frames = max(1, round(self.sample_rate * self.segment_seconds))
            block_frames = max(1, min(max_frames, self.sample_rate // 10))
            sequence = 0
            frames_in_segment = 0
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
                        segment = self.storage.begin_segment(stream_id, sequence, time.monotonic(), utc_now())
                        handle = open(segment['temp_path'], 'xb', buffering=0)
                    mono = data.mean(axis=1, keepdims=True)
                    pcm = (np.clip(np.nan_to_num(mono), -1, 1) * 32767).astype('<i2')
                    handle.write(pcm.tobytes())
                    frames_in_segment += len(data)
                    if frames_in_segment == max_frames:
                        handle.flush()
                        os.fsync(handle.fileno())
                        handle.close()
                        handle = None
                        self._pending.put(segment)
                        segment = None
                        frames_in_segment = 0
                        sequence += 1
        except Exception as exc:
            self.errors.append(f'{kind}: {exc}')
            self._stop.set()
            try:
                self.storage.add_event(self.meeting_id, 'capture_error', f'{kind}: {exc}')
            except Exception:
                pass
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

