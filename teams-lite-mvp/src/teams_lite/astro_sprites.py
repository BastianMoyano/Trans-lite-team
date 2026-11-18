"""Mascot spritesheets: the user's PNGs, then the characters shipped with the app, then a code-drawn Astro.

Contract for a character (see docs/astro-sprites.md): a folder `data/mascots/<name>/` with one PNG
per state, named `<name>_<state>.png`, `astro_<state>.png` or `<state>.png`. Each PNG is one
horizontal strip of square frames (frame width = image height), transparent background, any
number of frames. A missing or broken file falls back to the placeholder for that state only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from .astro import AstroState

MASCOTS_DIR = "mascots"
DEFAULT_CHARACTER = "astro"
BUILTIN_DIR = Path(__file__).resolve().parent / "assets" / MASCOTS_DIR  # Astro and Brasa ship with the app
FPS = {
    AstroState.IDLE: 4, AstroState.MOVING: 8, AstroState.RECORDING: 5, AstroState.STOPPING: 6,
    AstroState.CAPTURING: 8, AstroState.PROCESSING: 6, AstroState.SUCCESS: 6, AstroState.ERROR: 4,
}
_NAME = re.compile(r"[\w-]{1,40}")


@dataclass
class Animation:
    frames: list[QImage]
    fps: int
    source: str  # "custom" (data/mascots), "builtin" (shipped with the app) or "placeholder"


def _character_folders(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir()
            if entry.is_dir() and _NAME.fullmatch(entry.name) and any(entry.glob("*.png"))}


def list_characters(data_root: str | Path, *, builtin_root: str | Path = BUILTIN_DIR) -> list[str]:
    """Astro first (always available), then the shipped and the user's characters by name."""
    found = _character_folders(Path(data_root) / MASCOTS_DIR) | _character_folders(Path(builtin_root))
    return [DEFAULT_CHARACTER] + sorted(found - {DEFAULT_CHARACTER})


def _strip_frames(image: QImage) -> list[QImage] | None:
    size = image.height()
    if size <= 0 or image.width() % size:
        return None
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return [image.copy(index * size, 0, size, size) for index in range(image.width() // size)]


def _from_folder(folder: Path, character: str, state: AstroState, source: str,
                 problems: list[str]) -> Animation | None:
    if not folder.is_dir():
        return None
    for name in (f"{character}_{state.value}.png", f"astro_{state.value}.png", f"{state.value}.png"):
        path = folder / name
        if not path.is_file():
            continue
        image = QImage(str(path))
        frames = None if image.isNull() else _strip_frames(image)
        if frames is None:
            problems.append(
                f"{name}: no es una tira de fotogramas cuadrados (el ancho debe ser múltiplo del alto); "
                "se usa el dibujo incluido."
            )
            return None
        return Animation(frames, FPS[state], source)
    return None


def load_animations(data_root: str | Path, character: str, *,
                    builtin_root: str | Path = BUILTIN_DIR) -> tuple[dict[AstroState, Animation], list[str]]:
    """One animation per state, plus readable problems for the files that could not be used.

    Per state: the user's folder wins, then the character shipped with the app, then the placeholder.
    """
    valid = bool(_NAME.fullmatch(character or ""))
    user = Path(data_root) / MASCOTS_DIR / character if valid else None
    shipped = Path(builtin_root) / character if valid else None
    animations, problems = {}, []
    for state in AstroState:
        animation = None
        if user is not None:
            animation = _from_folder(user, character, state, "custom", problems)
        if animation is None and shipped is not None:
            animation = _from_folder(shipped, character, state, "builtin", problems)
        animations[state] = animation or placeholder_animation(state)
    return animations, problems


def scale_pixels(image: QImage, scale: int) -> QImage:
    """Whole-number scaling with no smoothing: pixel art stays sharp at any size."""
    return image.scaled(image.width() * scale, image.height() * scale,
                        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)


# ---------------------------------------------------------------------------------------------
# Placeholder: a small astral dragon drawn pixel by pixel (front view, 24x24 on a 32x32 canvas).
_PALETTE = {
    "K": "#1B1440", "P": "#6C4BD8", "L": "#9D86F2", "B": "#DDE6FF", "C": "#4FD1E8", "H": "#2A2A5C",
    "Y": "#F7C948", "W": "#FFFFFF", "E": "#10103A", "M": "#F27A9A",
}
_BODY = [
    "........................",
    "......Y.........Y.......",
    "......YY.......YY.......",
    ".......KKKKKKKKK........",
    "......KLLLLLLLLLK.......",
    ".....KHLLLLLLLLLHK......",
    "....KHHPLLLLLLLPHHK.....",
    "....KCHPPWEPPWEPHCK.....",
    "....KCHPPEEPPEEPHCK.....",
    "....KHHPPPPPPPPPHHK.....",
    ".....KPPPPMMMPPPPK......",
    "......KPPPPPPPPPK.......",
    "...KK..KKBBBBBKK..KK....",
    "..KLLK.KBBBBBBBK.KLLK...",
    ".KLLLLKKBBBBBBBKKLLLLK..",
    "..KLLK.KBBYBBBBK.KLLK...",
    "...KK..KBBBBBBBK..KK....",
    ".......KPBBBBBPK........",
    "........KPPPPPK.........",
    ".........KPPPPKK........",
    "..........KKPPPLK.......",
    "............KKLLK.......",
    "..............KK........",
    "........................",
]
_CANVAS, _ORIGIN = 32, (4, 7)
_ICONS = {
    "rec": ["..RRR..", ".RRRRR.", "RRRRRRR", "RRRRRRR", "RRRRRRR", ".RRRRR.", "..RRR.."],
    "stop": ["KKKKKKK", "KRRRRRK", "KRRRRRK", "KRRRRRK", "KRRRRRK", "KRRRRRK", "KKKKKKK"],
    "camera": [".KKK...", "KKKKKKK", "KCCCCCK", "KCKKKCK", "KCKWKCK", "KCKKKCK", "KKKKKKK"],
    "check": ["......G", ".....GG", "G...GG.", "GG.GG..", ".GGG...", "..G....", "......."],
    "warning": ["...Y...", "..YKY..", "..YKY..", ".YYKYY.", ".YYYYY.", "YYYKYYY", "YYYYYYY"],
    "star": ["...Y...", "...Y...", "YYYYYYY", ".YYYYY.", "..YYY..", ".YY.YY.", "Y.....Y"],
}
_ICON_PALETTE = {"R": "#F04848", "G": "#4ADE80", **_PALETTE}


def _canvas() -> QImage:
    image = QImage(_CANVAS, _CANVAS, QImage.Format.Format_ARGB32)
    image.fill(0)
    return image


def _paint(image: QImage, rows: list[str], left: int, top: int, palette: dict, swap: dict | None = None) -> None:
    for y, row in enumerate(rows):
        for x, key in enumerate(row):
            key = (swap or {}).get(key, key)
            if key != "." and 0 <= left + x < image.width() and 0 <= top + y < image.height():
                image.setPixelColor(left + x, top + y, QColor(palette[key]))


def _dragon(*, dy: int = 0, blink: bool = False, wings_up: bool = False, worried: bool = False,
            happy: bool = False) -> QImage:
    rows = list(_BODY)
    if blink or happy:
        rows[7] = "....KCHPPPPPPPPPHCK....."  # eyes closed: one dark line, drawn below
        rows[8] = "....KCHPKKKPKKKPHCK....." if happy else "....KCHPPKKPPKKPHCK....."
    if worried:
        rows[10] = ".....KPPPPKKKPPPPK......"  # flat mouth
    if wings_up:
        rows[12], rows[13], rows[14], rows[15], rows[16] = (
            ".KK....KKBBBBBKK....KK..", "KLLK...KBBBBBBBK...KLLK.", "KLLLKKKKBBBBBBBKKKKLLLK.",
            ".KK....KBBYBBBBK....KK..", ".......KBBBBBBBK........",
        )
    image = _canvas()
    _paint(image, rows, _ORIGIN[0], _ORIGIN[1] + dy, _PALETTE)
    return image


def _with_icon(image: QImage, icon: str, *, left: int = 24, top: int = 1) -> QImage:
    image = image.copy()
    _paint(image, _ICONS[icon], left, top, _ICON_PALETTE)
    return image


def _with_dots(image: QImage, lit: int) -> QImage:
    image = image.copy()
    for index in range(3):
        color = QColor("#4FD1E8" if index == lit else "#2A2A5C")
        for dx in range(2):
            for dy in range(2):
                image.setPixelColor(20 + index * 4 + dx, 2 + dy, color)
    return image


def _with_trail(image: QImage, phase: int) -> QImage:
    image = image.copy()
    for index, y in enumerate((14, 18, 22)):
        for x in range(0, 3 + (index + phase) % 3):
            image.setPixelColor(x, y, QColor("#4FD1E8"))
    return image


def placeholder_animation(state: AstroState) -> Animation:
    """Calm loops (a slow float and a blink) with one small icon telling the state apart."""
    float_ = [_dragon(dy=0), _dragon(dy=0), _dragon(dy=1), _dragon(dy=1, blink=True)]
    if state is AstroState.IDLE:
        frames = float_
    elif state is AstroState.MOVING:
        frames = [_with_trail(_dragon(dy=dy, wings_up=up), phase)
                  for phase, (dy, up) in enumerate(((0, True), (1, False), (0, True), (1, False)))]
    elif state is AstroState.RECORDING:
        frames = [_with_icon(_dragon(dy=dy), "rec") if lit else _dragon(dy=dy)
                  for dy, lit in ((0, True), (0, True), (1, False), (1, True))]
    elif state is AstroState.STOPPING:
        frames = [_with_icon(_dragon(dy=0, blink=True), "stop"), _with_icon(_dragon(dy=1, blink=True), "stop"),
                  _dragon(dy=1, blink=True), _dragon(dy=0)]
    elif state is AstroState.CAPTURING:
        flash = _with_icon(_dragon(dy=0, happy=True), "camera")
        flash.setPixelColor(30, 0, QColor("#FFFFFF"))
        frames = [_with_icon(_dragon(dy=0), "camera"), flash, _with_icon(_dragon(dy=1, happy=True), "camera"),
                  _with_icon(_dragon(dy=1), "camera")]
    elif state is AstroState.PROCESSING:
        frames = [_with_dots(_dragon(dy=index % 2), index % 3) for index in range(6)]
    elif state is AstroState.SUCCESS:
        frames = [_with_icon(_dragon(dy=-1, happy=True, wings_up=True), "check"),
                  _with_icon(_dragon(dy=0, happy=True), "check"),
                  _with_icon(_dragon(dy=-1, happy=True, wings_up=True), "star"),
                  _with_icon(_dragon(dy=0, happy=True), "star")]
    else:  # ERROR
        frames = [_with_icon(_dragon(dy=1, worried=True), "warning"), _dragon(dy=1, worried=True),
                  _with_icon(_dragon(dy=1, worried=True), "warning"), _dragon(dy=1, worried=True, blink=True)]
    return Animation(frames, FPS[state], "placeholder")
