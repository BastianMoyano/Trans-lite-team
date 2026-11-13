"""Optional OCR of the Teams window to build a meeting attendee list.

OCR text is visual context only. It does not identify voices or who is speaking.
Only the Teams window is read (never the whole screen), and screenshots are not kept.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from .window_capture import CapturedFrame, capture_teams_frame

PARTICIPANTS_FILE = "participants.json"

_TEAMS_UI_LABELS = {
    # Spanish Teams UI
    "chat", "personas", "participantes", "compartir", "salir", "reacciones", "más", "cámara",
    "micrófono", "levantar la mano", "vista", "grabando", "en la sala de espera", "organizador",
    "invitado", "presentador", "asistente", "reunión", "unirse", "silenciar",
    # English Teams UI
    "people", "share", "leave", "react", "more", "camera", "mic", "microphone", "raise hand",
    "view", "recording", "lobby", "organizer", "guest", "presenter", "attendee", "meeting",
    # App and product names that look like proper names
    "microsoft teams", "teams lite", "microsoft", "teams",
}
_NAME_PARTICLES = {"de", "del", "la", "las", "los", "da", "do", "dos", "das", "van", "von", "y", "e"}
_FORBIDDEN_CHARACTERS = set(':/\\@#=<>{}[]_*+~^%$&;"`') | set("0123456789")
_TRAILING_ROLE = re.compile(r"\s*\([^)]*\)\s*$")


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


def _clean_name(line: str) -> str:
    line = line.split("|")[0]
    line = _TRAILING_ROLE.sub("", line)
    return re.sub(r"\s+", " ", line).strip(" -•.,\t")


def _is_name_word(word: str, *, first: bool) -> bool:
    if word.casefold() in _NAME_PARTICLES and not first:
        return True
    if len(word) < 2 or not word[0].isupper():
        return False
    body = word[1:]
    if body.isupper():  # "QQ", "AP": acronyms or initials, not names
        return False
    return all(character.isalpha() or character in "'’-." for character in word)


def looks_like_person_name(line: str) -> bool:
    """Display names in Teams are "First Last"-style: 2-5 capitalized words, no symbols or digits."""
    if not 4 <= len(line) <= 50 or line.casefold() in _TEAMS_UI_LABELS:
        return False
    if any(character in _FORBIDDEN_CHARACTERS for character in line):
        return False
    words = line.split(" ")
    if not 2 <= len(words) <= 5:
        return False
    return all(_is_name_word(word, first=index == 0) for index, word in enumerate(words))


def extract_participant_names(text: str) -> list[str]:
    """Return de-duplicated person names from OCR text of the Teams window."""
    seen: set[str] = set()
    names: list[str] = []
    for raw in text.splitlines():
        candidate = _clean_name(raw)
        if not looks_like_person_name(candidate):
            continue
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            names.append(candidate)
    return names


def _tesseract_recognizer(tesseract_cmd: str | None) -> Callable[[Path], str]:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("Instala Teams Lite con el complemento 'ocr' e instala Tesseract OCR.") from exc
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        languages = "spa+eng" if "spa" in pytesseract.get_languages(config="") else "eng"
    except Exception:
        languages = "eng"

    def recognize(path: Path) -> str:
        with Image.open(path) as image:
            # --psm 11 (sparse text): Teams name labels are separate snippets, not paragraphs;
            # the default mode merges tiles on the same row into one line.
            return pytesseract.image_to_string(image, lang=languages, config="--psm 11")

    return recognize


def read_teams_names(
    output_dir: str | Path,
    *,
    title_hint: str = "Microsoft Teams",
    tesseract_cmd: str | None = None,
    capture: Callable[..., CapturedFrame] = capture_teams_frame,
    recognize: Callable[[Path], str] | None = None,
) -> dict:
    """OCR the Teams window once and return the attendee names it shows.

    Raises TeamsWindowUnavailable when Teams is not visible: it never reads the whole screen.
    """
    output = Path(output_dir)
    frame = capture(output, time.monotonic(), title_hint=title_hint)
    try:
        text = (recognize or _tesseract_recognizer(tesseract_cmd))(frame.path)
    finally:
        frame.path.unlink(missing_ok=True)  # participants' video tiles are not kept on disk
    timestamp = datetime.now(timezone.utc)
    result = {
        "captured_utc": timestamp.isoformat(),
        "names": extract_participant_names(text),
        "warning": "El OCR lista asistentes visibles; no identifica voces ni quién está hablando.",
    }
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / f"ocr-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def load_participants(meeting_dir: str | Path) -> list[str]:
    path = Path(meeting_dir) / PARTICIPANTS_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [str(name) for name in data.get("participants", [])]


def merge_participants(meeting_dir: str | Path, names: Iterable[str]) -> list[str]:
    """Add names to the meeting attendee list, keeping first-seen spelling and order."""
    participants = load_participants(meeting_dir)
    seen = {name.casefold() for name in participants}
    for name in names:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            participants.append(name)
    path = Path(meeting_dir) / PARTICIPANTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"participants": participants}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return participants
