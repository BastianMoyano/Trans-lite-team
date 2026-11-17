"""Finds the screenshots taken in a meeting: for its preview thumbnail and its gallery."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .moments import format_offset, load_moments

VISUAL_FRAMES_DIR = "visual-frames"


def _frame_label(path: Path) -> str:
    # Frame names carry the UTC capture stamp: teams-20261117T100500000000Z.png
    try:
        moment = datetime.strptime(path.stem.split("-", 1)[1][:15], "%Y%m%dT%H%M%S")
    except (IndexError, ValueError):
        return "Hablante activo"
    return f"Hablante activo · {moment:%H:%M:%S} UTC"


def meeting_screenshots(meeting_dir: str | Path) -> list[dict]:
    """Every screenshot of a meeting: marked moments by time, then active-speaker frames.

    Each entry has `path`, `kind` ("moment" or "frame") and a short `label`. Moment images
    that are missing or point outside the meeting folder are skipped.
    """
    meeting_dir = Path(meeting_dir)
    if not meeting_dir.is_dir():
        return []
    root = meeting_dir.resolve()
    shots = []
    for moment in load_moments(meeting_dir):  # sorted by offset; unsafe image paths already dropped
        image = moment.get("image")
        if image is None:
            continue
        path = meeting_dir / image
        if not path.resolve().is_relative_to(root) or not path.is_file():  # defence in depth
            continue
        note = (moment.get("note") or "").strip()
        label = format_offset(moment["offset_seconds"]) + (f" · {note}" if note else "")
        shots.append({"path": path, "kind": "moment", "label": label})
    frames = meeting_dir / VISUAL_FRAMES_DIR
    if frames.is_dir():
        # Names carry the UTC capture stamp, so the alphabetical order is chronological;
        # dot-prefixed files are partial writes.
        for path in sorted(p for p in frames.glob("*.png") if not p.name.startswith(".")):
            shots.append({"path": path, "kind": "frame", "label": _frame_label(path)})
    return shots


def meeting_thumbnail(meeting_dir: str | Path) -> Path | None:
    """First marked moment with a screenshot on disk, else the earliest visual frame, else None."""
    shots = meeting_screenshots(meeting_dir)
    return shots[0]["path"] if shots else None
