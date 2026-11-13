import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest import mock
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

    def test_meeting_folder_is_named_with_local_date_time_and_title(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            moment = datetime(2026, 11, 13, 7, 44, 31)
            with mock.patch('teams_lite.storage.local_now', return_value=moment):
                meeting = storage.create_meeting('Daily: ventas/equipo')

            self.assertEqual(meeting, '2026-11-13 07-44 Daily_ ventas_equipo')
            self.assertTrue((Path(d) / meeting).is_dir())
            row = storage.list_meetings()[0]
            self.assertEqual((row['id'], row['title']), (meeting, 'Daily: ventas/equipo'))

    def test_same_minute_and_title_gets_a_unique_folder(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            moment = datetime(2026, 11, 13, 7, 44)
            with mock.patch('teams_lite.storage.local_now', return_value=moment):
                first = storage.create_meeting('Daily')
                second = storage.create_meeting('daily')

            self.assertEqual(first, '2026-11-13 07-44 Daily')
            self.assertEqual(second, '2026-11-13 07-44 daily (2)')
            self.assertTrue((Path(d) / first).is_dir())
            self.assertTrue((Path(d) / second).is_dir())

    def test_long_titles_are_shortened_to_keep_windows_paths_valid(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            with mock.patch('teams_lite.storage.local_now', return_value=datetime(2026, 1, 2, 3, 4)):
                meeting = storage.create_meeting('Revisión ' * 20)

            self.assertLessEqual(len(meeting), len('2026-01-02 03-04 ') + 40)
            self.assertFalse(meeting.endswith((' ', '.')))
            self.assertTrue((Path(d) / meeting).is_dir())

    def test_delete_succeeds_when_windows_keeps_the_folder_locked(self):
        # OneDrive or the search indexer can hold a just-emptied folder open (WinError 5).
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('locked')
            (Path(d) / meeting / 'ocr').mkdir()
            storage.finish_meeting(meeting)

            denied = PermissionError(5, 'Acceso denegado')
            with mock.patch('teams_lite.storage.shutil.rmtree', side_effect=denied), \
                    mock.patch('teams_lite.storage.time.sleep'):
                storage.delete_meeting(meeting)

            self.assertNotIn(meeting, {row['id'] for row in storage.list_meetings()})
            self.assertFalse((Path(d) / meeting).exists())

    def test_leftover_deleted_folders_are_purged_on_startup(self):
        with tempfile.TemporaryDirectory() as d:
            leftover = Path(d) / '.delete-abc-def' / 'ocr'
            leftover.mkdir(parents=True)
            (leftover / 'frame.json').write_text('{}', encoding='utf-8')
            kept = Path(d) / 'not-a-quarantine'
            kept.mkdir()

            Storage(d)

            self.assertFalse((Path(d) / '.delete-abc-def').exists())
            self.assertTrue(kept.is_dir())

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
