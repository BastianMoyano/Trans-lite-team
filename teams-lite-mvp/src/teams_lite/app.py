"""PySide6 desktop application for Teams Lite."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .audio import Recorder
from .config import AppConfig, load_config
from .ocr import capture_name_hints
from .storage import Storage
from .transcription import AudioSource, CapabilityError, export_transcript, transcribe_recordings
from .visual_speakers import append_observation, apply_visual_speakers, load_observations
from .wevisone import WeVisOneDetector
from .window_capture import FrameSampler, capture_teams_frame


APP_STYLESHEET = """
QMainWindow, QWidget#appRoot {
    background: #F7F6F3;
    color: #37352F;
}
QLabel {
    color: #37352F;
}
QLabel#eyebrow {
    color: #9B9A97;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}
QLabel#pageTitle {
    color: #2F2E2A;
    font-size: 28px;
    font-weight: 700;
}
QLabel#subtitle, QLabel#fieldHint {
    color: #787774;
    font-size: 13px;
}
QLabel#sectionTitle {
    color: #37352F;
    font-size: 15px;
    font-weight: 600;
}
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E9E9E7;
    border-radius: 10px;
}
QFrame#noticeCard {
    background: #FBF3DB;
    border: 1px solid #E8DEC3;
    border-radius: 8px;
}
QLabel#noticeTitle {
    color: #64473A;
    font-size: 13px;
    font-weight: 600;
}
QLabel#noticeText {
    color: #6B5A50;
    font-size: 12px;
}
QLineEdit {
    min-height: 40px;
    padding: 0 12px;
    background: #FFFFFF;
    border: 1px solid #D9D9D6;
    border-radius: 6px;
    color: #37352F;
    selection-background-color: #D3E5EF;
}
QLineEdit:focus {
    border: 1px solid #2383E2;
}
QPushButton {
    min-height: 40px;
    padding: 0 16px;
    border-radius: 6px;
    border: 1px solid #D9D9D6;
    background: #FFFFFF;
    color: #37352F;
    font-weight: 600;
}
QPushButton:hover {
    background: #F1F1EF;
}
QPushButton:pressed {
    background: #E9E9E7;
}
QPushButton:disabled {
    color: #B4B4B0;
    background: #F7F7F5;
    border-color: #E9E9E7;
}
QPushButton#primaryButton {
    background: #37352F;
    border-color: #37352F;
    color: #FFFFFF;
}
QPushButton#primaryButton:hover {
    background: #4F4E48;
}
QPushButton#dangerButton:enabled {
    background: #FFF5F5;
    border-color: #F0C7C7;
    color: #C63C3C;
}
QPushButton#deleteButton:enabled {
    background: #FFFFFF;
    border-color: #E6C6C6;
    color: #B42318;
}
QPushButton#deleteButton:hover:enabled {
    background: #FFF5F5;
}
QCheckBox {
    color: #5A5955;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QLabel#statusBadge {
    padding: 6px 10px;
    background: #F1F1EF;
    border: 1px solid #E3E2DF;
    border-radius: 11px;
    color: #5F5E5A;
    font-size: 12px;
    font-weight: 600;
}
QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #FAFAF9;
    border: none;
    border-top: 1px solid #E9E9E7;
    color: #37352F;
    gridline-color: #EFEFEC;
    selection-background-color: #E7F3FF;
    selection-color: #37352F;
}
QTableWidget::item {
    padding: 8px;
}
QPlainTextEdit {
    min-height: 140px;
    padding: 12px;
    background: #FAFAF9;
    border: 1px solid #E9E9E7;
    border-radius: 6px;
    color: #37352F;
    font-family: "Consolas";
    font-size: 12px;
}
QHeaderView::section {
    min-height: 34px;
    padding: 0 8px;
    background: #FAFAF9;
    border: none;
    border-right: 1px solid #EFEFEC;
    color: #787774;
    font-size: 11px;
    font-weight: 600;
}
"""


class AppSignals(QObject):
    status = Signal(str)
    recording_stopped = Signal(str)
    transcript_ready = Signal(str)
    failure = Signal(str)
    ocr_ready = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.store = Storage(config.data_dir)
        self.meeting_id: str | None = None
        self.recorder: Recorder | None = None
        self.visual_sampler: FrameSampler | None = None
        self.signals = AppSignals()
        self.signals.status.connect(self._set_status)
        self.signals.recording_stopped.connect(self._after_stop)
        self.signals.transcript_ready.connect(self._transcript_done)
        self.signals.failure.connect(self._show_error)
        self.signals.ocr_ready.connect(self._ocr_done)
        self.setWindowTitle("Teams Lite")
        self.resize(980, 680)
        self.setMinimumSize(820, 580)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2_000)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)

        eyebrow = QLabel("CAPTURA RECUPERABLE DE REUNIONES")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)

        page_title = QLabel("Teams Lite")
        page_title.setObjectName("pageTitle")
        layout.addWidget(page_title)

        subtitle = QLabel("Graba el micrófono y el audio del sistema en pistas separadas y recuperables.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        notice_card = QFrame()
        notice_card.setObjectName("noticeCard")
        notice_layout = QVBoxLayout(notice_card)
        notice_layout.setContentsMargins(14, 12, 14, 12)
        notice_layout.setSpacing(4)
        notice_title = QLabel("Antes de grabar")
        notice_title.setObjectName("noticeTitle")
        notice = QLabel(
            "La grabación captura el micrófono predeterminado y la salida de Windows en pistas separadas. "
            "Avisa a los participantes y respeta las normas de grabación aplicables. "
            "Usa auriculares para evitar que el micrófono vuelva a captar las voces remotas."
        )
        notice.setObjectName("noticeText")
        notice.setWordWrap(True)
        notice_layout.addWidget(notice_title)
        notice_layout.addWidget(notice)
        layout.addWidget(notice_card)

        recording_card = QFrame()
        recording_card.setObjectName("card")
        recording_layout = QVBoxLayout(recording_card)
        recording_layout.setContentsMargins(18, 16, 18, 16)
        recording_layout.setSpacing(12)

        recording_title = QLabel("Nueva grabación")
        recording_title.setObjectName("sectionTitle")
        recording_layout.addWidget(recording_title)

        field_hint = QLabel("Título de la reunión")
        field_hint.setObjectName("fieldHint")
        recording_layout.addWidget(field_hint)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.title_input = QLineEdit("Reunión de Teams")
        self.title_input.setPlaceholderText("Escribe un título para la reunión")
        self.title_input.setAccessibleName("Título de la reunión")
        self.start_button = QPushButton("Iniciar grabación")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("Detener y transcribir")
        self.stop_button.setObjectName("dangerButton")
        self.ocr_checkbox = QCheckBox("Activar OCR visual opcional")
        self.ocr_checkbox.setChecked(self.config.ocr_enabled)
        self.ocr_button = QPushButton("Capturar nombres visibles")
        self.visual_speaker_checkbox = QCheckBox("Identificar hablante activo con WeVisOne-VL")
        self.visual_speaker_checkbox.setChecked(self.config.visual_speaker_enabled)
        self.stop_button.setEnabled(False)
        self.ocr_button.setEnabled(False)
        self._set_recording_controls(recording=False)
        controls.addWidget(self.title_input, 1)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        recording_layout.addLayout(controls)

        ocr_controls = QHBoxLayout()
        ocr_controls.setSpacing(10)
        ocr_controls.addWidget(self.ocr_checkbox)
        ocr_controls.addWidget(self.ocr_button)
        ocr_controls.addWidget(self.visual_speaker_checkbox)
        ocr_controls.addStretch(1)
        recording_layout.addLayout(ocr_controls)

        status_row = QHBoxLayout()
        status_caption = QLabel("Estado")
        status_caption.setObjectName("fieldHint")
        self.status_label = QLabel("Listo")
        self.status_label.setObjectName("statusBadge")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row.addWidget(status_caption)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        recording_layout.addLayout(status_row)
        layout.addWidget(recording_card)

        history_card = QFrame()
        history_card.setObjectName("card")
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(0, 16, 0, 0)
        history_layout.setSpacing(12)
        history_title = QLabel("Grabaciones recientes")
        history_title.setObjectName("sectionTitle")
        history_title.setContentsMargins(18, 0, 18, 0)
        history_layout.addWidget(history_title)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Inicio (UTC)", "Título", "Estado", "ID de reunión"])
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        history_layout.addWidget(self.table, 1)

        transcript_header = QHBoxLayout()
        transcript_title = QLabel("Transcripción")
        transcript_title.setObjectName("sectionTitle")
        self.delete_button = QPushButton("Eliminar reunión")
        self.delete_button.setObjectName("deleteButton")
        self.delete_button.setEnabled(False)
        transcript_header.addWidget(transcript_title)
        transcript_header.addStretch(1)
        transcript_header.addWidget(self.delete_button)
        transcript_container = QWidget()
        transcript_container.setContentsMargins(18, 0, 18, 16)
        transcript_layout = QVBoxLayout(transcript_container)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        transcript_layout.setSpacing(10)
        transcript_layout.addLayout(transcript_header)
        self.transcript_viewer = QPlainTextEdit()
        self.transcript_viewer.setReadOnly(True)
        self.transcript_viewer.setPlaceholderText("Selecciona una reunión para ver su transcripción.")
        transcript_layout.addWidget(self.transcript_viewer)
        history_layout.addWidget(transcript_container)
        layout.addWidget(history_card, 1)
        self.setCentralWidget(root)
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.ocr_button.clicked.connect(self.capture_ocr)
        self.table.itemSelectionChanged.connect(self.show_selected_transcript)
        self.delete_button.clicked.connect(self.delete_selected_meeting)

    def _set_recording_controls(self, *, recording: bool, finalizing: bool = False) -> None:
        self.start_button.setVisible(not recording)
        self.start_button.setEnabled(not recording)
        self.stop_button.setVisible(recording)
        self.stop_button.setEnabled(recording and not finalizing)

    def start_recording(self) -> None:
        if self.recorder is not None:
            return
        try:
            meeting_id = self.store.create_meeting(self.title_input.text().strip() or "Reunión de Teams")
            recording_origin = time.monotonic()
            recorder = Recorder(
                self.store,
                meeting_id,
                sample_rate=self.config.sample_rate,
                segment_seconds=self.config.segment_seconds,
            )
            recorder.start()
        except Exception as exc:
            self._show_error(f"No se pudo iniciar la grabación: {exc}")
            return
        self.meeting_id = meeting_id
        self.recorder = recorder
        if self.visual_speaker_checkbox.isChecked():
            self._start_visual_tracking(meeting_id, recording_origin)
        self._set_recording_controls(recording=True)
        self.ocr_button.setEnabled(self.ocr_checkbox.isChecked())
        self._set_status("Grabando el micrófono y el audio del sistema…")
        self._refresh()

    def stop_recording(self) -> None:
        if self.recorder is None or self.meeting_id is None:
            return
        recorder, meeting_id = self.recorder, self.meeting_id
        visual_sampler, self.visual_sampler = self.visual_sampler, None
        self._set_recording_controls(recording=True, finalizing=True)
        self.ocr_button.setEnabled(False)
        self._set_status("Finalizando los segmentos de audio recuperables…")

        def work() -> None:
            try:
                if visual_sampler is not None:
                    visual_sampler.stop()
                    if visual_sampler.errors:
                        self.store.add_event(meeting_id, "visual_tracking_warning", visual_sampler.errors[-1])
                recorder.stop()
                if recorder.errors:
                    self.signals.failure.emit("; ".join(recorder.errors))
                self.signals.recording_stopped.emit(meeting_id)
            except Exception as exc:
                self.signals.failure.emit(f"No se pudo detener la grabación correctamente: {exc}")
                self.signals.recording_stopped.emit(meeting_id)

        threading.Thread(target=work, name="ui-stop-recording", daemon=True).start()

    def _after_stop(self, meeting_id: str) -> None:
        self.recorder = None
        self.meeting_id = None
        self._set_recording_controls(recording=False)
        self._refresh()
        if not self.config.auto_transcribe:
            self._set_status("Grabación finalizada. La transcripción automática está desactivada.")
            return
        self._set_status("Transcribiendo los segmentos finalizados mediante CPU…")
        threading.Thread(
            target=self._transcribe_meeting,
            args=(meeting_id,),
            name="offline-transcription",
            daemon=True,
        ).start()

    def _transcribe_meeting(self, meeting_id: str) -> None:
        try:
            rows = [
                row for row in self.store.list_segments(meeting_id)
                if row["status"] in ("finalized", "recovered")
            ]
            if not rows:
                raise RuntimeError("No hay segmentos de audio finalizados disponibles.")
            origin = min(row["started_monotonic"] for row in rows)
            sources = [
                AudioSource(
                    row["path"],
                    "local" if row["kind"] == "microphone" else "remote",
                    max(0.0, row["started_monotonic"] - origin),
                )
                for row in rows
            ]
            transcript = transcribe_recordings(
                sources,
                model_dir=self.config.parakeet_model_dir,
                chunk_seconds=20,
            )
            meeting_dir = Path(self.config.data_dir) / meeting_id
            observations = load_observations(meeting_dir / "speaker-observations.jsonl")
            transcript = apply_visual_speakers(transcript, observations)
            export_transcript(transcript, meeting_dir / "transcript.json", meeting_dir / "transcript.txt")
            self.signals.transcript_ready.emit(str(meeting_dir / "transcript.txt"))
        except CapabilityError as exc:
            self.signals.failure.emit(f"El audio se guardó, pero la transcripción no está disponible: {exc}")
        except Exception as exc:
            self.signals.failure.emit(f"El audio se guardó, pero la transcripción falló: {exc}")

    def capture_ocr(self) -> None:
        if self.meeting_id is None:
            return
        meeting_id = self.meeting_id
        self._set_status("Capturando OCR visual opcional…")

        def work() -> None:
            try:
                output = Path(self.config.data_dir) / meeting_id / "ocr"
                result = capture_name_hints(output, self.config.ocr_region, self.config.tesseract_cmd)
                self.signals.ocr_ready.emit(
                    f"Se capturaron {len(result['name_hints'])} nombres visibles."
                )
            except Exception as exc:
                self.signals.failure.emit(f"Falló la captura OCR: {exc}")

        threading.Thread(target=work, name="visual-ocr", daemon=True).start()

    def _start_visual_tracking(self, meeting_id: str, recording_origin: float) -> None:
        meeting_dir = Path(self.config.data_dir) / meeting_id
        detector = WeVisOneDetector(
            model_path=self.config.wevisone_model_dir,
            device="cpu",
            dtype="float32",
            observation_seconds=self.config.visual_sample_seconds,
        )

        def capture():
            return capture_teams_frame(
                meeting_dir / "visual-frames",
                recording_origin,
                title_hint=self.config.teams_window_title,
            )

        def detect(frame) -> None:
            observation = detector.detect(frame)
            if observation is not None:
                append_observation(meeting_dir / "speaker-observations.jsonl", observation)

        self.visual_sampler = FrameSampler(
            capture,
            detect,
            interval_seconds=self.config.visual_sample_seconds,
        )
        self.visual_sampler.start()

    def _refresh(self) -> None:
        selected_id = self._selected_meeting_id()
        meetings = self.store.list_meetings()
        self.table.setRowCount(len(meetings))
        for row_number, meeting in enumerate(meetings):
            for column, key in enumerate(("started_utc", "title", "status", "id")):
                value = meeting[key]
                if key == "status":
                    value = {
                        "recording": "Grabando",
                        "completed": "Completada",
                        "interrupted": "Interrumpida",
                    }.get(str(value), str(value))
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
                if column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, meeting["status"])
                self.table.setItem(row_number, column, item)
            if meeting["id"] == selected_id:
                self.table.selectRow(row_number)

    def _selected_meeting_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def show_selected_transcript(self) -> None:
        meeting_id = self._selected_meeting_id()
        self.delete_button.setEnabled(meeting_id is not None)
        if meeting_id is None:
            self.transcript_viewer.clear()
            self.transcript_viewer.setPlaceholderText("Selecciona una reunión para ver su transcripción.")
            return
        transcript = self.store.root / meeting_id / "transcript.txt"
        if not transcript.is_file():
            self.transcript_viewer.clear()
            self.transcript_viewer.setPlaceholderText(
                "La transcripción todavía no está disponible para esta reunión."
            )
            return
        content = transcript.read_text(encoding="utf-8").strip()
        self.transcript_viewer.setPlainText(content)
        if not content:
            self.transcript_viewer.setPlaceholderText("La transcripción está vacía.")

    def _confirm_delete(self, title: str) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Eliminar reunión")
        dialog.setText(f'¿Quieres eliminar permanentemente "{title}"?')
        dialog.setInformativeText("Se eliminarán el audio, la transcripción y los metadatos asociados.")
        delete_action = dialog.addButton("Eliminar", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is delete_action

    def delete_selected_meeting(self) -> None:
        row = self.table.currentRow()
        meeting_id = self._selected_meeting_id()
        if row < 0 or meeting_id is None:
            return
        title_item = self.table.item(row, 1)
        status_item = self.table.item(row, 2)
        title = title_item.text() if title_item is not None else "Reunión"
        status = status_item.data(Qt.ItemDataRole.UserRole) if status_item is not None else None
        if meeting_id == self.meeting_id or status == "recording":
            self._show_error("Detén la grabación antes de eliminar esta reunión.")
            return
        if not self._confirm_delete(title):
            return
        try:
            self.store.delete_meeting(meeting_id)
        except Exception as exc:
            self._show_error(f"No se pudo eliminar la reunión: {exc}")
            return
        self.transcript_viewer.clear()
        self.delete_button.setEnabled(False)
        self._set_status("Reunión eliminada.")
        self._refresh()

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _transcript_done(self, path: str) -> None:
        self._set_status(f"Transcripción guardada: {path}")
        self._refresh()
        self.show_selected_transcript()

    def _ocr_done(self, message: str) -> None:
        self._set_status(message + " El OCR no identifica voces.")

    def _show_error(self, message: str) -> None:
        self._set_status(message)
        QMessageBox.warning(self, "Teams Lite", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.recorder is not None:
            QMessageBox.warning(self, "Teams Lite", "Detén la grabación antes de cerrar Teams Lite.")
            event.ignore()
            return
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grabador recuperable Teams Lite")
    parser.add_argument("--config", default="config.json", help="Ruta del archivo de configuración JSON")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("La captura de audio de Teams Lite solo es compatible con Windows.", file=sys.stderr)
        return 2
    config = load_config(args.config)
    application = QApplication(sys.argv[:1])
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 10))
    window = MainWindow(config)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

