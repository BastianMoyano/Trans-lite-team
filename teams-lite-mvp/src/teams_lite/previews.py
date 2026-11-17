"""Picks the screenshot that represents a meeting in the preview browser."""
from __future__ import annotations

from pathlib import Path

from .moments import load_moments

VISUAL_FRAMES_DIR = "visual-frames"


def meeting_thumbnail(meeting_dir: str | Path) -> Path | None:
    """First marked moment with a screenshot on disk, else the earliest visual frame, else None."""
    meeting_dir = Path(meeting_dir)
    if not meeting_dir.is_dir():
        return None
    for moment in load_moments(meeting_dir):  # already sorted by offset
        image = moment.get("image")
        if isinstance(image, str) and image and (meeting_dir / image).is_file():
            return meeting_dir / image
    frames = meeting_dir / VISUAL_FRAMES_DIR
    if frames.is_dir():
        # Names carry the UTC capture stamp, so the alphabetical order is chronological;
        # dot-prefixed files are partial writes.
        candidates = sorted(path for path in frames.glob("*.png") if not path.name.startswith("."))
        if candidates:
            return candidates[0]
    return None
