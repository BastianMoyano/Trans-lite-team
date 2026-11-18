# Astro — animated pixel-art mascot
Objective: turn the floating mascot into Astro, a pixel-art astral dragon whose animation follows the real recording, capture, processing and error events.
Branch: feat/mascot-quick-actions (not delivered yet) · Delivery: ask-on-risk · Test runner: `.venv\Scripts\python -m unittest discover -s tests` (from teams-lite-mvp)

## Specs
- S1: states and messages (user prompt §3), verbatim names: IDLE "Listo para comenzar"; MOVING; RECORDING "Grabando reunión"; STOPPING; CAPTURING "Captura realizada"; PROCESSING "Procesando grabación"; SUCCESS "¡Reunión guardada!"; ERROR (an understandable error message). "Crear una máquina de estados que controle las transiciones de Astro. El estado visual debe actualizarse a partir de eventos reales del sistema, no mediante simulaciones." "La operación de grabación nunca debe depender de que una animación termine o se ejecute correctamente."
- S2: real operations (§4): record, stop (confirm when stored), capture a screenshot of the meeting window (camera animation when it succeeds), processing shows transcription progress and its end or error.
- S3: interaction (§5): "Al hacer clic en Astro, mostrar un pequeño menú de acciones" with start/stop and capture; "Mostrar mensajes breves mediante globos pixel art"; "No interrumpir el trabajo del usuario"; "Evitar animaciones excesivamente llamativas"; "Permitir silenciar los efectos y desactivar movimientos". Assumption: the menu also keeps OCR and "Abrir Teams Lite" from the previous mascot spec (mascot-quick-actions S2); sounds = the confirmation beep of quick captures.
- S4: graphics (§2, §7): eight spritesheets astro_idle.png … astro_error.png, transparent background, uniform frame size, same character scale, loopable. Answer: "Motor ahora, sprites tuyos (Recommended)" — the engine ships with a simple code-drawn pixel-art placeholder; the user's PNGs placed in `data/mascots/astro/` (one folder per character, S6) replace it without code changes. Contract: one horizontal strip per state, square frames (frame width = image height), any frame count; scaled by whole numbers with no smoothing ("Tamaño adaptable sin perder la definición de los píxeles").
- S5: technical (§6): low CPU and memory, no GPU; UI timers play the frames.
- S6: "tambien quiero esta mascota" (second reference image: an orange dragon with headphones named "AstroMeet"). That design recreates Charizard (Pokémon): orange body, teal wing membranes, cream belly, flame on the tail tip, so it is not reproduced; the Teams/Zoom logos in it are not used either. Scope: the engine supports several characters, chosen in the Mascota section, each a folder `data/mascots/<name>/` with the eight PNGs; docs include a prompt for an original fire dragon with a distinct silhouette and palette.

## Tasks
- T1 [S1,S2] inline — astro.AstroMachine: event-driven states, transient states with durations, messages, progress — done, RED (no module) → GREEN 8 with a fake clock
- T2 [S4,S5,S6] inline — sprite loading per character (user PNGs or placeholder), frame contract validation, nearest-neighbour scaling — done, RED (no module) → GREEN 5; placeholder contact sheet inspected (8 states, 4-6 frames each)
- T3 [S1-S3,S5] inline — Astro widget (sprite, bubble, click menu, drag = MOVING, sound/motion settings, size) wired to real app events — pending
- T4 [S4,S6] inline — docs/astro-sprites.md (contract + prompts for Astro and an original fire dragon), README — pending

## Log
- L1 (2026-11-17): user pasted "PROMPT: Astro — Mascota Pixel Art para reuniones" (sections 1-8) with a reference sheet image (Astro hero + six poses: IDLE, MOVIENDO, GRABANDO, DETENER, CAPTURAR, LISTO). Answer: "Motor ahora, sprites tuyos (Recommended)".
- L2: Cutting the poses from the reference failed: the starry background shares the character's dark blues/violets, so colour flood fill removed wings, body and headphones; each state has a single pose (no frames to animate), and PROCESSING/ERROR have none. Hence the placeholder + drop-in contract.
- L3 (2026-11-17): "tambien quiero esta mascota" + second reference image (orange headphone dragon, "AstroMeet"). Not reproduced: it is Charizard; offered character folders and an original fire-dragon prompt instead.
