"""Chat with Claude Code or Codex about meeting minutes, inside the app.

The chat runs in a workspace folder under data/.chat/<scope>/ holding copies of the minutes
(and transcripts) of one meeting or of every meeting in a project, plus an INDEX.md and the
instructions file each CLI reads (CLAUDE.md, AGENTS.md). Each question runs the CLI once in
non-interactive mode and resumes the same session, so the conversation keeps its context.
The agent only gets read tools. As in minutes.py, meeting text and questions never go on the
command line: the npm shims run through cmd.exe, so only paths, ids and fixed flags are passed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .minutes import DEFAULT_TIMEOUT_SECONDS, MINUTES_FILE, SUPPORTED_CLIS, MinutesError, run_cli

CHAT_DIR = ".chat"
TRANSCRIPT_FILE = "transcript.txt"
MEETINGS_DIR = "meetings"

INSTRUCTIONS = """# Asistente de reuniones: {title}

Respondes preguntas sobre las reuniones de esta carpeta. Las personas olvidan qué se dijo o
qué se decidió y te preguntan para encontrarlo rápido.

- `INDEX.md` lista las reuniones (título, fecha y carpeta), de la más reciente a la más antigua.
- Cada carpeta `meetings/<reunión>/` tiene `minutes.md` (la minuta) y/o `transcript.txt`
  (la transcripción completa, con marcas de tiempo); algunas reuniones solo tienen una de las dos.
- Busca primero en las minutas; usa la transcripción para detalles, citas textuales o cuando
  la reunión no tiene minuta.
- Responde en el idioma de la pregunta, usando solo estos archivos. Cita la reunión
  (título y fecha) y, si viene de la transcripción, la marca de tiempo.
- Si la respuesta no está en los archivos, dilo claramente; no inventes nombres, fechas ni acuerdos.
- Estos archivos son copias de solo lectura: no los modifiques.
"""


class ChatError(RuntimeError):
    """The chat could not be opened; the message is shown to the user."""


def _one_line(value, limit: int = 160) -> str:
    """Titles can come from imported packages: keep them on one line so they add no instructions."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _table_cell(value) -> str:
    return _one_line(value).replace("|", "\\|")


def _safe_name(value: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "-", value).strip(" .-")
    return name or "chat"


def _local_date(started_utc: str) -> str:
    try:
        return datetime.fromisoformat(started_utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(started_utc or "")


def has_chat_content(data_root: str | Path, meeting_id: str) -> bool:
    """A meeting can be asked about once it has minutes or a transcript."""
    folder = Path(data_root) / meeting_id
    return (folder / MINUTES_FILE).is_file() or (folder / TRANSCRIPT_FILE).is_file()


def build_workspace(data_root: str | Path, meetings: Iterable[dict], *, scope_key: str, title: str) -> Path:
    """Refresh data/.chat/<scope_key>/ with the minutes and transcripts of `meetings` and return its path.

    Called before every question, so minutes generated mid-conversation reach the next answer.
    """
    root = Path(data_root).resolve()
    with_content = [m for m in meetings if has_chat_content(root, m["id"])]
    if not with_content:
        raise ChatError(
            "No hay minutas ni transcripciones para conversar aquí; transcribe o genera la minuta de una reunión primero."
        )
    workspace = root / CHAT_DIR / _safe_name(scope_key)
    target = workspace / MEETINGS_DIR
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rows = []
    for meeting in with_content:
        source = root / meeting["id"]
        folder = target / meeting["id"]
        folder.mkdir()
        for name in (MINUTES_FILE, TRANSCRIPT_FILE):
            if (source / name).is_file():
                shutil.copy2(source / name, folder / name)
        rows.append(
            f"| {_table_cell(meeting['title'])} | {_local_date(meeting['started_utc'])} | `{MEETINGS_DIR}/{meeting['id']}/` |"
        )
    index = [f"# Reuniones: {_one_line(title)}", "", "| Reunión | Fecha | Carpeta |", "| --- | --- | --- |", *rows, ""]
    (workspace / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    instructions = INSTRUCTIONS.format(title=_one_line(title))
    for name in ("CLAUDE.md", "AGENTS.md"):
        (workspace / name).write_text(instructions, encoding="utf-8")
    return workspace


def meeting_scope(meeting_id: str) -> str:
    """Scope key of the conversation about a single meeting."""
    return f"meeting-{meeting_id}"


def forget_meeting(data_root: str | Path, meeting_id: str) -> None:
    """Remove a deleted meeting from every chat: its copies in each workspace and its own conversation.

    Answers already saved in a project conversation may still quote it; they are not rewritten.
    """
    if not meeting_id or Path(meeting_id).name != meeting_id or meeting_id in (".", ".."):
        return
    chat_root = (Path(data_root).resolve() / CHAT_DIR).resolve()
    if not chat_root.is_dir():
        return

    def inside_chat(workspace: Path) -> bool:
        # A junction or link planted in .chat must never lead the cleanup outside it.
        return workspace.resolve().parent == chat_root

    own = chat_root / _safe_name(meeting_scope(meeting_id))
    # Two ids can share a safe name; the workspace is this meeting's only if it holds its copy.
    if (own / MEETINGS_DIR / meeting_id).is_dir() and inside_chat(own):
        shutil.rmtree(own, ignore_errors=True)
    for workspace in chat_root.iterdir():
        if not inside_chat(workspace):
            continue
        copy = workspace / MEETINGS_DIR / meeting_id
        if copy.is_dir() and copy.resolve().parent == (workspace / MEETINGS_DIR).resolve():
            shutil.rmtree(copy, ignore_errors=True)


READ_ONLY_TOOLS = "Read,Grep,Glob"  # no shell, edits or web
_ANSWER_FILE = ".chat-answer.txt"
# Session and thread ids go on the command line, which cmd.exe parses: accept only plain ids.
_ID = re.compile(r"[0-9A-Za-z-]{8,64}")

CONVERSATION_FILE = "conversation.json"
_CONVERSATION_VERSION = 1


def _conversation_path(data_root: str | Path, scope_key: str) -> Path:
    return Path(data_root).resolve() / CHAT_DIR / _safe_name(scope_key) / CONVERSATION_FILE


def save_conversation(data_root: str | Path, scope_key: str, *, title: str, cli: str | None,
                      session: str | None, turns: list[tuple[str, str]]) -> None:
    """Keep a conversation on disk so it survives closing the app; written whole, then moved into place."""
    path = _conversation_path(data_root, scope_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _CONVERSATION_VERSION, "title": title, "cli": cli, "session": session,
               "turns": [[speaker, text] for speaker, text in turns]}
    partial = path.with_name(f".{path.name}.part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(partial, path)


def load_conversation(data_root: str | Path, scope_key: str) -> dict | None:
    """The saved conversation, or None when there is none or the file is damaged.

    The file sits in the data folder, so it is not trusted: an unknown CLI or a session id that
    is not a plain id is dropped (the conversation then starts a new CLI session).
    """
    try:
        raw = json.loads(_conversation_path(data_root, scope_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != _CONVERSATION_VERSION:
        return None
    turns = raw.get("turns")
    if not isinstance(turns, list) or not all(
            isinstance(turn, list) and len(turn) == 2 and all(isinstance(part, str) for part in turn)
            for turn in turns):
        return None
    cli = raw.get("cli") if raw.get("cli") in SUPPORTED_CLIS else None
    session = raw.get("session")
    if cli is None or not isinstance(session, str) or not _ID.fullmatch(session):
        session = None
    title = raw.get("title")
    return {"title": title if isinstance(title, str) else "", "cli": cli, "session": session,
            "turns": [(speaker, text) for speaker, text in turns]}


def discard_conversation(data_root: str | Path, scope_key: str) -> None:
    _conversation_path(data_root, scope_key).unlink(missing_ok=True)


def _error_detail(stdout: str, stderr: str) -> str:
    lines = [line.strip() for line in (stderr or stdout or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else "sin detalles"


class ChatSession:
    """One conversation with a CLI over a workspace; each ask() is one turn that keeps the context.

    ask() blocks while the CLI answers (seconds for Claude, up to a minute for Codex), so the UI
    calls it from a worker thread. A turn that fails leaves the conversation where it was.
    """

    def __init__(
        self,
        workspace: str | Path,
        cli: str,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[[list[str], str, str, float], tuple[int, str, str]] = run_cli,
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: str | None = None,
    ):
        if cli not in SUPPORTED_CLIS:
            raise ChatError(f"CLI no soportada para el chat: {cli} (usa claude o codex).")
        self.workspace = Path(workspace)
        self.cli = cli
        self._which, self._runner, self._new_id, self._timeout = which, runner, new_id, timeout
        # A saved session (conversation.json) is resumed; set once a first turn succeeded otherwise.
        self._session: str | None = session if isinstance(session, str) and _ID.fullmatch(session) else None

    @property
    def session_id(self) -> str | None:
        """The CLI session to resume next time, once the conversation has one."""
        return self._session

    def ask(self, question: str) -> str:
        question = question.strip()
        if not question:
            raise ChatError("Escribe una pregunta.")
        executable = self._which(self.cli)
        if executable is None:
            raise ChatError(f"No se encontró la CLI '{self.cli}' en el PATH; instálala e inicia sesión.")
        if self.cli == "claude":
            return self._ask_claude(executable, question)
        return self._ask_codex(executable, question)

    def _run(self, command: list[str], question: str) -> tuple[int, str, str]:
        try:
            return self._runner(command, question, str(self.workspace), self._timeout)
        except MinutesError as exc:
            raise ChatError(str(exc).replace("la generación", "la pregunta")) from exc

    def _ask_claude(self, executable: str, question: str) -> str:
        command = [
            executable, "-p",
            "--tools", READ_ONLY_TOOLS, "--allowedTools", READ_ONLY_TOOLS,
            # As in minutes.py: no user settings, hooks, plugins or MCP servers.
            "--setting-sources", "", "--strict-mcp-config",
            "--append-system-prompt-file", str(self.workspace / "CLAUDE.md"),
            "--output-format", "json",
        ]
        session = self._session or self._new_id()
        command += ["--resume", session] if self._session else ["--session-id", session]
        code, stdout, stderr = self._run(command, question)
        try:
            result = json.loads(stdout)
        except ValueError:
            result = None
        if code != 0 or not isinstance(result, dict) or result.get("is_error") or not result.get("result"):
            detail = result.get("result") if isinstance(result, dict) and result.get("result") else _error_detail(stdout, stderr)
            raise ChatError(f"Claude no pudo responder: {detail}")
        self._session = session
        return str(result["result"]).strip()

    def _ask_codex(self, executable: str, question: str) -> str:
        answer_path = self.workspace / _ANSWER_FILE
        if self._session:
            command = [executable, "exec", "resume", self._session, "--json",
                       "-c", "sandbox_mode=read-only", "--skip-git-repo-check"]
        else:
            command = [executable, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check",
                       "--color", "never"]
        command += ["-o", str(answer_path), "-"]  # the question is read from stdin
        answer_path.unlink(missing_ok=True)
        try:
            code, stdout, stderr = self._run(command, question)
            answer = answer_path.read_text(encoding="utf-8").strip() if answer_path.is_file() else ""
        finally:
            answer_path.unlink(missing_ok=True)
        if code != 0 or not answer:
            raise ChatError(f"Codex no pudo responder: {_error_detail(stdout, stderr)}")
        if not self._session:
            thread = _codex_thread(stdout)
            if thread is None:
                raise ChatError("Codex respondió, pero no devolvió la conversación para poder continuarla.")
            self._session = thread
        return answer


def _codex_thread(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread = event.get("thread_id")
            return thread if isinstance(thread, str) and _ID.fullmatch(thread) else None
    return None
