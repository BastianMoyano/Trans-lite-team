import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from teams_lite.chat import CHAT_DIR, ChatError, build_workspace, launch_chat


def _meeting(root, meeting_id, *, minutes=None, transcript=None, title=None, started="2026-11-10T14:00:00+00:00"):
    folder = Path(root) / meeting_id
    folder.mkdir(parents=True)
    if minutes is not None:
        (folder / "minutes.md").write_text(minutes, encoding="utf-8")
    if transcript is not None:
        (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    return {"id": meeting_id, "title": title or meeting_id, "started_utc": started}


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})


class BuildWorkspaceTests(unittest.TestCase):
    def test_copies_minutes_and_transcripts_of_meetings_with_minutes_only(self):
        with tempfile.TemporaryDirectory() as root:
            kickoff = _meeting(root, "Kickoff", minutes="# Minuta\nDecidimos usar Azure.", transcript="[00:00] Local: Azure")
            no_transcript = _meeting(root, "Seguimiento", minutes="# Minuta\nSin novedades.")
            raw_only = _meeting(root, "Daily", transcript="[00:00] Local: hola")

            workspace = build_workspace(root, [kickoff, no_transcript, raw_only], scope_key="project-a-b", title="Acme › CRM")

            self.assertEqual(workspace, Path(root).resolve() / CHAT_DIR / "project-a-b")
            meetings = workspace / "meetings"
            self.assertEqual(sorted(p.name for p in meetings.iterdir()), ["Kickoff", "Seguimiento"])
            self.assertEqual((meetings / "Kickoff" / "minutes.md").read_text(encoding="utf-8"), "# Minuta\nDecidimos usar Azure.")
            self.assertEqual((meetings / "Kickoff" / "transcript.txt").read_text(encoding="utf-8"), "[00:00] Local: Azure")
            self.assertFalse((meetings / "Seguimiento" / "transcript.txt").exists())
            # The originals are copies, never moved.
            self.assertTrue((Path(root) / "Kickoff" / "minutes.md").is_file())

    def test_writes_index_and_instructions_for_both_clis(self):
        with tempfile.TemporaryDirectory() as root:
            meeting = _meeting(root, "Kickoff", minutes="# Minuta", title="Kickoff CRM")

            workspace = build_workspace(root, [meeting], scope_key="meeting-Kickoff", title="Kickoff CRM")

            index = (workspace / "INDEX.md").read_text(encoding="utf-8")
            self.assertIn("Kickoff CRM", index)
            self.assertIn("meetings/Kickoff/", index)
            claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(claude, (workspace / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("Kickoff CRM", claude)
            self.assertIn("INDEX.md", claude)

    def test_rebuild_drops_meetings_no_longer_in_scope_and_refreshes_minutes(self):
        with tempfile.TemporaryDirectory() as root:
            first = _meeting(root, "Kickoff", minutes="v1")
            second = _meeting(root, "Cierre", minutes="cierre")
            build_workspace(root, [first, second], scope_key="p", title="P")
            (Path(root) / "Kickoff" / "minutes.md").write_text("v2", encoding="utf-8")

            workspace = build_workspace(root, [first], scope_key="p", title="P")

            self.assertEqual([p.name for p in (workspace / "meetings").iterdir()], ["Kickoff"])
            self.assertEqual((workspace / "meetings" / "Kickoff" / "minutes.md").read_text(encoding="utf-8"), "v2")

    def test_scope_without_minutes_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            raw_only = _meeting(root, "Daily", transcript="hola")
            with self.assertRaisesRegex(ChatError, "No hay minutas"):
                build_workspace(root, [raw_only], scope_key="p", title="P")
            with self.assertRaisesRegex(ChatError, "No hay minutas"):
                build_workspace(root, [], scope_key="p", title="P")

    def test_scope_key_cannot_escape_the_chat_folder(self):
        with tempfile.TemporaryDirectory() as root:
            meeting = _meeting(root, "Kickoff", minutes="# Minuta")
            workspace = build_workspace(root, [meeting], scope_key="../../evil", title="P")
            self.assertEqual(workspace.parent, Path(root).resolve() / CHAT_DIR)


class LaunchChatTests(unittest.TestCase):
    def test_claude_opens_in_windows_terminal_with_read_only_tools(self):
        popen = FakePopen()
        which = {"claude": r"C:\npm\claude.CMD", "wt": r"C:\WindowsApps\wt.exe"}.get
        with tempfile.TemporaryDirectory() as workspace:
            launch_chat(workspace, "claude", which=which, popen=popen)

        self.assertEqual(len(popen.calls), 1)
        self.assertEqual(
            popen.calls[0]["command"],
            [r"C:\WindowsApps\wt.exe", "-d", workspace, r"C:\npm\claude.CMD", "--tools", "Read,Grep,Glob"],
        )

    def test_codex_falls_back_to_a_new_console_without_windows_terminal(self):
        popen = FakePopen()
        which = {"codex": r"C:\npm\codex.CMD"}.get
        with tempfile.TemporaryDirectory() as workspace:
            launch_chat(workspace, "codex", which=which, popen=popen)

        call = popen.calls[0]
        self.assertEqual(call["command"], [r"C:\npm\codex.CMD", "--sandbox", "read-only"])
        self.assertEqual(call["cwd"], workspace)
        if sys.platform == "win32":
            self.assertEqual(call["creationflags"], subprocess.CREATE_NEW_CONSOLE)

    def test_missing_cli_raises_without_launching(self):
        popen = FakePopen()
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ChatError, "No se encontró la CLI 'codex'"):
                launch_chat(workspace, "codex", which=lambda _name: None, popen=popen)
        self.assertEqual(popen.calls, [])

    def test_unsupported_cli_is_rejected(self):
        with self.assertRaisesRegex(ChatError, "no soportada"):
            launch_chat(".", "gemini", which=lambda name: name, popen=FakePopen())

    def test_launch_failure_becomes_chat_error(self):
        def failing_popen(command, **kwargs):
            raise OSError("access denied")

        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ChatError, "access denied"):
                launch_chat(workspace, "claude", which={"claude": "claude.CMD"}.get, popen=failing_popen)


if __name__ == "__main__":
    unittest.main()
