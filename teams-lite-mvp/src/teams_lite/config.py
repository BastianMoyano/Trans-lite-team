"""Validated JSON configuration for Teams Lite."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys


@dataclass(frozen=True)
class AppConfig:
    data_dir: str = "./data"
    sample_rate: int = 48_000
    segment_seconds: int = 30
    auto_transcribe: bool = True
    parakeet_model_dir: str = "./models/parakeet-v3"
    ocr_enabled: bool = False
    ocr_region: tuple[int, int, int, int] | None = None
    tesseract_cmd: str | None = None
    visual_speaker_enabled: bool = False
    visual_sample_seconds: float = 2.0
    teams_window_title: str = "Microsoft Teams"
    wevisone_model_dir: str = "./models/wevisone-vl-2b"
    minutes_cli: str = "claude"

    def validate(self) -> "AppConfig":
        if self.minutes_cli not in ("claude", "codex"):
            raise ValueError("minutes_cli debe ser claude o codex")
        if self.sample_rate not in (16_000, 44_100, 48_000):
            raise ValueError("sample_rate debe ser 16000, 44100 o 48000")
        if not 5 <= self.segment_seconds <= 300:
            raise ValueError("segment_seconds debe estar entre 5 y 300")
        if self.ocr_region is not None and (
            len(self.ocr_region) != 4 or any(not isinstance(value, int) for value in self.ocr_region)
        ):
            raise ValueError("ocr_region debe contener cuatro coordenadas enteras")
        if not 0.5 <= self.visual_sample_seconds <= 60:
            raise ValueError("visual_sample_seconds debe estar entre 0.5 y 60")
        if not self.teams_window_title.strip():
            raise ValueError("teams_window_title no puede estar vacío")
        return self


def default_config_path() -> Path:
    """config.json next to the packaged executable, or in the working directory from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    return Path("config.json")


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        config = AppConfig().validate()
        path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
        return config
    # utf-8-sig accepts files with or without a BOM (Windows PowerShell 5.1 writes one).
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if raw.get("ocr_region") is not None:
        raw["ocr_region"] = tuple(raw["ocr_region"])
    return AppConfig(**raw).validate()

