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


def transcribe_recordings(
    inputs: Iterable[AudioSource], *, model_dir: str | Path | None = None,
    recognizer: Callable[[str], str] | None = None, chunk_seconds: float = 20,
    speaker_mapping: Mapping[str, str] | None = None,
) -> list[TranscriptSegment]:
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
