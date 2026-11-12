"""Optional screenshot OCR for participant-name hints.

OCR text is visual context only. It does not identify voices.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re


def extract_name_hints(text: str) -> list[str]:
    """Return conservative, de-duplicated line candidates from OCR text."""
    seen: set[str] = set()
    names: list[str] = []
    for raw in text.splitlines():
        candidate = re.sub(r"\s+", " ", raw).strip(" |-•\t")
        if not 2 <= len(candidate) <= 80 or not any(char.isalpha() for char in candidate):
            continue
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            names.append(candidate)
    return names


def capture_name_hints(
    output_dir: str | Path,
    region: tuple[int, int, int, int] | None = None,
    tesseract_cmd: str | None = None,
) -> dict:
    try:
        from PIL import ImageGrab
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "Instala Teams Lite con el complemento 'ocr' e instala Tesseract OCR."
        ) from exc
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    image = ImageGrab.grab(bbox=region)
    image_path = output / f"ocr-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.png"
    image.save(image_path)
    text = pytesseract.image_to_string(image)
    result = {
        "captured_utc": timestamp.isoformat(),
        "image": str(image_path),
        "name_hints": extract_name_hints(text),
        "warning": "Las pistas visuales del OCR no identifican voces ni demuestran quién está hablando.",
    }
    result_path = image_path.with_suffix(".json")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result

