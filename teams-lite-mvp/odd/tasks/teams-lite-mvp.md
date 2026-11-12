# Teams Lite MVP

Objective: Build a functional Windows desktop MVP in Python + SQLite that prioritizes robust, recoverable audio capture and performs offline transcription after recording.
Branch: `codex/teams-lite-mvp`. Delivery: `exception-ok` (local deliverable; no remote PR). Test runner: `python -m pytest`.

## Specs

S1. Build: "MVP funcional de Teams Lite para Windows, solo Python + SQLite, con PySide6".
S2. Capture: "prioridad absoluta de grabación robusta de audio de Microsoft Teams y micrófono en segmentos recuperables".
S3. Transcription: "transcripción automática al terminar con NVIDIA Parakeet TDT 0.6B V3 usando backend CPU/ONNX cuando sea viable".
S4. Optional visual module: "Incluye captura visual y OCR de nombres como módulo ligero opcional".
S5. Attribution: include timestamps and cautious speaker association; "evita prometer que OCR identifica voces por sí solo".
S6. Delivery: include configuration, project structure, installation and execution instructions, tests, and limitations.
S7. Platform constraints: "No usar Node, Electron ni GPU; orientado a PCs Windows con 32GB RAM."
S8. Evidence: "Guarda archivos entregables y distingue qué componentes funcionan vs requieren validación en Teams real."
S9. Compatibility: "Evalúa compatibilidad actual de bibliotecas".

Assumptions: 64-bit Windows 10/11 and CPython 3.12. The MVP records the default render loopback and default microphone as separate stems; it does not hook a private Teams API.

## Tasks

- T1 [S1,S2,S5] route=delegated (parallel unit: core audio + storage) status=complete commit=fa982a9
- T2 [S3,S5,S9] route=delegated (parallel unit: transcription + speaker association) status=complete commit=fa48e97
- T3 [S1,S4,S6,S7,S8] route=inline status=complete commit=6254f95
- T4 [S1-S9] route=inline status=complete commit=6254f95

## Log

L1 (2026-11-12): "Construye un MVP funcional de Teams Lite para Windows, solo Python + SQLite, con PySide6, prioridad absoluta de grabación robusta de audio de Microsoft Teams y micrófono en segmentos recuperables y transcripción automática al terminar con NVIDIA Parakeet TDT 0.6B V3 usando backend CPU/ONNX cuando sea viable. Incluye captura visual y OCR de nombres como módulo ligero opcional, timestamps y asociación prudente de hablantes, configuración, estructura de proyecto, instrucciones de instalación y ejecución, pruebas y limitaciones. No usar Node, Electron ni GPU; orientado a PCs Windows con 32GB RAM. Guarda archivos entregables y distingue qué componentes funcionan vs requieren validación en Teams real. Evalúa compatibilidad actual de bibliotecas y evita prometer que OCR identifica voces por sí solo."
L2 (2026-11-12): Current-library research recommends CPython 3.12, PySide6, SoundCard WASAPI loopback, recoverable WAV segments, SQLite metadata, onnx-asr + ONNX Runtime CPU for Parakeet, and optional Tesseract OCR. Real Teams/hardware validation remains required.
L3 (2026-11-12): Engram mirror pending because this generated workspace has no resolved Engram project.
L4 (2026-11-12): T1 RED confirmed missing modules; GREEN confirmed focused audio/storage tests and full suite coverage with fake audio sources. Real Teams and Windows device behavior remains unvalidated.
L5 (2026-11-12): T2 RED confirmed missing modules; GREEN confirmed six focused tests. Installed onnx-asr 0.12.0 and verified Manager CPU/offline API signatures; no model was downloaded or transcribed.

L6 (2026-11-12): T3/T4 added the PySide6 UI, JSON configuration, optional Tesseract OCR hints, packaging, and documentation. Full test suite: 12 passed. Headless PySide6 window smoke test passed. All optional dependency versions installed through a short drive mapping; the original long path exceeded Windows' default path-length limit.
L7 (2026-11-12): Risk: item 5 because dependency and Windows environment compatibility are part of the deliverable. Real Teams capture, physical audio devices, model inference, Tesseract binary, and long-duration behavior remain pending external validation.
L8 (2026-11-12): Independent verification found two blockers: startup metadata failure leaked a finalizer thread, and SoundCard's documented defective Windows mono path was selected. One correction batch now creates metadata before workers, cleans startup failures, captures native channels, and explicitly downmixes to mono. Focused RED reproduced both; GREEN: 4 focused audio tests and 14 full tests passed. Blocker-limited independent recheck passed first/second stream failures and stereo downmix.

