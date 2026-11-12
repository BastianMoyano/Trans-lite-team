import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from teams_lite.app import MainWindow
from teams_lite.config import AppConfig
from teams_lite.storage import Storage


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_selected_meeting_shows_transcript_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Reunión de prueba')
            storage.finish_meeting(meeting_id)
            transcript = Path(d) / meeting_id / 'transcript.txt'
            transcript.write_text('[00:00:00.000] Local: Hola\n', encoding='utf-8')

            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                row = next(
                    index for index in range(window.table.rowCount())
                    if window.table.item(index, 0).data(Qt.ItemDataRole.UserRole) == meeting_id
                )
                window.table.selectRow(row)
                self.application.processEvents()

                self.assertEqual(window.transcript_viewer.toPlainText(), '[00:00:00.000] Local: Hola')
                self.assertTrue(window.delete_button.isEnabled())
                self.assertEqual(window.start_button.text(), 'Iniciar grabación')
                self.assertEqual(window.delete_button.text(), 'Eliminar reunión')

                window._confirm_delete = lambda _title: True
                window.delete_selected_meeting()

                self.assertNotIn(meeting_id, {row['id'] for row in storage.list_meetings()})
                self.assertFalse((Path(d) / meeting_id).exists())
                self.assertEqual(window.status_label.text(), 'Reunión eliminada.')
            finally:
                window.close()

    def test_recording_controls_show_only_the_available_action(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                self.assertFalse(window.start_button.isHidden())
                self.assertTrue(window.stop_button.isHidden())

                window._set_recording_controls(recording=True)
                self.assertTrue(window.start_button.isHidden())
                self.assertFalse(window.stop_button.isHidden())
                self.assertTrue(window.stop_button.isEnabled())

                window._set_recording_controls(recording=True, finalizing=True)
                self.assertFalse(window.stop_button.isEnabled())

                window._set_recording_controls(recording=False)
                self.assertFalse(window.start_button.isHidden())
                self.assertTrue(window.stop_button.isHidden())
            finally:
                window.close()

    def test_selected_meeting_without_transcript_shows_pending_state(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Sin transcripción')
            storage.finish_meeting(meeting_id)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.table.selectRow(0)
                self.application.processEvents()
                self.assertEqual(window.transcript_viewer.toPlainText(), '')
                self.assertIn('todavía no está disponible', window.transcript_viewer.placeholderText())
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
