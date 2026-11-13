import http.client
import json
import unittest
from pathlib import Path

from teams_lite import __version__
from teams_lite.updates import UpdateInfo, check_for_update, parse_version

ROOT = Path(__file__).resolve().parents[1]


def release(tag, assets=("TeamsLite-Setup-0.2.0.exe",)):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/example/releases/tag/{tag}",
        "assets": [{"name": name, "browser_download_url": f"https://github.com/example/download/{name}"}
                   for name in assets],
    }


class UpdateCheckTests(unittest.TestCase):
    def test_newer_release_is_offered_with_its_installer(self):
        info = check_for_update("0.1.0", fetch=lambda: release("v0.2.0"))
        self.assertEqual(
            info,
            UpdateInfo(
                version="0.2.0",
                page_url="https://github.com/example/releases/tag/v0.2.0",
                installer_url="https://github.com/example/download/TeamsLite-Setup-0.2.0.exe",
            ),
        )

    def test_same_or_older_release_is_not_offered(self):
        self.assertIsNone(check_for_update("0.2.0", fetch=lambda: release("v0.2.0")))
        self.assertIsNone(check_for_update("0.10.0", fetch=lambda: release("v0.9.9")))

    def test_versions_compare_as_numbers_not_text(self):
        self.assertIsNotNone(check_for_update("0.9.0", fetch=lambda: release("v0.10.0")))

    def test_release_without_installer_points_to_the_release_page(self):
        info = check_for_update("0.1.0", fetch=lambda: release("v0.2.0", assets=("notes.txt",)))
        self.assertIsNone(info.installer_url)
        self.assertEqual("https://github.com/example/releases/tag/v0.2.0", info.page_url)

    def test_offline_or_unexpected_answers_never_raise(self):
        def offline():
            raise OSError("sin conexión")

        def bad_json():
            raise json.JSONDecodeError("bad", "", 0)

        def cut_short():
            raise http.client.IncompleteRead(b"{")

        for fetch in (offline, bad_json, cut_short, lambda: {}, lambda: release("nightly"),
                      lambda: release("v0.2.0-rc1")):
            with self.subTest(fetch=fetch):
                self.assertIsNone(check_for_update("0.1.0", fetch=fetch))

    def test_parse_version(self):
        self.assertEqual((1, 2, 3), parse_version("v1.2.3"))
        self.assertEqual((1, 2, 3), parse_version("1.2.3"))
        self.assertIsNone(parse_version("1.2"))

    def test_app_version_matches_the_packaged_version(self):
        # build_windows.ps1 names the installer after pyproject.toml; the app compares __version__.
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject)


if __name__ == "__main__":
    unittest.main()
