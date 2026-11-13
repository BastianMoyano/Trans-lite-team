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


class TeamsWindowMinimized(TeamsWindowUnavailable):
    """Teams is open but minimized: Windows does not render its content, so nothing can be read."""


@dataclass(frozen=True)
class TeamsWindow:
    handle: int
    title: str
    bounds: tuple[int, int, int, int]
    minimized: bool = False

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
            bool(user32.IsIconic(handle)),
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
    visible = [window for window in matches if not window.minimized]
    if not visible:
        raise TeamsWindowMinimized(
            "Teams está minimizado: restaura su ventana (puede quedar en otro monitor o detrás de otras)."
        )
    matches = visible
    return max(matches, key=lambda window: (window.area, window.title.casefold(), window.handle))


_PW_RENDERFULLCONTENT = 0x00000002


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def grab_window(*, window: int):
    """Ask the window to render itself with PrintWindow(PW_RENDERFULLCONTENT).

    Teams is WebView2 (Chromium): a screen grab of its window returns plain black, while
    PrintWindow with PW_RENDERFULLCONTENT renders its real content, even when other
    windows cover it (verified with an Edge window). Minimized windows still render nothing.
    """
    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    handle = ctypes.c_void_p(window)
    rectangle = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
        raise TeamsWindowUnavailable("No se pudo leer el tamaño de la ventana de Teams")
    width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
    if width <= 0 or height <= 0:
        raise TeamsWindowUnavailable("La ventana de Teams no tiene un tamaño capturable")
    window_dc = user32.GetWindowDC(handle)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(handle, memory_dc, _PW_RENDERFULLCONTENT):
            raise TeamsWindowUnavailable("Windows no pudo dibujar la ventana de Teams para capturarla")
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0):
            raise TeamsWindowUnavailable("No se pudieron leer los píxeles de la ventana de Teams")
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(handle, window_dc)


def _is_all_black(image) -> bool:
    extrema = getattr(image, "getextrema", None)
    if extrema is None:
        return False
    bands = extrema()
    if bands and not isinstance(bands[0], tuple):
        bands = (bands,)
    return all(high == 0 for _, high in bands)


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
        grabber = grab_window
    window = select_teams_window(windows(), title_hint)
    captured_monotonic = monotonic()
    captured_utc = utc_now()
    try:
        image = grabber(window=window.handle)
    except TypeError:
        image = grabber(bbox=window.bounds, all_screens=True)
    if _is_all_black(image):
        raise TeamsWindowUnavailable("La captura de Teams salió negra; no se guarda una imagen vacía.")
    return _save_frame(image, output_dir, "teams", captured_utc, captured_monotonic, meeting_origin_monotonic,
                       window.handle)


def _save_frame(image, output_dir, prefix, captured_utc, captured_monotonic, meeting_origin_monotonic, handle):
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = captured_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = destination / f"{prefix}-{stamp}.png"
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
        handle,
    )


def cursor_monitor_bounds() -> tuple[int, int, int, int]:
    """Bounds of the monitor under the mouse cursor (where the presenter is working)."""
    if os.name != "nt":
        raise TeamsWindowUnavailable("La captura de pantalla solo está disponible en Windows")

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise TeamsWindowUnavailable("No se pudo leer la posición del cursor")
    monitor = user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(MonitorInfo)
    if not monitor or not user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
        raise TeamsWindowUnavailable("No se pudo identificar el monitor activo")
    area = info.rcMonitor
    return area.left, area.top, area.right, area.bottom


def capture_active_screen(
    output_dir: str | Path,
    meeting_origin_monotonic: float,
    *,
    bounds: Callable[[], tuple[int, int, int, int]] = cursor_monitor_bounds,
    grabber: Callable[..., object] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CapturedFrame:
    """Screenshot the whole monitor under the cursor. Only for moments the user marks on purpose."""
    if grabber is None:
        from PIL import ImageGrab

        grabber = ImageGrab.grab
    area = bounds()
    captured_monotonic = monotonic()
    captured_utc = utc_now()
    image = grabber(bbox=area, all_screens=True)
    if _is_all_black(image):
        raise TeamsWindowUnavailable("La captura de pantalla salió negra; no se guarda una imagen vacía.")
    return _save_frame(image, output_dir, "screen", captured_utc, captured_monotonic, meeting_origin_monotonic, 0)


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
