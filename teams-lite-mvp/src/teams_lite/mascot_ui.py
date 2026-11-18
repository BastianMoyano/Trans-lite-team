"""Astro, the floating pixel-art mascot, and the Mascota settings section."""
from __future__ import annotations

from pathlib import Path
import time

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .astro import AstroMachine, AstroState
from .astro_sprites import MASCOTS_DIR, Animation, list_characters, placeholder_animation, scale_pixels
from .library_ui import PlacementPicker
from .mascot import DEFAULT_TITLE, SCALES, MascotSettings
from .moments import format_offset
from .storage import Storage

DRAG_THRESHOLD = 4  # pixels: a smaller movement is a click that opens the menu
BUBBLE_SECONDS = 4.0
POLL_MS = 400  # reactions (capture, success, error) expire on their own; checked this often
SIZE_LABELS = {2: "Pequeña", 3: "Mediana", 4: "Grande", 5: "Muy grande"}


def mascot_face(size: int) -> QPixmap:
    """The first idle frame, used as the section's icon."""
    frame = placeholder_animation(AstroState.IDLE).frames[0]
    return QPixmap.fromImage(scale_pixels(frame, max(1, size // frame.width())))


class MascotWidget(QWidget):
    """Astro floats over Teams: its look follows the app's real state and a click opens its actions."""

    record_requested = Signal()
    capture_requested = Signal()
    ocr_requested = Signal()
    open_requested = Signal()
    moved = Signal(int, int)
    sound_toggled = Signal(bool)
    motion_toggled = Signal(bool)

    def __init__(self, machine: AstroMachine, parent: QWidget | None = None):
        # WindowDoesNotAcceptFocus: clicking Astro never takes the keyboard away from Teams.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Teams Lite · Astro")
        self.machine = machine
        self._animations: dict[AstroState, Animation] = {}
        self._scale = 3
        self._motion = True
        self._frame = 0
        self._shown_state: AstroState | None = None
        self._last_message: str | None = None
        self._started: float | None = None
        self._press: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._dragging = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.bubble = QLabel()
        self.bubble.setObjectName("astroBubble")
        self.bubble.setWordWrap(True)
        self.bubble.setMaximumWidth(220)
        self.bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bubble.hide()
        layout.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignHCenter)
        self.sprite = QLabel()
        self.sprite.setToolTip("Astro · clic para sus acciones · arrástralo para moverlo")
        self.sprite.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.sprite, 0, Qt.AlignmentFlag.AlignHCenter)
        self.timer_label = QLabel()
        self.timer_label.setObjectName("mascotTimer")
        self.timer_label.hide()
        layout.addWidget(self.timer_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.menu = QMenu(self)
        self.record_action = self.menu.addAction("Grabar reunión", self.record_requested.emit)
        self.capture_action = self.menu.addAction("Capturar reunión", self.capture_requested.emit)
        self.ocr_action = self.menu.addAction("Leer asistentes (OCR)", self.ocr_requested.emit)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction("Abrir Teams Lite", self.open_requested.emit)
        self.menu.addSeparator()
        self.sound_action = self.menu.addAction("Efectos de sonido")
        self.sound_action.setCheckable(True)
        self.sound_action.toggled.connect(self.sound_toggled.emit)
        self.motion_action = self.menu.addAction("Animaciones y movimiento")
        self.motion_action.setCheckable(True)
        self.motion_action.toggled.connect(self.motion_toggled.emit)

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._next_frame)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.refresh)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self.bubble.hide)
        self.set_recording(False)

    # Appearance -------------------------------------------------------------------------------
    def set_animations(self, animations: dict[AstroState, Animation]) -> None:
        self._animations = animations
        self._shown_state = None
        self.refresh()

    def set_scale(self, scale: int) -> None:
        self._scale = scale if scale in SCALES else 3
        self._render()

    def set_motion(self, enabled: bool) -> None:
        self._motion = enabled
        self.motion_action.blockSignals(True)
        self.motion_action.setChecked(enabled)
        self.motion_action.blockSignals(False)
        self._restart_frames()
        self._render()

    def set_sound(self, enabled: bool) -> None:
        self.sound_action.blockSignals(True)
        self.sound_action.setChecked(enabled)
        self.sound_action.blockSignals(False)

    def refresh(self) -> None:
        """Show the machine's current state; called on every event and while a reaction is shown."""
        state = self.machine.state
        if state is not self._shown_state:
            self._shown_state = state
            self._frame = 0
            self._restart_frames()
        self._render()
        message = self.machine.message
        if message and message != self._last_message:
            self.bubble.setText(message)
            self.bubble.show()
            self.adjustSize()
            seconds = BUBBLE_SECONDS * (1.5 if state is AstroState.ERROR else 1)
            self._bubble_timer.start(int(seconds * 1000))
        self._last_message = message

    def _restart_frames(self) -> None:
        animation = self._animations.get(self._shown_state) if self._shown_state else None
        if self._motion and animation is not None and len(animation.frames) > 1 and self.isVisible():
            self.frame_timer.start(int(1000 / animation.fps))
        else:
            self.frame_timer.stop()

    def _next_frame(self) -> None:
        self._frame += 1
        self._render()

    def _render(self) -> None:
        animation = self._animations.get(self._shown_state) if self._shown_state else None
        if animation is None or not animation.frames:
            return
        frame = animation.frames[self._frame % len(animation.frames)] if self._motion else animation.frames[0]
        self.sprite.setPixmap(QPixmap.fromImage(scale_pixels(frame, self._scale)))
        self.adjustSize()

    def set_recording(self, recording: bool, started_monotonic: float | None = None) -> None:
        self.record_action.setText("Detener grabación" if recording else "Grabar reunión")
        self.capture_action.setEnabled(recording)
        self.ocr_action.setEnabled(recording)
        self._started = started_monotonic if recording else None
        self.timer_label.setVisible(recording)
        if recording:
            self._tick()
            self._clock.start()
        else:
            self._clock.stop()
        self.adjustSize()

    def _tick(self) -> None:
        if self._started is not None:
            self.timer_label.setText(format_offset(max(0.0, time.monotonic() - self._started)))

    # Timers only run while Astro is on screen: nothing to draw, nothing to spend.
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._poll.start()
        self._restart_frames()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self._poll.stop()
        self.frame_timer.stop()

    # A click opens the menu; a drag moves Astro (MOVING while dragged) -----------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._drag_offset = self._press - self.frameGeometry().topLeft()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            position = event.globalPosition().toPoint()
            if not self._dragging and (position - self._press).manhattanLength() >= DRAG_THRESHOLD:
                self._dragging = True
                self.machine.move_started()
            if self._dragging:
                self.move(position - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._press is not None:
            if self._dragging:
                self.machine.move_finished()
                self.moved.emit(self.x(), self.y())
            else:
                self.menu.popup(event.globalPosition().toPoint())
            self._press, self._dragging = None, False
        super().mouseReleaseEvent(event)


class MascotSettingsPage(QWidget):
    """The Mascota section: what Astro records with, how it looks and whether it is shown."""

    changed = Signal(object)  # MascotSettings

    def __init__(self, store: Storage, settings: MascotSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._store = store
        self._settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        face = QLabel()
        face.setPixmap(mascot_face(64))
        heading.addWidget(face)
        titles = QVBoxLayout()
        title = QLabel("Mascota")
        title.setObjectName("pageTitle")
        hint = QLabel(
            "Astro flota sobre Teams y muestra lo que pasa: grabando, capturando, procesando o si algo falló. "
            "Haz clic en Astro para grabar o detener al instante (sin pedir nombre, organización ni proyecto: "
            "usa los valores de esta sección), capturar la reunión o leer los asistentes."
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(hint)
        heading.addLayout(titles, 1)
        layout.addLayout(heading)

        recording = QFrame()
        recording.setObjectName("card")
        form = QVBoxLayout(recording)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)
        section = QLabel("Grabación rápida")
        section.setObjectName("sectionTitle")
        form.addWidget(section)
        name_hint = QLabel("Nombre de las reuniones que inicia Astro")
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
        layout.addWidget(recording)

        look = QFrame()
        look.setObjectName("card")
        options = QVBoxLayout(look)
        options.setContentsMargins(20, 18, 20, 18)
        options.setSpacing(10)
        section = QLabel("Astro")
        section.setObjectName("sectionTitle")
        options.addWidget(section)
        self.show_checkbox = QCheckBox("Mostrar a Astro sobre las demás ventanas")
        self.show_checkbox.setChecked(settings.enabled)
        self.sound_checkbox = QCheckBox("Efectos de sonido (aviso al capturar)")
        self.sound_checkbox.setChecked(settings.sound)
        self.motion_checkbox = QCheckBox("Animaciones y movimiento (desactívalo para una imagen fija)")
        self.motion_checkbox.setChecked(settings.motion)
        for checkbox in (self.show_checkbox, self.sound_checkbox, self.motion_checkbox):
            options.addWidget(checkbox)
        row = QHBoxLayout()
        row.setSpacing(8)
        size_hint = QLabel("Tamaño")
        size_hint.setObjectName("fieldHint")
        self.size_combo = QComboBox()
        for scale in SCALES:
            self.size_combo.addItem(SIZE_LABELS[scale], scale)
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(settings.scale)))
        character_hint = QLabel("Personaje")
        character_hint.setObjectName("fieldHint")
        self.character_combo = QComboBox()
        self.character_combo.setAccessibleName("Personaje de la mascota")
        for widget in (size_hint, self.size_combo, character_hint, self.character_combo):
            row.addWidget(widget)
        row.addStretch(1)
        options.addLayout(row)
        buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("Abrir carpeta de personajes")
        self.open_folder_button.setObjectName("ghostButton")
        self.open_folder_button.setToolTip("Pon aquí una carpeta por personaje con sus 8 PNG (ver docs/astro-sprites.md)")
        self.reset_position_button = QPushButton("Volver a la posición inicial")
        self.reset_position_button.setObjectName("ghostButton")
        buttons.addWidget(self.open_folder_button)
        buttons.addWidget(self.reset_position_button)
        buttons.addStretch(1)
        options.addLayout(buttons)
        self.sprite_notice = QLabel()
        self.sprite_notice.setObjectName("fieldHint")
        self.sprite_notice.setWordWrap(True)
        options.addWidget(self.sprite_notice)
        layout.addWidget(look)
        layout.addStretch(1)
        self.reload_characters()

        for checkbox in (self.show_checkbox, self.sound_checkbox, self.motion_checkbox):
            checkbox.toggled.connect(lambda _checked: self._emit())
        self.title_input.editingFinished.connect(self._emit)
        self.placement_picker.organization_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.placement_picker.project_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.size_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.character_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.reset_position_button.clicked.connect(self._reset_position)
        self.open_folder_button.clicked.connect(self._open_folder)

    def settings(self) -> MascotSettings:
        return self._settings

    def reload_characters(self) -> None:
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for name in list_characters(self._store.root):
            self.character_combo.addItem(name.capitalize(), name)
        self.character_combo.setCurrentIndex(max(0, self.character_combo.findData(self._settings.character)))
        self.character_combo.blockSignals(False)

    def set_sprite_problems(self, problems: list[str], custom: bool) -> None:
        if problems:
            self.sprite_notice.setText("Algunos dibujos no se pudieron usar: " + " ".join(problems))
        elif not custom:
            self.sprite_notice.setText(
                "Astro usa un dibujo provisional. Agrega tus PNG en la carpeta de personajes para reemplazarlo."
            )
        else:
            self.sprite_notice.setText("")

    def set_flag(self, name: str, value: bool) -> None:
        """Mirror a change made from Astro's menu without saving it twice."""
        checkbox = {"sound": self.sound_checkbox, "motion": self.motion_checkbox}[name]
        checkbox.blockSignals(True)
        checkbox.setChecked(value)
        checkbox.blockSignals(False)
        setattr(self._settings, name, value)

    def reload_placements(self) -> None:
        """Organizations or projects changed elsewhere: refresh the choices, keep the selection.

        When the default was deleted the picker falls back (to the Cofre or "Sin proyecto") and that is saved.
        """
        self.placement_picker.reload()
        self._emit()

    def _open_folder(self) -> None:
        folder = Path(self._store.root) / MASCOTS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reset_position(self) -> None:
        self._settings.position = None
        self.changed.emit(self._settings)

    def _emit(self) -> None:
        self._settings.enabled = self.show_checkbox.isChecked()
        self._settings.sound = self.sound_checkbox.isChecked()
        self._settings.motion = self.motion_checkbox.isChecked()
        self._settings.title = self.title_input.text().strip() or DEFAULT_TITLE
        self._settings.organization_id = self.placement_picker.organization_id()
        self._settings.project_id = self.placement_picker.project_id()
        self._settings.scale = int(self.size_combo.currentData() or 3)
        self._settings.character = str(self.character_combo.currentData() or "astro")
        self.changed.emit(self._settings)
