import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from teams_lite.app import _MEETING_ROLE, MainWindow
from teams_lite.config import AppConfig
from teams_lite.moments import load_moments
from teams_lite.ocr import load_participants, merge_participants
from teams_lite.storage import Storage
from teams_lite.updates import UpdateInfo
from teams_lite.window_capture import TeamsWindowMinimized


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
            window.resize(1000, 900)
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
                self.assertEqual(labels, ['Teams Lite', 'Vista previa', 'Administrar'])
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


if __name__ == '__main__':
    unittest.main()
