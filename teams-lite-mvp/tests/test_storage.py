import tempfile
import unittest
from pathlib import Path
from teams_lite.storage import Storage

class StorageTests(unittest.TestCase):
    def test_recover_raw_segment_and_persist_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            db = Storage(d)
            meeting = db.create_meeting('example')
            stream = db.create_stream(meeting, 'microphone', 8000, 1)
            segment = db.begin_segment(stream, 0, 1.0, '2026-01-01T00:00:00+00:00')
            Path(segment['temp_path']).write_bytes(b'\x01\x00' * 80)
            db.recover()
            rows = db.list_segments(meeting)
            self.assertEqual(rows[0]['status'], 'recovered')
            self.assertEqual(rows[0]['frames'], 80)
            self.assertTrue(Path(rows[0]['path']).is_file())
            self.assertEqual(Storage(d).list_meetings()[0]['status'], 'interrupted')

    def test_delete_completed_meeting_removes_only_its_records_and_files(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            target = storage.create_meeting('target')
            stream = storage.create_stream(target, 'microphone', 8000, 1)
            segment = storage.begin_segment(stream, 0, 1.0, '2026-01-01T00:00:00+00:00')
            Path(segment['temp_path']).write_bytes(b'\x01\x00' * 80)
            storage.add_event(target, 'note', 'target event')
            (Path(d) / target / 'transcript.txt').write_text('target transcript', encoding='utf-8')
            storage.finish_meeting(target)

            other = storage.create_meeting('other')
            storage.finish_meeting(other)
            other_file = Path(d) / other / 'keep.txt'
            other_file.write_text('keep', encoding='utf-8')

            storage.delete_meeting(target)

            self.assertNotIn(target, {meeting['id'] for meeting in storage.list_meetings()})
            self.assertEqual(storage.list_segments(target), [])
            self.assertEqual(storage.list_events(target), [])
            self.assertFalse((Path(d) / target).exists())
            self.assertTrue(other_file.is_file())

    def test_delete_recording_meeting_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('active')
            marker = Path(d) / meeting / 'keep.txt'
            marker.write_text('keep', encoding='utf-8')

            with self.assertRaisesRegex(RuntimeError, 'grabando'):
                storage.delete_meeting(meeting)

            self.assertIn(meeting, {row['id'] for row in storage.list_meetings()})
            self.assertTrue(marker.is_file())

if __name__ == '__main__': unittest.main()
