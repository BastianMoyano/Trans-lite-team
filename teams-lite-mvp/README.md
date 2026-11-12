# Teams Lite MVP

Teams Lite is a Windows desktop MVP that prioritizes **not losing meeting audio**. It records the default microphone and Windows system output as separate, recoverable WAV segments, stores metadata in SQLite, and optionally transcribes finalized audio with NVIDIA Parakeet TDT 0.6B V3 through ONNX Runtime on CPU.

It does not use Node, Electron, a GPU, or private Microsoft Teams APIs.

## What works in this deliverable

- PySide6 desktop UI with visible recording state.
- Separate microphone and default Windows output-loopback capture.
- Native multichannel WASAPI capture with an explicit mono downmix, avoiding SoundCard's documented defective mono-recording path.
- 30-second WAV segments by default, with raw `.pcm.part` recovery after interruption.
- SQLite meetings, streams, segments, timestamps, statuses, and error events.
- Automatic post-recording transcription pipeline with CPU-only ONNX provider selection.
- Timestamped JSON and plain-text transcript export.
- Conservative labels: microphone is `Local`; loopback is `Remote/Unknown`.
- Optional screenshot plus Tesseract OCR name hints.
- Unit tests that do not require Teams, audio hardware, Tesseract, or model downloads.

## What still requires validation on real Teams PCs

- Concurrent microphone and WASAPI loopback stability on each audio-driver combination.
- Long meetings, sleep/resume, device changes, Bluetooth headsets, and Teams device switching.
- Actual Parakeet model download/layout, memory use, speed, and accuracy on target CPUs.
- Timestamp drift between independent microphone and loopback devices.
- OCR crop coordinates for each Teams layout, display scale, language, and theme.
- A packaged installer and code signing.

The application cannot reliably identify individual remote speakers: Teams output is normally a mixed stream. OCR reads visible text only. It **does not identify voices** and must not be presented as speaker recognition.

## Requirements

- 64-bit Windows 10 or 11.
- 64-bit CPython 3.12 (recommended).
- 32 GB RAM recommended for the full transcription option.
- Microsoft Visual C++ 2019 or later runtime for ONNX Runtime.
- Tesseract OCR only if the optional OCR feature is enabled.

Compatibility was checked on 2026-11-12 against PySide6 6.11–6.12, SoundCard 0.4.6, onnx-asr 0.12.0, and ONNX Runtime 1.31.1. `onnx-asr` is a third-party Beta package, so keep these versions pinned until real-device validation is complete.

## Install

If the project is stored under a very long Windows path, create the virtual environment through a temporary drive mapping; Qt contains deeply nested files and Windows may otherwise reject installation:

```powershell
subst T: $PWD.Path
py -3.12 -m venv T:\.venv
T:\.venv\Scripts\Activate.ps1
```

For a normal-length path:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[asr,ocr,dev]"
Copy-Item config.example.json config.json
```

For capture-only operation, omit optional extras:

```powershell
pip install -e ".[dev]"
```

### Prepare Parakeet ONNX files

The UI deliberately avoids downloading a model while recording. Pre-provision the model once, then point `parakeet_model_dir` in `config.json` at that directory:

```powershell
python -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', 'models/parakeet-v3')"
```

This downloads community ONNX conversion files used by `onnx-asr`; it is not NVIDIA's original `.nemo` runtime. Pin and validate the downloaded revision before production use.

### Optional Tesseract

Install Tesseract separately and set `tesseract_cmd` in `config.json` if it is not on `PATH`. Set `ocr_enabled` to `true`. `ocr_region` is either `null` for the whole screen or `[left, top, right, bottom]` for a Teams participant area.

## Run

```powershell
teams-lite --config config.json
# or
python -m teams_lite --config config.json
```

1. Confirm the Windows default input/output devices match the devices Teams uses.
2. Notify participants and obtain any required consent.
3. Start recording before the important conversation begins.
4. Optionally capture visible name hints while the participant panel is on screen.
5. Stop recording. WAV finalization completes before CPU transcription starts.

Meeting data is stored under `data/<meeting-id>/`; metadata is in `data/meetings.sqlite3`.

## Recovery model

Each capture stream writes raw 16-bit PCM to a `.pcm.part` file. On segment rotation or stop, a separate finalizer writes a WAV header, flushes the file, atomically replaces the final path, and then marks the SQLite row finalized. On the next launch, rows left in `writing` state are recovered when possible and meetings left in `recording` state become `interrupted`.

This minimizes loss, but it is not a hard real-time or forensic recorder. Abrupt power loss may still lose the operating system's unwritten disk cache.

## Tests

```powershell
python -m pytest
# stdlib-only runner if pytest is not installed
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The tests cover SQLite persistence/recovery, segmented audio with fake devices, configuration, OCR text filtering, transcription chunking/exports with a fake recognizer, and conservative speaker labels.

## Project structure

```text
src/teams_lite/
  app.py            PySide6 UI and background orchestration
  audio.py          microphone/loopback capture and segment rotation
  storage.py        SQLite schema and interrupted-segment recovery
  transcription.py  CPU ONNX transcription and timestamped exports
  speakers.py       conservative source labels/manual mapping
  ocr.py            optional screenshot OCR hints
  config.py         validated JSON settings
tests/               hardware-independent unit tests
odd/tasks/           implementation logbook and acceptance specs
```

## Important limitations

- System loopback captures the selected Windows render endpoint, not Teams by process name.
- Native device channels are averaged to one mono archival track; this is intentionally not spatial audio.
- Independent devices can drift. Timestamps are useful alignment hints, not sample-accurate synchronization.
- Current transcript times are audio-chunk boundaries, not word-level timestamps.
- Fixed 20-second ASR chunks can split speech at a boundary; VAD is a future improvement.
- Multiple remote participants remain mixed and labeled `Remote/Unknown` unless a human supplies a mapping.
- Construct only one `Storage` instance per data directory while recording; startup recovery is intentionally eager.
- `Recorder.stop()` waits for device reads and segment finalization. A faulty driver may delay shutdown.
- Recording laws and company policies vary. The operator is responsible for notice and consent.

## Compatibility evidence

- [Microsoft WASAPI loopback recording](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
- [SoundCard documentation](https://soundcard.readthedocs.io/en/latest/)
- [onnx-asr usage and local model loading](https://github.com/istupakov/onnx-asr/blob/main/docs/usage.md)
- [onnx-asr package metadata](https://pypi.org/project/onnx-asr/)
- [ONNX Runtime installation requirements](https://onnxruntime.ai/docs/install/)
- [PySide6 package metadata](https://pypi.org/project/PySide6/)
- [NVIDIA Parakeet model documentation](https://docs.nvidia.com/nemo/speech/nightly/asr/asr_checkpoints.html)
- [Tesseract documentation](https://tesseract-ocr.github.io/tessdoc/)

