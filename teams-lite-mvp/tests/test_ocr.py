import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from teams_lite.ocr import (
    extract_name_hints,
    extract_participant_names,
    load_participants,
    merge_participants,
    read_teams_names,
)
from teams_lite.window_capture import CapturedFrame, TeamsWindowUnavailable

# Lines a full-screen OCR actually produced during real meetings (Codex, PowerShell, Teams Lite).
REAL_FULL_SCREEN_NOISE = [
    "ng = Archivo Edición Ver Ayuda - o x a",
    "Codex QQ _Importar entregable de Teams Lite co BS (a)",
    "ti] Ha trabajado durante 2min 12s >",
    ": Continué con la opción 2: sin reportar el fallo.",
    "Transcriptor-teams",
    "Interfaz completa en espajiol.",
    "PS C:\\Users\\BastianIsa",
    "env\\Scripts\\python.exe",
    "Graba el micrófono y el audio del sistema en pistas separadas y rec",
    "Antes de grabar",
    "Nueva grabación",
    "Teams Lite",
    "Copyright (C) Microsof#",
]
TEAMS_UI_NOISE = [
    "Chat", "Personas", "Compartir", "Salir", "Reacciones", "Más", "Cámara", "Micrófono",
    "Microsoft Teams", "Levantar la mano", "Compartir contenido", "AP", "RD", "12:45",
]


class OcrTests(unittest.TestCase):
    def test_extracts_unique_visual_name_hints(self):
        text = " Ana Pérez \n\nANA PÉREZ\n--\nBob Smith\n"
        self.assertEqual(extract_name_hints(text), ["Ana Pérez", "Bob Smith"])

    def test_participant_names_keep_people_and_drop_screen_and_teams_noise(self):
        text = "\n".join(
            REAL_FULL_SCREEN_NOISE
            + TEAMS_UI_NOISE
            + ["Ana Pérez", "Rodrigo Díaz (Invitado)", "María de la Fuente", "Lucia Gomez | Zenith", "ana pérez"]
        )
        self.assertEqual(
            extract_participant_names(text),
            ["Ana Pérez", "Rodrigo Díaz", "María de la Fuente", "Lucia Gomez"],
        )

    def test_reads_names_from_the_teams_window_and_keeps_no_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "ocr"

            def capture(output_dir, origin, *, title_hint):
                self.assertEqual("Microsoft Teams", title_hint)
                path = Path(output_dir) / "teams-frame.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (10, 10)).save(path)
                return CapturedFrame(path, 0.0, "2026-11-12T18:00:00+00:00", 42)

            result = read_teams_names(
                output,
                title_hint="Microsoft Teams",
                capture=capture,
                recognize=lambda path: "Chat\nAna Pérez\nCompartir\nRodrigo Díaz\n",
            )

            self.assertEqual(["Ana Pérez", "Rodrigo Díaz"], result["names"])
            self.assertEqual([], list(output.glob("*.png")))
            saved = json.loads(next(output.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(["Ana Pérez", "Rodrigo Díaz"], saved["names"])

    def test_missing_teams_window_never_falls_back_to_full_screen(self):
        def capture(output_dir, origin, *, title_hint):
            raise TeamsWindowUnavailable("No se encontró una ventana visible de Microsoft Teams")

        def recognize(path):
            raise AssertionError("OCR must not run without a Teams window")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(TeamsWindowUnavailable):
                read_teams_names(Path(temp), title_hint="Microsoft Teams", capture=capture, recognize=recognize)

    def test_merge_participants_dedupes_and_keeps_first_seen_order(self):
        with tempfile.TemporaryDirectory() as temp:
            meeting_dir = Path(temp)
            self.assertEqual([], load_participants(meeting_dir))
            merge_participants(meeting_dir, ["Ana Pérez", "Rodrigo Díaz"])
            merged = merge_participants(meeting_dir, ["ANA PÉREZ", "Lucia Gomez"])
            self.assertEqual(["Ana Pérez", "Rodrigo Díaz", "Lucia Gomez"], merged)
            self.assertEqual(merged, load_participants(meeting_dir))


if __name__ == "__main__":
    unittest.main()
