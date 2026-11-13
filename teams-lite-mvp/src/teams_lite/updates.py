"""Check GitHub Releases for a newer Teams Lite version.

Best effort: offline, rate-limited or unexpected answers simply mean "no update offered".
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import http.client
import json
import re
import urllib.request

RELEASES_API = "https://api.github.com/repos/BastianMoyano/Trans-lite-team/releases/latest"
INSTALLER_PREFIX = "TeamsLite-Setup-"
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    page_url: str
    installer_url: str | None


def parse_version(text: str) -> tuple[int, int, int] | None:
    """'v1.2.3' or '1.2.3' as numbers; pre-releases and other tags are not offered."""
    match = _VERSION.fullmatch(str(text).strip())
    return tuple(int(part) for part in match.groups()) if match else None


def fetch_latest_release(url: str = RELEASES_API, timeout: float = 5) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "TeamsLite-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_for_update(current: str, fetch: Callable[[], dict] = fetch_latest_release) -> UpdateInfo | None:
    try:
        release = fetch()
        latest = parse_version(release.get("tag_name", ""))
        installed = parse_version(current)
        if latest is None or installed is None or latest <= installed:
            return None
        installer = next(
            (
                asset.get("browser_download_url")
                for asset in release.get("assets", [])
                if str(asset.get("name", "")).startswith(INSTALLER_PREFIX) and str(asset["name"]).endswith(".exe")
            ),
            None,
        )
        return UpdateInfo(".".join(map(str, latest)), str(release.get("html_url", "")), installer)
    except (OSError, http.client.HTTPException, ValueError, AttributeError, KeyError, TypeError):
        # HTTPException covers answers cut short (IncompleteRead), which are not OSError.
        return None
