import unittest
from teams_lite.speakers import speaker_for_source

class SpeakerTests(unittest.TestCase):
    def test_cautious_association(self):
        self.assertEqual(speaker_for_source('local'), 'Local')
        self.assertEqual(speaker_for_source('remote'), 'Remoto/Desconocido')
        self.assertEqual(speaker_for_source('remote', {'remote': 'Ana'}), 'Ana')
        self.assertEqual(speaker_for_source('other'), 'Desconocido')
