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

from .minutes import DEFAULT_TIMEOUT_SECONDS, MINUTES_FILE, SUPPORTED_CLIS, MinutesError, run_cli_streaming

ProgressCallback = Callable[[str, str], None]  # (kind, value): ("text", answer so far) or ("status", activity)
StreamingRunner = Callable[[list[str], str, str, float, Callable[[str], None]], tuple[int, str, str]]

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
  (título y fecha) y, si viene de la transcripción, la marca de tiempo de la línea citada
  exactamente con la forma `[HH:MM:SS]`, sin milisegundos: la línea `[00:12:34.500] Local: …`
  se cita como `[00:12:34]`. La app convierte esas marcas en enlaces a la transcripción.
- Si la respuesta no está en los archivos, dilo claramente; no inventes nombres, fechas ni acuerdos.
- Estos archivos son copias de solo lectura: no los modifiques.
"""


class ChatError(RuntimeError):
    """The chat could not be opened; the message is shown to the user."""


class _RunnerError(ChatError):
    """The CLI process itself failed (timeout, could not start), not the conversation."""


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


TIMESTAMP_SCHEME = "ts"  # link scheme of a transcript citation: ts:<seconds>
# Code blocks and code spans are matched only to be kept as written; a [H:MM:SS] or [HH:MM:SS]
# citation (optionally with .mmm) becomes a link unless it already is one or is escaped.
_CODE_OR_TIMESTAMP = re.compile(
    r"(?P<fence>^ {0,3}(?P<mark>`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=mark)[`~]*[ \t]*$|\Z))"
    r"|(?P<code>(?P<ticks>`+)(?:(?!\n[ \t]*\n).)+?(?<!`)(?P=ticks)(?!`))"
    r"|(?<![\\\]])\[(?P<clock>(?P<h>\d{1,2}):(?P<m>[0-5]\d):(?P<s>[0-5]\d)(?:\.\d{1,3})?)\](?![(\[\]])",
    re.MULTILINE | re.DOTALL,
)


def link_timestamps(markdown: str) -> str:
    """Turn transcript citations such as [00:12:34] into ts:<seconds> links, keeping their text."""

    def replace(match: re.Match) -> str:
        if match["clock"] is None:
            return match[0]  # code stays as written
        seconds = int(match["h"]) * 3600 + int(match["m"]) * 60 + int(match["s"])
        return f"[\\[{match['clock']}\\]]({TIMESTAMP_SCHEME}:{seconds})"

    return _CODE_OR_TIMESTAMP.sub(replace, markdown)


def forget_meeting(data_root: str | Path, meeting_id: str) -> None:
    """Remove a deleted meeting from every chat: its copies in each workspace and its own conversation.

    Answers already saved in a project conversation may still quote it; they are not rewritten.
    """
    if not meeting_id or Path(meeting_id).name != meeting_id or meeting_id in (".", ".."):
        return
    try:
        _forget_copies(data_root, meeting_id)
    finally:
        # Last and best effort: a history file Windows keeps locked must not keep the copies.
        try:
            discard_conversation(data_root, meeting_scope(meeting_id))
        except OSError:
            pass


def _forget_copies(data_root: str | Path, meeting_id: str) -> None:
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
# The first character is a letter or digit, so an id can never be read as an option ("--x").
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{7,63}")

# Saved conversations live next to .chat, never inside it: the CLI searches its workspace, and
# earlier answers (possibly quoting a deleted meeting) must not become search material.
HISTORY_DIR = ".chat-history"
_CONVERSATION_VERSION = 1


def conversation_path(data_root: str | Path, scope_key: str) -> Path:
    """Where the conversation of a scope is saved: data/.chat-history/<scope>.json."""
    return Path(data_root).resolve() / HISTORY_DIR / f"{_safe_name(scope_key)}.json"


def _history_is_contained(path: Path) -> bool:
    """A link or junction planted at data/.chat-history must never lead reads or deletes outside."""
    folder = path.parent
    return not folder.exists() or folder.resolve() == folder


def _read_conversation(path: Path) -> dict | None:
    if not _history_is_contained(path):
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # deeply nested JSON exhausts the parser
        return None
    return raw if isinstance(raw, dict) and raw.get("version") == _CONVERSATION_VERSION else None


def save_conversation(data_root: str | Path, scope_key: str, *, title: str, cli: str | None,
                      session: str | None, turns: list[tuple[str, str]]) -> None:
    """Keep a conversation on disk so it survives closing the app; written whole, then moved into place."""
    path = conversation_path(data_root, scope_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _history_is_contained(path):
        raise OSError(f"{HISTORY_DIR} no es una carpeta de la aplicación (es un enlace)")
    # The scope is stored too: two scopes can share a safe file name, and must not read each other.
    payload = {"version": _CONVERSATION_VERSION, "scope": scope_key, "title": title, "cli": cli,
               "session": session, "turns": [[speaker, text] for speaker, text in turns]}
    partial = path.with_name(f".{path.name}.part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(partial, path)


def load_conversation(data_root: str | Path, scope_key: str) -> dict | None:
    """The saved conversation, or None when there is none or the file is damaged.

    The file sits in the data folder, so it is not trusted: an unknown CLI or a session id that
    is not a plain id is dropped (the conversation then starts a new CLI session).
    """
    raw = _read_conversation(conversation_path(data_root, scope_key))
    if raw is None or raw.get("scope") != scope_key:
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
    """Forget the saved conversation of this scope (never another scope's that shares the file name)."""
    path = conversation_path(data_root, scope_key)
    if not _history_is_contained(path):
        return
    raw = _read_conversation(path)
    if raw is None or raw.get("scope") == scope_key:  # a damaged file is dropped too
        path.unlink(missing_ok=True)


def _error_detail(stdout: str, stderr: str) -> str:
    lines = [line.strip() for line in (stderr or stdout or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else "sin detalles"


class ChatSession:
    """One conversation with a CLI over a workspace; each ask() is one turn that keeps the context.

    ask() blocks while the CLI answers (seconds for Claude, up to a minute for Codex), so the UI
    calls it from a worker thread; the CLI's JSON events report the answer and its activity as
    they arrive. A turn that fails leaves the conversation where it was.
    """

    def __init__(
        self,
        workspace: str | Path,
        cli: str,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: StreamingRunner = run_cli_streaming,
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: str | None = None,
    ):
        if cli not in SUPPORTED_CLIS:
            raise ChatError(f"CLI no soportada para el chat: {cli} (usa claude o codex).")
        self.workspace = Path(workspace)
        self.cli = cli
        self._which, self._runner, self._new_id, self._timeout = which, runner, new_id, timeout
        # A saved session is resumed; set once a first turn succeeded otherwise.
        self._session: str | None = session if isinstance(session, str) and _ID.fullmatch(session) else None
        # Until it answers once, a restored session may be gone from the CLI (pruned, other PC).
        self._unconfirmed_restore = self._session is not None
        self.restarted = False  # set when a lost saved session was replaced by a new one

    @property
    def session_id(self) -> str | None:
        """The CLI session to resume next time, once the conversation has one."""
        return self._session

    def ask(self, question: str, on_progress: ProgressCallback | None = None) -> str:
        """Answer one question; `on_progress(kind, value)` hears the answer as it arrives.

        kind is "text" (the answer written so far) or "status" (what the agent is doing, in
        Spanish). It runs on the thread that called ask().
        """
        question = question.strip()
        if not question:
            raise ChatError("Escribe una pregunta.")
        executable = self._which(self.cli)
        if executable is None:
            raise ChatError(f"No se encontró la CLI '{self.cli}' en el PATH; instálala e inicia sesión.")
        report = on_progress or (lambda _kind, _value: None)
        ask = self._ask_claude if self.cli == "claude" else self._ask_codex
        try:
            answer = ask(executable, question, report)
        except _RunnerError:
            raise  # a timeout or a CLI that cannot start: a new session would not help
        except ChatError:
            if not self._unconfirmed_restore:
                raise
            # The saved session no longer exists for the CLI: ask again in a new one, once.
            self._unconfirmed_restore, self._session, self.restarted = False, None, True
            report("status", "La sesión anterior ya no está disponible; empezando una nueva…")
            answer = ask(executable, question, report)
        self._unconfirmed_restore = False
        return answer

    def _run(self, command: list[str], question: str, on_event: Callable[[dict], None]) -> tuple[int, str, str]:
        def on_line(line: str) -> None:
            try:
                event = json.loads(line)
            except ValueError:
                return  # not an event: ignored
            if isinstance(event, dict):
                on_event(event)

        try:
            return self._runner(command, question, str(self.workspace), self._timeout, on_line)
        except MinutesError as exc:
            raise _RunnerError(str(exc).replace("la generación", "la pregunta")) from exc

    def _ask_claude(self, executable: str, question: str, report: ProgressCallback) -> str:
        command = [
            executable, "-p",
            "--tools", READ_ONLY_TOOLS, "--allowedTools", READ_ONLY_TOOLS,
            # As in minutes.py: no user settings, hooks, plugins or MCP servers.
            "--setting-sources", "", "--strict-mcp-config",
            "--append-system-prompt-file", str(self.workspace / "CLAUDE.md"),
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        ]
        session = self._session or self._new_id()
        command += ["--resume", session] if self._session else ["--session-id", session]
        stream = _ClaudeStream(report)
        code, stdout, stderr = self._run(command, question, stream.feed)
        result = stream.result
        if code != 0 or not isinstance(result, dict) or result.get("is_error") or not result.get("result"):
            detail = result.get("result") if isinstance(result, dict) and result.get("result") else _error_detail(stdout, stderr)
            raise ChatError(f"Claude no pudo responder: {detail}")
        self._session = session
        return str(result["result"]).strip()

    def _ask_codex(self, executable: str, question: str, report: ProgressCallback) -> str:
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
            code, stdout, stderr = self._run(command, question, lambda event: _codex_progress(event, report))
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


WRITING = "Escribiendo la respuesta…"
SEARCHING = "Buscando en los archivos…"


def _tool_status(name, tool_input) -> str:
    """Short Spanish description of a Claude tool call, for the progress line."""
    if name == "Read":
        path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        file_name = _one_line(re.split(r"[\\/]", path)[-1], 60) if isinstance(path, str) else ""
        return f"Leyendo {file_name}…" if file_name else "Leyendo un archivo…"
    if name in ("Grep", "Glob"):
        return SEARCHING
    return "Consultando una herramienta…"


class _ClaudeStream:
    """Follows Claude's stream-json events: the text of the current message, tool calls, the result."""

    def __init__(self, report: ProgressCallback):
        self._report = report
        self._text = ""
        self.result: dict | None = None

    def feed(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "stream_event" and isinstance(event.get("event"), dict):
            inner = event["event"]
            if inner.get("type") == "message_start":
                # A turn can hold several messages (text before a tool call, then the answer).
                if self._text:
                    self._text = ""
                    self._report("text", "")
            elif inner.get("type") == "content_block_delta" and isinstance(inner.get("delta"), dict):
                delta = inner["delta"]
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str) and delta["text"]:
                    if not self._text:
                        self._report("status", WRITING)
                    self._text += delta["text"]
                    self._report("text", self._text)
        elif kind == "assistant" and isinstance(event.get("message"), dict):
            content = event["message"].get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self._report("status", _tool_status(block.get("name"), block.get("input")))
        elif kind == "result":
            self.result = event


def _codex_progress(event: dict, report: ProgressCallback) -> None:
    item = event.get("item")
    if not isinstance(item, dict):
        return
    if event.get("type") == "item.started":
        if item.get("type") == "command_execution":
            report("status", SEARCHING)  # the read-only sandbox runs commands to read the copies
        elif item.get("type") == "mcp_tool_call":
            report("status", "Consultando una herramienta…")
    elif event.get("type") == "item.completed" and item.get("type") == "agent_message":
        if isinstance(item.get("text"), str) and item["text"]:
            report("text", item["text"])  # Codex sends each message whole


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
