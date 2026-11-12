import json
import tempfile
import unittest
from pathlib import Path

from teams_lite.config import load_config


class ConfigTests(unittest.TestCase):
    def test_creates_and_reads_default_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = load_config(path)
            self.assertEqual(config.sample_rate, 48_000)
            self.assertFalse(config.visual_speaker_enabled)
            self.assertTrue(path.exists())

    def test_rejects_unsafe_segment_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"segment_seconds": 1}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "segment_seconds"):
                load_config(path)

    def test_rejects_invalid_visual_sampling_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"visual_sample_seconds": 0}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "visual_sample_seconds"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()

