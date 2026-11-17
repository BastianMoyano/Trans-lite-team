# In-app chat about meeting minutes
Objective: "Conversar con IA" runs inside Teams Lite as a chat panel instead of opening a terminal window.
Branch: feat/in-app-chat · Delivery: ask-on-risk · Test runner: `.venv\Scripts\python -m unittest discover -s tests` (from teams-lite-mvp)

## Specs
- S1: "el conversar con la ia debe salir en la seccion de la misma aplicacion no que abra una ventana nueva, como si fuera perte de la apps" — the chat is part of the app window; no terminal or external window opens.
- S2: placement answer "En la misma sección (Recommended)": in Teams Lite, a "Chat" tab next to Transcripción / Minuta for the selected meeting; in Vista previa, a chat panel to the right of the meeting list of the project (or Cofre / Sin proyecto) being browsed.
- S3: from the placement question: "escribes la pregunta, la respuesta aparece en la app y la conversación sigue con contexto" — multi-turn: each answer keeps the context of the earlier turns of that conversation. Assumptions: one conversation per meeting or project while the app is open; "Nueva conversación" starts over and rebuilds the copied minutes; only meetings with minutes are included (unchanged rule); answers render as Markdown.
- Assumptions (engine): questions go through stdin only (never on the command line). Claude runs `claude -p` with read-only tools Read/Grep/Glob, no user settings/hooks/MCP, the instructions from a file, and `--session-id`/`--resume`. Codex runs `codex exec --json --sandbox read-only` and `codex exec resume <thread>` with `-c sandbox_mode=read-only`; Codex keeps the user's Codex configuration (plugins and MCP servers), which is documented.

## Tasks
- T1 [S1,S3] inline — chat engine: ChatSession over both CLIs, terminal launch removed, untrusted titles sanitized in instructions/index — Risk: item 2 (external CLI process and its read-only tool flags) — done, RED import error → GREEN 13 chat tests
- T2 [S1,S2,S3] inline — ChatPanel UI, Teams Lite "Chat" tab, Vista previa side panel, README — done, RED (app import) → GREEN 6 UI tests; suite 194 OK; offscreen screenshots of both placements

## Log
- L1 (2026-11-17): "el conversar con la ia debe salir en la seccion de la misma aplicacion no que abra una ventana nueva, como si fuera perte de la apps" + placement answer "En la misma sección (Recommended)".
- L2: Probes on a dummy folder (no meeting data). Claude 2.1.326: `claude -p --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --setting-sources "" --strict-mcp-config --append-system-prompt-file <file> --session-id <uuid> --output-format json` read the file (~6 s); `--resume <uuid>` kept context (~5 s). Codex 0.167.2: `codex exec --json --sandbox read-only --skip-git-repo-check -o <file> -` answered (27-84 s) and `codex exec resume <thread_id> --json -c sandbox_mode=read-only -o <file> -` kept context (15 s); thread id comes from the `thread.started` event. `--ignore-user-config` broke Codex file reading (Windows sandbox policy); `-c mcp_servers={}`, `--disable plugins/apps` did not stop the user's engram MCP calls, so Codex keeps the user's config.
- L3: Commit 1ded9fa. A full-suite run before it reported 2 failures that later runs did not reproduce: 9 more full runs gave 8 OK and 1 failure in test_audio test_default_device_change_switches_to_the_new_device (timing-based audio test, untouched here; test_audio flakiness already logged in windows-installer L12); ChatUiTests + test_chat 10/10 OK in isolation. Follow-up: stabilize the test_audio timing tests.
