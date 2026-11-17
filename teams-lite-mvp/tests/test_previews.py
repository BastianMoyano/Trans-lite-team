import json
import tempfile
import unittest
from pathlib import Path

from teams_lite.previews import meeting_screenshots, meeting_thumbnail


def _write_moments(meeting_dir, moments):
    lines = [json.dumps(moment) for moment in moments]
    (meeting_dir / 'moments.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')


class MeetingThumbnailTests(unittest.TestCase):
    def test_first_moment_screenshot_wins_over_visual_frames(self):
        with tempfile.TemporaryDirectory() as d:
            meeting = Path(d)
            (meeting / 'moments').mkdir()
            (meeting / 'moments' / 'teams-2.jpg').write_bytes(b'jpg')
            (meeting / 'moments' / 'teams-3.jpg').write_bytes(b'jpg')
            (meeting / 'visual-frames').mkdir()
            (meeting / 'visual-frames' / 'teams-1.png').write_bytes(b'png')
            _write_moments(meeting, [
                {'offset_seconds': 30.0, 'image': 'moments/teams-3.jpg'},
                {'offset_seconds': 5.0, 'image': None},  # a moment without capture is skipped
                {'offset_seconds': 10.0, 'image': 'moments/teams-2.jpg'},
            ])

            self.assertEqual(meeting_thumbnail(meeting), meeting / 'moments' / 'teams-2.jpg')

    def test_falls_back_to_the_earliest_visual_frame(self):
        with tempfile.TemporaryDirectory() as d:
            meeting = Path(d)
            frames = meeting / 'visual-frames'
            frames.mkdir()
            (frames / 'teams-20261117T100500000000Z.png').write_bytes(b'png')
            (frames / 'teams-20261117T100000000000Z.png').write_bytes(b'png')
            (frames / '.teams-20261117T090000000000Z.part.png').write_bytes(b'partial')
            _write_moments(meeting, [{'offset_seconds': 1.0, 'image': 'moments/deleted.jpg'}])

            self.assertEqual(meeting_thumbnail(meeting), frames / 'teams-20261117T100000000000Z.png')

    def test_moment_images_outside_the_meeting_are_never_used(self):
        with tempfile.TemporaryDirectory() as d:
            meeting = Path(d) / 'meeting'
            meeting.mkdir()
            outside = Path(d) / 'outside.jpg'
            outside.write_bytes(b'jpg')
            _write_moments(meeting, [
                {'offset_seconds': 1.0, 'image': str(outside)},
                {'offset_seconds': 2.0, 'image': '../outside.jpg'},
                {'offset_seconds': 3.0, 'image': '\\\\attacker\\share\\x.jpg'},
            ])

            self.assertIsNone(meeting_thumbnail(meeting))

    def test_meeting_without_screenshots_has_no_thumbnail(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(meeting_thumbnail(Path(d)))
            self.assertIsNone(meeting_thumbnail(Path(d) / 'missing'))


class MeetingScreenshotsTests(unittest.TestCase):
    def test_lists_marked_moments_by_time_then_active_speaker_frames(self):
        with tempfile.TemporaryDirectory() as d:
            meeting = Path(d) / 'meeting'
            (meeting / 'moments').mkdir(parents=True)
            for name in ('a.jpg', 'b.jpg'):
                (meeting / 'moments' / name).write_bytes(b'jpg')
            frames = meeting / 'visual-frames'
            frames.mkdir()
            (frames / 'teams-20261117T100500000000Z.png').write_bytes(b'png')
            (frames / 'teams-20261117T100000000000Z.png').write_bytes(b'png')
            (frames / '.teams-20261117T090000000000Z.part.png').write_bytes(b'partial')
            (Path(d) / 'outside.jpg').write_bytes(b'jpg')
            _write_moments(meeting, [
                {'offset_seconds': 75.0, 'image': 'moments/b.jpg', 'note': 'Demo del CRM'},
                {'offset_seconds': 3.0, 'image': 'moments/a.jpg', 'note': ''},
                {'offset_seconds': 9.0, 'image': None},  # a moment without capture
                {'offset_seconds': 12.0, 'image': 'moments/missing.jpg'},
                {'offset_seconds': 15.0, 'image': '../outside.jpg'},
            ])

            shots = meeting_screenshots(meeting)

            self.assertEqual([shot['path'] for shot in shots], [
                meeting / 'moments' / 'a.jpg', meeting / 'moments' / 'b.jpg',
                frames / 'teams-20261117T100000000000Z.png', frames / 'teams-20261117T100500000000Z.png',
            ])
            self.assertEqual([shot['kind'] for shot in shots], ['moment', 'moment', 'frame', 'frame'])
            self.assertEqual([shot['label'] for shot in shots], [
                '00:00:03', '00:01:15 · Demo del CRM', 'Hablante activo · 10:00:00 UTC', 'Hablante activo · 10:05:00 UTC',
            ])
            self.assertEqual(meeting_thumbnail(meeting), meeting / 'moments' / 'a.jpg')

    def test_meeting_without_screenshots_lists_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(meeting_screenshots(Path(d)), [])
            self.assertEqual(meeting_screenshots(Path(d) / 'missing'), [])


if __name__ == '__main__':
    unittest.main()
