import ctypes
import sys
import unittest
from ctypes import wintypes

from teams_lite.hotkey import WM_HOTKEY, hotkey_id_from_native_event


def native_message(message, w_param=0):
    msg = wintypes.MSG()
    msg.message = message
    msg.wParam = w_param
    return msg


@unittest.skipUnless(sys.platform == "win32", "global hotkeys are Windows specific")
class HotkeyTests(unittest.TestCase):
    def test_reads_the_hotkey_id_from_a_windows_message(self):
        msg = native_message(WM_HOTKEY, 0x4D4B)
        self.assertEqual(0x4D4B, hotkey_id_from_native_event(b"windows_generic_MSG", ctypes.addressof(msg)))
        self.assertEqual(0x4D4B, hotkey_id_from_native_event("windows_generic_MSG", ctypes.addressof(msg)))

    def test_reads_the_event_type_qt_really_passes(self):
        # Qt hands nativeEvent a QByteArray, not bytes; str(QByteArray) is "b'windows_generic_MSG'".
        from PySide6.QtCore import QByteArray

        msg = native_message(WM_HOTKEY, 0x4D4B)
        self.assertEqual(
            0x4D4B, hotkey_id_from_native_event(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg))
        )

    def test_ignores_other_messages_and_event_types(self):
        msg = native_message(0x0100, 7)  # WM_KEYDOWN
        self.assertIsNone(hotkey_id_from_native_event(b"windows_generic_MSG", ctypes.addressof(msg)))
        hotkey = native_message(WM_HOTKEY, 7)
        self.assertIsNone(hotkey_id_from_native_event(b"xcb_generic_event_t", ctypes.addressof(hotkey)))
        self.assertIsNone(hotkey_id_from_native_event(b"windows_generic_MSG", 0))


if __name__ == "__main__":
    unittest.main()
