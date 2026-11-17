import json
import tempfile
import unittest
from pathlib import Path

from teams_lite.previews import meeting_thumbnail


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

    def test_meeting_without_screenshots_has_no_thumbnail(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(meeting_thumbnail(Path(d)))
            self.assertIsNone(meeting_thumbnail(Path(d) / 'missing'))


if __name__ == '__main__':
    unittest.main()
