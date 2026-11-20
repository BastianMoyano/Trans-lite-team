import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from teams_lite.minutes import (
    MINUTES_PROMPT,
    MinutesError,
    generate_minutes,
    load_minutes,
    run_cli,
    run_cli_streaming,
)

TRANSCRIPT = "Asistentes (detectados por OCR): Ana Pérez\n\n[00:00:01.000] Local: Hola"


class FakeRunner:
    def __init__(self, returncode=0, stdout="# Minuta\n\n## Objetivo\nCerrar alcance.\n", stderr="", codex_output=None):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.codex_output = codex_output
        self.calls = []

    def __call__(self, command, stdin_text, cwd, timeout):
        self.calls.append({"command": command, "stdin": stdin_text, "cwd": Path(cwd), "timeout": timeout})
        if self.codex_output is not None:
            Path(command[command.index("-o") + 1]).write_text(self.codex_output, encoding="utf-8")
        return self.returncode, self.stdout, self.stderr


def which(name):
    return f"C:/npm/{name}.CMD"


class MinutesTests(unittest.TestCase):
    def test_claude_writes_minutes_from_stdout_without_tools(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as meeting_dir:
            path = generate_minutes(TRANSCRIPT, meeting_dir, cli="claude", runner=runner, which=which)

            self.assertEqual(Path(meeting_dir) / "minutes.md", path)
            self.assertEqual("# Minuta\n\n## Objetivo\nCerrar alcance.\n", load_minutes(meeting_dir))
            call = runner.calls[0]
            self.assertEqual("C:/npm/claude.CMD", call["command"][0])
            for flag in ("-p", "--strict-mcp-config", "--no-session-persistence"):
                self.assertIn(flag, call["command"])
            self.assertEqual("", call["command"][call["command"].index("--tools") + 1])
            # Without this, user hooks/plugins load (~100 s) and a tool request hangs until the timeout.
            self.assertEqual("", call["command"][call["command"].index("--setting-sources") + 1])
            # Meeting content travels only through stdin, never as a cmd.exe argument.
            self.assertTrue(call["stdin"].startswith(MINUTES_PROMPT))
            self.assertIn(TRANSCRIPT, call["stdin"])
            self.assertFalse(any("Ana Pérez" in part for part in call["command"]))
            # The model runs in an empty scratch folder, not next to the meeting data.
            self.assertNotEqual(Path(meeting_dir), call["cwd"])
            self.assertFalse(call["cwd"].exists())

    def test_codex_reads_the_last_message_file_in_a_read_only_sandbox(self):
        runner = FakeRunner(stdout="progress noise", codex_output="# Minuta Codex\n")
        with tempfile.TemporaryDirectory() as meeting_dir:
            generate_minutes(TRANSCRIPT, meeting_dir, cli="codex", runner=runner, which=which)
            self.assertEqual("# Minuta Codex\n", load_minutes(meeting_dir))
            command = runner.calls[0]["command"]
            self.assertEqual(["C:/npm/codex.CMD", "exec"], command[:2])
            self.assertEqual("read-only", command[command.index("--sandbox") + 1])
            for flag in ("--skip-git-repo-check", "--ephemeral"):
                self.assertIn(flag, command)
            self.assertEqual("-", command[-1])

    def test_missing_cli_names_the_command_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            with self.assertRaisesRegex(MinutesError, "claude"):
                generate_minutes(TRANSCRIPT, meeting_dir, cli="claude", runner=FakeRunner(), which=lambda _: None)
            self.assertIsNone(load_minutes(meeting_dir))

    def test_failed_cli_reports_stderr_and_keeps_previous_minutes(self):
        runner = FakeRunner(returncode=1, stdout="", stderr="Error: not logged in. Run claude auth login")
        with tempfile.TemporaryDirectory() as meeting_dir:
            (Path(meeting_dir) / "minutes.md").write_text("# Minuta anterior\n", encoding="utf-8")
            with self.assertRaisesRegex(MinutesError, "claude auth login"):
                generate_minutes(TRANSCRIPT, meeting_dir, cli="claude", runner=runner, which=which)
            self.assertEqual("# Minuta anterior\n", load_minutes(meeting_dir))

    def test_empty_answer_is_an_error(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            with self.assertRaisesRegex(MinutesError, "vacía"):
                generate_minutes(TRANSCRIPT, meeting_dir, cli="claude", runner=FakeRunner(stdout="  \n"), which=which)
            self.assertIsNone(load_minutes(meeting_dir))

    def test_empty_transcript_is_rejected_before_calling_the_cli(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as meeting_dir:
            with self.assertRaises(MinutesError):
                generate_minutes("   ", meeting_dir, cli="claude", runner=runner, which=which)
            self.assertEqual([], runner.calls)

    def test_unknown_cli_is_rejected(self):
        with tempfile.TemporaryDirectory() as meeting_dir:
            with self.assertRaisesRegex(MinutesError, "gemini"):
                generate_minutes(TRANSCRIPT, meeting_dir, cli="gemini", runner=FakeRunner(), which=which)


@unittest.skipUnless(sys.platform == "win32", "process-tree kill is Windows specific")
class RunCliTests(unittest.TestCase):
    def test_timeout_kills_grandchildren_that_hold_the_pipes(self):
        # Reproduces the npm .CMD shim: cmd.exe starts a child that keeps stdout open.
        with tempfile.TemporaryDirectory() as work:
            script = Path(work) / "shim.cmd"
            script.write_text(f'@"{sys.executable}" -c "import time; time.sleep(60)"\r\n', encoding="utf-8")
            started = os.times().elapsed
            with self.assertRaisesRegex(MinutesError, "tiempo"):
                run_cli([str(script)], "hola", work, timeout=2)
            self.assertLess(os.times().elapsed - started, 20)

    def test_returns_exit_code_and_output(self):
        with tempfile.TemporaryDirectory() as work:
            code, out, err = run_cli(
                [
                    sys.executable,
                    "-c",
                    # Like the Node-based CLIs, the child speaks UTF-8 on its pipes.
                    "import sys; sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8'); "
                    "print(sys.stdin.read().upper())",
                ],
                "hola ñandú",
                work,
                timeout=30,
            )
        self.assertEqual(0, code)
        self.assertEqual("HOLA ÑANDÚ", out.strip())


class RunCliStreamingTests(unittest.TestCase):
    def test_each_line_reaches_the_callback_as_it_arrives(self):
        child = (
            "import sys, time; sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8'); "
            "question = sys.stdin.read(); print('uno', flush=True); time.sleep(0.6); "
            "print(question.upper(), flush=True); print('fallo', file=sys.stderr); sys.exit(3)"
        )
        seen = []
        with tempfile.TemporaryDirectory() as work:
            code, out, err = run_cli_streaming(
                [sys.executable, "-c", child], "hola ñandú", work, 30,
                lambda line: seen.append((line, time.monotonic())),
            )
        self.assertEqual(3, code)
        self.assertEqual(["uno", "HOLA ÑANDÚ"], [line for line, _ in seen])
        self.assertEqual(["uno", "HOLA ÑANDÚ"], out.splitlines())
        self.assertEqual("fallo", err.strip())
        # The first line arrived before the child finished, not all at once at the end.
        self.assertGreater(seen[1][1] - seen[0][1], 0.3)

    @unittest.skipUnless(sys.platform == "win32", "process-tree kill is Windows specific")
    def test_timeout_kills_grandchildren_that_hold_the_pipes(self):
        with tempfile.TemporaryDirectory() as work:
            script = Path(work) / "shim.cmd"
            script.write_text(f'@"{sys.executable}" -c "import time; time.sleep(60)"\r\n', encoding="utf-8")
            started = os.times().elapsed
            with self.assertRaisesRegex(MinutesError, "tiempo"):
                run_cli_streaming([str(script)], "hola", work, 2, lambda _line: None)
            self.assertLess(os.times().elapsed - started, 20)

    def test_missing_executable_is_a_minutes_error(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(MinutesError, "No se pudo ejecutar"):
                run_cli_streaming([str(Path(work) / "missing.exe")], "", work, 5, lambda _line: None)


if __name__ == "__main__":
    unittest.main()
