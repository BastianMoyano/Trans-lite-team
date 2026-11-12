"""Timestamped visual evidence for cautious remote-speaker attribution."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
from collections.abc import Iterable

from .transcription import TranscriptSegment


@dataclass(frozen=True)
class SpeakerObservation:
    start: float
    end: float
    speaker: str
    confidence: float
    captured_utc: str

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.start, self.end, self.confidence)):
            raise ValueError("Los tiempos y la confianza deben ser números finitos")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("El intervalo visual debe tener una duración positiva")
        name = re.sub(r"\s+", " ", self.speaker).strip()
        if not name:
            raise ValueError("El nombre visual no puede estar vacío")
        if not 0 <= self.confidence <= 1:
            raise ValueError("La confianza visual debe estar entre 0 y 1")
        captured = datetime.fromisoformat(self.captured_utc.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            raise ValueError("captured_utc debe incluir zona horaria")
        object.__setattr__(self, "speaker", name)


def append_observation(path: str | Path, observation: SpeakerObservation) -> None:
    """Append one durable JSONL observation without rewriting prior evidence."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(observation), ensure_ascii=False, sort_keys=True) + "\n"
    with destination.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def load_observations(path: str | Path) -> list[SpeakerObservation]:
    """Read ordered evidence, tolerating only an interrupted final JSONL record."""
    source = Path(path)
    if not source.exists():
        return []
    lines = source.read_text(encoding="utf-8").splitlines()
    observations: list[SpeakerObservation] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            observations.append(SpeakerObservation(**json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            if index != len(lines) - 1:
                raise
    return sorted(
        observations,
        key=lambda item: (item.start, item.end, item.speaker.casefold(), -item.confidence),
    )


def apply_visual_speakers(
    segments: Iterable[TranscriptSegment],
    observations: Iterable[SpeakerObservation],
    *,
    min_confidence: float = 0.6,
    min_dominance: float = 0.6,
) -> list[TranscriptSegment]:
    """Attribute remote chunks only when overlapping visual evidence is dominant."""
    if not 0 <= min_confidence <= 1 or not 0.5 < min_dominance <= 1:
        raise ValueError("Los umbrales visuales no son válidos")
    evidence = sorted(observations, key=lambda item: (item.start, item.end, item.speaker.casefold()))
    attributed: list[TranscriptSegment] = []
    for segment in segments:
        if segment.source != "remote":
            attributed.append(segment)
            continue
        scores: dict[str, float] = {}
        display_names: dict[str, str] = {}
        for observation in evidence:
            if observation.confidence < min_confidence:
                continue
            overlap = min(segment.end, observation.end) - max(segment.start, observation.start)
            if overlap <= 0:
                continue
            key = observation.speaker.casefold()
            display_names.setdefault(key, observation.speaker)
            scores[key] = scores.get(key, 0.0) + overlap * observation.confidence
        if not scores:
            attributed.append(segment)
            continue
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        best_key, best_score = ranked[0]
        total_score = sum(scores.values())
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if math.isclose(best_score, second_score) or best_score / total_score < min_dominance:
            attributed.append(segment)
            continue
        attributed.append(replace(segment, speaker=display_names[best_key]))
    return attributed
