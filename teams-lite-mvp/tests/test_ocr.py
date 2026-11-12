import unittest

from teams_lite.ocr import extract_name_hints


class OcrTests(unittest.TestCase):
    def test_extracts_unique_visual_name_hints(self):
        text = " Ana Pérez \n\nANA PÉREZ\n--\nBob Smith\n"
        self.assertEqual(extract_name_hints(text), ["Ana Pérez", "Bob Smith"])


if __name__ == "__main__":
    unittest.main()

