import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from teams_lite.moments import capture_moment, load_moments, moments_for_minutes
from teams_lite.window_capture import CapturedFrame, TeamsWindowMinimized


def fake_capture(offset):
    def capture(output_dir, origin, *, title_hint=None):  # Teams capture passes title_hint, screen does not
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"teams-{offset}.png"
        Image.new("RGB", (64, 48), "navy").save(path)
        return CapturedFrame(path, offset, "2026-11-12T20:00:00+00:00", 7)

    return capture


class MomentTests(unittest.TestCase):
    def test_marks_moment_with_a_compressed_teams_screenshot(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            moment = capture_moment(meeting_dir, 0.0, note="  Presupuesto aprobado ", capture=fake_capture(754.2))

            self.assertEqual(754.2, moment["offset_seconds"])
            self.assertEqual("Presupuesto aprobado", moment["note"])
            self.assertIsNone(moment["problem"])
            image = Path(meeting_dir) / moment["image"]
            self.assertEqual(".jpg", image.suffix)
            with Image.open(image) as saved:
                self.assertEqual("JPEG", saved.format)
            self.assertEqual([], list((Path(meeting_dir) / "moments").glob("*.png")))
            self.assertEqual([moment], load_moments(meeting_dir))

    def test_minimized_teams_still_records_the_moment_without_an_image(self):
        def minimized(output_dir, origin, *, title_hint):
            raise TeamsWindowMinimized("Teams está minimizado: restaura su ventana.")

        with tempfile.TemporaryDirectory() as meeting_dir:
            moment = capture_moment(
                meeting_dir, 100.0, capture=minimized, screen_capture=None, monotonic=lambda: 130.5
            )

            self.assertEqual(30.5, moment["offset_seconds"])
            self.assertIsNone(moment["image"])
            self.assertIsNone(moment["source"])
            self.assertIn("minimizado", moment["problem"])
            self.assertEqual([moment], load_moments(meeting_dir))

    def test_minimized_teams_falls_back_to_the_presenters_screen(self):
        # One monitor, presenting: Teams is minimized and what matters is the shared screen.
        def minimized(output_dir, origin, *, title_hint):
            raise TeamsWindowMinimized("Teams está minimizado: restaura su ventana.")

        with tempfile.TemporaryDirectory() as meeting_dir:
            moment = capture_moment(
                meeting_dir, 0.0, note="Demo", capture=minimized, screen_capture=fake_capture(42.0)
            )
            self.assertEqual("screen", moment["source"])
            self.assertEqual(42.0, moment["offset_seconds"])
            self.assertTrue((Path(meeting_dir) / moment["image"]).is_file())
            self.assertIn("minimizado", moment["problem"])
            self.assertIn("tu pantalla", moment["problem"])

    def test_screen_fallback_failure_still_records_the_moment(self):
        def unavailable(*args, **kwargs):
            raise TeamsWindowMinimized("Teams está minimizado")

        with tempfile.TemporaryDirectory() as meeting_dir:
            moment = capture_moment(meeting_dir, 0.0, capture=unavailable, screen_capture=unavailable)
            self.assertIsNone(moment["image"])
            self.assertIsNone(moment["source"])
            self.assertEqual(1, len(load_moments(meeting_dir)))

    def test_teams_capture_is_marked_as_teams_source(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            moment = capture_moment(meeting_dir, 0.0, capture=fake_capture(3.0), screen_capture=None)
            self.assertEqual("teams", moment["source"])

    def test_load_sorts_by_time_and_skips_damaged_lines(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            capture_moment(meeting_dir, 0.0, capture=fake_capture(90.0))
            capture_moment(meeting_dir, 0.0, capture=fake_capture(12.0))
            with (Path(meeting_dir) / "moments.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{roto\n")
            self.assertEqual([12.0, 90.0], [moment["offset_seconds"] for moment in load_moments(meeting_dir)])

    def test_moments_are_summarized_for_the_minutes(self):
        moments = [
            {"offset_seconds": 754.2, "note": "Presupuesto aprobado", "image": "moments/a.jpg", "problem": None},
            {"offset_seconds": 3725.0, "note": "", "image": None, "problem": "Teams está minimizado"},
        ]
        self.assertEqual(
            "Momentos marcados como importantes por quien grabó:\n"
            "- [00:12:34] Presupuesto aprobado (con captura de pantalla)\n"
            "- [01:02:05] (sin nota)",
            moments_for_minutes(moments),
        )
        self.assertEqual("", moments_for_minutes([]))

    def test_records_are_json_lines(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            capture_moment(meeting_dir, 0.0, capture=fake_capture(5.0))
            lines = (Path(meeting_dir) / "moments.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            self.assertEqual(5.0, json.loads(lines[0])["offset_seconds"])


if __name__ == "__main__":
    unittest.main()
