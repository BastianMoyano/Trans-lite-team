import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from PIL import Image

from teams_lite.window_capture import (
    CapturedFrame,
    FrameSampler,
    TeamsWindow,
    TeamsWindowMinimized,
    TeamsWindowUnavailable,
    capture_teams_frame,
    select_teams_window,
)


class MinimizedTeamsTests(unittest.TestCase):
    def test_only_minimized_teams_explains_why_nothing_can_be_captured(self):
        # Windows reports minimized windows as visible, parked at -32000 with a title-bar size.
        windows = [TeamsWindow(9, "Reunión | Microsoft Teams", (-32000, -32000, -31840, -31972), minimized=True)]
        with self.assertRaises(TeamsWindowMinimized) as caught:
            select_teams_window(windows)
        self.assertIsInstance(caught.exception, TeamsWindowUnavailable)
        self.assertIn("minimizado", str(caught.exception))

    def test_prefers_a_visible_teams_window_over_a_minimized_one(self):
        windows = [
            TeamsWindow(9, "Reunión | Microsoft Teams", (-32000, -32000, -31840, -31972), minimized=True),
            TeamsWindow(4, "Vista compacta | Microsoft Teams", (100, 100, 500, 400)),
        ]
        self.assertEqual(4, select_teams_window(windows).handle)

    def test_capture_never_grabs_a_minimized_window(self):
        grabber = mock.Mock()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(TeamsWindowMinimized):
                capture_teams_frame(
                    temp,
                    0.0,
                    windows=lambda: [TeamsWindow(9, "Microsoft Teams", (-32000, -32000, -31840, -31972), minimized=True)],
                    grabber=grabber,
                )
        grabber.assert_not_called()


class WindowCaptureTests(unittest.TestCase):
    def test_selects_largest_matching_teams_window_across_monitors(self):
        windows = [
            TeamsWindow(1, "Teams Lite", (0, 0, 1200, 800)),
            TeamsWindow(2, "Microsoft Teams", (-1920, 0, -960, 700)),
            TeamsWindow(3, "Reunión | Microsoft Teams", (0, 0, 1600, 900)),
        ]

        selected = select_teams_window(windows)

        self.assertEqual(3, selected.handle)

    def test_capture_writes_recoverable_frame_with_relative_time(self):
        window = TeamsWindow(7, "Reunión | Microsoft Teams", (-1200, 10, -200, 710))
        calls = []

        def grabber(**kwargs):
            calls.append(kwargs)
            return Image.new("RGB", (10, 10), "white")

        with tempfile.TemporaryDirectory() as temp:
            frame = capture_teams_frame(
                Path(temp),
                meeting_origin_monotonic=100.0,
                windows=lambda: [window],
                grabber=grabber,
                monotonic=lambda: 112.5,
                utc_now=lambda: datetime(2026, 11, 12, 14, 32, 5, tzinfo=timezone.utc),
            )

            self.assertEqual(12.5, frame.offset_seconds)
            self.assertEqual(7, frame.window_handle)
            self.assertTrue(frame.path.is_file())
            self.assertEqual({"window": 7}, calls[0])

    def test_all_black_capture_is_rejected_instead_of_saved(self):
        # How a failed grab of a GPU-composited (Chromium/WebView2) window looks: a single black color.
        window = TeamsWindow(7, "Reunión | Microsoft Teams", (0, 0, 800, 600))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(TeamsWindowUnavailable) as caught:
                capture_teams_frame(
                    Path(temp),
                    0,
                    windows=lambda: [window],
                    grabber=lambda **kwargs: Image.new("RGB", (800, 600), "black"),
                )
            self.assertIn("negra", str(caught.exception))
            self.assertEqual([], list(Path(temp).iterdir()))

    def test_missing_teams_window_fails_without_capturing_desktop(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(TeamsWindowUnavailable):
                capture_teams_frame(Path(temp), 0, windows=lambda: [])
            self.assertEqual([], list(Path(temp).iterdir()))

    def test_sampler_runs_off_thread_and_stops_cleanly(self):
        captured = []
        ready = threading.Event()

        def capture():
            frame = CapturedFrame(Path("frame.png"), len(captured), "2026-11-12T14:32:05+00:00", 1)
            captured.append(frame)
            if len(captured) >= 2:
                ready.set()
            return frame

        sampler = FrameSampler(capture, lambda frame: None, interval_seconds=0.01)
        started = time.monotonic()
        sampler.start()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(ready.wait(1))
        sampler.stop()

        self.assertFalse(sampler.running)
        self.assertEqual([], sampler.errors)

    def test_sampler_stop_is_safe_during_start_and_from_callback(self):
        original_thread = threading.Thread
        start_entered = threading.Event()
        release_start = threading.Event()
        failures = []

        class DelayedThread:
            def __init__(self, *args, **kwargs):
                self.real = original_thread(*args, **kwargs)
                self.started = False

            def start(self):
                start_entered.set()
                release_start.wait(1)
                self.real.start()
                self.started = True

            def is_alive(self):
                return self.real.is_alive()

            def join(self):
                if not self.started:
                    raise RuntimeError("cannot join thread before it is started")
                self.real.join()

            @property
            def ident(self):
                return self.real.ident

        sampler = None

        def capture():
            return CapturedFrame(Path("frame.png"), 0, "2026-11-12T14:32:05+00:00", 1)

        def stop_from_callback(_frame):
            sampler.stop()

        sampler = FrameSampler(capture, stop_from_callback, interval_seconds=0.01)
        with mock.patch("teams_lite.window_capture.threading.Thread", DelayedThread):
            starter = original_thread(target=lambda: _record_failure(sampler.start, failures))
            starter.start()
            self.assertTrue(start_entered.wait(1))
            stopper = original_thread(target=lambda: _record_failure(sampler.stop, failures))
            stopper.start()
            release_start.set()
            starter.join()
            stopper.join()

        self.assertEqual([], failures)
        self.assertEqual([], sampler.errors)

    def test_failed_image_save_removes_partial_frame(self):
        window = TeamsWindow(7, "Microsoft Teams", (0, 0, 100, 100))

        class BrokenImage:
            def save(self, path, format):
                Path(path).write_bytes(b"partial")
                raise OSError("disk full")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with self.assertRaises(OSError):
                capture_teams_frame(
                    output,
                    0,
                    windows=lambda: [window],
                    grabber=lambda **kwargs: BrokenImage(),
                )
            self.assertEqual([], list(output.iterdir()))


def _record_failure(action, failures):
    try:
        action()
    except Exception as exc:
        failures.append(str(exc))


if __name__ == "__main__":
    unittest.main()
