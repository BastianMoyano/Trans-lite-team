import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teams_lite.config import default_config_path, load_config


class ConfigTests(unittest.TestCase):
    def test_creates_and_reads_default_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = load_config(path)
            self.assertEqual(config.sample_rate, 48_000)
            self.assertFalse(config.visual_speaker_enabled)
            self.assertTrue(path.exists())

    def test_reads_config_written_with_utf8_bom(self):
        # Windows PowerShell 5.1 `Set-Content -Encoding UTF8` prepends a BOM.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"data_dir": "C:/datos", "sample_rate": 44_100}), encoding="utf-8-sig")
            config = load_config(path)
            self.assertEqual(config.data_dir, "C:/datos")
            self.assertEqual(config.sample_rate, 44_100)

    def test_default_config_path_is_next_to_frozen_executable(self):
        executable = Path(tempfile.gettempdir()) / "TeamsLite" / "TeamsLite.exe"
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(executable)):
            self.assertEqual(default_config_path(), executable.resolve().parent / "config.json")

    def test_default_config_path_is_relative_when_running_from_source(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(default_config_path(), Path("config.json"))

    def test_minutes_cli_defaults_to_claude_and_accepts_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            self.assertEqual("claude", load_config(path).minutes_cli)
            path.write_text(json.dumps({"minutes_cli": "codex"}), encoding="utf-8")
            self.assertEqual("codex", load_config(path).minutes_cli)

    def test_rejects_unknown_minutes_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"minutes_cli": "gemini"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "minutes_cli"):
                load_config(path)

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

