import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from teams_lite.astro import AstroState
from teams_lite.astro_sprites import (
    BUILTIN_DIR,
    DEFAULT_CHARACTER,
    list_characters,
    load_animations,
    scale_pixels,
)


def _strip(path, frames, size, color=QColor(200, 60, 60)):
    image = QImage(size * frames, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    for frame in range(frames):
        image.setPixelColor(frame * size + frame % size, 1, color)  # a different pixel per frame
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))


class AstroSpritesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_built_in_astro_and_brasa_ship_with_the_app(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list_characters(d), ['astro', 'brasa'])
            for character in ('astro', 'brasa'):
                animations, problems = load_animations(d, character)
                self.assertEqual(problems, [])
                for state, animation in animations.items():
                    with self.subTest(character=character, state=state):
                        self.assertEqual(animation.source, 'builtin')
                        self.assertEqual(len(animation.frames), 8)
                        self.assertEqual({(f.width(), f.height()) for f in animation.frames}, {(64, 64)})
            self.assertTrue((BUILTIN_DIR / 'astro' / 'astro_idle.png').is_file())

    def test_placeholder_animates_every_state_with_square_frames(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as no_builtins:
            animations, problems = load_animations(d, DEFAULT_CHARACTER, builtin_root=no_builtins)
            self.assertEqual(problems, [])
            self.assertEqual(set(animations), set(AstroState))
            for state, animation in animations.items():
                with self.subTest(state=state):
                    self.assertEqual(animation.source, 'placeholder')
                    self.assertGreaterEqual(len(animation.frames), 2)
                    self.assertGreater(animation.fps, 0)
                    sizes = {(frame.width(), frame.height()) for frame in animation.frames}
                    self.assertEqual(len(sizes), 1)
                    width, height = sizes.pop()
                    self.assertEqual(width, height)
                    self.assertTrue(any(frame.pixelColor(x, y).alpha()
                                        for frame in animation.frames[:1]
                                        for x in range(width) for y in range(height)))
            idle = animations[AstroState.IDLE].frames
            self.assertNotEqual(idle[0], idle[-1])  # it actually moves

    def test_user_strip_replaces_only_its_state(self):
        with tempfile.TemporaryDirectory() as d:
            _strip(Path(d) / 'mascots' / 'astro' / 'astro_recording.png', frames=3, size=16)

            animations, problems = load_animations(d, 'astro')

            self.assertEqual(problems, [])
            recording = animations[AstroState.RECORDING]
            self.assertEqual(recording.source, 'custom')
            self.assertEqual(len(recording.frames), 3)
            self.assertEqual({(f.width(), f.height()) for f in recording.frames}, {(16, 16)})
            self.assertEqual(recording.frames[1].pixelColor(1, 1).red(), 200)
            self.assertEqual(animations[AstroState.IDLE].source, 'builtin')  # the rest stays the shipped Astro

    def test_a_strip_that_breaks_the_contract_falls_back_and_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / 'mascots' / 'astro' / 'astro_idle.png'
            broken.parent.mkdir(parents=True)
            QImage(50, 16, QImage.Format.Format_ARGB32).save(str(broken))  # 50 is not a multiple of 16
            (broken.parent / 'astro_error.png').write_bytes(b'not a png')

            animations, problems = load_animations(d, 'astro')

            self.assertEqual(animations[AstroState.IDLE].source, 'builtin')  # falls back to the shipped frames
            self.assertEqual(animations[AstroState.ERROR].source, 'builtin')
            self.assertEqual(len(problems), 2)
            self.assertTrue(any('astro_idle.png' in problem and 'cuadrados' in problem for problem in problems))
            self.assertTrue(any('astro_error.png' in problem for problem in problems))

    def test_characters_are_folders_and_their_files_may_use_the_character_name(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as no_builtins:
            self.assertEqual(list_characters(d, builtin_root=no_builtins), ['astro'])
            _strip(Path(d) / 'mascots' / 'nube' / 'nube_idle.png', frames=2, size=8)
            _strip(Path(d) / 'mascots' / 'nube' / 'success.png', frames=4, size=8)
            (Path(d) / 'mascots' / 'vacia').mkdir()

            self.assertEqual(list_characters(d, builtin_root=no_builtins), ['astro', 'nube'])
            self.assertEqual(list_characters(d), ['astro', 'brasa', 'nube'])
            animations, _ = load_animations(d, 'nube', builtin_root=no_builtins)
            self.assertEqual(len(animations[AstroState.IDLE].frames), 2)
            self.assertEqual(len(animations[AstroState.SUCCESS].frames), 4)
            self.assertEqual(animations[AstroState.ERROR].source, 'placeholder')
            # A name that tries to leave the mascots folder is ignored.
            animations, _ = load_animations(d, '../../outside')
            self.assertTrue(all(a.source == 'placeholder' for a in animations.values()))

    def test_scaling_keeps_hard_pixels(self):
        image = QImage(2, 1, QImage.Format.Format_ARGB32)
        image.setPixelColor(0, 0, QColor(255, 0, 0))
        image.setPixelColor(1, 0, QColor(0, 0, 255))

        scaled = scale_pixels(image, 3)

        self.assertEqual((scaled.width(), scaled.height()), (6, 3))
        self.assertEqual({scaled.pixelColor(x, 2).name() for x in range(3)}, {'#ff0000'})
        self.assertEqual({scaled.pixelColor(x, 0).name() for x in range(3, 6)}, {'#0000ff'})


if __name__ == '__main__':
    unittest.main()
