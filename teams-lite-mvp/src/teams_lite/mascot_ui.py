"""The floating mascot with quick actions, and the Mascota settings section."""
from __future__ import annotations

import time

from PySide6.QtCore import QByteArray, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .library_ui import PlacementPicker
from .mascot import DEFAULT_TITLE, MascotSettings
from .moments import format_offset
from .storage import Storage

# A friendly round face in the app's teal: the mascot's avatar and drag handle.
_FACE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
    '<circle cx="24" cy="24" r="22" fill="#46C7B5"/>'
    '<circle cx="17" cy="20" r="4" fill="#0F1B1C"/><circle cx="31" cy="20" r="4" fill="#0F1B1C"/>'
    '<circle cx="18.3" cy="18.7" r="1.3" fill="#FFFFFF"/><circle cx="32.3" cy="18.7" r="1.3" fill="#FFFFFF"/>'
    '<path d="M15 29 Q24 37 33 29" stroke="#0F1B1C" stroke-width="3" fill="none" stroke-linecap="round"/>'
    '</svg>'
)


def mascot_face(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(_FACE_SVG.encode())).render(painter)
    painter.end()
    return pixmap


class MascotWidget(QWidget):
    """Always-on-top bubble over Teams: record or stop, mark a moment and read attendees in one click."""

    record_requested = Signal()
    capture_requested = Signal()
    ocr_requested = Signal()
    open_requested = Signal()
    moved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Teams Lite · Mascota")
        self._drag_offset: QPoint | None = None
        self._started: float | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("mascotBody")
        outer.addWidget(body)
        layout = QHBoxLayout(body)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(6)
        self.avatar = QLabel()
        self.avatar.setPixmap(mascot_face(36))
        self.avatar.setToolTip("Arrástrame para moverme · doble clic abre Teams Lite")
        self.avatar.setCursor(Qt.CursorShape.OpenHandCursor)
        layout.addWidget(self.avatar)
        self.record_button = QPushButton("Grabar")
        self.record_button.setObjectName("mascotRecord")
        self.record_button.setToolTip("Graba al instante con el nombre, la organización y el proyecto por defecto")
        self.capture_button = QPushButton("Captura")
        self.capture_button.setObjectName("mascotAction")
        self.capture_button.setToolTip("Marca el momento con un pantallazo (como Ctrl+Alt+M)")
        self.ocr_button = QPushButton("OCR")
        self.ocr_button.setObjectName("mascotAction")
        self.ocr_button.setToolTip("Lee los asistentes visibles en Teams")
        self.timer_label = QLabel()
        self.timer_label.setObjectName("mascotTimer")
        for widget in (self.record_button, self.capture_button, self.ocr_button, self.timer_label):
            layout.addWidget(widget)
        self.record_button.clicked.connect(self.record_requested.emit)
        self.capture_button.clicked.connect(self.capture_requested.emit)
        self.ocr_button.clicked.connect(self.ocr_requested.emit)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self.set_recording(False)

    def set_recording(self, recording: bool, started_monotonic: float | None = None) -> None:
        self.record_button.setText("Detener" if recording else "Grabar")
        self.record_button.setProperty("recording", recording)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.capture_button.setEnabled(recording)
        self.ocr_button.setEnabled(recording)
        self._started = started_monotonic if recording else None
        self.timer_label.setVisible(recording)
        if recording:
            self._tick()
            self._clock.start()
        else:
            self._clock.stop()

    def _tick(self) -> None:
        if self._started is not None:
            self.timer_label.setText(format_offset(max(0.0, time.monotonic() - self._started)))

    # Dragging: the mascot has no title bar, so any click-and-drag on it moves it.
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.x(), self.y())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.open_requested.emit()
        super().mouseDoubleClickEvent(event)


class MascotSettingsPage(QWidget):
    """The Mascota section: what the mascot records with, and whether it is shown."""

    changed = Signal(object)  # MascotSettings

    def __init__(self, store: Storage, settings: MascotSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        face = QLabel()
        face.setPixmap(mascot_face(48))
        heading.addWidget(face)
        titles = QVBoxLayout()
        title = QLabel("Mascota")
        title.setObjectName("pageTitle")
        hint = QLabel(
            "La mascota flota sobre Teams. Con un clic graba al instante, sin pedir nombre, organización "
            "ni proyecto: usa los valores de esta sección. Mientras grabas, Captura marca el momento con "
            "un pantallazo y OCR lee los asistentes."
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(hint)
        heading.addLayout(titles, 1)
        layout.addLayout(heading)

        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)
        self.show_checkbox = QCheckBox("Mostrar la mascota sobre las demás ventanas")
        self.show_checkbox.setChecked(settings.enabled)
        form.addWidget(self.show_checkbox)
        name_hint = QLabel("Nombre de las reuniones que inicia la mascota")
        name_hint.setObjectName("fieldHint")
        form.addWidget(name_hint)
        self.title_input = QLineEdit(settings.title)
        self.title_input.setPlaceholderText(DEFAULT_TITLE)
        self.title_input.setAccessibleName("Nombre por defecto de la reunión")
        form.addWidget(self.title_input)
        placement_hint = QLabel("Dónde se guardan (sin organización van al Cofre de reuniones)")
        placement_hint.setObjectName("fieldHint")
        form.addWidget(placement_hint)
        self.placement_picker = PlacementPicker(store)
        self.placement_picker.set_placement(settings.organization_id, settings.project_id)
        form.addWidget(self.placement_picker)
        self.reset_position_button = QPushButton("Volver a la posición inicial")
        self.reset_position_button.setObjectName("ghostButton")
        form.addWidget(self.reset_position_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)
        layout.addStretch(1)

        self.show_checkbox.toggled.connect(lambda _checked: self._emit())
        self.title_input.editingFinished.connect(self._emit)
        self.placement_picker.organization_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.placement_picker.project_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.reset_position_button.clicked.connect(self._reset_position)

    def settings(self) -> MascotSettings:
        return self._settings

    def set_position(self, position: tuple[int, int] | None) -> None:
        self._settings.position = position

    def reload_placements(self) -> None:
        """Organizations or projects changed elsewhere: refresh the choices, keep the selection.

        When the default was deleted the picker falls back (to the Cofre or "Sin proyecto") and that is saved.
        """
        self.placement_picker.reload()
        self._emit()

    def _reset_position(self) -> None:
        self._settings.position = None
        self.changed.emit(self._settings)

    def _emit(self) -> None:
        self._settings.enabled = self.show_checkbox.isChecked()
        self._settings.title = self.title_input.text().strip() or DEFAULT_TITLE
        self._settings.organization_id = self.placement_picker.organization_id()
        self._settings.project_id = self.placement_picker.project_id()
        self.changed.emit(self._settings)
