"""Astro's rooms: the pixel art that dresses every section (headers, corners, empty states).

The images in assets/room were cropped from the redesign mockups with feathered edges, so they blend
into the theme. They are decoration only: a missing or broken file is simply not drawn.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

ROOM_DIR = Path(__file__).resolve().parent / "assets" / "room"

ROOM_COLORS = {
    "floor": "#0E0F3A",
    "floor_glow": "#5B2BB8",
    "gold": "#F7C948",
}

# Section name (as in the side menu) -> (asset key, gold subtitle, description)
SECTIONS = {
    "Grabar": ("grabar", "La sala de comando de Astro", "Captura, transcribe y convierte tus reuniones en conocimiento."),
    "Biblioteca": ("biblioteca", "La biblioteca de Astro",
                   "Explora reuniones, revisa sus momentos y conversa con la IA desde un mismo lugar."),
    "Organización": ("organizacion", "La sala de planificación",
                     "Organiza tus reuniones por organización y proyecto, y mantén todo en orden."),
    "Mascota": ("mascota", "La habitación de Astro", "Configura cómo te acompaña Astro durante tus reuniones."),
}
HEADER_HEIGHT = 150


@lru_cache(maxsize=None)
def room_pixmap(name: str) -> QPixmap:
    """The room image, or a null pixmap when it is missing or unreadable (the caller draws nothing)."""
    path = ROOM_DIR / name
    return QPixmap(str(path)) if path.is_file() else QPixmap()


def _fit(pixmap: QPixmap, *, height: int | None = None, width: int | None = None) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    if height is not None and pixmap.height() > height:
        pixmap = pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    if width is not None and pixmap.width() > width:
        pixmap = pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    return pixmap


def title_row(text: str, icon: str, color: str | None = None, size: int = 22) -> tuple[QHBoxLayout, QLabel]:
    """A card title with its outline icon on the left, as in the mockups."""
    from .app import NAV_ICON_COLOR, _render_svg  # late: app imports this module
    row = QHBoxLayout()
    row.setSpacing(10)
    picture = QLabel()
    picture.setPixmap(_render_svg(icon, color or NAV_ICON_COLOR, size))
    row.addWidget(picture)
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    row.addWidget(label, 1)
    return row, label


class SectionHeader(QWidget):
    """Icon, title, gold subtitle and description on the left; the room's scene painted on the right."""

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        key, subtitle, description = SECTIONS[name]
        self.scene = room_pixmap(f"header_{key}.png")
        self.setFixedHeight(HEADER_HEIGHT)  # never grows with the free space of a short section
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(18)
        self.icon = QLabel()
        self.icon.setPixmap(_fit(room_pixmap(f"icon_{key}.png"), height=76))
        self.icon.setFixedWidth(84)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        self.title = QLabel(name)
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel(f"✦ {subtitle}")
        self.subtitle.setObjectName("roomSubtitle")
        self.description = QLabel(description)
        self.description.setObjectName("roomDescription")
        self.description.setWordWrap(True)
        for label in (self.title, self.subtitle, self.description):
            texts.addWidget(label)
        texts.addStretch(1)
        layout.addLayout(texts, 1)
        self.trailing = QVBoxLayout()  # room for a section's own status (e.g. the recording state)
        layout.addLayout(self.trailing)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.scene.isNull():
            return
        height = self.height()
        width = round(self.scene.width() * height / self.scene.height())
        texts_right = max(label.geometry().x() + label.sizeHint().width()
                          for label in (self.title, self.subtitle, self.description))
        width = min(width, self.width() - texts_right - 16)  # the scene starts where the texts end
        if width < 120:
            return
        source_width = round(width * self.scene.height() / height)
        source = QRectF(self.scene.width() - source_width, 0, source_width, self.scene.height())
        # Draw the visible part off screen and fade its left edge, wherever the cut falls.
        ratio = self.devicePixelRatioF()
        canvas = QPixmap(round(width * ratio), round(height * ratio))
        canvas.setDevicePixelRatio(ratio)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(0, 0, width, height), self.scene, source)
        fade = QLinearGradient(0, 0, min(140, width / 3), 0)
        fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        fade.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(QRectF(0, 0, width, height), fade)
        painter.end()
        painter = QPainter(self)
        painter.drawPixmap(self.width() - width, 0, canvas)
        painter.end()


class RoomStack(QStackedWidget):
    """The sections' stack, painted as Astro's room: night floor glow, plants and books, Brasa asleep."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("roomStack")
        self.left_art = room_pixmap("room_left.png")
        self.right_art = room_pixmap("room_right.png")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(ROOM_COLORS["floor"]))
        glow = QRadialGradient(rect.center().x(), rect.bottom() + 40, max(rect.width(), 1) * 0.6)
        glow.setColorAt(0.0, QColor(91, 43, 184, 90))
        glow.setColorAt(1.0, QColor(91, 43, 184, 0))
        painter.fillRect(rect, glow)
        top = QLinearGradient(0, 0, 0, 220)
        top.setColorAt(0.0, QColor(30, 20, 90, 110))
        top.setColorAt(1.0, QColor(30, 20, 90, 0))
        painter.fillRect(rect, top)
        size = max(120, min(260, rect.height() // 3))
        if not self.left_art.isNull():
            art = _fit(self.left_art, height=round(size * 0.75))
            painter.drawPixmap(8, rect.bottom() - art.height() - 4, art)
        if not self.right_art.isNull():
            art = _fit(self.right_art, height=size)
            painter.drawPixmap(rect.right() - art.width(), rect.bottom() - art.height() - 4, art)
        painter.end()


class SideRail(QFrame):
    """The side menu, with the lantern and plants at its foot."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sideRail")
        self.lantern = room_pixmap("sidebar_lantern.png")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if self.lantern.isNull():
            return
        painter = QPainter(self)
        art = _fit(self.lantern, width=self.width())
        painter.drawPixmap(0, self.height() - art.height(), art)
        painter.end()


class EmptyState(QWidget):
    """An illustrated empty state; text()/setText() behave like the label it replaces."""

    def __init__(self, art: str, text: str = "", hint: str = "", *, art_height: int = 96,
                 parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.art = QLabel()
        self.art.setPixmap(_fit(room_pixmap(art), height=art_height))
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel(text)
        self.label.setObjectName("emptyState")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.hint = QLabel(hint)
        self.hint.setObjectName("emptyHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        for widget in (self.art, self.label, self.hint):
            layout.addWidget(widget)
        layout.addStretch(1)

    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:  # noqa: N802 - mirrors QLabel
        self.label.setText(text)
