# Active speaker identification

Objective: identify the visible active Teams participant and associate that evidence with remote transcript segments, then package the desktop app as a Windows executable. Branch: `codex/active-speaker-tracking`. Delivery strategy: `feature-branch-chain`; revised forecast: approximately 650 authored changed lines. Test runner: `python -m unittest discover -s tests -v` with `PYTHONPATH=src` and `QT_QPA_PLATFORM=offscreen`.
Engram mirror: pending because the runtime session is not registered.

## Specs

S1. Capture the Teams meeting surface while recording, including when it is displayed on a secondary monitor. Authorized requirement: "Teams detecta quién habla y enciende el borde."

S2. Sample recoverable meeting images without blocking audio capture or the UI. Authorized requirement: "Capturamos una imagen de la ventana."

S3. Provide an optional WeVisOne-VL visual detector that can locate the participant tile identified by the active-speaker border. It must fail closed when the model is unavailable or evidence is ambiguous. Authorized requirement: "WeVisOne‑VL usa **referring localization** para encontrar “el recuadro con borde de hablante activo”."

S4. Read the participant name associated with the detected tile and preserve confidence/evidence rather than asserting identity without support. Authorized requirement: "Después usa **OCR grounding** para leer el nombre dentro de ese recuadro."

S5. Persist timestamped, meeting-scoped speaker observations such as the exact example `14:32:05 → Rodrigo`, ordered deterministically and recoverable after interruption. Authorized requirement: "Teams Lite guarda algo como `14:32:05 → Rodrigo`."

S6. Associate only remote transcript segments with overlapping, sufficiently confident observations; keep `Remoto/Desconocido` for missing, tied, low-confidence, or stale evidence. Authorized requirement: "Ese dato se cruza con la transcripción remota del mismo instante."

S7. Produce a Windows executable that starts the complete Teams Lite application without requiring a manual Python command. Model weights remain external so they can be updated independently and do not inflate the executable by several gigabytes. Authorized requirement: "anda por la siguiente etapa y crea un ejecutable  para ejecutar todo".

## Tasks

- T1 — S5-S6 — inline — complete — Add timestamped observation storage, deterministic interval matching, and RED/GREEN unit tests. Route: no writer trigger; this is a dependent foundation unit. Evidence: RED `ModuleNotFoundError: teams_lite.visual_speakers`; GREEN 4 focused tests and 23 full-suite tests passed. Commit: `1bb1c45`.
- T2 — S1-S2 — inline — complete — Add a Teams-window capture/sampling boundary with secondary-monitor support and graceful failure tests. Route: no writer trigger; depends on T1 observation format. Evidence: RED `ModuleNotFoundError: teams_lite.window_capture`; GREEN 4 focused tests, 27 full-suite tests, and native enumeration of 22 visible windows including two Microsoft Teams windows. Commit: `590a086`; bounded concurrency correction: pending.
- T3 — S3-S4 — inline — pending — Add the optional WeVisOne-VL adapter and deterministic fake-backed tests without requiring model weights in the normal suite. Route: no writer trigger; depends on T1-T2 interfaces. Risk: item 5, optional dependencies and runtime environment.
- T4 — S1-S6 — inline — pending — Wire lifecycle/UI/configuration, export attributed transcripts, update documentation, run the full suite, and validate the optional runtime where hardware permits. Route: no writer trigger; integration depends on T1-T3. Risk: item 5, optional dependencies and runtime environment.
- T5 — S7 — inline — pending — Add deterministic Windows packaging, build the executable, and smoke-test its startup. Route: no writer trigger; packaging depends on the completed T2-T4 application. Risk: item 5, delivery and environment.

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

L5 — 2026-11-12 — After T1 reached 219 authored lines, the total feature forecast was revised to approximately 520 lines. The `ask-on-risk` delivery strategy now requires choosing a chain strategy before the next work-unit commit.

L6 — 2026-11-12 — User request verbatim: "anda por la siguiente etapa y crea un ejecutable  para ejecutar todo". The user authorized continuation without selecting the alternate delivery shape, so the previously recommended `feature-branch-chain` strategy is used. Executable packaging is added as S7/T5; model weights remain external because bundling multi-gigabyte weights would make rebuilds and updates impractical.

L7 — 2026-11-12 — T2 implemented native Windows enumeration, largest matching Teams-window selection, secondary-monitor-safe Pillow capture, atomic PNG frame writes, and a stoppable background sampler. A native probe found 22 visible windows and two Microsoft Teams candidates. An initial probe exposed a missing explicit `ctypes.wintypes` import; the root cause was fixed and the probe then succeeded. Risk: item 4 because sampler thread lifecycle and callback ordering are concurrent.

L8 — 2026-11-12 — Independent T2 verification reproduced a stop-during-start race, self-join from callbacks, and retained partial PNG files after save failure. Regression tests observed both failures. One bounded correction now serializes lifecycle transitions, avoids joining the current worker, and always removes partial frames; both targeted regression tests pass. Risk: item 4.
