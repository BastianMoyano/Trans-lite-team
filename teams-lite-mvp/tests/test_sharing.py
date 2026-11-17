import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from teams_lite.sharing import PackageError, export_meeting, import_meeting, read_package, suggest_placement
from teams_lite.storage import Storage, meeting_folder_name

MEETING_FILES = {
    'transcript.txt': '[00:00:01.000] Local: Hola',
    'transcript.json': '[{"start": 1.0, "end": 2.0, "speaker": "Local", "text": "Hola"}]',
    'minutes.md': '# Minuta',
    'participants.json': '["Ana Pérez"]',
    'moments.jsonl': '{"offset_seconds": 2.0, "image": "moments/teams-1.jpg"}\n',
    'moments/teams-1.jpg': 'jpeg bytes',
    'ocr/ocr-1.json': '{"names": ["Ana Pérez"]}',
    'speaker-observations.jsonl': '{"offset_seconds": 1.0}\n',
}


def _recorded_meeting(storage, title='Demo CRM', **placement):
    meeting = storage.create_meeting(title, **placement)
    stream = storage.create_stream(meeting, 'microphone', 8000, 1)
    segment = storage.begin_segment(stream, 0, 5.0, '2026-11-17T10:00:00+00:00')
    Path(segment['temp_path']).write_bytes(b'\x01\x00' * 80)
    storage.finalize_segment(segment['id'])
    storage.add_event(meeting, 'note', 'started')
    meeting_dir = storage.root / meeting
    for name, content in MEETING_FILES.items():
        (meeting_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (meeting_dir / name).write_text(content, encoding='utf-8')
    frames = meeting_dir / 'visual-frames'
    frames.mkdir()
    (frames / 'teams-20261117T100000000000Z.png').write_bytes(b'first')
    (frames / 'teams-20261117T100500000000Z.png').write_bytes(b'later')
    storage.finish_meeting(meeting)
    return meeting


def _snapshot(storage):
    meetings = storage.list_meetings()
    files = sorted(str(path.relative_to(storage.root)) for path in storage.root.rglob('*')
                   if path.name != 'meetings.sqlite3' and not path.name.startswith('meetings.sqlite3-'))
    return meetings, files


class ExportImportTests(unittest.TestCase):
    def setUp(self):
        self._dirs = [tempfile.TemporaryDirectory() for _ in range(3)]
        self.source = Storage(self._dirs[0].name)
        self.target = Storage(self._dirs[1].name)
        self.out = Path(self._dirs[2].name)

    def tearDown(self):
        for directory in self._dirs:
            directory.cleanup()

    def test_light_package_travels_without_audio_and_lands_in_the_chosen_project(self):
        zenith = self.source.create_organization('Zenith')
        crm = self.source.create_project(zenith, 'CRM')
        meeting = _recorded_meeting(self.source, organization_id=zenith, project_id=crm)
        original = next(row for row in self.source.list_meetings() if row['id'] == meeting)

        package = export_meeting(self.source, meeting, self.out / 'demo.zip')

        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
        self.assertFalse([name for name in names if name.endswith('.wav')])
        self.assertIn('files/visual-frames/teams-20261117T100000000000Z.png', names)
        self.assertNotIn('files/visual-frames/teams-20261117T100500000000Z.png', names)
        manifest = read_package(package)
        self.assertEqual((manifest['organization'], manifest['project']), ('Zenith', 'CRM'))
        self.assertFalse(manifest['includes_audio'])

        acme = self.target.create_organization('Acme')
        web = self.target.create_project(acme, 'Web')
        imported = import_meeting(self.target, package, acme, web)

        [row] = self.target.list_meetings(project_id=web)
        self.assertEqual(row['id'], imported)
        for key in ('title', 'started_utc', 'ended_utc', 'status'):
            self.assertEqual(row[key], original[key])
        started = datetime.fromisoformat(original['started_utc']).astimezone()
        self.assertEqual(imported, meeting_folder_name('Demo CRM', started))
        folder = self.target.root / imported
        for name, content in MEETING_FILES.items():
            self.assertEqual((folder / name).read_text(encoding='utf-8'), content)
        self.assertEqual(sorted(path.name for path in (folder / 'visual-frames').iterdir()),
                         ['teams-20261117T100000000000Z.png'])
        self.assertEqual(self.target.list_segments(imported), [])
        self.assertEqual([event['kind'] for event in self.target.list_events(imported)], ['note', 'imported'])

    def test_package_with_audio_can_be_transcribed_again_after_import(self):
        meeting = _recorded_meeting(self.source)
        [source_segment] = self.source.list_segments(meeting)

        package = export_meeting(self.source, meeting, self.out / 'demo.zip', include_audio=True)
        self.assertTrue(read_package(package)['includes_audio'])
        imported = import_meeting(self.target, package)

        [segment] = self.target.list_segments(imported)
        self.assertEqual(segment['status'], 'finalized')
        self.assertEqual((segment['kind'], segment['sample_rate'], segment['channels'], segment['frames']),
                         ('microphone', 8000, 1, 80))
        self.assertEqual(segment['started_monotonic'], source_segment['started_monotonic'])
        path = Path(segment['path'])
        self.assertEqual(path.parent, self.target.root / imported)
        self.assertEqual(path.read_bytes(), Path(source_segment['path']).read_bytes())
        self.assertEqual([row['id'] for row in self.target.list_meetings(organization_id=None)], [imported])

    def test_importing_the_same_package_twice_creates_two_meetings(self):
        meeting = _recorded_meeting(self.source)
        package = export_meeting(self.source, meeting, self.out / 'demo.zip', include_audio=True)

        first = import_meeting(self.target, package)
        second = import_meeting(self.target, package)

        self.assertNotEqual(first, second)
        self.assertNotEqual(self.target.list_segments(first)[0]['stream_id'],
                            self.target.list_segments(second)[0]['stream_id'])
        self.assertTrue(Path(self.target.list_segments(second)[0]['path']).is_file())

    def test_exporting_a_meeting_that_is_recording_is_rejected(self):
        meeting = self.source.create_meeting('live')

        with self.assertRaisesRegex(RuntimeError, 'grabando'):
            export_meeting(self.source, meeting, self.out / 'live.zip')
        with self.assertRaisesRegex(ValueError, 'Reunión desconocida'):
            export_meeting(self.source, 'missing', self.out / 'missing.zip')
        self.assertEqual(list(self.out.iterdir()), [])

    def test_export_replaces_an_existing_file_only_when_complete(self):
        meeting = _recorded_meeting(self.source)
        destination = self.out / 'demo.zip'
        destination.write_bytes(b'old')

        export_meeting(self.source, meeting, destination)

        self.assertTrue(zipfile.is_zipfile(destination))
        self.assertEqual([path.name for path in self.out.iterdir()], ['demo.zip'])

    def _package(self, name, members, manifest=None):
        path = self.out / name
        with zipfile.ZipFile(path, 'w') as archive:
            if manifest is not False:
                archive.writestr('manifest.json', json.dumps(manifest or {
                    'format': 'teams-lite-meeting', 'version': 1,
                    'meeting': {'title': 'x', 'started_utc': '2026-11-17T10:00:00+00:00',
                                'ended_utc': None, 'status': 'completed'},
                    'organization': None, 'project': None, 'includes_audio': False,
                    'streams': [], 'segments': [], 'events': [],
                }))
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def test_unsafe_or_invalid_packages_are_rejected_without_changes(self):
        existing = _recorded_meeting(self.target, title='kept')
        before = _snapshot(self.target)
        outside = self.target.root.parent / 'evil.txt'
        not_zip = self.out / 'plain.zip'
        not_zip.write_text('not a zip', encoding='utf-8')
        packages = [
            self._package('escape.zip', {'files/../../evil.txt': 'x'}),
            self._package('absolute.zip', {'files/C:/evil.txt': 'x'}),
            self._package('rooted.zip', {'files//evil.txt': 'x'}),
            # Windows trims trailing dots and spaces: ".. " would act like ".." and leave undeletable folders.
            self._package('dot-space.zip', {'files/x/.. /.. /evil.txt': 'x'}),
            self._package('dots-space.zip', {'files/x/... /evil.txt': 'x'}),
            self._package('trailing-dot.zip', {'files/x./evil.txt': 'x'}),
            self._package('dot-space-dir.zip', {'files/x/.. /': ''}),
            self._package('no-manifest.zip', {'files/a.txt': 'x'}, manifest=False),
            self._package('other-format.zip', {}, manifest={'format': 'something', 'version': 1}),
            self._package('newer.zip', {}, manifest={'format': 'teams-lite-meeting', 'version': 99}),
            not_zip,
        ]
        for package in packages:
            with self.subTest(package=package.name):
                with self.assertRaises(PackageError):
                    import_meeting(self.target, package)
                self.assertEqual(_snapshot(self.target), before)
        self.assertFalse(outside.exists())
        self.assertIn(existing, {row['id'] for row in self.target.list_meetings()})

    def _audio_manifest(self, **segment_changes):
        segment = {'stream_id': 's', 'sequence': 0, 'started_monotonic': 1.0,
                   'started_utc': '2026-11-17T10:00:00+00:00', 'ended_utc': None, 'frames': 1,
                   'status': 'finalized', 'file': 'audio/a.wav'}
        segment.update(segment_changes)
        return {
            'format': 'teams-lite-meeting', 'version': 1,
            'meeting': {'title': 'x', 'started_utc': '2026-11-17T10:00:00+00:00', 'ended_utc': None,
                        'status': 'completed'},
            'organization': None, 'project': None, 'includes_audio': True,
            'streams': [{'id': 's', 'kind': 'microphone', 'sample_rate': 8000, 'channels': 1}],
            'segments': [segment], 'events': [],
        }

    def test_malformed_values_are_rejected_as_damaged_packages(self):
        before = _snapshot(self.target)
        twice = self._audio_manifest()
        twice['segments'].append(dict(twice['segments'][0]))
        same_file = self._audio_manifest()
        same_file['segments'].append(dict(same_file['segments'][0], sequence=1))
        cases = {
            'huge-sequence': self._audio_manifest(sequence=2 ** 63),
            'huge-frames': self._audio_manifest(frames=2 ** 63),
            'nan-offset': self._audio_manifest(started_monotonic=float('nan')),
            'infinite-offset': self._audio_manifest(started_monotonic=float('inf')),
            'year-one': self._audio_manifest(started_utc='0001-01-01T00:00:00+00:00'),
            'duplicate-segment': twice,
            'shared-audio-file': same_file,
        }
        far_meeting = self._audio_manifest()
        far_meeting['meeting']['started_utc'] = '9999-12-31T23:59:59+00:00'
        cases['far-future-meeting'] = far_meeting
        for name, manifest in cases.items():
            with self.subTest(case=name):
                package = self._package(f'{name}.zip', {'audio/a.wav': 'RIFF'}, manifest=manifest)
                with self.assertRaises(PackageError):
                    import_meeting(self.target, package)
                self.assertEqual(_snapshot(self.target), before)

    def test_file_and_folder_with_the_same_name_is_a_package_error(self):
        before = _snapshot(self.target)
        package = self._package('clash.zip', {'files/a': 'x', 'files/a/b': 'y'})

        with self.assertRaises(PackageError):
            import_meeting(self.target, package)

        self.assertEqual(_snapshot(self.target), before)

    def test_folder_entries_of_rezipped_packages_are_accepted(self):
        package = self._package('rezipped.zip', {'files/': '', 'audio/': '', 'files/moments/': '',
                                                 'files/transcript.txt': 'hola'})

        imported = import_meeting(self.target, package)

        self.assertEqual((self.target.root / imported / 'transcript.txt').read_text(encoding='utf-8'), 'hola')

    def test_package_larger_than_the_free_disk_space_is_rejected(self):
        meeting = _recorded_meeting(self.source)
        package = export_meeting(self.source, meeting, self.out / 'demo.zip')
        before = _snapshot(self.target)
        full_disk = mock.Mock(free=10)

        with mock.patch('teams_lite.sharing.shutil.disk_usage', return_value=full_disk):
            with self.assertRaisesRegex(PackageError, 'espacio'):
                import_meeting(self.target, package)

        self.assertEqual(_snapshot(self.target), before)

    def test_failed_import_leaves_no_meeting_or_folder(self):
        meeting = _recorded_meeting(self.source)
        package = export_meeting(self.source, meeting, self.out / 'demo.zip')
        acme = self.target.create_organization('Acme')
        other = self.target.create_project(self.target.create_organization('Other'), 'P')
        before = _snapshot(self.target)

        with self.assertRaisesRegex(ValueError, 'no pertenece'):
            import_meeting(self.target, package, acme, other)

        self.assertEqual(_snapshot(self.target), before)

    def test_leftover_import_staging_is_purged_on_startup(self):
        staging = self.target.root / '.import-abc' / 'moments'
        staging.mkdir(parents=True)
        (staging / 'x.jpg').write_bytes(b'x')

        Storage(self.target.root)

        self.assertFalse((self.target.root / '.import-abc').exists())

    def test_placement_is_suggested_by_name_ignoring_case(self):
        zenith = self.target.create_organization('zenith')
        crm = self.target.create_project(zenith, 'crm')
        self.assertEqual(suggest_placement(self.target, {'organization': 'Zenith', 'project': 'CRM'}), (zenith, crm))
        self.assertEqual(suggest_placement(self.target, {'organization': 'ZENITH', 'project': 'Otro'}), (zenith, None))
        self.assertEqual(suggest_placement(self.target, {'organization': 'Acme', 'project': 'CRM'}), (None, None))
        self.assertEqual(suggest_placement(self.target, {'organization': None, 'project': None}), (None, None))


if __name__ == '__main__':
    unittest.main()
