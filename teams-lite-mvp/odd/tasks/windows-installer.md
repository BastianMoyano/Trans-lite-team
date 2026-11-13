# Windows installer and GitHub publication
Objective: commit all pending work, build a Windows installer for Teams Lite with its dependencies, desktop shortcut and ready config, and push to GitHub.
Branch: codex/active-speaker-tracking · Delivery: ask-on-risk · Test runner: `.venv\Scripts\python -m unittest discover -s tests` (from teams-lite-mvp)

## Specs
- S1: "a este repo debemos subir todo" — repo: "https://github.com/BastianMoyano/Trans-lite-team.git". Commit all pending source work and push it. Excluded (already gitignored, unchanged): `data/`, `models/`, `dist/`, `build/`, `.venv*/`, `config.json`. Assumption: `.atl/` is local tooling and is not pushed; `odd/tasks/` is tracked like the existing feature documents.
- S2: "crear un instalador" — one Windows setup .exe built with the installed Inno Setup 6 from the PyInstaller bundle.
- S3: "ponga todas sus dependencia" — the setup ships the app with its Python runtime and libraries, and installs the Parakeet model. Assumption: the 2.4 GB model is downloaded during installation from Hugging Face (`istupakov/parakeet-tdt-0.6b-v3-onnx`, pinned revision, SHA-256 verified), because it does not fit a GitHub release asset (2 GB) next to the app. Tesseract OCR is offered as an optional install. The minutes CLI (claude/codex) needs a personal sign-in and is documented, not bundled.
- S4: "quedeje configurado todo" — the installer writes config.json with absolute paths under `%LOCALAPPDATA%\TeamsLite` (data and models) and keeps an existing config on upgrade. Per-user install, no admin rights. Uninstall keeps recorded meetings.
- S5: "la app acceso directo desde el escritorio" — desktop shortcut (plus Start menu entry).

## Tasks
- T1 [S1] inline — commit pending work in work units — pending
- T2 [S2-S5] inline — Inno Setup script + build integration + README — pending
- T3 [S2-S5] inline — build the setup and verify install/shortcut/config on this PC — pending
- T4 [S1] inline — add remote and push (public repo: confirm with user) — pending

## Log
- L1 (2026-11-13): "a este repo debemos subir todo y crear un instalador, ponga todas sus dependencia y quedeje configurado todo y la app acceso directo desde el escritorio" + "https://github.com/BastianMoyano/Trans-lite-team.git"
- L2: Findings — remote repo is PUBLIC and empty; no local main branch; Parakeet model is 2.4 GB (encoder-model.onnx.data); Inno Setup 6 at %LOCALAPPDATA%\Programs\Inno Setup 6; HF revision 8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce. Route: inline (no writer trigger: one dependent sequence).
