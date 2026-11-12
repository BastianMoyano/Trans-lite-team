import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from teams_lite.wevisone import (
    WeVisOneDetector,
    VisualModelUnavailable,
    parse_grounded_text,
    parse_single_box,
)
from teams_lite.window_capture import CapturedFrame


class FakeModel:
    def __init__(self, referring, ocr):
        self.referring = referring
        self.ocr = ocr
        self.calls = []

    def predict(self, image, *, task, query="", decode_matting=False):
        self.calls.append((image.size, task, query, decode_matting))
        response = self.referring if task == "referring" else self.ocr
        return SimpleNamespace(response=response)


class WeVisOneTests(unittest.TestCase):
    def test_parses_normalized_boxes_and_grounded_text(self):
        self.assertEqual((0.1, 0.2, 0.8, 0.9), parse_single_box("<box><100><200><800><900></box>"))
        self.assertEqual(
            [("Rodrigo", (0.1, 0.8, 0.4, 0.9))],
            parse_grounded_text("<ref>Rodrigo</ref><box><100><800><400><900></box>"),
        )

    def test_detects_name_from_active_tile_and_returns_timed_observation(self):
        model = FakeModel(
            "<ref>active speaker</ref><box><100><100><900><900></box>",
            "<ref>microphone</ref><box><50><100><300><200></box>, "
            "<ref>Rodrigo Díaz</ref><box><80><760><520><900></box>",
        )
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "frame.png"
            Image.new("RGB", (1000, 800), "white").save(image_path)
            frame = CapturedFrame(image_path, 12.0, "2026-11-12T14:32:05+00:00", 7)

            observation = WeVisOneDetector(model=model, observation_seconds=2).detect(frame)

        self.assertEqual("Rodrigo Díaz", observation.speaker)
        self.assertEqual((12.0, 14.0), (observation.start, observation.end))
        self.assertGreaterEqual(observation.confidence, 0.6)
        self.assertEqual(["referring", "ocr"], [call[1] for call in model.calls])
        self.assertEqual((800, 640), model.calls[1][0])

    def test_ambiguous_tile_or_missing_name_fails_closed(self):
        ambiguous = FakeModel(
            "<box><0><0><500><500></box>, <box><500><0><1000><500></box>",
            "<ref>Rodrigo</ref><box><100><800><400><900></box>",
        )
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "frame.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            frame = CapturedFrame(image_path, 0, "2026-11-12T14:32:05+00:00", 1)
            self.assertIsNone(WeVisOneDetector(model=ambiguous).detect(frame))

    def test_missing_optional_runtime_has_actionable_error(self):
        def missing_loader():
            raise ImportError("missing")

        with self.assertRaisesRegex(VisualModelUnavailable, "WeVisOne-VL"):
            WeVisOneDetector(model_loader=missing_loader).load()


if __name__ == "__main__":
    unittest.main()
