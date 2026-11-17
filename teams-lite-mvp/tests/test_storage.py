import sqlite3
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

class LibraryTests(unittest.TestCase):
    def test_existing_database_is_migrated_and_old_meetings_land_in_the_vault(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = sqlite3.connect(Path(d) / 'meetings.sqlite3')
            legacy.execute(
                'CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT NOT NULL, started_utc TEXT NOT NULL, '
                'ended_utc TEXT, status TEXT NOT NULL)')
            legacy.execute("INSERT INTO meetings VALUES ('old','Old','2026-01-01T00:00:00+00:00',"
                           "'2026-01-01T01:00:00+00:00','completed')")
            legacy.commit()
            legacy.close()

            Storage(d)
            storage = Storage(d)  # opening twice must not fail: the migration is idempotent

            [meeting] = storage.list_meetings()
            self.assertEqual(meeting['title'], 'Old')
            self.assertEqual(meeting['status'], 'completed')
            self.assertIsNone(meeting['organization_id'])
            self.assertIsNone(meeting['project_id'])
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], ['old'])

    def test_meeting_without_placement_goes_to_the_vault(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('loose')
            storage.create_organization('Zenith')

            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])

    def test_organization_names_are_required_and_unique_ignoring_case(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('  Zenith  ')
            with self.assertRaisesRegex(ValueError, 'Ya existe'):
                storage.create_organization('zenith')
            with self.assertRaisesRegex(ValueError, 'nombre'):
                storage.create_organization('   ')
            other = storage.create_organization('Acme')
            with self.assertRaisesRegex(ValueError, 'Ya existe'):
                storage.rename_organization(other, 'ZENITH')
            storage.rename_organization(zenith, 'Zenith Consulting')

            self.assertEqual([org['name'] for org in storage.list_organizations()], ['Acme', 'Zenith Consulting'])

    def test_project_names_are_unique_per_organization(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            acme = storage.create_organization('Acme')
            storage.create_project(zenith, 'CRM')
            with self.assertRaisesRegex(ValueError, 'Ya existe'):
                storage.create_project(zenith, 'crm')
            storage.create_project(acme, 'CRM')
            with self.assertRaisesRegex(ValueError, 'Organización desconocida'):
                storage.create_project('missing', 'CRM')

            self.assertEqual([project['name'] for project in storage.list_projects(zenith)], ['CRM'])
            self.assertEqual([project['name'] for project in storage.list_projects(acme)], ['CRM'])

    def test_meetings_are_filed_by_organization_and_project_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            moments = iter(['2026-01-01T09:00:00+00:00', '2026-01-02T09:00:00+00:00', '2026-01-03T09:00:00+00:00'])
            with mock.patch('teams_lite.storage.utc_now', side_effect=lambda: next(moments)):
                first = storage.create_meeting('first', organization_id=zenith, project_id=crm)
                loose = storage.create_meeting('no project', organization_id=zenith)
                second = storage.create_meeting('second', organization_id=zenith, project_id=crm)

            self.assertEqual([row['id'] for row in storage.list_meetings(project_id=crm)], [second, first])
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=zenith, project_id=None)],
                             [loose])
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=zenith)],
                             [second, loose, first])
            self.assertEqual(storage.list_meetings(organization_id=None), [])
            [organization] = storage.list_organizations()
            self.assertEqual(organization['meeting_count'], 3)
            [project] = storage.list_projects(zenith)
            self.assertEqual(project['meeting_count'], 2)

    def test_project_must_belong_to_the_chosen_organization(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            acme = storage.create_organization('Acme')
            acme_project = storage.create_project(acme, 'Web')
            meeting = storage.create_meeting('m')

            with self.assertRaisesRegex(ValueError, 'no pertenece'):
                storage.move_meeting(meeting, zenith, acme_project)
            with self.assertRaisesRegex(ValueError, 'no pertenece'):
                storage.move_meeting(meeting, None, acme_project)
            with self.assertRaisesRegex(ValueError, 'no pertenece'):
                storage.create_meeting('bad', organization_id=zenith, project_id=acme_project)
            with self.assertRaisesRegex(ValueError, 'Reunión desconocida'):
                storage.move_meeting('missing', acme, acme_project)

            self.assertEqual([row['title'] for row in storage.list_meetings()], ['m'])
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])

    def test_move_meeting_between_projects_organizations_and_the_vault(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            meeting = storage.create_meeting('m')
            folder = Path(d) / meeting

            storage.move_meeting(meeting, zenith, crm)
            self.assertEqual([row['id'] for row in storage.list_meetings(project_id=crm)], [meeting])
            storage.move_meeting(meeting, zenith, None)
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=zenith, project_id=None)],
                             [meeting])
            storage.move_meeting(meeting, None, None)
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])
            self.assertTrue(folder.is_dir())  # metadata only: the folder never moves

    def test_deleting_an_organization_returns_its_meetings_to_the_vault(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            filed = storage.create_meeting('filed', organization_id=zenith, project_id=crm)
            acme = storage.create_organization('Acme')
            kept = storage.create_meeting('kept', organization_id=acme)

            storage.delete_organization(zenith)

            self.assertEqual([org['name'] for org in storage.list_organizations()], ['Acme'])
            self.assertEqual(storage.list_projects(zenith), [])
            [vault] = storage.list_meetings(organization_id=None)
            self.assertEqual((vault['id'], vault['project_id']), (filed, None))
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=acme)], [kept])

    def test_deleting_a_project_keeps_its_meetings_in_the_organization(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            web = storage.create_project(zenith, 'Web')
            filed = storage.create_meeting('filed', organization_id=zenith, project_id=crm)
            storage.rename_project(web, 'Portal')
            with self.assertRaisesRegex(ValueError, 'Ya existe'):
                storage.rename_project(web, 'crm')

            storage.delete_project(crm)

            self.assertEqual([project['name'] for project in storage.list_projects(zenith)], ['Portal'])
            self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=zenith, project_id=None)],
                             [filed])

class RenameMeetingTests(unittest.TestCase):
    def test_rename_changes_only_the_title(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            meeting = storage.create_meeting('Reunión de Teams', organization_id=zenith)
            other = storage.create_meeting('Otra')
            storage.finish_meeting(meeting)
            before = storage.get_meeting(meeting)

            storage.rename_meeting(meeting, '  Kickoff   CRM  ')

            after = storage.get_meeting(meeting)
            self.assertEqual(after['title'], 'Kickoff CRM')
            self.assertEqual({k: v for k, v in after.items() if k != 'title'},
                             {k: v for k, v in before.items() if k != 'title'})
            self.assertTrue((Path(d) / meeting).is_dir())  # the folder keeps its name
            self.assertEqual(storage.get_meeting(other)['title'], 'Otra')

    def test_rename_rejects_empty_titles_and_unknown_meetings(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('Daily')
            with self.assertRaisesRegex(ValueError, 'Escribe un nombre'):
                storage.rename_meeting(meeting, '   ')
            with self.assertRaisesRegex(ValueError, 'Reunión desconocida'):
                storage.rename_meeting('missing', 'Nuevo')
            self.assertEqual(storage.get_meeting(meeting)['title'], 'Daily')

if __name__ == '__main__': unittest.main()
