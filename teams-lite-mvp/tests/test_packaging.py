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
        for package in ("onnx_asr", "onnxruntime", "soundcard"):
            self.assertIn(f'"{package}"', collected, f"{package} is needed at runtime")


if __name__ == "__main__":
    unittest.main()
