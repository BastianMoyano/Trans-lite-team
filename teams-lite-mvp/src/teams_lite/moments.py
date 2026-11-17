"""Important moments marked during a recording, with a screenshot of the Teams window.

A moment is always recorded with its time in the recording, even when Teams cannot be
captured (minimized or not visible): the instant itself is useful for the minutes.
Screenshots stay local in data/<meeting>/moments/; the minutes only receive the text.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from PIL import Image

from .window_capture import CapturedFrame, TeamsWindowUnavailable, capture_active_screen, capture_teams_frame

MOMENTS_DIR = "moments"
MOMENTS_FILE = "moments.jsonl"
_JPEG_QUALITY = 85
_write_lock = threading.Lock()


def _to_jpeg(png_path: Path) -> Path:
    jpeg_path = png_path.with_suffix(".jpg")
    with Image.open(png_path) as image:
        image.convert("RGB").save(jpeg_path, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    png_path.unlink(missing_ok=True)
    return jpeg_path


def capture_moment(
    meeting_dir: str | Path,
    recording_origin: float,
    *,
    note: str = "",
    title_hint: str = "Microsoft Teams",
    capture: Callable[..., CapturedFrame] = capture_teams_frame,
    screen_capture: Callable[..., CapturedFrame] | None = capture_active_screen,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict:
    """Mark the current instant and screenshot Teams, or the presenter's screen when Teams is hidden.

    With a single monitor the presenter shares their screen and Teams is often minimized; that
    screen is what participants see, so it is the useful capture. The fallback is only used for
    moments marked on purpose, never by automatic OCR.
    """
    meeting_dir = Path(meeting_dir)
    offset = max(0.0, monotonic() - recording_origin)
    image: str | None = None
    source: str | None = None
    problem: str | None = None
    try:
        frame = capture(meeting_dir / MOMENTS_DIR, recording_origin, title_hint=title_hint)
        source = "teams"
    except TeamsWindowUnavailable as exc:
        problem = str(exc)
        frame = None
        if screen_capture is not None:
            try:
                frame = screen_capture(meeting_dir / MOMENTS_DIR, recording_origin)
                source = "screen"
                problem = f"{exc} Se capturó tu pantalla en su lugar."
            except TeamsWindowUnavailable as screen_exc:
                problem = f"{exc} Tampoco se pudo capturar tu pantalla: {screen_exc}"
    if frame is not None:
        offset = frame.offset_seconds
        image = _to_jpeg(frame.path).relative_to(meeting_dir).as_posix()
    moment = {
        "offset_seconds": round(offset, 3),
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "note": note.strip(),
        "image": image,
        "source": source,
        "problem": problem,
    }
    with _write_lock:
        meeting_dir.mkdir(parents=True, exist_ok=True)
        with (meeting_dir / MOMENTS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(moment, ensure_ascii=False) + "\n")
    return moment


def contained_image(image) -> str | None:
    """A moment image path relative to its meeting folder, or None when it is missing or could leave it.

    moments.jsonl can arrive inside an imported package, so an absolute, drive, UNC or ".." path
    must never be opened: on Windows it could reach another folder or a network share.
    """
    if not isinstance(image, str) or not image or "\\" in image or ":" in image or image.startswith("/"):
        return None
    parts = image.split("/")
    # Windows drops trailing dots and spaces from names, so ".. " would behave like "..".
    if any(part in ("", ".", "..") or part != part.rstrip(" .") for part in parts):
        return None
    return image


def load_moments(meeting_dir: str | Path) -> list[dict]:
    path = Path(meeting_dir) / MOMENTS_FILE
    if not path.is_file():
        return []
    moments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            moment = json.loads(line)
        except ValueError:
            continue  # a line cut by a crash must not hide the other moments
        if isinstance(moment, dict) and isinstance(moment.get("offset_seconds"), (int, float)):
            moment["image"] = contained_image(moment.get("image"))
            moments.append(moment)
    return sorted(moments, key=lambda moment: moment["offset_seconds"])


def format_offset(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def moments_for_minutes(moments: Iterable[dict]) -> str:
    lines = []
    for moment in moments:
        note = moment.get("note") or "(sin nota)"
        suffix = " (con captura de pantalla)" if moment.get("image") else ""
        lines.append(f"- [{format_offset(moment['offset_seconds'])}] {note}{suffix}")
    if not lines:
        return ""
    return "Momentos marcados como importantes por quien grabó:\n" + "\n".join(lines)
