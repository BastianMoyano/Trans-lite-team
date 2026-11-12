# Active speaker identification

Objective: identify the visible active Teams participant and associate that evidence with remote transcript segments. Branch: `codex/active-speaker-tracking`. Delivery strategy: `ask-on-risk`; forecast: approximately 380 authored changed lines. Test runner: `python -m unittest discover -s tests -v` with `PYTHONPATH=src` and `QT_QPA_PLATFORM=offscreen`.
Engram mirror: pending because the runtime session is not registered.

## Specs

S1. Capture the Teams meeting surface while recording, including when it is displayed on a secondary monitor. Authorized requirement: "Teams detecta quién habla y enciende el borde."

S2. Sample recoverable meeting images without blocking audio capture or the UI. Authorized requirement: "Capturamos una imagen de la ventana."

S3. Provide an optional WeVisOne-VL visual detector that can locate the participant tile identified by the active-speaker border. It must fail closed when the model is unavailable or evidence is ambiguous. Authorized requirement: "WeVisOne‑VL usa **referring localization** para encontrar “el recuadro con borde de hablante activo”."

S4. Read the participant name associated with the detected tile and preserve confidence/evidence rather than asserting identity without support. Authorized requirement: "Después usa **OCR grounding** para leer el nombre dentro de ese recuadro."

S5. Persist timestamped, meeting-scoped speaker observations such as the exact example `14:32:05 → Rodrigo`, ordered deterministically and recoverable after interruption. Authorized requirement: "Teams Lite guarda algo como `14:32:05 → Rodrigo`."

S6. Associate only remote transcript segments with overlapping, sufficiently confident observations; keep `Remoto/Desconocido` for missing, tied, low-confidence, or stale evidence. Authorized requirement: "Ese dato se cruza con la transcripción remota del mismo instante."

## Tasks

- T1 — S5-S6 — inline — complete — Add timestamped observation storage, deterministic interval matching, and RED/GREEN unit tests. Route: no writer trigger; this is a dependent foundation unit. Evidence: RED `ModuleNotFoundError: teams_lite.visual_speakers`; GREEN 4 focused tests passed. Commit: pending.
- T2 — S1-S2 — inline — pending — Add a Teams-window capture/sampling boundary with secondary-monitor support and graceful failure tests. Route: no writer trigger; depends on T1 observation format.
- T3 — S3-S4 — inline — pending — Add the optional WeVisOne-VL adapter and deterministic fake-backed tests without requiring model weights in the normal suite. Route: no writer trigger; depends on T1-T2 interfaces. Risk: item 5, optional dependencies and runtime environment.
- T4 — S1-S6 — inline — pending — Wire lifecycle/UI/configuration, export attributed transcripts, update documentation, run the full suite, and validate the optional runtime where hardware permits. Route: no writer trigger; integration depends on T1-T3. Risk: item 5, optional dependencies and runtime environment.

## Log

L1 — 2026-11-12 — User request verbatim:

> vamos por eso  
>  
> 1. Teams detecta quién habla y enciende el borde.  
> 2. Capturamos una imagen de la ventana.  
> 3. WeVisOne‑VL usa **referring localization** para encontrar “el recuadro con borde de hablante activo”.  
> 4. Después usa **OCR grounding** para leer el nombre dentro de ese recuadro.  
> 5. Teams Lite guarda algo como `14:32:05 → Rodrigo`.  
> 6. Ese dato se cruza con la transcripción remota del mismo instante.

L2 — 2026-11-12 — Exploration confirmed the current OCR is a one-shot desktop-region capture, while transcription already exposes monotonic-derived remote intervals suitable for overlap matching. The machine exposes Intel integrated graphics only; WeVisOne-VL acceleration is not proven, so the backend remains optional and recording must not depend on it.

L3 — 2026-11-12 — Baseline imported on `codex/active-speaker-tracking` as commit `f05064c` before feature source changes.

L4 — 2026-11-12 — T1 implemented durable JSONL observations with trailing-partial recovery and confidence-weighted overlap attribution. Local segments remain unchanged; remote segments retain `Remoto/Desconocido` when evidence is missing, weak, or tied. Risk: none.
