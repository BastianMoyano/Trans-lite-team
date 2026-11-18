"""Export a meeting to a shareable .zip package and import packages from other PCs.

A package holds `manifest.json`, the meeting files under `files/` and, when asked for,
the audio segments under `audio/`. Packages come from other people: every name and value
is validated before anything is written, and nothing may land outside the meeting folder.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import uuid
import zipfile

from .previews import VISUAL_FRAMES_DIR
from .storage import Storage, _purge_directory

PACKAGE_FORMAT = "teams-lite-meeting"
PACKAGE_VERSION = 1
MANIFEST = "manifest.json"
_FILES, _AUDIO = "files", "audio"
_PLAYABLE_SEGMENTS = {"finalized", "recovered"}


class PackageError(ValueError):
    """The file is not a meeting package this version can import safely."""


def export_meeting(store: Storage, meeting_id: str, destination: str | Path, include_audio: bool = False) -> Path:
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise ValueError("Reunión desconocida")
    if meeting["status"] == "recording":
        raise RuntimeError("No se puede exportar una reunión mientras se está grabando")
    meeting_dir = store.root / meeting_id
    destination = Path(destination)
    segments = [
        segment for segment in store.list_segments(meeting_id)
        if segment["status"] in _PLAYABLE_SEGMENTS and Path(segment["path"]).is_file()
    ] if include_audio else []
    organization, project = _placement_names(store, meeting)
    manifest = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "meeting": {key: meeting[key] for key in ("title", "started_utc", "ended_utc", "status")},
        "organization": organization,
        "project": project,
        "includes_audio": include_audio,
        "streams": [],
        "segments": [],
        "events": [
            {key: event[key] for key in ("timestamp_utc", "monotonic", "kind", "detail")}
            for event in store.list_events(meeting_id)
        ],
    }
    streams = {}
    for segment in segments:
        streams.setdefault(segment["stream_id"], {
            "id": segment["stream_id"], "kind": segment["kind"],
            "sample_rate": segment["sample_rate"], "channels": segment["channels"],
        })
        manifest["segments"].append({
            "stream_id": segment["stream_id"],
            "sequence": segment["sequence"],
            "started_monotonic": segment["started_monotonic"],
            "started_utc": segment["started_utc"],
            "ended_utc": segment["ended_utc"],
            "frames": segment["frames"],
            "status": segment["status"],
            "file": f"{_AUDIO}/{Path(segment['path']).name}",
        })
    manifest["streams"] = list(streams.values())

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in _shared_files(meeting_dir):
                archive.write(path, f"{_FILES}/{path.relative_to(meeting_dir).as_posix()}")
            for segment in segments:
                archive.write(segment["path"], f"{_AUDIO}/{Path(segment['path']).name}")
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(partial, destination)  # an interrupted export never leaves a broken package behind
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _placement_names(store: Storage, meeting: dict) -> tuple[str | None, str | None]:
    organization_id, project_id = meeting.get("organization_id"), meeting.get("project_id")
    if organization_id is None:
        return None, None
    organization = next((org["name"] for org in store.list_organizations() if org["id"] == organization_id), None)
    project = next(
        (row["name"] for row in store.list_projects(organization_id) if row["id"] == project_id), None
    )
    return organization, project


def _shared_files(meeting_dir: Path) -> list[Path]:
    """Meeting files without audio, partial writes or the bulk of the active-speaker frames."""
    if not meeting_dir.is_dir():
        return []
    frames = sorted(
        path for path in (meeting_dir / VISUAL_FRAMES_DIR).glob("*.png") if not path.name.startswith(".")
    )
    skipped_frames = set(frames[1:])  # the first frame stays: it is the fallback preview
    shared = []
    for path in sorted(meeting_dir.rglob("*")):
        if not path.is_file() or path in skipped_frames:
            continue
        if path.suffix.lower() == ".wav" or path.name.endswith(".part") or ".part." in path.name:
            continue
        shared.append(path)
    return shared


def _member_parts(name: str) -> list[str] | None:
    """Split an archive member name, or None when it could escape the destination folder."""
    if "\\" in name or ":" in name or name.startswith("/"):
        return None
    parts = name.rstrip("/").split("/")
    # Windows drops trailing dots and spaces from names, so ".. " would behave like "..".
    if any(part in ("", ".", "..") or part != part.rstrip(" .") for part in parts):
        return None
    return parts


_MAX_INTEGER = 2 ** 62  # SQLite stores signed 64-bit integers


def _text(value, *, optional: bool = False) -> bool:
    return (optional and value is None) or isinstance(value, str)


def _integer(value, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= _MAX_INTEGER


def _number(value) -> bool:
    # json.loads accepts NaN and Infinity, which the database and the transcript timeline cannot use.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    try:
        moment = datetime.fromisoformat(value)
        moment.astimezone()  # the meeting folder name uses local time
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return 1970 <= moment.year <= 9000


def _check_manifest(manifest, audio_members: set[str]) -> None:
    invalid = PackageError("El paquete de reunión está dañado o incompleto")
    if not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT:
        raise PackageError("El archivo no es un paquete de reunión de AstroMeet")
    if not _integer(manifest.get("version"), 1):
        raise invalid
    if manifest["version"] > PACKAGE_VERSION:
        raise PackageError("El paquete se creó con una versión más nueva de AstroMeet; actualiza la aplicación")
    meeting = manifest.get("meeting")
    if not (isinstance(meeting, dict) and _text(meeting.get("title")) and meeting["title"].strip()
            and _timestamp(meeting.get("started_utc")) and _timestamp(meeting.get("ended_utc"), optional=True)
            and _text(meeting.get("status"))):
        raise invalid
    if not (_text(manifest.get("organization"), optional=True) and _text(manifest.get("project"), optional=True)):
        raise invalid
    streams, segments, events = (manifest.get(key) for key in ("streams", "segments", "events"))
    if not all(isinstance(value, list) for value in (streams, segments, events)):
        raise invalid
    stream_ids = set()
    for stream in streams:
        if not (isinstance(stream, dict) and _text(stream.get("id")) and stream.get("kind") in ("microphone", "loopback")
                and _integer(stream.get("sample_rate"), 1) and _integer(stream.get("channels"), 1)):
            raise invalid
        stream_ids.add(stream["id"])
    sequences, files = set(), set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise invalid
        key, file = (segment.get("stream_id"), segment.get("sequence")), segment.get("file")
        if key in sequences or file in files:  # each segment is one row and one WAV of its own
            raise invalid
        sequences.add(key)
        files.add(file)
        if not (segment.get("stream_id") in stream_ids
                and _integer(segment.get("sequence")) and _integer(segment.get("frames"))
                and _number(segment.get("started_monotonic")) and _timestamp(segment.get("started_utc"))
                and _timestamp(segment.get("ended_utc"), optional=True)
                and segment.get("status") in _PLAYABLE_SEGMENTS and segment.get("file") in audio_members):
            raise invalid
    for event in events:
        if not (isinstance(event, dict) and _text(event.get("timestamp_utc")) and _number(event.get("monotonic"))
                and _text(event.get("kind")) and _text(event.get("detail"))):
            raise invalid


def _open(path: str | Path) -> tuple[zipfile.ZipFile, dict, list[tuple[zipfile.ZipInfo, list[str]]]]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError("El archivo no es un paquete de reunión válido") from exc
    try:
        members = []
        audio_members = set()
        for info in archive.infolist():
            if info.filename == MANIFEST:
                continue
            parts = _member_parts(info.filename)
            if parts is not None and len(parts) == 1 and parts[0] in (_FILES, _AUDIO) and info.is_dir():
                continue  # folder entries written by Explorer or 7-Zip when a package is re-zipped
            if parts is None or parts[0] not in (_FILES, _AUDIO) or len(parts) < 2:
                raise PackageError(f"El paquete contiene una ruta no permitida: {info.filename}")
            if parts[0] == _AUDIO and (len(parts) != 2 or info.is_dir()):
                raise PackageError(f"El paquete contiene una ruta no permitida: {info.filename}")
            if parts[0] == _AUDIO:
                audio_members.add(info.filename)
            members.append((info, parts))
        try:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
        except KeyError as exc:
            raise PackageError("El archivo no es un paquete de reunión de AstroMeet") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackageError("El paquete de reunión está dañado o incompleto") from exc
        _check_manifest(manifest, audio_members)
    except BaseException:
        archive.close()
        raise
    return archive, manifest, members


def read_package(path: str | Path) -> dict:
    """Validated manifest of a package, without extracting anything."""
    archive, manifest, _members = _open(path)
    archive.close()
    return manifest


def suggest_placement(store: Storage, manifest: dict) -> tuple[str | None, str | None]:
    """The same-named organization and project on this PC, ignoring case; otherwise the vault."""
    name = manifest.get("organization")
    if not name:
        return None, None
    organization = next(
        (org for org in store.list_organizations() if org["name"].casefold() == name.casefold()), None
    )
    if organization is None:
        return None, None
    project_name = manifest.get("project") or ""
    project = next(
        (row for row in store.list_projects(organization["id"]) if row["name"].casefold() == project_name.casefold()),
        None,
    )
    return organization["id"], project["id"] if project else None


def import_meeting(
    store: Storage, path: str | Path, organization_id: str | None = None, project_id: str | None = None
) -> str:
    """Extract a package into a new meeting filed under the given organization and project."""
    archive, manifest, members = _open(path)
    staging = store.root / f".import-{uuid.uuid4().hex}"
    try:
        with archive:
            needed = sum(info.file_size for info, _parts in members)
            if needed > shutil.disk_usage(store.root).free:
                raise PackageError("No hay espacio suficiente en disco para importar el paquete")
            staging.mkdir()
            root = staging.resolve()
            try:
                for info, parts in members:
                    target = staging.joinpath(*parts[1:])
                    if not target.resolve().is_relative_to(root):  # defence in depth after _member_parts
                        raise PackageError(f"El paquete contiene una ruta no permitida: {info.filename}")
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
            except (OSError, zipfile.BadZipFile) as exc:
                raise PackageError(f"No se pudo extraer el paquete: {exc}") from exc
        segments = [dict(segment, file=segment["file"].split("/", 1)[1]) for segment in manifest["segments"]]
        return store.adopt_meeting(
            staging,
            manifest["meeting"],
            organization_id=organization_id,
            project_id=project_id,
            streams=manifest["streams"],
            segments=segments,
            events=manifest["events"],
        )
    finally:
        if staging.exists():
            _purge_directory(staging)
