"""Recoverable capture of the visible Microsoft Teams meeting window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from collections.abc import Callable, Iterable


class TeamsWindowUnavailable(RuntimeError):
    """No visible Teams meeting window can be captured."""


@dataclass(frozen=True)
class TeamsWindow:
    handle: int
    title: str
    bounds: tuple[int, int, int, int]

    @property
    def area(self) -> int:
        left, top, right, bottom = self.bounds
        return max(0, right - left) * max(0, bottom - top)


@dataclass(frozen=True)
class CapturedFrame:
    path: Path
    offset_seconds: float
    captured_utc: str
    window_handle: int


def list_visible_windows() -> list[TeamsWindow]:
    if os.name != "nt":
        raise TeamsWindowUnavailable("La captura de ventanas de Teams solo está disponible en Windows")
    user32 = ctypes.windll.user32
    windows: list[TeamsWindow] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def visit(handle, _context):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)
        rectangle = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            return True
        windows.append(TeamsWindow(
            int(handle),
            title_buffer.value,
            (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom),
        ))
        return True

    user32.EnumWindows(visit, 0)
    return windows


def select_teams_window(
    windows: Iterable[TeamsWindow], title_hint: str = "Microsoft Teams"
) -> TeamsWindow:
    available = [window for window in windows if window.area > 0 and window.title.strip()]
    hint = title_hint.casefold().strip()
    matches = [window for window in available if hint and hint in window.title.casefold()]
    if not matches:
        matches = [
            window for window in available
            if "teams" in window.title.casefold() and "teams lite" not in window.title.casefold()
        ]
    if not matches:
        raise TeamsWindowUnavailable("No se encontró una ventana visible de Microsoft Teams")
    return max(matches, key=lambda window: (window.area, window.title.casefold(), window.handle))


def capture_teams_frame(
    output_dir: str | Path,
    meeting_origin_monotonic: float,
    *,
    title_hint: str = "Microsoft Teams",
    windows: Callable[[], Iterable[TeamsWindow]] = list_visible_windows,
    grabber: Callable[..., object] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CapturedFrame:
    if grabber is None:
        from PIL import ImageGrab
        grabber = ImageGrab.grab
    window = select_teams_window(windows(), title_hint)
    captured_monotonic = monotonic()
    captured_utc = utc_now()
    try:
        image = grabber(window=window.handle)
    except TypeError:
        image = grabber(bbox=window.bounds, all_screens=True)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = captured_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = destination / f"teams-{stamp}.png"
    temporary = destination / f".{path.stem}.part.png"
    try:
        image.save(temporary, format="PNG")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return CapturedFrame(
        path,
        max(0.0, captured_monotonic - meeting_origin_monotonic),
        captured_utc.isoformat(),
        window.handle,
    )


class FrameSampler:
    def __init__(
        self,
        capture: Callable[[], CapturedFrame],
        on_frame: Callable[[CapturedFrame], None],
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("El intervalo de captura debe ser mayor que cero")
        self.capture = capture
        self.on_frame = on_frame
        self.interval_seconds = interval_seconds
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lifecycle:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle:
            if self.running:
                raise RuntimeError("El muestreo visual ya está en ejecución")
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="teams-window-sampler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lifecycle:
            thread = self._thread
        if thread is not None and thread.ident is not None and thread.ident != threading.get_ident():
            thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.on_frame(self.capture())
            except Exception as exc:
                self.errors.append(str(exc))
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, self.interval_seconds - elapsed))
