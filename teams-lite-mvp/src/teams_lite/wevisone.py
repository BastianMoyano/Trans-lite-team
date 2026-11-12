"""Optional WeVisOne-VL adapter for visual active-speaker evidence."""
from __future__ import annotations

from pathlib import Path
import re
from collections.abc import Callable

from PIL import Image

from .visual_speakers import SpeakerObservation
from .window_capture import CapturedFrame


class VisualModelUnavailable(RuntimeError):
    """The optional visual-language runtime or local model is unavailable."""


_BOX = re.compile(
    r"<box>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*</box>",
    re.IGNORECASE,
)
_GROUNDED_TEXT = re.compile(
    r"<ref>(.*?)</ref>\s*<box>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*</box>",
    re.IGNORECASE | re.DOTALL,
)
_UI_LABELS = {
    "active speaker", "camera", "chat", "leave", "meeting", "microphone",
    "more", "people", "raise", "react", "share", "teams",
}


def _normalized_box(values: tuple[str, str, str, str]) -> tuple[float, float, float, float] | None:
    coordinates = tuple(int(value) for value in values)
    x1, y1, x2, y2 = coordinates
    if not all(0 <= value <= 1000 for value in coordinates) or x2 <= x1 or y2 <= y1:
        return None
    return tuple(value / 1000 for value in coordinates)


def parse_single_box(response: str) -> tuple[float, float, float, float] | None:
    boxes = [box for values in _BOX.findall(response or "") if (box := _normalized_box(values))]
    return boxes[0] if len(boxes) == 1 else None


def parse_grounded_text(response: str) -> list[tuple[str, tuple[float, float, float, float]]]:
    grounded = []
    for match in _GROUNDED_TEXT.finditer(response or ""):
        text = re.sub(r"\s+", " ", match.group(1)).strip(" |,-")
        box = _normalized_box(match.groups()[1:])
        if text and box is not None:
            grounded.append((text, box))
    return grounded


def _participant_name(response: str) -> str | None:
    candidates = []
    for text, box in parse_grounded_text(response):
        key = text.casefold()
        if box[1] < 0.5 or key in _UI_LABELS:
            continue
        if not any(character.isalpha() for character in text) or len(text) > 80:
            continue
        candidates.append((box[3], box[2] - box[0], text))
    return max(candidates, default=(0, 0, None))[2]


class WeVisOneDetector:
    def __init__(
        self,
        *,
        model=None,
        model_path: str | Path | None = None,
        device: str = "cpu",
        dtype: str = "float32",
        observation_seconds: float = 2.0,
        model_loader: Callable[[], object] | None = None,
    ) -> None:
        if observation_seconds <= 0:
            raise ValueError("La duración de la observación debe ser mayor que cero")
        self.model = model
        self.model_path = Path(model_path) if model_path else None
        self.device = device
        self.dtype = dtype
        self.observation_seconds = observation_seconds
        self.model_loader = model_loader

    def load(self):
        if self.model is not None:
            return self.model
        try:
            if self.model_loader is not None:
                self.model = self.model_loader()
            else:
                if self.model_path is None or not self.model_path.is_dir():
                    raise FileNotFoundError("directorio del modelo no configurado")
                from wevisone_vl import WeVisOneVL
                self.model = WeVisOneVL(
                    str(self.model_path),
                    backend="transformers",
                    device=self.device,
                    dtype=self.dtype,
                )
        except (ImportError, OSError, RuntimeError) as exc:
            raise VisualModelUnavailable(
                "WeVisOne-VL no está disponible; instala el complemento visual y configura un modelo local."
            ) from exc
        return self.model

    def detect(self, frame: CapturedFrame) -> SpeakerObservation | None:
        model = self.load()
        with Image.open(frame.path) as source:
            image = source.convert("RGB")
        located = model.predict(
            image,
            task="referring",
            query="the participant tile outlined by the active speaker border",
            decode_matting=False,
        )
        tile = parse_single_box(getattr(located, "response", ""))
        if tile is None:
            return None
        width, height = image.size
        pixels = (
            max(0, min(width - 1, round(tile[0] * width))),
            max(0, min(height - 1, round(tile[1] * height))),
            max(1, min(width, round(tile[2] * width))),
            max(1, min(height, round(tile[3] * height))),
        )
        if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
            return None
        crop = image.crop(pixels)
        recognized = model.predict(crop, task="ocr", decode_matting=False)
        name = _participant_name(getattr(recognized, "response", ""))
        if name is None:
            return None
        return SpeakerObservation(
            frame.offset_seconds,
            frame.offset_seconds + self.observation_seconds,
            name,
            0.75,
            frame.captured_utc,
        )
