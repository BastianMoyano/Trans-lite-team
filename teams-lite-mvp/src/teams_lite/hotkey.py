"""System-wide hotkey (Windows RegisterHotKey) so moments can be marked while Teams has focus."""
from __future__ import annotations

import ctypes
import sys

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_M = 0x4D

MOMENT_HOTKEY_ID = 0x4D4B  # any value in 0x0000-0xBFFF identifies an application hotkey
MOMENT_HOTKEY_LABEL = "Ctrl+Alt+M"
_NATIVE_WINDOWS_EVENT = "windows_generic_MSG"


def register_moment_hotkey(window_handle: int) -> bool:
    """Register Ctrl+Alt+M for the window; False when another app already owns the combination."""
    if sys.platform != "win32" or not window_handle:
        return False
    return bool(
        ctypes.windll.user32.RegisterHotKey(
            ctypes.c_void_p(window_handle), MOMENT_HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_M
        )
    )


def unregister_moment_hotkey(window_handle: int) -> None:
    if sys.platform == "win32" and window_handle:
        ctypes.windll.user32.UnregisterHotKey(ctypes.c_void_p(window_handle), MOMENT_HOTKEY_ID)


def hotkey_id_from_native_event(event_type, message) -> int | None:
    """Return the hotkey id carried by a Qt native Windows event, or None for anything else."""
    if sys.platform != "win32":
        return None
    # Qt passes a QByteArray (str() of it is "b'...'"); bytes() handles QByteArray, bytes and bytearray.
    kind = event_type if isinstance(event_type, str) else bytes(event_type).decode(errors="replace")
    address = int(message) if message is not None else 0
    if kind != _NATIVE_WINDOWS_EVENT or not address:
        return None
    from ctypes import wintypes

    msg = wintypes.MSG.from_address(address)
    return int(msg.wParam) if msg.message == WM_HOTKEY else None
