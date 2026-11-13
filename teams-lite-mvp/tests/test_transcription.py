import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from teams_lite.transcription import AudioSource, CapabilityError, export_transcript, transcribe_recordings, load_recognizer

class TranscriptionTests(unittest.TestCase):
    def test_chunks_offsets_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / 'audio.wav'
            with wave.open(str(wav), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(10)
                w.writeframes(b'\0\0' * 25)
            lengths = []
            def recognize(path):
                with wave.open(str(path), 'rb') as w:
                    lengths.append(w.getnframes())
                return 'hello'
            segments = transcribe_recordings([AudioSource(wav, 'remote', 5)], recognizer=recognize, chunk_seconds=1)
            self.assertEqual(lengths, [10, 10, 5])
            self.assertEqual([(s.start, s.end, s.speaker) for s in segments], [(5, 6, 'Remoto/Desconocido'), (6, 7, 'Remoto/Desconocido'), (7, 7.5, 'Remoto/Desconocido')])
            export_transcript(segments, root/'out.json', root/'out.txt')
            self.assertEqual(json.loads((root/'out.json').read_text(encoding='utf-8'))[0]['text'], 'hello')
            self.assertIn('[00:00:05.000 - 00:00:06.000] Remoto/Desconocido: hello', (root/'out.txt').read_text(encoding='utf-8'))
    def _write_wav(self, path, pieces, rate=1000):
        """pieces: list of (seconds, amplitude 0..1); a 50 Hz tone stands in for speech."""
        import numpy as np
        chunks = []
        for seconds, amplitude in pieces:
            t = np.arange(int(seconds * rate)) / rate
            chunks.append(amplitude * np.sin(2 * np.pi * 50 * t))
        samples = (np.concatenate(chunks) * 32767).astype('<i2')
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(samples.tobytes())

    def test_skip_silence_transcribes_only_the_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'audio.wav'
            self._write_wav(wav, [(3, 0.0), (2, 0.3), (5, 0.0)])
            lengths = []
            def recognize(path):
                with wave.open(str(path), 'rb') as w:
                    lengths.append(w.getnframes() / w.getframerate())
                return 'hola'
            segments = transcribe_recordings([AudioSource(wav, 'remote', 10)], recognizer=recognize,
                                             chunk_seconds=20, skip_silence=True)
            self.assertEqual(1, len(lengths))
            self.assertLess(lengths[0], 4)  # ~2 s of speech plus padding, not the whole 10 s
            self.assertEqual(1, len(segments))
            self.assertAlmostEqual(12.5, segments[0].start, delta=0.01)  # 10 + 3 - 0.5 padding
            self.assertAlmostEqual(15.5, segments[0].end, delta=0.01)

    def test_progress_grows_with_the_audio_processed_and_ends_at_one(self):
        for skip_silence in (False, True):
            with self.subTest(skip_silence=skip_silence), tempfile.TemporaryDirectory() as tmp:
                first, second = Path(tmp) / 'mic.wav', Path(tmp) / 'remote.wav'
                self._write_wav(first, [(2, 0.3), (2, 0.0), (2, 0.3)])
                self._write_wav(second, [(6, 0.3)])
                reported = []
                transcribe_recordings(
                    [AudioSource(first, 'local', 0), AudioSource(second, 'remote', 0)],
                    recognizer=lambda path: 'hola', chunk_seconds=1, skip_silence=skip_silence,
                    progress=reported.append,
                )
                self.assertGreater(len(reported), 2)
                self.assertEqual(reported, sorted(reported))
                self.assertTrue(all(0 < value <= 1 for value in reported))
                self.assertEqual(1.0, reported[-1])
                # The first track is half of the audio: progress passes 0.5 only on the second one.
                self.assertTrue(any(0.4 <= value <= 0.5 for value in reported), reported)

    def test_skip_silence_never_calls_the_model_for_a_silent_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'audio.wav'
            self._write_wav(wav, [(10, 0.0)])
            calls = []
            segments = transcribe_recordings([AudioSource(wav, 'local', 0)],
                                             recognizer=lambda p: calls.append(p) or 'x', skip_silence=True)
            self.assertEqual([], calls)
            self.assertEqual([], segments)

    def test_skip_silence_ignores_short_noise_bursts(self):
        # A 0.3 s click or cough made Parakeet hallucinate fillers ("Um", "And then") on real audio.
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'audio.wav'
            self._write_wav(wav, [(4, 0.0), (0.3, 0.3), (4, 0.0), (1.5, 0.3), (3, 0.0)])
            lengths = []
            def recognize(path):
                with wave.open(str(path), 'rb') as w:
                    lengths.append(w.getnframes() / w.getframerate())
                return 'texto'
            segments = transcribe_recordings([AudioSource(wav, 'local', 0)], recognizer=recognize, skip_silence=True)
            self.assertEqual(1, len(lengths))  # only the 1.5 s of real sound
            self.assertGreater(segments[0].start, 7.5)

    def test_skip_silence_keeps_quiet_speech_and_splits_long_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'audio.wav'
            # -40 dBFS speech is quiet but real; 25 s of continuous speech exceeds a 10 s chunk.
            self._write_wav(wav, [(1, 0.0), (25, 0.01 * 1.414), (1, 0.0)])
            lengths = []
            def recognize(path):
                with wave.open(str(path), 'rb') as w:
                    lengths.append(w.getnframes() / w.getframerate())
                return 'texto'
            transcribe_recordings([AudioSource(wav, 'local', 0)], recognizer=recognize,
                                  chunk_seconds=10, skip_silence=True)
            self.assertGreaterEqual(len(lengths), 3)
            self.assertTrue(all(length <= 10 for length in lengths))
            self.assertGreaterEqual(sum(lengths), 25)

    def test_requires_local_model(self):
        with self.assertRaisesRegex(CapabilityError, 'directorio local'):
            load_recognizer(None)
    def test_dependency_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('teams_lite.transcription.importlib.import_module', side_effect=ImportError('missing')):
                with self.assertRaisesRegex(CapabilityError, 'onnx-asr'):
                    load_recognizer(tmp)
    def test_loads_cpu_offline(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            model = SimpleNamespace(recognize=lambda path: 'text')
            manager = Mock(); manager.create_asr.return_value = model
            loader = SimpleNamespace(Manager=Mock(return_value=manager))
            with patch('teams_lite.transcription.importlib.import_module', return_value=loader):
                recognize = load_recognizer(tmp)
            loader.Manager.assert_called_once_with(providers=['CPUExecutionProvider'])
            manager.create_asr.assert_called_once_with('nemo-parakeet-tdt-0.6b-v3', tmp, offline=True)
            self.assertEqual(recognize('x'), 'text')

    def test_rejects_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            transcribe_recordings([], recognizer=lambda _: '', chunk_seconds=31)
