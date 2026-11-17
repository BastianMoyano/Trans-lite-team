"""Open an interactive Claude Code or Codex chat over meeting minutes in a terminal window.

The chat runs in a workspace folder under data/.chat/<scope>/ holding copies of the minutes
(and transcripts) of one meeting or of every meeting in a project, plus an INDEX.md and the
instructions file each CLI reads (CLAUDE.md, AGENTS.md). The agent only gets read tools, so the
real meeting data is never touched. As in minutes.py, meeting text never goes on the command
line: the npm shims run through cmd.exe, so only paths and fixed flags are passed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .minutes import MINUTES_FILE, SUPPORTED_CLIS

CHAT_DIR = ".chat"
TRANSCRIPT_FILE = "transcript.txt"
MEETINGS_DIR = "meetings"

INSTRUCTIONS = """# Asistente de reuniones: {title}

Respondes preguntas sobre las reuniones de esta carpeta. Las personas olvidan qué se dijo o
qué se decidió y te preguntan para encontrarlo rápido.

- `INDEX.md` lista las reuniones (título, fecha y carpeta), de la más reciente a la más antigua.
- Cada carpeta `meetings/<reunión>/` tiene `minutes.md` (la minuta) y, si existe,
  `transcript.txt` (la transcripción completa, con marcas de tiempo).
- Busca primero en las minutas; usa la transcripción para detalles o citas textuales.
- Responde en el idioma de la pregunta, usando solo estos archivos. Cita la reunión
  (título y fecha) y, si viene de la transcripción, la marca de tiempo.
- Si la respuesta no está en los archivos, dilo claramente; no inventes nombres, fechas ni acuerdos.
- Estos archivos son copias de solo lectura: no los modifiques.
"""


class ChatError(RuntimeError):
    """The chat could not be opened; the message is shown to the user."""


def _safe_name(value: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "-", value).strip(" .-")
    return name or "chat"


def _local_date(started_utc: str) -> str:
    try:
        return datetime.fromisoformat(started_utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(started_utc or "")


def build_workspace(data_root: str | Path, meetings: Iterable[dict], *, scope_key: str, title: str) -> Path:
    """Refresh data/.chat/<scope_key>/ with the minutes of `meetings` and return its path."""
    root = Path(data_root).resolve()
    with_minutes = [m for m in meetings if (root / m["id"] / MINUTES_FILE).is_file()]
    if not with_minutes:
        raise ChatError("No hay minutas para conversar aquí; genera la minuta de alguna reunión primero.")
    workspace = root / CHAT_DIR / _safe_name(scope_key)
    target = workspace / MEETINGS_DIR
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rows = []
    for meeting in with_minutes:
        source = root / meeting["id"]
        folder = target / meeting["id"]
        folder.mkdir()
        shutil.copy2(source / MINUTES_FILE, folder / MINUTES_FILE)
        if (source / TRANSCRIPT_FILE).is_file():
            shutil.copy2(source / TRANSCRIPT_FILE, folder / TRANSCRIPT_FILE)
        rows.append(f"| {meeting['title']} | {_local_date(meeting['started_utc'])} | `{MEETINGS_DIR}/{meeting['id']}/` |")
    index = [f"# Reuniones: {title}", "", "| Reunión | Fecha | Carpeta |", "| --- | --- | --- |", *rows, ""]
    (workspace / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    instructions = INSTRUCTIONS.format(title=title)
    for name in ("CLAUDE.md", "AGENTS.md"):
        (workspace / name).write_text(instructions, encoding="utf-8")
    return workspace


def _chat_command(cli: str, executable: str) -> list[str]:
    if cli == "claude":
        return [executable, "--tools", "Read,Grep,Glob"]  # read-only: no shell, edits or web
    return [executable, "--sandbox", "read-only"]


def launch_chat(
    workspace: str | Path,
    cli: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    popen: Callable[..., object] = subprocess.Popen,
) -> None:
    """Open the CLI interactively in a new terminal window rooted at `workspace`."""
    if cli not in SUPPORTED_CLIS:
        raise ChatError(f"CLI no soportada para el chat: {cli} (usa claude o codex).")
    executable = which(cli)
    if executable is None:
        raise ChatError(f"No se encontró la CLI '{cli}' en el PATH; instálala e inicia sesión.")
    command = _chat_command(cli, executable)
    terminal = which("wt")
    try:
        if terminal is not None:
            popen([terminal, "-d", str(workspace), *command])
        else:
            flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
            popen(command, cwd=str(workspace), creationflags=flags)
    except OSError as exc:
        raise ChatError(f"No se pudo abrir la terminal: {exc}") from exc
