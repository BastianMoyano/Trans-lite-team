"""Offline chunk-level transcription of independent PCM WAV sources.

Times bound the audio chunk, not individual words or precisely detected speech.
"""
from __future__ import annotations

import importlib
import json
import math
import tempfile
import wave
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .speakers import speaker_for_source


class CapabilityError(RuntimeError):
    """The optional offline transcription capability is unavailable."""


@dataclass(frozen=True)
class AudioSource:
    path: str | Path
    source: str
    offset_seconds: float = 0


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    source: str
    speaker: str
    text: str


def load_recognizer(model_dir: str | Path | None) -> Callable[[str], str]:
    if model_dir is None or not Path(model_dir).is_dir():
        raise CapabilityError('Proporciona un directorio local existente para la transcripción sin conexión.')
    try:
        loader = importlib.import_module('onnx_asr.loader')
    except (ImportError, OSError) as exc:
        raise CapabilityError('Instala versiones compatibles de onnx-asr y ONNX Runtime para CPU.') from exc
    try:
        manager = loader.Manager(providers=['CPUExecutionProvider'])
        model = manager.create_asr('nemo-parakeet-tdt-0.6b-v3', str(model_dir), offline=True)
    except Exception as exc:
        raise CapabilityError(
            'No se pudo cargar el modelo local Parakeet mediante ONNX Runtime para CPU; '
            'verifica los archivos y la compatibilidad de los paquetes.'
        ) from exc
    return model.recognize


SPEECH_THRESHOLD_DBFS = -48.0  # meeting speech sits around -35..-15 dBFS; room noise below -55
_FRAME_SECONDS = 0.25
_PAD_SECONDS = 0.5
_MERGE_GAP_SECONDS = 2.0  # fewer, longer calls: per-call model overhead ate most of the gain
_MIN_SOUND_SECONDS = 0.75  # shorter bursts (clicks, coughs) made the model hallucinate fillers


def speech_regions(samples, rate: int, *, max_seconds: float,
                   threshold_dbfs: float = SPEECH_THRESHOLD_DBFS) -> list[tuple[int, int]]:
    """Return (start, end) sample ranges that contain sound, padded and merged.

    A conservative energy gate: it only drops stretches that are clearly silent, so quiet
    speech is kept. Regions longer than max_seconds are split for the recognizer.
    """
    import numpy as np

    data = np.asarray(samples, dtype=np.float32) / 32768.0
    frame = max(1, int(rate * _FRAME_SECONDS))
    count = len(data) // frame + (1 if len(data) % frame else 0)
    if not count:
        return []
    padded = np.zeros(count * frame, dtype=np.float32)
    padded[:len(data)] = data
    rms = np.sqrt(np.mean(padded.reshape(count, frame) ** 2, axis=1)) + 1e-12
    loud = np.flatnonzero(20 * np.log10(rms) >= threshold_dbfs)
    if not len(loud):
        return []
    pad, gap = int(rate * _PAD_SECONDS), int(rate * _MERGE_GAP_SECONDS)
    regions: list[list[int]] = []  # [start, end, loud frame count]
    for index in loud:
        start = max(0, index * frame - pad)
        end = min(len(data), (index + 1) * frame + pad)
        if regions and start - regions[-1][1] <= gap:
            regions[-1][1] = max(regions[-1][1], end)
            regions[-1][2] += 1
        else:
            regions.append([start, end, 1])
    min_frames = max(1, round(_MIN_SOUND_SECONDS / _FRAME_SECONDS))
    limit = max(1, int(rate * max_seconds))
    pieces = []
    for start, end, sound_frames in regions:
        if sound_frames < min_frames:
            continue
        while end - start > limit:
            pieces.append((start, start + limit))
            start += limit
        pieces.append((start, end))
    return pieces


def transcribe_recordings(
    inputs: Iterable[AudioSource], *, model_dir: str | Path | None = None,
    recognizer: Callable[[str], str] | None = None, chunk_seconds: float = 20,
    speaker_mapping: Mapping[str, str] | None = None, skip_silence: bool = False,
) -> list[TranscriptSegment]:
    """Transcribe each source in chunks of at most chunk_seconds.

    With skip_silence, only the stretches with sound reach the model. In real meetings each
    track is 40-80 % silence (the other side is talking), so this is the main speed-up.
    """
    if not math.isfinite(chunk_seconds) or not 0 < chunk_seconds <= 30:
        raise ValueError('chunk_seconds debe ser mayor que cero y no superar 30')
    recognize = recognizer if recognizer is not None else load_recognizer(model_dir)
    segments = []
    with tempfile.TemporaryDirectory(prefix='teams-lite-asr-') as temp:
        chunk_path = Path(temp) / 'chunk.wav'
        for item in inputs:
            if not math.isfinite(item.offset_seconds) or item.offset_seconds < 0:
                raise ValueError('offset_seconds debe ser un número finito no negativo')
            with wave.open(str(item.path), 'rb') as source:
                if source.getcomptype() != 'NONE':
                    raise ValueError('Solo se admiten archivos WAV PCM sin compresión')
                rate = source.getframerate()
                if skip_silence:
                    _transcribe_speech(source, item, rate, chunk_seconds, chunk_path, recognize,
                                       speaker_mapping, segments)
                    continue
                frames_per_chunk = max(1, int(rate * chunk_seconds))
                position = 0
                while True:
                    frames = source.readframes(frames_per_chunk)
                    count = len(frames) // (source.getnchannels() * source.getsampwidth())
                    if not count:
                        break
                    with wave.open(str(chunk_path), 'wb') as chunk:
                        chunk.setnchannels(source.getnchannels())
                        chunk.setsampwidth(source.getsampwidth())
                        chunk.setframerate(rate)
                        chunk.writeframes(frames)
                    text = recognize(str(chunk_path))
                    if not isinstance(text, str):
                        raise TypeError('El reconocedor debe devolver texto como una cadena')
                    text = text.strip()
                    if text:
                        segments.append(TranscriptSegment(
                            round(item.offset_seconds + position / rate, 6),
                            round(item.offset_seconds + (position + count) / rate, 6),
                            item.source, speaker_for_source(item.source, speaker_mapping), text,
                        ))
                    position += count
    return sorted(segments, key=lambda s: (s.start, s.end, s.source, s.text))


def _transcribe_speech(source, item, rate, chunk_seconds, chunk_path, recognize, speaker_mapping, segments):
    import numpy as np

    channels, width = source.getnchannels(), source.getsampwidth()
    if width != 2:
        raise ValueError('skip_silence requiere audio PCM de 16 bits')
    raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw, dtype='<i2')
    mono = samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples
    for start, end in speech_regions(mono, rate, max_seconds=chunk_seconds):
        with wave.open(str(chunk_path), 'wb') as chunk:
            chunk.setnchannels(channels)
            chunk.setsampwidth(width)
            chunk.setframerate(rate)
            chunk.writeframes(raw[start * channels * width:end * channels * width])
        text = recognize(str(chunk_path))
        if not isinstance(text, str):
            raise TypeError('El reconocedor debe devolver texto como una cadena')
        text = text.strip()
        if text:
            segments.append(TranscriptSegment(
                round(item.offset_seconds + start / rate, 6),
                round(item.offset_seconds + end / rate, 6),
                item.source, speaker_for_source(item.source, speaker_mapping), text,
            ))


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def export_transcript(segments: Iterable[TranscriptSegment], json_path: str | Path, text_path: str | Path) -> None:
    ordered = sorted(segments, key=lambda s: (s.start, s.end, s.source, s.text))
    Path(json_path).write_text(json.dumps([asdict(s) for s in ordered], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [f'[{_timestamp(s.start)} - {_timestamp(s.end)}] {s.speaker}: {s.text}' for s in ordered]
    Path(text_path).write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
