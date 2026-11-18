import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QBoxLayout

from teams_lite.app import APP_STYLESHEET, COLORS, _MEETING_ROLE, MainWindow
from teams_lite.config import AppConfig
from teams_lite.library_ui import THUMBNAIL_ROLE, ExportOptionsDialog, MoveMeetingDialog
from teams_lite.sharing import export_meeting, read_package
from teams_lite.moments import load_moments
from teams_lite.ocr import load_participants, merge_participants
from teams_lite.storage import Storage
from teams_lite.updates import UpdateInfo
from teams_lite.window_capture import TeamsWindowMinimized


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_shell_has_distinct_identity_and_discoverable_navigation(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.show()
                self.application.processEvents()
                self.assertEqual(COLORS['accent'], '#46C7B5')
                self.assertIn(COLORS['accent'], APP_STYLESHEET)
                self.assertEqual(window.windowTitle(), 'AstroMeet')
                self.assertFalse(window.windowIcon().isNull())
                self.assertEqual(window.brand_title.text(), 'AstroMeet')
                self.assertEqual(window.brand_tagline.text(), 'Tu compañero inteligente de reuniones')
                self.assertEqual(window.mascot.windowTitle(), 'AstroMeet · Astro')
                self.assertIn('abrir AstroMeet', window.mascot.sprite.toolTip())
                self.assertEqual(
                    [window.side_nav.item(index).text() for index in range(window.side_nav.count())],
                    ['Grabar', 'Biblioteca', 'Organización', 'Mascota'],
                )
                self.assertTrue(all(not window.side_nav.item(index).icon().isNull()
                                    for index in range(window.side_nav.count())))
                self.assertEqual(window.move_button.text(), 'Mover')
                self.assertEqual(window.export_button.text(), 'Exportar')
                self.assertEqual(window.view_transcript_button.text(), 'Transcripción')
                self.assertEqual(window.view_minutes_button.text(), 'Minuta')
                self.assertEqual(window.view_chat_button.text(), 'Chat con IA')
                window.resize(1010, 600)
                self.application.processEvents()
                self.assertEqual(window.overview_columns.direction(), QBoxLayout.Direction.TopToBottom)
                window.resize(1190, 780)
                self.application.processEvents()
                self.assertEqual(window.overview_columns.direction(), QBoxLayout.Direction.LeftToRight)
            finally:
                window.close()

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

                self.assertEqual(window.transcript_text(), '[00:00:00.000] Local: Hola')
                self.assertIs(window.transcript_stack.currentWidget(), window.transcript_table)
                self.assertEqual(window.transcript_table.rowCount(), 1)
                self.assertEqual(window.transcript_table.item(0, 0).text(), '00:00.000')
                self.assertEqual(window.transcript_table.item(0, 1).text(), 'Local')
                self.assertEqual(window.transcript_table.item(0, 2).text(), 'Hola')
                self.assertTrue(window.copy_button.isEnabled())
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
                self.assertEqual(window.transcript_text(), '')
                self.assertIs(window.transcript_stack.currentWidget(), window.transcript_placeholder)
                self.assertIn('todavía no está disponible', window.transcript_placeholder.text())
                self.assertFalse(window.copy_button.isEnabled())
            finally:
                window.close()

    def test_transcript_json_renders_time_ranges_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Con turnos')
            storage.finish_meeting(meeting_id)
            meeting_dir = Path(d) / meeting_id
            (meeting_dir / 'transcript.txt').write_text(
                '[00:00:00.356 - 00:00:20.390] Local: Hola\n'
                '[00:00:21.000 - 00:00:29.200] Ana: Buen día\n',
                encoding='utf-8',
            )
            (meeting_dir / 'transcript.json').write_text(
                '[{"start": 0.356, "end": 20.39, "source": "local", "speaker": "Local", "text": "Hola"},'
                ' {"start": 21.0, "end": 29.2, "source": "remote", "speaker": "Ana", "text": "Buen día"}]',
                encoding='utf-8',
            )
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.table.selectRow(0)
                self.application.processEvents()
                self.assertEqual(window.transcript_table.rowCount(), 2)
                self.assertEqual(window.transcript_table.item(0, 0).text(), '00:00.356 –\n00:20.390')
                self.assertEqual(window.transcript_table.item(1, 1).text(), 'Ana')
                self.assertEqual(window.transcript_meta.text(), f'2 turnos · 29 s · reunión {meeting_id[:8]}…')
                self.assertEqual(window.history_count.text(), '1')
            finally:
                window.close()

    def test_visual_speaker_control_uses_configured_state(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(
                data_dir=d,
                auto_transcribe=False,
                visual_speaker_enabled=True,
            ))
            window.timer.stop()
            try:
                self.assertTrue(window.visual_speaker_checkbox.isChecked())
                self.assertIn("hablante activo", window.visual_speaker_checkbox.text().casefold())
            finally:
                window.close()

    def test_visual_tracking_follows_recording_lifecycle(self):
        class FakeRecorder:
            def __init__(self, store, meeting_id, **kwargs):
                self.store = store
                self.meeting_id = meeting_id
                self.errors = []

            def start(self):
                pass

            def stop(self):
                self.store.finish_meeting(self.meeting_id)

        class FakeSampler:
            instances = []

            def __init__(self, capture, on_frame, **kwargs):
                self.started = False
                self.stopped = False
                self.errors = []
                self.instances.append(self)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as d, \
             mock.patch("teams_lite.app.Recorder", FakeRecorder), \
             mock.patch("teams_lite.app.FrameSampler", FakeSampler), \
             mock.patch("teams_lite.app.visual_model_problem", return_value=None):
            window = MainWindow(AppConfig(
                data_dir=d,
                auto_transcribe=False,
                visual_speaker_enabled=True,
            ))
            window.timer.stop()
            try:
                window.start_recording()
                sampler = FakeSampler.instances[-1]
                self.assertTrue(sampler.started)
                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertTrue(sampler.stopped)
                self.assertIsNone(window.recorder)
            finally:
                window.close()

    def test_missing_visual_model_warns_and_still_records_audio(self):
        class FakeRecorder:
            def __init__(self, store, meeting_id, **kwargs):
                self.store = store
                self.meeting_id = meeting_id
                self.errors = []

            def start(self):
                pass

            def stop(self):
                self.store.finish_meeting(self.meeting_id)

        sampler_factory = mock.Mock()
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as model_dir, \
             mock.patch("teams_lite.app.Recorder", FakeRecorder), \
             mock.patch("teams_lite.app.FrameSampler", sampler_factory), \
             mock.patch("teams_lite.app.QMessageBox.warning") as modal_warning:
            (Path(model_dir) / "config.json").write_text("{}", encoding="utf-8")
            window = MainWindow(AppConfig(
                data_dir=d,
                auto_transcribe=False,
                visual_speaker_enabled=True,
                wevisone_model_dir=model_dir,
            ))
            window.timer.stop()
            try:
                window.start_recording()
                self.assertIsNotNone(window.recorder)
                sampler_factory.assert_not_called()
                self.assertIn("model.safetensors", window.status_label.text())
                events = Storage(d).list_events(window.meeting_id)
                self.assertIn("visual_tracking_disabled", [event["kind"] for event in events])
                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                modal_warning.assert_not_called()
            finally:
                window.close()


class _FakeRecorder:
    def __init__(self, store, meeting_id, **kwargs):
        self.store = store
        self.meeting_id = meeting_id
        self.errors = []

    def start(self):
        pass

    def stop(self):
        self.store.finish_meeting(self.meeting_id)


class QuickStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    _close = lambda self, window: MomentHotkeyTests._close(self, window)

    def _window(self, d):
        window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
        window.timer.stop()
        window._show_error = mock.Mock(side_effect=AssertionError('no dialog expected'))
        return window

    def test_quick_start_records_at_once_with_the_defaults(self):
        from teams_lite.mascot import MascotSettings, save_mascot_settings
        with tempfile.TemporaryDirectory() as d, mock.patch('teams_lite.app.Recorder', _FakeRecorder):
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            save_mascot_settings(d, MascotSettings(title='Daily ventas', organization_id=zenith, project_id=crm))
            window = self._window(d)
            try:
                window.title_input.setText('Título escrito a mano')
                window.quick_start_recording()

                self.assertIsNotNone(window.recorder)
                meeting = storage.get_meeting(window.meeting_id)
                self.assertEqual((meeting['title'], meeting['organization_id'], meeting['project_id']),
                                 ('Daily ventas', zenith, crm))
                self.assertEqual(window.title_input.text(), 'Título escrito a mano')  # the form is untouched
                self.assertEqual(window.history_scope.text(), 'Zenith › CRM')  # recent list shows it
                window.quick_start_recording()  # already recording: nothing new starts
                self.assertEqual(len(storage.list_meetings()), 1)
            finally:
                self._close(window)

    def test_missing_default_organization_records_in_the_vault_and_says_so(self):
        from teams_lite.mascot import MascotSettings, save_mascot_settings
        with tempfile.TemporaryDirectory() as d, mock.patch('teams_lite.app.Recorder', _FakeRecorder):
            Storage(d)
            save_mascot_settings(d, MascotSettings(organization_id='deleted-org'))
            window = self._window(d)
            try:
                window.quick_start_recording()
                meeting = window.store.get_meeting(window.meeting_id)
                self.assertEqual((meeting['organization_id'], meeting['project_id']), (None, None))
                self.assertIn('Cofre de reuniones', window.status_label.text())
            finally:
                self._close(window)


def _mouse(kind, widget, x, y):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    local = QPointF(x, y)
    return QMouseEvent(kind, local, widget.mapToGlobal(local), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)


class MascotUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    _close = lambda self, window: MomentHotkeyTests._close(self, window)

    def _window(self, d):
        window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
        window.timer.stop()
        window._show_error = mock.Mock(side_effect=AssertionError('no dialog expected'))
        return window

    def _wait_for(self, condition, seconds=2.0):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def test_astro_floats_and_runs_the_quick_actions_from_its_menu(self):
        from teams_lite.astro import AstroState
        with tempfile.TemporaryDirectory() as d, mock.patch('teams_lite.app.Recorder', _FakeRecorder):
            window = self._window(d)
            try:
                mascot = window.mascot
                self.assertFalse(mascot.isHidden())
                self.assertTrue(mascot.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                self.assertTrue(mascot.windowFlags() & Qt.WindowType.FramelessWindowHint)
                self.assertTrue(mascot.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                self.assertEqual(mascot.sprite.pixmap().width(), 64 * 3)  # the shipped 64 px Astro at the default size
                self.assertFalse(hasattr(mascot, 'menu'))  # settings live in the Mascota section only
                for button in (mascot.record_button, mascot.capture_button, mascot.ocr_button):
                    self.assertTrue(button.isHidden())  # folded until Astro is clicked

                sprite_before = mascot.sprite.mapToGlobal(mascot.sprite.rect().topLeft())
                mascot.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, mascot, 10, 10))
                mascot.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, mascot, 10, 10))
                self._wait_for(lambda: not mascot.record_button.isHidden())
                self.application.processEvents()
                self.assertEqual(mascot.sprite.mapToGlobal(mascot.sprite.rect().topLeft()), sprite_before)
                sprite = mascot.sprite.geometry()
                self.assertLess(mascot.record_button.geometry().right(), sprite.left())  # left
                self.assertGreater(mascot.capture_button.geometry().left(), sprite.right())  # right
                self.assertGreater(mascot.ocr_button.geometry().top(), sprite.bottom())  # below
                self.assertEqual(mascot.record_button.text(), 'Grabar')
                self.assertFalse(mascot.capture_button.isEnabled())
                self.assertFalse(mascot.ocr_button.isEnabled())

                mascot.record_button.click()  # starts at once with the defaults
                self.assertIsNotNone(window.recorder)
                self.assertEqual(window.store.get_meeting(window.meeting_id)['title'], 'Reunión rápida')
                self.assertIs(window.astro.state, AstroState.RECORDING)
                self.assertEqual(mascot.bubble.text(), 'Grabando reunión')
                self.assertTrue(mascot.record_button.isHidden())  # an action folds the buttons again
                self.assertEqual(mascot.record_button.text(), 'Detener')
                self.assertTrue(mascot.capture_button.isEnabled())
                self.assertTrue(mascot.ocr_button.isEnabled())
                self.assertFalse(mascot.timer_label.isHidden())

                with mock.patch.object(window, 'mark_moment') as mark, mock.patch.object(window, 'capture_ocr') as ocr:
                    mascot.capture_button.click()
                    mascot.ocr_button.click()
                mark.assert_called_once_with(from_hotkey=True)
                ocr.assert_called_once_with()

                mascot.record_button.click()  # stops
                OcrAttendeeTests._wait_until_stopped(self, window)
                self.assertIsNone(window.recorder)
                self.assertIs(window.astro.state, AstroState.SUCCESS)  # stored, no transcription configured
                self.assertEqual(mascot.bubble.text(), '¡Reunión guardada!')
                self.assertEqual(mascot.record_button.text(), 'Grabar')

                window.showMinimized()
                mascot.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, mascot, 10, 10))
                mascot.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, mascot, 10, 10))
                mascot.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, mascot, 10, 10))
                self.assertFalse(window.isMinimized())  # a double click opens Teams Lite
                self._wait_for(lambda: False, seconds=0.6)  # longer than the double-click interval
                self.assertTrue(mascot.record_button.isHidden())  # ...and does not unfold the buttons
            finally:
                self._close(window)
            self.assertTrue(mascot.isHidden())  # closing Teams Lite closes its mascot

    def test_astro_bubble_shows_the_whole_message(self):
        with tempfile.TemporaryDirectory() as d:
            window = self._window(d)
            try:
                bubble = window.mascot.bubble
                for message in ('Listo para comenzar',
                                'Teams está minimizado: restaura su ventana (puede quedar en otro monitor '
                                'o detrás de otras).'):
                    with self.subTest(message=message):
                        window.astro.error(message)
                        self.application.processEvents()
                        self.assertEqual(bubble.text(), message)
                        self.assertGreaterEqual(bubble.height(), bubble.heightForWidth(bubble.width()))
                        self.assertLessEqual(bubble.width(), 240)
                        self.assertGreaterEqual(bubble.width(), min(240, bubble.fontMetrics()
                                                                    .horizontalAdvance('Listo para comenzar')))
                        self.assertGreaterEqual(window.mascot.width(), bubble.width())  # never clipped by the window
            finally:
                window.close()

    def test_astro_mirrors_real_events(self):
        from teams_lite.astro import AstroState
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.QMessageBox.warning'):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                window.quick_start_recording()
                meeting_id = window.meeting_id
                window.signals.moment_marked.emit({'offset_seconds': 3.0, 'image': 'moments/a.jpg', 'source': 'teams'})
                self.application.processEvents()
                self.assertIs(window.astro.state, AstroState.CAPTURING)
                window.signals.moment_marked.emit({'offset_seconds': 4.0, 'image': None,
                                                   'problem': 'Teams está minimizado.'})
                self.application.processEvents()
                self.assertIs(window.astro.state, AstroState.ERROR)
                self.assertEqual(window.mascot.bubble.text(), 'Teams está minimizado.')
                window.signals.ocr_ready.emit('OCR: 3 nombres en pantalla · 3 asistentes en total.')
                self.application.processEvents()
                self.assertIs(window.astro.state, AstroState.CAPTURING)
                window._show_error('No se pudo leer la ventana de Teams.')
                self.assertEqual(window.astro.message, 'No se pudo leer la ventana de Teams.')
                window.stop_recording()
                OcrAttendeeTests._wait_until_stopped(self, window)

                window.astro.recording_started()
                window.astro.stop_requested()
                window.astro.recording_stopped(processing=True)
                window.signals.transcription_progress.emit(meeting_id, 0.5)
                self.application.processEvents()
                self.assertEqual(window.astro.message, 'Procesando grabación · 50 %')
                window.signals.transcript_ready.emit(str(window.store.root / meeting_id / 'transcript.txt'))
                window.signals.transcription_finished.emit(meeting_id)
                self.application.processEvents()
                self.assertIs(window.astro.state, AstroState.SUCCESS)

                window.astro.recording_stopped(processing=True)  # a transcription that fails
                window.signals.transcription_finished.emit(meeting_id)
                self.application.processEvents()
                self.assertIs(window.astro.state, AstroState.ERROR)
            finally:
                window.close()

    def test_recording_never_depends_on_astro(self):
        with tempfile.TemporaryDirectory() as d, mock.patch('teams_lite.app.Recorder', _FakeRecorder):
            window = self._window(d)
            try:
                with mock.patch.object(window.astro, 'recording_started', side_effect=RuntimeError('sprite failure')), \
                     mock.patch.object(window.mascot, 'set_recording', side_effect=RuntimeError('paint failure')):
                    window.quick_start_recording()
                self.assertIsNotNone(window.recorder)
                self.assertTrue(window.store.get_meeting(window.meeting_id)['status'] == 'recording')
            finally:
                self._close(window)

    def test_sound_motion_size_and_character_options_apply_at_once(self):
        from teams_lite.mascot import load_mascot_settings
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app._confirmation_beep') as beep, \
             mock.patch('teams_lite.app.capture_moment', return_value={'offset_seconds': 1.0, 'image': None}):
            strip = QImage(32, 16, QImage.Format.Format_ARGB32)
            strip.fill(0)
            (Path(d) / 'mascots' / 'brasa').mkdir(parents=True)
            strip.save(str(Path(d) / 'mascots' / 'brasa' / 'idle.png'))
            window = self._window(d)
            try:
                page, mascot = window.mascot_page, window.mascot
                self.assertTrue(mascot.frame_timer.isActive())
                page.motion_checkbox.setChecked(False)
                self.assertFalse(mascot.frame_timer.isActive())  # a still frame: no animation work
                self.assertFalse(load_mascot_settings(d).motion)
                page.motion_checkbox.setChecked(True)
                self.assertTrue(mascot.frame_timer.isActive())

                page.size_combo.setCurrentIndex(page.size_combo.findData(4))
                self.assertEqual(mascot.sprite.pixmap().width(), 64 * 4)
                self.assertEqual(page.character_combo.currentData(), 'astro')
                self.assertEqual([page.character_combo.itemData(i) for i in range(page.character_combo.count())],
                                 ['astro', 'brasa'])
                page.character_combo.setCurrentIndex(page.character_combo.findData('brasa'))
                self.assertEqual(mascot.sprite.pixmap().width(), 16 * 4)  # the user's 16 px frames
                self.assertEqual(load_mascot_settings(d).character, 'brasa')

                page.sound_checkbox.setChecked(False)
                window.quick_start_recording()
                mascot.capture_button.click()
                beep.assert_not_called()  # effects muted
            finally:
                self._close(window)


    def test_mascot_section_sets_the_defaults_and_visibility(self):
        from teams_lite.mascot import load_mascot_settings
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            window = self._window(d)
            try:
                page = window.mascot_page
                window.side_nav.setCurrentRow(3)
                self.assertIs(window.pages.currentWidget(), page)
                self.assertEqual(page.title_input.text(), 'Reunión rápida')

                page.title_input.setText('Daily ventas')
                page.title_input.editingFinished.emit()
                picker = page.placement_picker
                picker.organization_combo.setCurrentIndex(picker.organization_combo.findData(zenith))
                picker.project_combo.setCurrentIndex(picker.project_combo.findData(crm))
                saved = load_mascot_settings(d)
                self.assertEqual((saved.title, saved.organization_id, saved.project_id), ('Daily ventas', zenith, crm))

                page.show_checkbox.setChecked(False)
                self.assertTrue(window.mascot.isHidden())
                self.assertFalse(load_mascot_settings(d).enabled)
            finally:
                window.close()

            reopened = self._window(d)
            try:
                self.assertTrue(reopened.mascot.isHidden())
                self.assertFalse(reopened.mascot_page.show_checkbox.isChecked())
                self.assertEqual(reopened.mascot_page.title_input.text(), 'Daily ventas')
            finally:
                reopened.close()

    def test_deleting_the_default_organization_moves_the_default_to_the_vault(self):
        from teams_lite.mascot import MascotSettings, load_mascot_settings, save_mascot_settings
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            save_mascot_settings(d, MascotSettings(title='Daily', organization_id=zenith))
            window = self._window(d)
            try:
                admin = window.admin_page
                admin.confirm = lambda *_args: True
                admin.organization_list.setCurrentRow(0)
                admin.delete_organization_button.click()

                saved = load_mascot_settings(d)
                self.assertEqual((saved.title, saved.organization_id), ('Daily', None))
                self.assertEqual(window.mascot_page.placement_picker.organization_combo.currentText(),
                                 'Cofre de reuniones')
            finally:
                window.close()

    def test_mascot_remembers_where_it_was_dropped(self):
        from teams_lite.mascot import load_mascot_settings
        with tempfile.TemporaryDirectory() as d:
            window = self._window(d)
            try:
                window.mascot.move(140, 260)
                window.mascot.moved.emit(140, 260)  # end of a drag
                self.assertEqual(load_mascot_settings(d).position, (140, 260))
            finally:
                window.close()
            reopened = self._window(d)
            try:
                self.assertEqual((reopened.mascot.x(), reopened.mascot.y()), (140, 260))
            finally:
                reopened.close()


class MomentHotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _close(self, window):
        if window.recorder is not None:  # never leave a recording open: closing would ask to confirm
            window.stop_recording()
            OcrAttendeeTests._wait_until_stopped(self, window)
        window.close()

    @staticmethod
    def _kinds(window):
        return [event['kind'] for event in window.store.list_events(window.meeting_id)]

    def test_unavailable_hotkey_is_retried_while_recording_and_logged(self):
        attempts = iter([False, False, True])
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.register_moment_hotkey', side_effect=lambda _handle: next(attempts)), \
             mock.patch('teams_lite.app.unregister_moment_hotkey') as unregister:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                window.start_recording()
                self.assertFalse(window._hotkey_registered)
                self.assertTrue(window._hotkey_retry_timer.isActive())
                self.assertIn('reintentando', window.moment_hint.text())
                self.assertEqual(self._kinds(window).count('moment_hotkey_unavailable'), 1)

                window._hotkey_retry_timer.timeout.emit()  # still taken: logged only once
                self.assertFalse(window._hotkey_registered)
                self.assertEqual(self._kinds(window).count('moment_hotkey_unavailable'), 1)
                window._hotkey_retry_timer.timeout.emit()  # released by the other app
                self.assertTrue(window._hotkey_registered)
                self.assertFalse(window._hotkey_retry_timer.isActive())
                self.assertIn('Atajo global activo', window.moment_hint.text())
                self.assertIn('moment_hotkey_ready', self._kinds(window))

                window.stop_recording()
                OcrAttendeeTests._wait_until_stopped(self, window)
                unregister.assert_called_once()
            finally:
                self._close(window)

    @unittest.skipUnless(sys.platform == 'win32', 'WM_HOTKEY is a Windows message')
    def test_hotkey_press_is_logged_and_marks_a_moment(self):
        import ctypes
        from ctypes import wintypes
        from PySide6.QtCore import QByteArray
        from teams_lite.hotkey import MOMENT_HOTKEY_ID, WM_HOTKEY
        fake_moment = {'offset_seconds': 1.0, 'image': None, 'source': None, 'problem': None, 'note': ''}
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.register_moment_hotkey', return_value=True), \
             mock.patch('teams_lite.app.unregister_moment_hotkey'), \
             mock.patch('teams_lite.app._confirmation_beep'), \
             mock.patch('teams_lite.app.capture_moment', return_value=fake_moment) as capture:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                window.start_recording()
                self.assertIn('moment_hotkey_ready', self._kinds(window))
                message = wintypes.MSG()
                message.message, message.wParam = WM_HOTKEY, MOMENT_HOTKEY_ID
                handled = window.nativeEvent(QByteArray(b'windows_generic_MSG'), ctypes.addressof(message))
                self.assertEqual(handled, (True, 0))
                self.assertIn('moment_hotkey_pressed', self._kinds(window))
                deadline = time.monotonic() + 2
                while not capture.called and time.monotonic() < deadline:
                    time.sleep(0.01)
                capture.assert_called_once()
                window.stop_recording()
                OcrAttendeeTests._wait_until_stopped(self, window)
            finally:
                self._close(window)


class OcrAttendeeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _wait_until_stopped(self, window):
        deadline = time.monotonic() + 2
        while window.recorder is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def test_ocr_samples_teams_attendees_during_recording(self):
        samplers = []

        class FakeSampler:
            def __init__(self, capture, on_frame, *, interval_seconds):
                self.capture, self.on_frame, self.interval_seconds = capture, on_frame, interval_seconds
                self.started = self.stopped = False
                self.errors = []
                samplers.append(self)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as d, \
             mock.patch("teams_lite.app.Recorder", _FakeRecorder), \
             mock.patch("teams_lite.app.FrameSampler", FakeSampler):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=True))
            window.timer.stop()
            try:
                window.start_recording()
                meeting_id = window.meeting_id
                self.assertEqual(1, len(samplers))
                sampler = samplers[0]
                self.assertTrue(sampler.started)
                self.assertEqual(60, sampler.interval_seconds)

                sampler.on_frame({"names": ["Ana Pérez", "Rodrigo Díaz"]})
                sampler.on_frame({"names": ["ana pérez", "Lucia Gomez"]})
                self.application.processEvents()
                self.assertEqual(
                    ["Ana Pérez", "Rodrigo Díaz", "Lucia Gomez"],
                    load_participants(Path(d) / meeting_id),
                )
                self.assertIn("3 asistentes", window.status_label.text())

                window.stop_recording()
                self._wait_until_stopped(window)
                self.assertTrue(sampler.stopped)
            finally:
                window.close()

    def test_ocr_disabled_starts_no_sampler(self):
        sampler_factory = mock.Mock()
        with tempfile.TemporaryDirectory() as d, \
             mock.patch("teams_lite.app.Recorder", _FakeRecorder), \
             mock.patch("teams_lite.app.FrameSampler", sampler_factory):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                window.start_recording()
                sampler_factory.assert_not_called()
                window.stop_recording()
                self._wait_until_stopped(window)
            finally:
                window.close()

    def test_attendees_are_shown_and_copied_with_the_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Con asistentes')
            storage.finish_meeting(meeting_id)
            meeting_dir = Path(d) / meeting_id
            (meeting_dir / 'transcript.txt').write_text('[00:00:01.000] Local: Hola\n', encoding='utf-8')
            merge_participants(meeting_dir, ['Ana Pérez', 'Rodrigo Díaz'])
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.table.selectRow(0)
                self.application.processEvents()
                self.assertIn('Ana Pérez, Rodrigo Díaz', window.transcript_attendees.text())
                self.assertFalse(window.transcript_attendees.isHidden())

                window.copy_transcript()
                self.assertEqual(
                    'Asistentes (detectados por OCR): Ana Pérez, Rodrigo Díaz\n\n[00:00:01.000] Local: Hola',
                    QApplication.clipboard().text(),
                )
            finally:
                window.close()

    def test_meeting_without_attendees_copies_only_the_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Sin asistentes')
            storage.finish_meeting(meeting_id)
            (Path(d) / meeting_id / 'transcript.txt').write_text('[00:00:01.000] Local: Hola\n', encoding='utf-8')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.table.selectRow(0)
                self.application.processEvents()
                self.assertTrue(window.transcript_attendees.isHidden())
                window.copy_transcript()
                self.assertEqual('[00:00:01.000] Local: Hola', QApplication.clipboard().text())
            finally:
                window.close()


class MinutesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _meeting_with_transcript(self, d, *, minutes=None):
        storage = Storage(d)
        meeting_id = storage.create_meeting('Comité CRM')
        storage.finish_meeting(meeting_id)
        meeting_dir = Path(d) / meeting_id
        (meeting_dir / 'transcript.txt').write_text('[00:00:01.000] Local: Migramos antes del 30\n', encoding='utf-8')
        merge_participants(meeting_dir, ['Ana Pérez'])
        if minutes is not None:
            (meeting_dir / 'minutes.md').write_text(minutes, encoding='utf-8')
        return meeting_id, meeting_dir

    def _open(self, d, **config):
        window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, **config))
        window.timer.stop()
        window.table.selectRow(0)
        self.application.processEvents()
        return window

    def _wait(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def test_generate_minutes_uses_selected_cli_and_shows_the_result(self):
        calls = []

        def fake_generate(transcript, meeting_dir, *, cli):
            calls.append((transcript, Path(meeting_dir), cli))
            (Path(meeting_dir) / 'minutes.md').write_text('# Minuta\n\n## Decisiones\n- Migrar antes del 30\n', encoding='utf-8')
            return Path(meeting_dir) / 'minutes.md'

        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.generate_minutes', side_effect=fake_generate):
            meeting_id, meeting_dir = self._meeting_with_transcript(d)
            window = self._open(d, minutes_cli='codex')
            try:
                self.assertEqual('codex', window.minutes_cli_combo.currentData())
                self.assertEqual('Generar minuta', window.generate_minutes_button.text())
                window.generate_minutes_button.click()
                self._wait(lambda: window.view_minutes_button.isChecked())

                transcript, called_dir, cli = calls[0]
                self.assertEqual('codex', cli)
                self.assertEqual(meeting_dir.resolve(), called_dir.resolve())
                self.assertTrue(transcript.startswith('Asistentes (detectados por OCR): Ana Pérez'))
                self.assertIs(window.transcript_stack.currentWidget(), window.minutes_view)
                self.assertIn('Migrar antes del 30', window.minutes_view.toPlainText())
                self.assertIn('Minuta lista', window.status_label.text())
                self.assertEqual('Regenerar minuta', window.generate_minutes_button.text())
                self.assertTrue(window.generate_minutes_button.isEnabled())
            finally:
                window.close()

    def test_failed_generation_reports_the_error_without_a_modal(self):
        from teams_lite.minutes import MinutesError

        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.generate_minutes', side_effect=MinutesError('claude terminó con error (código 1): not logged in')), \
             mock.patch('teams_lite.app.QMessageBox.warning') as modal:
            self._meeting_with_transcript(d)
            window = self._open(d)
            try:
                window.generate_minutes_button.click()
                self._wait(lambda: window.generate_minutes_button.isEnabled())
                self.assertIn('not logged in', window.status_label.text())
                self.assertIs(window.transcript_stack.currentWidget(), window.transcript_table)
                modal.assert_not_called()
            finally:
                window.close()

    def test_minutes_view_shows_saved_minutes_and_copies_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            self._meeting_with_transcript(d, minutes='# Minuta guardada\n')
            window = self._open(d)
            try:
                self.assertIs(window.transcript_stack.currentWidget(), window.transcript_table)
                window.view_minutes_button.click()
                self.application.processEvents()
                self.assertIs(window.transcript_stack.currentWidget(), window.minutes_view)
                window.copy_transcript()
                self.assertEqual('# Minuta guardada\n', QApplication.clipboard().text())

                window.view_transcript_button.click()
                self.application.processEvents()
                window.copy_transcript()
                self.assertTrue(QApplication.clipboard().text().endswith('Local: Migramos antes del 30'))
            finally:
                window.close()

    def test_minutes_view_without_minutes_explains_how_to_create_them(self):
        with tempfile.TemporaryDirectory() as d:
            self._meeting_with_transcript(d)
            window = self._open(d)
            try:
                window.view_minutes_button.click()
                self.application.processEvents()
                self.assertIs(window.transcript_stack.currentWidget(), window.transcript_placeholder)
                self.assertIn('Generar minuta', window.transcript_placeholder.text())
                self.assertFalse(window.copy_button.isEnabled())
            finally:
                window.close()

    def test_generate_is_disabled_without_a_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            storage.finish_meeting(storage.create_meeting('Sin transcripción'))
            window = self._open(d)
            try:
                self.assertFalse(window.generate_minutes_button.isEnabled())
            finally:
                window.close()


class FakeChatSession:
    """Stands in for the CLI: answers instantly, fails for the question 'falla'."""

    sessions = []

    def __init__(self, workspace, cli, **_kwargs):
        self.workspace, self.cli, self.questions = workspace, cli, []
        FakeChatSession.sessions.append(self)

    def ask(self, question):
        from teams_lite.chat import ChatError
        if question == 'falla':
            raise ChatError("No se encontró la CLI 'claude' en el PATH; instálala e inicia sesión.")
        self.questions.append(question)
        return f'Respuesta {len(self.questions)} a {question}'


class ChatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    _meeting_with_transcript = MinutesUiTests._meeting_with_transcript
    _open = MinutesUiTests._open
    _click_card = staticmethod(lambda page, label: LibraryUiTests._click_card(page, label))

    _wait = MinutesUiTests._wait

    def setUp(self):
        FakeChatSession.sessions = []
        patcher = mock.patch('teams_lite.chat_ui.ChatSession', FakeChatSession)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ask(self, panel, question):
        panel.input.setText(question)
        panel.send_button.click()
        self._wait(lambda: not panel.busy)

    def test_meeting_chat_tab_answers_inside_the_app_and_keeps_the_conversation(self):
        with tempfile.TemporaryDirectory() as d, mock.patch('subprocess.Popen') as popen:
            meeting_id, _ = self._meeting_with_transcript(d, minutes='# Minuta\nMigrar antes del 30\n')
            window = self._open(d, minutes_cli='codex')
            try:
                panel = window.chat_panel
                self.assertEqual(window.view_chat_button.text(), 'Chat con IA')
                window.view_chat_button.click()
                self.assertIs(window.transcript_stack.currentWidget(), panel)
                self.assertTrue(window.recording_page.isAncestorOf(panel))  # part of the app window

                self._ask(panel, '¿Qué se acordó?')
                self._ask(panel, '¿Cuándo?')

                [session] = FakeChatSession.sessions
                self.assertEqual(session.cli, 'codex')
                self.assertEqual(Path(session.workspace), Path(d).resolve() / '.chat' / f'meeting-{meeting_id}')
                copied = Path(session.workspace) / 'meetings' / meeting_id / 'minutes.md'
                self.assertIn('Migrar antes del 30', copied.read_text(encoding='utf-8'))
                self.assertEqual(session.questions, ['¿Qué se acordó?', '¿Cuándo?'])
                history = panel.history.toPlainText()
                self.assertIn('¿Qué se acordó?', history)
                self.assertIn('Respuesta 2 a ¿Cuándo?', history)
                self.assertEqual(panel.input.text(), '')
                popen.assert_not_called()  # no terminal or other window is opened
                self.assertTrue(window.copy_button.isEnabled())
                self.assertIn('Respuesta 1', window._current_export())
            finally:
                window.close()

    def test_meeting_without_minutes_explains_it_in_the_chat_tab(self):
        with tempfile.TemporaryDirectory() as d:
            self._meeting_with_transcript(d)
            window = self._open(d)
            try:
                window.view_chat_button.click()
                panel = window.chat_panel
                self.assertFalse(panel.input.isEnabled())
                self.assertFalse(panel.send_button.isEnabled())
                self.assertIn('minuta', panel.notice.text())
            finally:
                window.close()

    def test_each_meeting_keeps_its_own_conversation_and_new_conversation_starts_over(self):
        with tempfile.TemporaryDirectory() as d:
            first, _ = self._meeting_with_transcript(d, minutes='# Minuta A\n')
            window = self._open(d)
            try:
                panel = window.chat_panel
                window.view_chat_button.click()
                self._ask(panel, 'Pregunta A')
                panel.new_button.click()
                self.assertNotIn('Pregunta A', panel.history.toPlainText())
                self._ask(panel, 'Pregunta B')

                self.assertEqual(len(FakeChatSession.sessions), 2)
                self.assertEqual(FakeChatSession.sessions[1].questions, ['Pregunta B'])
            finally:
                window.close()

    def test_failed_answer_is_shown_in_the_panel_without_a_dialog(self):
        with tempfile.TemporaryDirectory() as d, mock.patch('teams_lite.app.QMessageBox.warning') as modal:
            self._meeting_with_transcript(d, minutes='# Minuta\n')
            window = self._open(d)
            try:
                panel = window.chat_panel
                window.view_chat_button.click()
                self._ask(panel, 'falla')
                self.assertIn('No se encontró la CLI', panel.notice.text())
                self.assertTrue(panel.input.isEnabled())
                modal.assert_not_called()
            finally:
                window.close()

    def test_project_chat_panel_opens_next_to_the_meetings_in_vista_previa(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            with_minutes = storage.create_meeting('Kickoff', organization_id=zenith, project_id=crm)
            storage.create_meeting('Daily', organization_id=zenith, project_id=crm)
            other = storage.create_meeting('Suelta')
            for meeting_id in (with_minutes, other):
                (Path(d) / meeting_id / 'minutes.md').write_text('# Minuta\n', encoding='utf-8')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                page = window.preview_page
                window.side_nav.setCurrentRow(1)
                self.assertTrue(page.chat_button.isHidden())
                self._click_card(page, 'Zenith')
                self.assertTrue(page.chat_button.isHidden())
                self._click_card(page, 'CRM')
                self.assertFalse(page.chat_button.isHidden())
                self.assertTrue(page.chat_panel.isHidden())

                page.chat_button.click()
                self.assertFalse(page.chat_panel.isHidden())
                self.assertTrue(page.isAncestorOf(page.chat_panel))
                page.chat_panel.cli_combo.setCurrentIndex(page.chat_panel.cli_combo.findData('claude'))
                self._ask(page.chat_panel, '¿Qué pasó en el proyecto?')

                [session] = FakeChatSession.sessions
                self.assertEqual(session.cli, 'claude')
                self.assertEqual(Path(session.workspace).parent, Path(d).resolve() / '.chat')
                self.assertEqual([p.name for p in (Path(session.workspace) / 'meetings').iterdir()], [with_minutes])
                self.assertIn('Zenith › CRM', (Path(session.workspace) / 'CLAUDE.md').read_text(encoding='utf-8'))

                page.go_to_level(0)
                self.assertTrue(page.chat_panel.isHidden())
            finally:
                window.close()

    def test_project_chat_without_minutes_explains_why(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            storage.create_meeting('Suelta')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                page = window.preview_page
                self._click_card(page, 'Cofre de reuniones')
                page.chat_button.click()
                self.assertIn('minuta', page.chat_panel.notice.text())
                self.assertFalse(page.chat_panel.send_button.isEnabled())
                self.assertEqual(FakeChatSession.sessions, [])
            finally:
                window.close()


class UiPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_stylesheet_never_declares_font_fallback_lists(self):
        # 'font-family: "Cascadia Mono", "Consolas"' made Qt load its whole font database:
        # +230 MB of RAM when the window was shown, versus +14 MB with a single installed family.
        import re
        from PySide6.QtGui import QFontDatabase

        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                stylesheet = window.styleSheet()
                families = re.findall(r'font-family:\s*([^;]+);', stylesheet)
                self.assertTrue(families)
                platform_has_fonts = bool(QFontDatabase.families())
                for declaration in families:
                    self.assertNotIn(',', declaration, f'fallback list in stylesheet: {declaration}')
                    if platform_has_fonts:  # the offscreen test platform ships almost no fonts
                        self.assertTrue(QFontDatabase.hasFamily(declaration.strip().strip('"')), declaration)
            finally:
                window.close()

    def test_meeting_list_is_painted_not_built_from_widgets(self):
        # One widget per meeting (4 styled labels each) took ~13 ms per row: 300 meetings = 4 s at startup.
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            for index in range(5):
                storage.finish_meeting(storage.create_meeting(f'Reunión {index}'))
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                self.assertEqual(5, window.table.rowCount())
                self.assertTrue(all(window.table.cellWidget(row, 0) is None for row in range(5)))
                meeting = window.table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1)
                self.assertEqual('completed', meeting['status'])
                self.assertTrue(meeting['title'].startswith('Reunión'))
                self.assertIsNotNone(window.table.itemDelegateForColumn(0))
                pixmap = window.table.viewport().grab()  # painting must not raise
                self.assertFalse(pixmap.isNull())
            finally:
                window.close()

    def test_transcript_rows_fit_their_text_without_per_cell_measuring(self):
        from PySide6.QtWidgets import QHeaderView

        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Larga')
            storage.finish_meeting(meeting_id)
            long_text = 'palabra ' * 120
            (Path(d) / meeting_id / 'transcript.txt').write_text(
                f'[00:00:01.000 - 00:00:02.000] Local: Hola\n[00:00:03.000 - 00:00:30.000] Local: {long_text}\n',
                encoding='utf-8',
            )
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.resize(1200, 900)  # two-column layout: the transcript takes the right half
            window.show()
            try:
                window.table.selectRow(0)
                settle = time.monotonic() + 0.4  # first layout gives the text column its real width
                while time.monotonic() < settle:
                    self.application.processEvents()
                    time.sleep(0.02)
                header = window.transcript_table.verticalHeader()
                self.assertNotEqual(QHeaderView.ResizeMode.ResizeToContents, header.sectionResizeMode(0))
                short, long = window.transcript_table.rowHeight(0), window.transcript_table.rowHeight(1)
                self.assertGreater(long, short * 2)  # the long turn wraps onto several lines

                # Wider window: the long turn wraps onto fewer lines once resizing stops. (Widening,
                # because the offscreen test platform measures fonts wider than Windows and its
                # minimum content width exceeds small windows.)
                window.resize(1800, 900)
                deadline = time.monotonic() + 2
                while window.transcript_table.rowHeight(1) >= long and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.02)
                self.assertLess(window.transcript_table.rowHeight(1), long)
            finally:
                window.close()


class TranscriptionSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_app_transcribes_only_the_speech(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.transcribe_recordings', return_value=[]) as transcribe:
            meeting_id = Storage(d).create_meeting('Velocidad')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.store.list_segments = lambda _id: [
                {'path': 'mic.wav', 'kind': 'microphone', 'started_monotonic': 1.0, 'status': 'finalized'},
            ]
            try:
                window._transcribe_meeting(meeting_id)
                self.assertTrue(transcribe.call_args.kwargs['skip_silence'])
            finally:
                window.close()


class TranscriptionProgressUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _card(self, window, meeting_id):
        for row in range(window.table.rowCount()):
            item = window.table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) == meeting_id:
                return item.data(_MEETING_ROLE)
        raise AssertionError(f"no card for {meeting_id}")

    def test_card_shows_transcription_progress_until_it_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Progreso')
            storage.finish_meeting(meeting_id)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.store.list_segments = lambda _id: [
                {'path': 'mic.wav', 'kind': 'microphone', 'started_monotonic': 1.0, 'status': 'finalized'},
            ]
            seen = []

            def transcribe(sources, **kwargs):
                kwargs['progress'](0.25)
                kwargs['progress'](0.251)  # same whole percent: no extra repaint
                kwargs['progress'](0.5)
                seen.append(self._card(window, meeting_id)['transcription_progress'])
                return []

            try:
                with mock.patch('teams_lite.app.transcribe_recordings', side_effect=transcribe), \
                        mock.patch('teams_lite.app.export_transcript'):
                    window._transcribe_meeting(meeting_id)
                self.assertEqual([0.5], seen)
                self.assertIsNone(self._card(window, meeting_id).get('transcription_progress'))
            finally:
                window.close()

    def test_failed_transcription_clears_the_progress(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Falla')
            storage.finish_meeting(meeting_id)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.store.list_segments = lambda _id: [
                {'path': 'mic.wav', 'kind': 'microphone', 'started_monotonic': 1.0, 'status': 'finalized'},
            ]

            def transcribe(sources, **kwargs):
                kwargs['progress'](0.3)
                raise RuntimeError('modelo roto')

            try:
                with mock.patch('teams_lite.app.transcribe_recordings', side_effect=transcribe), \
                        mock.patch.object(window, '_show_error'):
                    window._transcribe_meeting(meeting_id)
                self.assertIsNone(self._card(window, meeting_id).get('transcription_progress'))
            finally:
                window.close()


class UpdateBannerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_banner_offers_the_new_version_and_downloads_the_installer(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                self.assertTrue(window.update_banner.isHidden())
                window.show_update(UpdateInfo('0.2.0', 'https://example/page', 'https://example/setup.exe'))
                self.assertFalse(window.update_banner.isHidden())
                self.assertIn('0.2.0', window.update_text.text())
                with mock.patch('teams_lite.app.QDesktopServices.openUrl') as open_url:
                    window.update_button.click()
                self.assertEqual('https://example/setup.exe', open_url.call_args.args[0].toString())
                window.update_dismiss_button.click()
                self.assertTrue(window.update_banner.isHidden())
            finally:
                window.close()

    def test_creating_the_window_never_checks_for_updates(self):
        # Only main() checks, so tests and other embedders never reach the network.
        with tempfile.TemporaryDirectory() as d, \
                mock.patch('teams_lite.app.check_for_update') as check:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.close()
        check.assert_not_called()


class AudioWarningUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_recovered_audio_problems_are_shown_while_recording(self):
        class WarningRecorder(_FakeRecorder):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.warnings = []

        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', WarningRecorder), \
             mock.patch('teams_lite.app.QMessageBox.warning'):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.start_recording()
                window._refresh()
                self.assertIn('Grabando', window.status_label.text())

                window.recorder.warnings.append('loopback: cambió el dispositivo predeterminado; se sigue grabando en el nuevo.')
                window._refresh()
                self.assertIn('cambió el dispositivo', window.status_label.text())

                window._set_status('Otro mensaje')
                window._refresh()  # the same warning is not repeated on every tick
                self.assertEqual('Otro mensaje', window.status_label.text())

                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
            finally:
                window.close()


class MomentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        # A failing assertion mid-recording must fail the test, not block on a modal dialog offscreen.
        patcher = mock.patch('teams_lite.app.QMessageBox.warning')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _wait(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def _fake_capture_moment(self, calls):
        from teams_lite.moments import capture_moment

        def fake(meeting_dir, recording_origin, *, note, title_hint):
            calls.append({"note": note, "title_hint": title_hint})
            return capture_moment(
                meeting_dir,
                recording_origin,
                note=note,
                capture=lambda *args, **kwargs: (_ for _ in ()).throw(TeamsWindowMinimized("Teams está minimizado")),
                screen_capture=None,  # never screenshot the real desktop from a test
            )

        return fake

    def test_mark_moment_button_records_note_while_recording(self):
        calls = []
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.capture_moment', side_effect=self._fake_capture_moment(calls)):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                self.assertFalse(window.mark_moment_button.isEnabled())
                window.start_recording()
                self.assertTrue(window.mark_moment_button.isEnabled())
                window.moment_note_input.setText('Presupuesto aprobado')
                window.mark_moment_button.click()
                self._wait(lambda: 'Momento 1' in window.status_label.text())

                self.assertEqual([{'note': 'Presupuesto aprobado', 'title_hint': 'Microsoft Teams'}], calls)
                self.assertEqual('', window.moment_note_input.text())
                self.assertIn('minimizado', window.status_label.text())
                self.assertEqual(['Presupuesto aprobado'], [m['note'] for m in load_moments(Path(d) / window.meeting_id)])

                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertFalse(window.mark_moment_button.isEnabled())
            finally:
                window.close()

    @unittest.skipUnless(sys.platform == 'win32', 'global hotkeys are Windows specific')
    def test_global_hotkey_message_marks_a_moment_without_note(self):
        import ctypes
        from ctypes import wintypes
        from teams_lite.hotkey import MOMENT_HOTKEY_ID, WM_HOTKEY

        calls = []
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.capture_moment', side_effect=self._fake_capture_moment(calls)):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                msg = wintypes.MSG()
                msg.message, msg.wParam = WM_HOTKEY, MOMENT_HOTKEY_ID
                handled, _ = window.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg))
                self.assertFalse(handled)  # not recording: the hotkey does nothing
                self.assertEqual([], calls)

                window.start_recording()
                window.moment_note_input.setText('no se usa con el atajo')
                handled, _ = window.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg))
                self.assertTrue(handled)
                self._wait(lambda: bool(calls) and 'Momento 1' in window.status_label.text())
                self.assertEqual('', calls[0]['note'])
                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
            finally:
                window.close()

    def test_moments_appear_in_the_transcript_and_reach_the_minutes(self):
        from teams_lite.moments import MOMENTS_FILE

        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting_id = storage.create_meeting('Con momentos')
            storage.finish_meeting(meeting_id)
            meeting_dir = Path(d) / meeting_id
            (meeting_dir / 'transcript.txt').write_text(
                '[00:00:01.000 - 00:00:05.000] Local: Hola\n[00:00:20.000 - 00:00:25.000] Local: Chao\n',
                encoding='utf-8',
            )
            (meeting_dir / MOMENTS_FILE).write_text(
                '{"offset_seconds": 12.0, "note": "Diagrama de arquitectura", "image": "moments/a.jpg", "problem": null}\n',
                encoding='utf-8',
            )
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.table.selectRow(0)
                self.application.processEvents()
                rows = [
                    (window.transcript_table.item(row, 1).text(), window.transcript_table.item(row, 2).text())
                    for row in range(window.transcript_table.rowCount())
                ]
                self.assertEqual(3, len(rows))
                self.assertEqual('Momento', rows[1][0].lstrip('📌 ').strip())
                self.assertIn('Diagrama de arquitectura', rows[1][1])
                self.assertIn('1 momento', window.transcript_meta.text())
                self.assertIn(
                    'Momentos marcados como importantes por quien grabó:\n- [00:00:12] Diagrama de arquitectura',
                    window.export_text(),
                )
                self.assertTrue(window.export_text().endswith('Local: Chao'))
            finally:
                window.close()

    def test_attendee_ocr_warns_when_teams_is_minimized(self):
        samplers = []

        class FakeSampler:
            def __init__(self, capture, on_frame, *, interval_seconds):
                self.capture = capture
                self.errors = []
                samplers.append(self)

            def start(self):
                pass

            def stop(self):
                pass

        def minimized(*args, **kwargs):
            raise TeamsWindowMinimized('Teams está minimizado: restaura su ventana.')

        with tempfile.TemporaryDirectory() as d, \
             mock.patch('teams_lite.app.Recorder', _FakeRecorder), \
             mock.patch('teams_lite.app.FrameSampler', FakeSampler), \
             mock.patch('teams_lite.app.read_teams_names', side_effect=minimized):
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=True))
            window.timer.stop()
            try:
                window.start_recording()
                self.assertEqual({'names': []}, samplers[0].capture())
                self.application.processEvents()
                self.assertIn('minimizado', window.status_label.text())
                window.stop_recording()
                deadline = time.monotonic() + 2
                while window.recorder is not None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
            finally:
                window.close()


class VisualFailureDuringRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_model_failure_mid_recording_stops_sampling_and_warns(self):
        from teams_lite.wevisone import VisualModelUnavailable

        class BrokenDetector:
            def __init__(self, **kwargs):
                pass

            def detect(self, frame):
                raise VisualModelUnavailable("pesos dañados")

        sampler = mock.Mock()
        captured = {}

        def sampler_factory(capture, on_frame, **kwargs):
            captured["on_frame"] = on_frame
            return sampler

        with tempfile.TemporaryDirectory() as d, \
             mock.patch("teams_lite.app.WeVisOneDetector", BrokenDetector), \
             mock.patch("teams_lite.app.FrameSampler", side_effect=sampler_factory):
            meeting_id = Storage(d).create_meeting('Visual roto')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window._start_visual_tracking(meeting_id, time.monotonic())
                sampler.start.assert_called_once()
                captured["on_frame"](object())
                self.application.processEvents()

                sampler.stop.assert_called_once()
                kinds = [event["kind"] for event in Storage(d).list_events(meeting_id)]
                self.assertIn("visual_tracking_disabled", kinds)
                self.assertIn("pesos dañados", window.status_label.text())
                self.assertIn("audio sigue grabando", window.status_label.text())
            finally:
                window.close()


class LibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    _wait_until_stopped = OcrAttendeeTests._wait_until_stopped

    def test_side_menu_switches_between_sections(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                labels = [window.side_nav.item(row).text() for row in range(window.side_nav.count())]
                self.assertEqual(labels, ['Grabar', 'Biblioteca', 'Organización', 'Mascota'])
                self.assertIs(window.pages.currentWidget(), window.recording_page)
                self.assertTrue(window.recording_page.isAncestorOf(window.start_button))

                window.side_nav.setCurrentRow(1)
                self.assertIs(window.pages.currentWidget(), window.preview_page)
                window.side_nav.setCurrentRow(2)
                self.assertIs(window.pages.currentWidget(), window.admin_page)
                window.side_nav.setCurrentRow(0)
                self.assertIs(window.pages.currentWidget(), window.recording_page)
            finally:
                window.close()

    def test_recording_without_a_choice_goes_to_the_meeting_vault(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("teams_lite.app.Recorder", _FakeRecorder):
            storage = Storage(d)
            storage.create_project(storage.create_organization('Zenith'), 'CRM')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                picker = window.placement_picker
                self.assertEqual(picker.organization_combo.currentText(), 'Cofre de reuniones')
                self.assertEqual(picker.organization_combo.itemText(1), 'Zenith')
                self.assertFalse(picker.project_combo.isEnabled())

                window.start_recording()
                [meeting] = storage.list_meetings()
                self.assertEqual((meeting['organization_id'], meeting['project_id']), (None, None))
                window.stop_recording()
                self._wait_until_stopped(window)
            finally:
                window.close()

    def test_recording_is_filed_in_the_chosen_organization_and_project(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("teams_lite.app.Recorder", _FakeRecorder):
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False, ocr_enabled=False))
            window.timer.stop()
            try:
                picker = window.placement_picker
                picker.organization_combo.setCurrentIndex(picker.organization_combo.findData(zenith))
                self.assertTrue(picker.project_combo.isEnabled())
                self.assertEqual([picker.project_combo.itemText(i) for i in range(picker.project_combo.count())],
                                 ['Sin proyecto', 'CRM'])
                picker.project_combo.setCurrentIndex(picker.project_combo.findData(crm))

                window.start_recording()
                [meeting] = storage.list_meetings()
                self.assertEqual((meeting['organization_id'], meeting['project_id']), (zenith, crm))
                window.stop_recording()
                self._wait_until_stopped(window)
            finally:
                window.close()

    @staticmethod
    def _add_moment_screenshot(meeting_dir):
        from PIL import Image
        (meeting_dir / 'moments').mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (320, 180), (40, 90, 160)).save(meeting_dir / 'moments' / 'teams-1.jpg')
        (meeting_dir / 'moments.jsonl').write_text(
            '{"offset_seconds": 3.0, "image": "moments/teams-1.jpg"}\n', encoding='utf-8')

    @staticmethod
    def _grid_texts(page):
        return [page.grid.item(row).text().split('\n')[0] for row in range(page.grid.count())]

    @staticmethod
    def _click_card(page, label):
        item = next(page.grid.item(row) for row in range(page.grid.count())
                    if page.grid.item(row).text().split('\n')[0] == label)
        page.grid.itemClicked.emit(item)

    def test_preview_drills_from_organizations_to_projects_to_meetings_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            storage.create_project(zenith, 'Web')
            times = iter(['2026-11-01T10:00:00+00:00', '2026-11-03T10:00:00+00:00', '2026-11-02T10:00:00+00:00'])
            with mock.patch('teams_lite.storage.utc_now', side_effect=lambda: next(times)):
                oldest = storage.create_meeting('Kickoff', organization_id=zenith, project_id=crm)
                newest = storage.create_meeting('Demo', organization_id=zenith, project_id=crm)
                storage.create_meeting('Suelta')
            for meeting in (oldest, newest):
                storage.finish_meeting(meeting)
            self._add_moment_screenshot(Path(d) / newest)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                page = window.preview_page
                window.side_nav.setCurrentRow(1)
                self.assertEqual(self._grid_texts(page), ['Cofre de reuniones', 'Zenith'])
                self.assertIn('2 reuniones', page.grid.item(1).text())
                self.assertEqual(page.breadcrumb_labels(), ['Organizaciones'])

                self._click_card(page, 'Zenith')
                self.assertEqual(self._grid_texts(page), ['CRM', 'Web', 'Sin proyecto'])
                self.assertEqual(page.breadcrumb_labels(), ['Organizaciones', 'Zenith'])

                self._click_card(page, 'CRM')
                self.assertEqual(page.breadcrumb_labels(), ['Organizaciones', 'Zenith', 'CRM'])
                self.assertIs(page.stack.currentWidget(), page.meeting_list)
                rows = [page.meeting_list.item(row) for row in range(page.meeting_list.count())]
                self.assertEqual([item.data(Qt.ItemDataRole.UserRole) for item in rows], [newest, oldest])
                self.assertTrue(rows[0].text().startswith('Demo\n'))
                self.assertEqual(rows[0].data(THUMBNAIL_ROLE), str(storage.root / newest / 'moments' / 'teams-1.jpg'))
                self.assertIsNone(rows[1].data(THUMBNAIL_ROLE))
                self.assertFalse(rows[0].icon().isNull())

                page.go_to_level(0)
                self.assertEqual(self._grid_texts(page), ['Cofre de reuniones', 'Zenith'])
            finally:
                window.close()

    def test_vault_card_lists_unfiled_meetings_directly(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            storage.create_organization('Zenith')
            loose = storage.create_meeting('Suelta')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                page = window.preview_page
                self._click_card(page, 'Cofre de reuniones')
                self.assertEqual(page.breadcrumb_labels(), ['Organizaciones', 'Cofre de reuniones'])
                self.assertEqual(
                    [page.meeting_list.item(row).data(Qt.ItemDataRole.UserRole)
                     for row in range(page.meeting_list.count())],
                    [loose],
                )
            finally:
                window.close()

    def test_opening_a_meeting_from_preview_shows_it_in_teams_lite(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            first = storage.create_meeting('Primera')
            storage.finish_meeting(first)
            second = storage.create_meeting('Segunda')
            storage.finish_meeting(second)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                page = window.preview_page
                window.side_nav.setCurrentRow(1)
                self._click_card(page, 'Cofre de reuniones')
                item = next(page.meeting_list.item(row) for row in range(page.meeting_list.count())
                            if page.meeting_list.item(row).data(Qt.ItemDataRole.UserRole) == first)
                page.meeting_list.itemActivated.emit(item)
                self.application.processEvents()

                self.assertIs(window.pages.currentWidget(), window.recording_page)
                self.assertEqual(window.side_nav.currentRow(), 0)
                self.assertEqual(window._selected_meeting_id(), first)
            finally:
                window.close()

    @staticmethod
    def _list_texts(widget):
        return [widget.item(row).text() for row in range(widget.count())]

    def test_admin_manages_organizations_and_projects(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                admin = window.admin_page
                errors = []
                answers = iter(['Zenith', 'CRM', 'Portal', 'portal', 'Zenith Consulting'])
                admin.ask_name = lambda *_args: next(answers)
                admin.confirm = lambda *_args: True
                admin.show_error = errors.append

                admin.new_organization_button.click()
                self.assertEqual(self._list_texts(admin.organization_list), ['Zenith'])
                self.assertEqual(window.placement_picker.organization_combo.itemText(1), 'Zenith')
                admin.organization_list.setCurrentRow(0)
                self.assertTrue(admin.new_project_button.isEnabled())

                admin.new_project_button.click()
                admin.project_list.setCurrentRow(0)
                admin.rename_project_button.click()
                self.assertEqual(self._list_texts(admin.project_list), ['Portal'])
                admin.new_project_button.click()  # "portal" already exists in this organization
                self.assertEqual(len(errors), 1)
                self.assertIn('Ya existe', errors[0])
                self.assertEqual(self._list_texts(admin.project_list), ['Portal'])

                admin.rename_organization_button.click()
                self.assertEqual(self._list_texts(admin.organization_list), ['Zenith Consulting'])

                [organization] = storage.list_organizations()
                [project] = storage.list_projects(organization['id'])
                meeting = storage.create_meeting('m', organization_id=organization['id'], project_id=project['id'])
                admin.project_list.setCurrentRow(0)
                admin.delete_project_button.click()
                self.assertEqual(self._list_texts(admin.project_list), [])
                admin.delete_organization_button.click()
                self.assertEqual(self._list_texts(admin.organization_list), [])
                self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])
                self.assertEqual(window.placement_picker.organization_combo.count(), 1)
            finally:
                window.close()

    def test_admin_delete_asks_first_and_keeps_everything_when_cancelled(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                admin = window.admin_page
                questions = []
                admin.confirm = lambda title, text: questions.append(text) or False
                admin.organization_list.setCurrentRow(0)
                admin.delete_organization_button.click()

                self.assertIn('Cofre de reuniones', questions[0])
                self.assertEqual([org['id'] for org in storage.list_organizations()], [zenith])
            finally:
                window.close()

    def test_move_dialog_starts_from_the_current_placement(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            dialog = MoveMeetingDialog(storage, zenith, crm)
            try:
                self.assertEqual(dialog.placement(), (zenith, crm))
                dialog.picker.organization_combo.setCurrentIndex(0)
                self.assertEqual(dialog.placement(), (None, None))
            finally:
                dialog.close()

    def test_moving_a_meeting_from_preview_and_teams_lite(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            zenith = storage.create_organization('Zenith')
            crm = storage.create_project(zenith, 'CRM')
            meeting = storage.create_meeting('m')
            storage.finish_meeting(meeting)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                offered = []
                window._choose_placement = lambda meeting_row: offered.append(meeting_row['id']) or (zenith, crm)
                page = window.preview_page
                window.side_nav.setCurrentRow(1)
                self._click_card(page, 'Cofre de reuniones')
                page.meeting_list.setCurrentRow(0)
                self.assertTrue(page.move_button.isEnabled())
                page.move_button.click()

                self.assertEqual(offered, [meeting])
                [moved] = storage.list_meetings()
                self.assertEqual((moved['organization_id'], moved['project_id']), (zenith, crm))
                self.assertIs(page.stack.currentWidget(), page.empty_label)  # the vault is now empty

                window._choose_placement = lambda _meeting: (None, None)
                window.open_meeting(meeting)
                self.assertTrue(window.move_button.isEnabled())
                window.move_button.click()
                self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])
                self.assertEqual(window._selected_meeting_id(), meeting)

                window._choose_placement = lambda _meeting: None  # cancelled dialog changes nothing
                window.move_button.click()
                self.assertEqual([row['id'] for row in storage.list_meetings(organization_id=None)], [meeting])
            finally:
                window.close()


class WorkspaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _settle(self):
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.02)

    def test_recording_and_recent_on_the_left_transcript_on_the_right(self):
        with tempfile.TemporaryDirectory() as d:
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window.resize(1800, 1000)
            window.show()
            try:
                self._settle()
                recording, recent, transcript = window.recording_card, window.history_card, window.transcript_card
                origin = window.recording_page.widget()

                def box(widget):
                    top_left = widget.mapTo(origin, widget.rect().topLeft())
                    return top_left.x(), top_left.y(), widget.width(), widget.height()

                rx, ry, rw, rh = box(recording)
                hx, hy, hw, hh = box(recent)
                tx, ty, tw, th = box(transcript)
                self.assertEqual(rx, hx)  # one left column
                self.assertGreater(hy, ry + rh - 1)  # recent recordings below the recording card
                self.assertGreater(tx, rx + rw)  # transcript on the right
                self.assertEqual(ty, ry)  # the right half starts at the top
                self.assertGreaterEqual(th, (hy + hh) - ry - 2)  # and spans the left column's height
                self.assertLess(abs(tw - rw), 40)  # two halves

                window.resize(1010, 700)
                self._settle()
                self.assertEqual(window.overview_columns.direction(), QBoxLayout.Direction.TopToBottom)
                self.assertGreater(box(transcript)[1], box(recent)[1])  # narrow: transcript goes below
            finally:
                window.close()


class OrganizationSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_organization_section_guides_and_shows_counts(self):
        from teams_lite.library_ui import COUNT_ROLE
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            storage.create_meeting('Suelta')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                admin = window.admin_page
                self.assertFalse(admin.organization_empty.isHidden())
                self.assertIn('Crea la primera', admin.organization_empty.text())
                self.assertFalse(admin.project_empty.isHidden())
                self.assertIn('Elige una organización', admin.project_empty.text())
                self.assertEqual(admin.project_title.text(), 'Proyectos')
                self.assertEqual(admin.summary.text(),
                                 '0 organizaciones · 0 proyectos · 0 reuniones en organizaciones · 1 en el Cofre de reuniones')
                for button in (admin.new_organization_button, admin.rename_organization_button,
                               admin.delete_organization_button, admin.new_project_button,
                               admin.rename_project_button, admin.delete_project_button):
                    self.assertFalse(button.icon().isNull())

                zenith = storage.create_organization('Zenith')
                crm = storage.create_project(zenith, 'CRM')
                storage.create_meeting('Kickoff', organization_id=zenith, project_id=crm)
                storage.create_meeting('General', organization_id=zenith)
                storage.create_organization('Acme')
                admin.reload()

                self.assertTrue(admin.organization_empty.isHidden())
                names = [admin.organization_list.item(row).text() for row in range(admin.organization_list.count())]
                self.assertEqual(names, ['Acme', 'Zenith'])
                self.assertEqual(admin.organization_list.item(1).data(COUNT_ROLE), 2)
                self.assertFalse(admin.organization_list.item(1).icon().isNull())
                self.assertEqual(admin.summary.text(),
                                 '2 organizaciones · 1 proyecto · 2 reuniones en organizaciones · 1 en el Cofre de reuniones')

                admin.organization_list.setCurrentRow(1)
                self.assertEqual(admin.project_title.text(), 'Proyectos de Zenith')
                self.assertTrue(admin.project_empty.isHidden())
                self.assertEqual(admin.project_list.item(0).text(), 'CRM')
                self.assertEqual(admin.project_list.item(0).data(COUNT_ROLE), 1)

                admin.organization_list.setCurrentRow(0)
                self.assertEqual(admin.project_title.text(), 'Proyectos de Acme')
                self.assertFalse(admin.project_empty.isHidden())
                self.assertIn('Acme todavía no tiene proyectos', admin.project_empty.text())
            finally:
                window.close()


class ScreenshotGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _wait(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def test_capturas_tab_shows_every_screenshot_and_opens_it(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('Con capturas')
            storage.finish_meeting(meeting)
            folder = Path(d) / meeting
            (folder / 'moments').mkdir()
            Image.new('RGB', (320, 180), (40, 90, 160)).save(folder / 'moments' / 'a.jpg')
            (folder / 'moments.jsonl').write_text(
                '{"offset_seconds": 3.0, "image": "moments/a.jpg", "note": "Demo"}\n', encoding='utf-8')
            (folder / 'visual-frames').mkdir()
            Image.new('RGB', (320, 180), (10, 10, 10)).save(folder / 'visual-frames' / 'teams-20261117T100000000000Z.png')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.open_meeting(meeting)
                self.assertEqual(window.view_gallery_button.text(), 'Capturas')
                window.view_gallery_button.click()
                gallery = window.gallery
                self.assertIs(window.transcript_stack.currentWidget(), window.gallery_page)
                self.assertTrue(window.gallery_page.isAncestorOf(gallery))
                self._wait(lambda: gallery.count() == 2 and not gallery.item(1).icon().isNull())

                self.assertEqual([gallery.item(row).text() for row in range(gallery.count())],
                                 ['00:00:03 · Demo', 'Hablante activo · 10:00:00 UTC'])
                self.assertTrue(all(not gallery.item(row).icon().isNull() for row in range(gallery.count())))
                self.assertIn('2 capturas', window.gallery_summary.text())
                self.assertFalse(window.copy_button.isEnabled())

                with mock.patch('teams_lite.app.QDesktopServices.openUrl') as open_url:
                    gallery.itemActivated.emit(gallery.item(0))
                opened = Path(open_url.call_args.args[0].toLocalFile())
                self.assertEqual(opened.resolve(), (folder / 'moments' / 'a.jpg').resolve())
            finally:
                window.close()

    def test_meeting_without_screenshots_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('Sin capturas')
            storage.finish_meeting(meeting)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.open_meeting(meeting)
                window.view_gallery_button.click()
                self.assertEqual(window.gallery.count(), 0)
                self.assertIn('no tiene capturas', window.gallery_summary.text())
            finally:
                window.close()


class RecentRecordingsScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _listed(window):
        return [window.table.item(row, 1).text() for row in range(window.table.rowCount())]

    def _library(self, d):
        storage = Storage(d)
        zenith = storage.create_organization('Zenith')
        crm = storage.create_project(zenith, 'CRM')
        ids = {
            'Suelta': storage.create_meeting('Suelta'),
            'Kickoff': storage.create_meeting('Kickoff', organization_id=zenith, project_id=crm),
            'General': storage.create_meeting('General', organization_id=zenith),
        }
        for meeting in ids.values():
            storage.finish_meeting(meeting)
        return storage, zenith, crm, ids

    def test_recent_recordings_follow_the_project_chosen_for_recording(self):
        with tempfile.TemporaryDirectory() as d:
            storage, zenith, crm, _ = self._library(d)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                picker = window.placement_picker
                self.assertEqual(self._listed(window), ['Suelta'])
                self.assertEqual(window.history_scope.text(), 'Cofre de reuniones')
                self.assertEqual(window.history_count.text(), '1')

                picker.organization_combo.setCurrentIndex(picker.organization_combo.findData(zenith))
                self.assertEqual(self._listed(window), ['General'])
                self.assertEqual(window.history_scope.text(), 'Zenith › Sin proyecto')

                picker.project_combo.setCurrentIndex(picker.project_combo.findData(crm))
                self.assertEqual(self._listed(window), ['Kickoff'])
                self.assertEqual(window.history_scope.text(), 'Zenith › CRM')
            finally:
                window.close()

    def test_empty_project_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            storage.create_meeting('Suelta')
            empty = storage.create_organization('Vacía')
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                picker = window.placement_picker
                picker.organization_combo.setCurrentIndex(picker.organization_combo.findData(empty))
                self.assertTrue(window.table.isHidden())
                self.assertFalse(window.history_empty.isHidden())
                self.assertIn('este proyecto', window.history_empty.text())
            finally:
                window.close()

    def test_opening_or_moving_a_meeting_shows_it_in_its_project(self):
        with tempfile.TemporaryDirectory() as d:
            storage, zenith, crm, ids = self._library(d)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            try:
                window.open_meeting(ids['Kickoff'])
                self.assertEqual(window.history_scope.text(), 'Zenith › CRM')
                self.assertEqual(window._selected_meeting_id(), ids['Kickoff'])

                window._choose_placement = lambda _meeting: (None, None)
                window.move_meeting()
                self.assertEqual(window.history_scope.text(), 'Cofre de reuniones')
                self.assertEqual(sorted(self._listed(window)), ['Kickoff', 'Suelta'])
                self.assertEqual(window._selected_meeting_id(), ids['Kickoff'])
            finally:
                window.close()


class RenameMeetingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_rename_from_grabar_and_from_biblioteca(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(d)
            meeting = storage.create_meeting('Reunión de Teams')
            storage.finish_meeting(meeting)
            window = MainWindow(AppConfig(data_dir=d, auto_transcribe=False))
            window.timer.stop()
            window._show_error = mock.Mock()
            try:
                self.assertFalse(window.rename_button.isEnabled())
                window.open_meeting(meeting)
                self.assertTrue(window.rename_button.isEnabled())
                asked = []
                window._ask_meeting_title = lambda current: asked.append(current) or 'Kickoff CRM'
                window.rename_button.click()

                self.assertEqual(asked, ['Reunión de Teams'])
                self.assertEqual(storage.get_meeting(meeting)['title'], 'Kickoff CRM')
                self.assertEqual(window.table.item(window.table.currentRow(), 1).text(), 'Kickoff CRM')
                self.assertEqual(window._selected_meeting_id(), meeting)
                self.assertEqual(window.status_label.text(), 'Reunión renombrada.')

                window._ask_meeting_title = lambda current: None  # cancelled
                window.rename_button.click()
                window._ask_meeting_title = lambda current: '   '  # rejected by storage
                window.rename_button.click()
                self.assertEqual(storage.get_meeting(meeting)['title'], 'Kickoff CRM')
                window._show_error.assert_called_once()

                window._ask_meeting_title = lambda current: 'Demo final'
                window.preview_page.rename_meeting.emit(meeting)
                self.assertEqual(storage.get_meeting(meeting)['title'], 'Demo final')
            finally:
                window.close()


class SharingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self._dirs = [tempfile.TemporaryDirectory() for _ in range(3)]
        self.data, self.other, self.out = (Path(directory.name) for directory in self._dirs)

    def tearDown(self):
        for directory in self._dirs:
            directory.cleanup()

    def _wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertTrue(condition())

    def _window(self):
        window = MainWindow(AppConfig(data_dir=self.data, auto_transcribe=False))
        window.timer.stop()
        window._show_error = mock.Mock()
        return window

    def _package_from_other_pc(self, organization=None, project=None):
        source = Storage(self.other)
        placement = {}
        if organization:
            placement['organization_id'] = source.create_organization(organization)
            if project:
                placement['project_id'] = source.create_project(placement['organization_id'], project)
        meeting = source.create_meeting('Compartida', **placement)
        (source.root / meeting / 'transcript.txt').write_text('[00:00:01.000] Local: Hola\n', encoding='utf-8')
        source.finish_meeting(meeting)
        return export_meeting(source, meeting, self.out / 'compartida.zip')

    def test_export_options_leave_audio_off_by_default(self):
        dialog = ExportOptionsDialog()
        try:
            self.assertFalse(dialog.include_audio_checkbox.isChecked())
            self.assertFalse(dialog.include_audio())
            dialog.include_audio_checkbox.setChecked(True)
            self.assertTrue(dialog.include_audio())
        finally:
            dialog.close()

    def test_exporting_from_teams_lite_and_from_preview(self):
        storage = Storage(self.data)
        meeting = storage.create_meeting('Demo')
        storage.finish_meeting(meeting)
        window = self._window()
        try:
            asked = []
            window._choose_export = lambda row: asked.append(row['id']) or (self.out / 'demo.zip', False)
            window.open_meeting(meeting)
            self.assertTrue(window.export_button.isEnabled())
            window.export_button.click()
            self._wait_for(lambda: (self.out / 'demo.zip').is_file() and 'Exportada' in window.status_label.text())
            self.assertEqual(asked, [meeting])
            self.assertEqual(read_package(self.out / 'demo.zip')['meeting']['title'], 'Demo')

            window._choose_export = lambda _row: (self.out / 'preview.zip', True)
            window.preview_page.export_meeting.emit(meeting)
            self._wait_for(lambda: 'preview.zip' in window.status_label.text())
            self.assertTrue(read_package(self.out / 'preview.zip')['includes_audio'])

            window._choose_export = lambda _row: None  # cancelled
            window.export_button.click()
            self.assertEqual(sorted(path.name for path in self.out.iterdir()), ['demo.zip', 'preview.zip'])
            window._show_error.assert_not_called()
        finally:
            window.close()

    def test_import_lands_in_the_project_being_browsed(self):
        package = self._package_from_other_pc('Otra empresa', 'Otro proyecto')
        storage = Storage(self.data)
        zenith = storage.create_organization('Zenith')
        crm = storage.create_project(zenith, 'CRM')
        window = self._window()
        try:
            offered = []
            window._choose_import_file = lambda: package
            window._choose_import_placement = lambda org, proj, manifest: offered.append((org, proj)) or (org, proj)
            page = window.preview_page
            window.side_nav.setCurrentRow(1)
            LibraryUiTests._click_card(page, 'Zenith')
            LibraryUiTests._click_card(page, 'CRM')
            page.import_button.click()

            self._wait_for(lambda: len(storage.list_meetings(project_id=crm)) == 1)
            self.assertEqual(offered, [(zenith, crm)])
            [meeting] = storage.list_meetings(project_id=crm)
            self.assertEqual(meeting['title'], 'Compartida')
            self._wait_for(lambda: page.meeting_list.count() == 1)
            self.assertIn('importada', window.status_label.text())
        finally:
            window.close()

    def test_import_suggests_the_original_organization_and_project_by_name(self):
        package = self._package_from_other_pc('Zenith', 'CRM')
        storage = Storage(self.data)
        zenith = storage.create_organization('zenith')
        crm = storage.create_project(zenith, 'crm')
        window = self._window()
        try:
            offered = []
            window._choose_import_file = lambda: package
            window._choose_import_placement = lambda org, proj, manifest: offered.append((org, proj, manifest['project'])) or None
            window.preview_page.import_button.click()

            self.assertEqual(offered, [(zenith, crm, 'CRM')])
            self.assertEqual(storage.list_meetings(), [])  # cancelled at the placement step
        finally:
            window.close()

    def test_invalid_package_is_reported_and_imports_nothing(self):
        not_a_package = self.out / 'fake.zip'
        not_a_package.write_text('hola', encoding='utf-8')
        storage = Storage(self.data)
        window = self._window()
        try:
            window._choose_import_file = lambda: not_a_package
            window._choose_import_placement = mock.Mock()
            window.preview_page.import_button.click()

            window._show_error.assert_called_once()
            self.assertIn('paquete', window._show_error.call_args[0][0])
            window._choose_import_placement.assert_not_called()
            self.assertEqual(storage.list_meetings(), [])
        finally:
            window.close()


if __name__ == '__main__':
    unittest.main()
