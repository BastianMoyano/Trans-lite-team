import tempfile
import unittest
from pathlib import Path

from teams_lite.transcription import TranscriptSegment
from teams_lite.visual_speakers import (
    SpeakerObservation,
    apply_visual_speakers,
    append_observation,
    load_observations,
)


class VisualSpeakerTests(unittest.TestCase):
    def test_confident_overlapping_observations_attribute_only_remote_audio(self):
        segments = [
            TranscriptSegment(0, 10, "remote", "Remoto/Desconocido", "Hola"),
            TranscriptSegment(0, 10, "local", "Local", "Respuesta"),
        ]
        observations = [
            SpeakerObservation(0, 5, "Rodrigo", 0.9, "2026-11-12T14:32:05+00:00"),
            SpeakerObservation(5, 10, "Rodrigo", 0.85, "2026-11-12T14:32:10+00:00"),
        ]

        attributed = apply_visual_speakers(segments, observations)

        self.assertEqual("Rodrigo", attributed[0].speaker)
        self.assertEqual("Local", attributed[1].speaker)

    def test_ambiguous_low_confidence_or_missing_evidence_keeps_unknown(self):
        segment = TranscriptSegment(0, 10, "remote", "Remoto/Desconocido", "Hola")

        tied = [
            SpeakerObservation(0, 5, "Rodrigo", 0.9, "2026-11-12T14:32:05+00:00"),
            SpeakerObservation(5, 10, "Ana", 0.9, "2026-11-12T14:32:10+00:00"),
        ]
        weak = [SpeakerObservation(0, 10, "Rodrigo", 0.2, "2026-11-12T14:32:05+00:00")]

        self.assertEqual("Remoto/Desconocido", apply_visual_speakers([segment], tied)[0].speaker)
        self.assertEqual("Remoto/Desconocido", apply_visual_speakers([segment], weak)[0].speaker)
        self.assertEqual("Remoto/Desconocido", apply_visual_speakers([segment], [])[0].speaker)

    def test_jsonl_observations_are_deterministic_and_recover_trailing_partial_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "speaker-observations.jsonl"
            later = SpeakerObservation(5, 10, "Ana", 0.8, "2026-11-12T14:32:10+00:00")
            earlier = SpeakerObservation(0, 5, "Rodrigo", 0.9, "2026-11-12T14:32:05+00:00")
            append_observation(path, later)
            append_observation(path, earlier)
            with path.open("ab") as stream:
                stream.write(b'{"start":')

            self.assertEqual([earlier, later], load_observations(path))

    def test_observation_rejects_invalid_time_confidence_and_name(self):
        with self.assertRaises(ValueError):
            SpeakerObservation(5, 5, "Rodrigo", 0.9, "2026-11-12T14:32:05+00:00")
        with self.assertRaises(ValueError):
            SpeakerObservation(0, 5, "", 0.9, "2026-11-12T14:32:05+00:00")
        with self.assertRaises(ValueError):
            SpeakerObservation(0, 5, "Rodrigo", 1.1, "2026-11-12T14:32:05+00:00")


if __name__ == "__main__":
    unittest.main()
