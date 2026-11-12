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
