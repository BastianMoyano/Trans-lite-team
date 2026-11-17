"""Defaults the floating mascot uses to start a recording at once, stored in data/mascot.json."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from .storage import Storage

MASCOT_FILE = "mascot.json"
DEFAULT_TITLE = "Reunión rápida"


@dataclass
class MascotSettings:
    enabled: bool = True
    title: str = DEFAULT_TITLE
    organization_id: str | None = None
    project_id: str | None = None
    position: tuple[int, int] | None = None  # top-left corner on screen, remembered after a drag


def load_mascot_settings(data_root: str | Path) -> MascotSettings:
    """The saved settings; a missing, damaged or odd value falls back to its default."""
    try:
        raw = json.loads((Path(data_root) / MASCOT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return MascotSettings()
    if not isinstance(raw, dict):
        return MascotSettings()
    settings = MascotSettings()
    if isinstance(raw.get("enabled"), bool):
        settings.enabled = raw["enabled"]
    if isinstance(raw.get("title"), str) and raw["title"].strip():
        settings.title = raw["title"].strip()
    if isinstance(raw.get("organization_id"), str) and raw["organization_id"]:
        settings.organization_id = raw["organization_id"]
        if isinstance(raw.get("project_id"), str) and raw["project_id"]:
            settings.project_id = raw["project_id"]  # a project only makes sense inside its organization
    position = raw.get("position")
    if (isinstance(position, list) and len(position) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in position)):
        settings.position = (position[0], position[1])
    return settings


def save_mascot_settings(data_root: str | Path, settings: MascotSettings) -> None:
    path = Path(data_root) / MASCOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{MASCOT_FILE}.part")
    data = asdict(settings)
    data["position"] = list(settings.position) if settings.position is not None else None
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


def resolve_placement(store: Storage, settings: MascotSettings) -> tuple[str | None, str | None, str | None]:
    """Where a quick recording is filed: (organization, project, problem shown to the user or None).

    Defaults can point at an organization or project deleted since they were chosen.
    """
    if settings.organization_id is None:
        return None, None, None
    if settings.organization_id not in {org["id"] for org in store.list_organizations()}:
        return None, None, (
            "La organización por defecto de la mascota ya no existe; la reunión se guarda en el Cofre de reuniones."
        )
    if settings.project_id is None:
        return settings.organization_id, None, None
    if settings.project_id not in {project["id"] for project in store.list_projects(settings.organization_id)}:
        return settings.organization_id, None, (
            "El proyecto por defecto ya no existe; la reunión queda en la organización, sin proyecto."
        )
    return settings.organization_id, settings.project_id, None
