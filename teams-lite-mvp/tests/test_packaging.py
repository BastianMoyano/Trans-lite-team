import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def _spec_entry_script(self) -> Path:
        spec = (ROOT / "TeamsLite.spec").read_text(encoding="utf-8")
        match = re.search(r"Analysis\(\s*\[str\(root(?P<parts>(?:\s*/\s*\"[^\"]+\")+)\)\]", spec)
        self.assertIsNotNone(match, "TeamsLite.spec must declare its entry script as root / ...")
        parts = re.findall(r"\"([^\"]+)\"", match["parts"])
        return ROOT.joinpath(*parts)

    def test_executable_entry_script_runs_as_top_level_script(self):
        # PyInstaller runs the entry script outside its package, so relative imports fail there.
        entry = self._spec_entry_script()
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, str(entry), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)


class BundleSizeTests(unittest.TestCase):
    def test_bundle_leaves_out_the_unused_visual_model_stack(self):
        # torch alone was 435 MB of a 965 MB bundle, only for the optional WeVisOne model.
        spec = (ROOT / "TeamsLite.spec").read_text(encoding="utf-8")
        collected = re.search(r"for package in \((?P<body>.*?)\):", spec, re.DOTALL)["body"]
        excludes = re.search(r"excludes=\[(?P<body>.*?)\]", spec, re.DOTALL)["body"]
        for package in ("torch", "torchvision", "transformers", "accelerate", "wevisone_vl",
                        "qwen_vl_utils", "safetensors", "av", "scipy", "matplotlib"):
            self.assertNotIn(f'"{package}"', collected, f"{package} must not be collected")
            self.assertIn(f'"{package}"', excludes, f"{package} must be excluded")
        # pytesseract is imported lazily inside ocr.py, so PyInstaller does not find it on its own.
        for package in ("onnx_asr", "onnxruntime", "soundcard", "pytesseract"):
            self.assertIn(f'"{package}"', collected, f"{package} is needed at runtime")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "installer" / "TeamsLite.iss").read_text(encoding="utf-8")

    def test_installs_per_user_without_admin_rights(self):
        self.assertRegex(self.script, r"(?m)^PrivilegesRequired=lowest$")
        self.assertIn(r"DefaultDirName={localappdata}\Programs\TeamsLite", self.script)

    def test_never_ships_the_build_machine_config(self):
        # build_windows.ps1 writes absolute paths of the build PC into dist\TeamsLite\config.json.
        self.assertRegex(self.script, r'Source: "\{#SourceDir\}\\\*"; Excludes: "config\.json"')

    def test_creates_a_desktop_shortcut(self):
        self.assertRegex(self.script, r'(?m)^Name: "\{autodesktop\}\\Teams Lite"; Filename: "\{app\}\\TeamsLite\.exe"; Tasks: desktopicon')

    def test_downloads_every_model_file_from_a_pinned_revision_with_its_hash(self):
        files = re.findall(r"AddModelFile\('([^']+)', '([0-9a-f]{64})'\)", self.script)
        self.assertEqual(
            sorted(name for name, _ in files),
            ["config.json", "decoder_joint-model.onnx", "encoder-model.onnx", "encoder-model.onnx.data", "vocab.txt"],
        )
        self.assertIn("istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce/", self.script)
        self.assertNotIn("/resolve/main/", self.script)


if __name__ == "__main__":
    unittest.main()
