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


_WEIGHT_FILES = (
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
)


def visual_model_problem(model_path: str | Path | None) -> str | None:
    """Return why the local WeVisOne-VL model cannot load, or None when its files are present.

    This only inspects files, so it is cheap enough to run before every recording.
    """
    if not model_path:
        return "No hay un directorio de modelo WeVisOne-VL configurado (wevisone_model_dir)."
    path = Path(model_path)
    if not path.is_dir():
        return f"No existe el directorio del modelo WeVisOne-VL: {path}"
    if not (path / "config.json").is_file():
        return f"Falta config.json en el modelo WeVisOne-VL: {path}"
    if not any((path / name).is_file() for name in _WEIGHT_FILES):
        return (
            f"Faltan los pesos del modelo WeVisOne-VL (model.safetensors) en {path}; "
            "la descarga está incompleta."
        )
    return None


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


def _same_line(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap >= 0.5 * min(a[3] - a[1], b[3] - b[1])


def _participant_name(response: str) -> str | None:
    """Pick the bottom text line of the tile (where Teams shows the name).

    WeVisOne-VL returns one grounded <ref> per word, so words that share a line are
    joined left to right before choosing the lowest line.
    """
    words = []
    for text, box in parse_grounded_text(response):
        if box[1] < 0.5 or text.casefold() in _UI_LABELS:
            continue
        if not any(character.isalpha() for character in text):
            continue
        words.append((text, box))
    lines: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    for word in words:
        line = next((line for line in lines if _same_line(line[0][1], word[1])), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    candidates = []
    for line in lines:
        text = " ".join(text for text, _ in sorted(line, key=lambda word: word[1][0]))
        if len(text) > 80:
            continue
        bottom = max(box[3] for _, box in line)
        width = max(box[2] for _, box in line) - min(box[0] for _, box in line)
        candidates.append((bottom, width, text))
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
        if self.model_loader is None:
            problem = visual_model_problem(self.model_path)
            if problem is not None:
                raise VisualModelUnavailable(problem)
        try:
            if self.model_loader is not None:
                self.model = self.model_loader()
            else:
                from wevisone_vl import WeVisOneVL
                self.model = WeVisOneVL(
                    str(self.model_path),
                    backend="transformers",
                    device=self.device,
                    dtype=self.dtype,
                )
        except ImportError as exc:
            raise VisualModelUnavailable(
                'El complemento visual WeVisOne-VL no está instalado; instala el extra "vision".'
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise VisualModelUnavailable(f"No se pudo cargar el modelo WeVisOne-VL: {exc}") from exc
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
