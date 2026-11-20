"""Generate meeting minutes with a locally installed AI CLI (Claude Code or Codex).

The transcript is sent to the CLI through stdin only. The npm shims (claude.CMD, codex.CMD)
run through cmd.exe, so meeting text must never be passed as a command-line argument.
The model runs without tools (Claude) or in a read-only sandbox (Codex), inside an empty
scratch folder, and only its final answer is written to data/<meeting>/minutes.md.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

MINUTES_FILE = "minutes.md"
SUPPORTED_CLIS = ("claude", "codex")
CLI_LABELS = {"claude": "Claude", "codex": "Codex"}
DEFAULT_TIMEOUT_SECONDS = 600

MINUTES_PROMPT = """Redacta la minuta de la reunión cuya transcripción aparece al final, entre etiquetas <transcripcion>.

Reglas:
- Escribe en el idioma de la transcripción, en Markdown, y responde solo con la minuta.
- Secciones: Objetivo, Asistentes, Temas tratados, Decisiones, Tareas, Pendientes.
- En Tareas usa una tabla con las columnas Tarea, Responsable y Fecha; si algo no se dijo, escribe "No indicado".
- "Local" es la persona que grabó la reunión; "Remoto" agrupa a participantes remotos no identificados.
- La línea "Asistentes (detectados por OCR)", si existe, lista a quienes se vieron en pantalla.
- "Momentos marcados como importantes", si existe, son instantes que quien grabó señaló: relaciónalos con lo
  que se dijo en ese minuto y destácalos en la minuta.
- Usa solo información de la transcripción; no inventes nombres, fechas ni acuerdos."""

Runner = Callable[[list[str], str, str, float], tuple[int, str, str]]


class MinutesError(RuntimeError):
    """Minutes could not be generated; the message is shown to the user."""


def run_cli(command: list[str], stdin_text: str, cwd: str, timeout: float) -> tuple[int, str, str]:
    """Run a CLI feeding stdin; on timeout kill the whole process tree.

    Killing only the npm .CMD shim leaves its node/claude child alive holding the pipes,
    which would block forever, so the tree is terminated with taskkill /T on Windows.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise MinutesError(f"No se pudo ejecutar {Path(command[0]).name}: {exc}") from exc
    try:
        stdout, stderr = process.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        raise MinutesError(
            f"La CLI no respondió dentro del tiempo límite ({round(timeout)} s); se canceló la generación."
        ) from None
    return process.returncode, stdout or "", stderr or ""


def run_cli_streaming(
    command: list[str], stdin_text: str, cwd: str, timeout: float, on_line: Callable[[str], None]
) -> tuple[int, str, str]:
    """Like run_cli, but hand each stdout line to `on_line` as soon as the CLI prints it.

    `on_line` runs on the calling thread. On timeout the whole process tree is killed, which
    closes the pipes and ends the read loop.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise MinutesError(f"No se pudo ejecutar {Path(command[0]).name}: {exc}") from exc

    def feed() -> None:
        try:
            process.stdin.write(stdin_text)
            process.stdin.close()
        except OSError:
            pass  # the CLI exited without reading everything; its exit code tells why

    stderr_parts: list[str] = []
    timed_out = threading.Event()

    def expire() -> None:
        timed_out.set()
        _terminate_tree(process)

    helpers = [threading.Thread(target=feed, daemon=True),
               threading.Thread(target=lambda: stderr_parts.append(process.stderr.read()), daemon=True)]
    for helper in helpers:
        helper.start()
    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    lines: list[str] = []
    try:
        for line in process.stdout:
            lines.append(line)
            on_line(line.rstrip("\r\n"))
    except BaseException:
        _terminate_tree(process)  # a failing callback must not leave the CLI running
        raise
    finally:
        timer.cancel()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        for helper in helpers:
            helper.join(timeout=10)
    if timed_out.is_set():
        raise MinutesError(
            f"La CLI no respondió dentro del tiempo límite ({round(timeout)} s); se canceló la generación."
        )
    return process.returncode, "".join(lines), "".join(stderr_parts)


def _terminate_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()


def _kill_tree(process: subprocess.Popen) -> None:
    _terminate_tree(process)
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _command(cli: str, executable: str, output_path: Path) -> list[str]:
    if cli == "claude":
        return [
            executable, "-p",
            "--tools", "",  # no file, shell or web access: the model only writes the minutes
            # Skip user/project settings: their hooks and plugins add ~100 s per run and, when the
            # model asks for a tool, made `claude -p` hang (verified with an unguessable-file probe).
            "--setting-sources", "",
            "--strict-mcp-config",  # ignore the user's MCP servers
            "--no-session-persistence",
            "--output-format", "text",
        ]
    return [
        executable, "exec",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color", "never",
        "-o", str(output_path),
        "-",  # read the instructions from stdin
    ]


def _error_detail(stdout: str, stderr: str) -> str:
    detail = (stderr.strip() or stdout.strip())[-400:]
    return f": {detail}" if detail else ""


def generate_minutes(
    transcript: str,
    meeting_dir: str | Path,
    *,
    cli: str = "claude",
    runner: Runner = run_cli,
    which: Callable[[str], str | None] = shutil.which,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Ask the CLI for the minutes and save them; previous minutes survive any failure."""
    if cli not in SUPPORTED_CLIS:
        raise MinutesError(f"CLI no soportada para minutas: {cli} (usa claude o codex).")
    if not transcript.strip():
        raise MinutesError("La transcripción está vacía; no hay nada para resumir.")
    executable = which(cli)
    if executable is None:
        raise MinutesError(f"No se encontró la CLI '{cli}' en el PATH; instálala e inicia sesión.")
    stdin_text = f"{MINUTES_PROMPT}\n\n<transcripcion>\n{transcript.strip()}\n</transcripcion>\n"
    with tempfile.TemporaryDirectory(prefix="teams-lite-minutes-") as scratch:
        output_path = Path(scratch) / "minutes.md"
        code, stdout, stderr = runner(_command(cli, executable, output_path), stdin_text, scratch, timeout)
        if code != 0:
            raise MinutesError(f"{cli} terminó con error (código {code}){_error_detail(stdout, stderr)}")
        if cli == "codex":
            minutes = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
        else:
            minutes = stdout
    if not minutes.strip():
        raise MinutesError(f"{cli} devolvió una minuta vacía{_error_detail('', stderr)}")
    destination = Path(meeting_dir) / MINUTES_FILE
    temporary = destination.with_suffix(".md.tmp")
    temporary.write_text(minutes.strip() + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_minutes(meeting_dir: str | Path) -> str | None:
    path = Path(meeting_dir) / MINUTES_FILE
    return path.read_text(encoding="utf-8") if path.is_file() else None
