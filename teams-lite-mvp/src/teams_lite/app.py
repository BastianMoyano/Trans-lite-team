"""PySide6 desktop application for Teams Lite."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QByteArray, QEvent, QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHeaderView,
    QInputDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .audio import Recorder
from .config import AppConfig, default_config_path, load_config
from .hotkey import (
    MOMENT_HOTKEY_ID,
    MOMENT_HOTKEY_LABEL,
    hotkey_id_from_native_event,
    register_moment_hotkey,
    unregister_moment_hotkey,
)
from .library_ui import (
    THUMBNAIL_SIZE,
    AdminPage,
    ExportOptionsDialog,
    MoveMeetingDialog,
    PlacementPicker,
    PreviewPage,
)
from .chat_ui import ChatPanel
from .minutes import CLI_LABELS, MinutesError, generate_minutes, load_minutes
from .previews import meeting_screenshots
from .mascot import load_mascot_settings, resolve_placement, save_mascot_settings
from .mascot_ui import MascotSettingsPage, MascotWidget
from .moments import capture_moment, format_offset, load_moments, moments_for_minutes
from .ocr import load_participants, merge_participants, read_teams_names
from .sharing import PackageError, export_meeting, import_meeting, read_package, suggest_placement
from .storage import Storage
from .transcription import AudioSource, CapabilityError, export_transcript, transcribe_recordings
from .updates import UpdateInfo, check_for_update
from .visual_speakers import append_observation, apply_visual_speakers, load_observations
from .wevisone import VisualModelUnavailable, WeVisOneDetector, visual_model_problem
from .window_capture import FrameSampler, TeamsWindowUnavailable, capture_teams_frame


COLORS = {
    "background": "#17191C",
    "surface": "#20242A",
    "surface_raised": "#292F37",
    "border": "#3A424C",
    "text": "#F3F5F7",
    "text_soft": "#C7D0D9",
    "text_muted": "#929EAC",
    "text_subtle": "#697684",
    "accent": "#46C7B5",
    "accent_hover": "#5AD7C5",
    "accent_soft": "#173C3A",
    "focus": "#8BE0D2",
    "success": "#63D69A",
    "warning": "#F5B544",
    "danger": "#F87171",
}

APP_STYLESHEET = """
QMainWindow, QScrollArea#pageScroll, QWidget#pageContent {
    background: #17191C;
    color: #F3F5F7;
}
QLabel {
    color: #F3F5F7;
    background: transparent;
}
QLabel#eyebrow {
    color: #46C7B5;
    font-size: 12px;
}
QLabel#pageTitle {
    color: #F3F5F7;
    font-size: 20px;
    font-weight: 700;
}
QFrame#appMark {
    background: #173C3A;
    border: 1px solid #46C7B5;
    border-radius: 8px;
}
QFrame#sideRail {
    background: #121619;
    border-right: 1px solid #3A424C;
}
QLabel#brandTitle {
    color: #F3F5F7;
    font-size: 15px;
    font-weight: 700;
}
QLabel#brandTagline, QLabel#sideFooter {
    color: #697684;
    font-size: 11px;
}
QLabel#statePill {
    padding: 4px 10px;
    background: #12301F;
    border-radius: 6px;
    color: #4ADE80;
    font-size: 12px;
    font-weight: 600;
}
QLabel#statePill[tone="recording"] {
    background: #3A1717;
    color: #F87171;
}
QLabel#statePill[tone="busy"] {
    background: #3A2C12;
    color: #F5B544;
}
QFrame#noticeCard {
    background: #2A2116;
    border: 1px solid #4A3A22;
    border-radius: 8px;
}
QLabel#noticeText {
    color: #D9CBB4;
    font-size: 13px;
}
QFrame#updateCard {
    background: #13233A;
    border: 1px solid #24426B;
    border-radius: 8px;
}
QLabel#updateText {
    color: #CFE0F5;
    font-size: 13px;
}
QFrame#card {
    background: #20242A;
    border: 1px solid #3A424C;
    border-radius: 12px;
}
QLabel#sectionTitle {
    color: #F3F5F7;
    font-size: 16px;
    font-weight: 600;
}
QLabel#adminSummary {
    padding: 8px 12px;
    background: #172A2A;
    border: 1px solid #23413E;
    border-radius: 8px;
    color: #9EE6DA;
    font-size: 12px;
    font-weight: 600;
}
QFrame#mascotBody {
    background: #13201F;
    border: 1px solid #2D5A54;
    border-radius: 24px;
}
QPushButton#mascotRecord, QPushButton#mascotAction {
    min-height: 32px;
    padding: 0 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#mascotRecord {
    background: #46C7B5;
    border: none;
    color: #0F1B1C;
}
QPushButton#mascotRecord[recording="true"] {
    background: #F87171;
    color: #1A0E0E;
}
QPushButton#mascotAction {
    background: #1E2E2D;
    border: 1px solid #2D5A54;
    color: #DCEFEC;
}
QPushButton#mascotAction:disabled {
    color: #5E6E6C;
    border-color: #22302F;
}
QLabel#mascotTimer {
    color: #9EE6DA;
    font-size: 12px;
    font-weight: 600;
}
QLabel#historyScope {
    color: #46C7B5;
    font-size: 12px;
    font-weight: 600;
}
QLabel#sectionMeta, QLabel#fieldHint, QLabel#statusText {
    color: #929EAC;
    font-size: 12px;
}
QFrame#divider {
    background: #3A424C;
    max-height: 1px;
    min-height: 1px;
    border: none;
}
QLineEdit {
    min-height: 42px;
    padding: 0 12px;
    background: #17191C;
    border: 1px solid #3A424C;
    border-radius: 8px;
    color: #F3F5F7;
    font-size: 14px;
    selection-background-color: #3B3B3B;
}
QLineEdit:focus {
    border: 2px solid #8BE0D2;
}
QPushButton {
    min-height: 42px;
    padding: 0 18px;
    border-radius: 8px;
    border: 1px solid #3A424C;
    background: #20242A;
    color: #F3F5F7;
    font-size: 14px;
}
QPushButton:hover {
    background: #292F37;
    border-color: #53606D;
}
QPushButton:pressed {
    background: #173C3A;
}
QPushButton:disabled {
    color: #6B6B6B;
    background: #171717;
    border-color: #262626;
}
QPushButton#primaryButton {
    background: #46C7B5;
    border-color: #46C7B5;
    color: #10201E;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: #5AD7C5;
    border-color: #5AD7C5;
}
QPushButton#dangerButton:enabled {
    background: #2A1515;
    border-color: #5A2626;
    color: #F87171;
}
QPushButton#dangerButton:hover:enabled {
    background: #341A1A;
}
QPushButton#ghostButton, QPushButton#deleteButton {
    min-height: 36px;
    padding: 0 10px;
    border: none;
    background: transparent;
    color: #C7D0D9;
    font-size: 13px;
}
QPushButton#deleteButton {
    color: #F87171;
}
QPushButton#ghostButton:hover:enabled {
    background: #292F37;
}
QPushButton#deleteButton:hover:enabled {
    background: #2A1515;
}
QPushButton#ghostButton:disabled, QPushButton#deleteButton:disabled {
    background: transparent;
    color: #5A5A5A;
}
QCheckBox {
    color: #C7D0D9;
    spacing: 8px;
    font-size: 13px;
}
QLabel#statusBadge {
    padding: 2px 8px;
    border-radius: 4px;
    background: #12301F;
    color: #4ADE80;
    font-size: 12px;
}
QLabel#statusBadge[tone="recording"] {
    background: #3A1717;
    color: #F87171;
}
QLabel#statusBadge[tone="interrupted"] {
    background: #3A2C12;
    color: #F5B544;
}
QLabel#meetingTitle {
    color: #EDEDED;
    font-size: 13px;
    font-weight: 600;
}
QLabel#meetingDate {
    color: #8F8F8F;
    font-size: 12px;
}
QLabel#meetingId, QLabel#monoMuted {
    color: #8F8F8F;
    font-family: "{mono_family}";
    font-size: 11px;
}
QTableWidget {
    background: transparent;
    border: none;
    color: #EDEDED;
    outline: none;
}
QTableWidget#meetingList::item {
    border-left: 2px solid transparent;
}
QTableWidget#meetingList::item:selected {
    background: #173C3A;
    border-left: 3px solid #46C7B5;
}
QTableWidget#meetingList::item:hover:!selected {
    background: #1F1F1F;
}
QTableWidget#transcriptTable::item {
    padding: 10px 6px;
    border-bottom: 1px solid #242424;
}
QTableWidget#transcriptTable::item:selected {
    background: #232323;
    color: #EDEDED;
}
QListWidget#sideNav {
    background: transparent;
    border: none;
    padding: 8px 0;
    color: #C7D0D9;
    font-size: 14px;
    outline: none;
}
QListWidget#sideNav::item {
    padding: 0 12px;
    border-radius: 8px;
    border-left: 3px solid transparent;
}
QListWidget#sideNav::item:selected {
    background: #173C3A;
    color: #F3F5F7;
    border-left: 3px solid #46C7B5;
    font-weight: 600;
}
QListWidget#sideNav::item:hover:!selected {
    background: #20242A;
}
QListWidget#previewGrid, QListWidget#previewMeetings, QListWidget#adminList {
    background: transparent;
    border: none;
    color: #EDEDED;
    font-size: 13px;
    outline: none;
}
QListWidget#previewGrid::item {
    padding: 10px;
    border: 1px solid #2E2E2E;
    border-radius: 10px;
    background: #1A1A1A;
}
QListWidget#previewGrid::item:hover {
    background: #232323;
    border-color: #3A3A3A;
}
QListWidget#previewMeetings::item, QListWidget#adminList::item {
    padding: 6px 8px;
    border-radius: 8px;
}
QListWidget#previewMeetings::item:selected, QListWidget#adminList::item:selected {
    background: #232323;
    color: #EDEDED;
}
QListWidget#previewMeetings::item:hover:!selected, QListWidget#adminList::item:hover:!selected {
    background: #1F1F1F;
}
QPushButton#iconButton {
    min-width: 36px;
    max-width: 36px;
    padding: 0;
    border: none;
    background: transparent;
}
QPushButton#iconButton:hover {
    background: #232323;
}
QPushButton#iconButton:disabled {
    background: transparent;
}
QPushButton#crumb {
    min-height: 28px;
    padding: 0 6px;
    border: none;
    background: transparent;
    color: #8F8F8F;
    font-size: 13px;
}
QPushButton#crumb:hover {
    color: #EDEDED;
}
QPushButton#crumb:disabled {
    color: #EDEDED;
    background: transparent;
    font-weight: 600;
}
QLabel#emptyState {
    color: #6B6B6B;
    font-size: 13px;
}
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 2px;
}
QScrollBar::handle:vertical {
    min-height: 32px;
    background: #3A3A3A;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background: #4A4A4A;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QMessageBox {
    background: #1A1A1A;
}
QPushButton#segmentButton {
    min-height: 30px;
    padding: 0 14px;
    border: 1px solid #3A424C;
    border-radius: 6px;
    background: transparent;
    color: #929EAC;
    font-size: 13px;
}
QPushButton#segmentButton:checked {
    background: #173C3A;
    border-color: #46C7B5;
    color: #8BE0D2;
    font-weight: 600;
}
QPushButton#segmentButton:hover:!checked {
    color: #D4D4D4;
}
QPushButton#minutesButton {
    min-height: 32px;
    padding: 0 14px;
    border: 1px solid #46C7B5;
    border-radius: 6px;
    background: #46C7B5;
    color: #10201E;
    font-weight: 600;
    font-size: 13px;
}
QPushButton#minutesButton:hover:enabled {
    background: #5AD7C5;
}
QPushButton#minutesButton:disabled {
    background: #232323;
    border-color: #2E2E2E;
    color: #6B6B6B;
}
QComboBox {
    min-height: 32px;
    padding: 0 10px;
    border: 1px solid #3A3A3A;
    border-radius: 6px;
    background: #141414;
    color: #EDEDED;
    font-size: 13px;
}
QComboBox:disabled {
    color: #6B6B6B;
}
QComboBox QAbstractItemView {
    background: #1A1A1A;
    border: 1px solid #3A3A3A;
    color: #EDEDED;
    selection-background-color: #2A2A2A;
}
QTextBrowser#minutesView {
    background: transparent;
    border: none;
    color: #EDEDED;
    font-size: 13px;
}
QTextBrowser#chatHistory {
    background: #17191C;
    border: 1px solid #3A424C;
    border-radius: 8px;
    padding: 8px;
    color: #F3F5F7;
    font-size: 13px;
}
QPushButton#ghostButton:checked {
    background: #173C3A;
    border: 1px solid #46C7B5;
    color: #8BE0D2;
}
"""

MINUTES_DOCUMENT_CSS = """
h1 { font-size: 20px; font-weight: 700; margin: 4px 0 10px 0; }
h2 { font-size: 15px; font-weight: 600; margin: 16px 0 6px 0; color: #EDEDED; }
p, li { color: #D4D4D4; }
table { border-collapse: collapse; margin: 6px 0; }
th { background: #232323; color: #EDEDED; padding: 6px 10px; border: 1px solid #3A3A3A; }
td { color: #D4D4D4; padding: 6px 10px; border: 1px solid #2E2E2E; }
"""
_CLI_LABELS = CLI_LABELS

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{stroke}" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)
_ICON_PATHS = {
    "app": '<rect x="3" y="4" width="18" height="16" rx="3"/><polygon points="10 9 15 12 10 15 10 9"/>',
    "play": '<polygon points="7 4 19 12 7 20 7 4"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
    "warning": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "copy": (
        '<rect x="8" y="8" width="13" height="13" rx="2"/>'
        '<path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'
    ),
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
    "share": (
        '<path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/>'
        '<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>'
    ),
    "image": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
        '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>'
    ),
    "smile": (
        '<circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/>'
        '<line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>'
    ),
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "layers": (
        '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/>'
        '<polyline points="2 12 12 17 22 12"/>'
    ),
    "pencil": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4'
        'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    ),
}
_FILLED_ICONS = {"stop"}
OCR_INTERVAL_SECONDS = 60
HOTKEY_RETRY_SECONDS = 5
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_STATUS_LABELS = {"recording": "Grabando", "completed": "Completada", "interrupted": "Interrumpida"}
_TRANSCRIPT_LINE = re.compile(
    r"^\[(?P<start>[\d:.]+)(?:\s*-\s*(?P<end>[\d:.]+))?\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
)


_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Courier New")
_mono_family: str | None = None


def mono_font_family() -> str:
    """One installed monospace family.

    Never hand Qt a fallback list ('"Cascadia Mono", "Consolas"' in a stylesheet): that made it
    load the whole font database, +230 MB of RAM when the window was shown.
    """
    global _mono_family
    if _mono_family is None:
        installed = [family for family in _MONO_CANDIDATES if QFontDatabase.hasFamily(family)]
        system_fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        if not installed and QFontDatabase.hasFamily(system_fixed):
            installed = [system_fixed]
        if not installed:  # generic names such as "monospace" are fallback requests too
            installed = [family for family in QFontDatabase.families() if QFontDatabase.isFixedPitch(family)]
        if not installed:
            installed = QFontDatabase.families()[:1]
        _mono_family = installed[0] if installed else QFont().family()
    return _mono_family


def _confirmation_beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        pass


def _render_svg(name: str, color: str, size: int) -> QPixmap:
    fill = color if name in _FILLED_ICONS else "none"
    svg = _SVG_TEMPLATE.format(fill=fill, stroke=color, body=_ICON_PATHS[name])
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _with_gap(pixmap: QPixmap, gap: int) -> QPixmap:
    """Pad an icon on the right so button text does not touch it."""
    scale = pixmap.devicePixelRatio()
    padded = QPixmap(pixmap.width() + round(gap * scale), pixmap.height())
    padded.setDevicePixelRatio(scale)
    padded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(padded)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return padded


def set_button_icon(button: QPushButton, name: str, color: str, size: int = 14, gap: int = 8) -> None:
    icon = QIcon()
    icon.addPixmap(_with_gap(_render_svg(name, color, size), gap), QIcon.Mode.Normal)
    icon.addPixmap(_with_gap(_render_svg(name, COLORS["text_subtle"], size), gap), QIcon.Mode.Disabled)
    button.setIcon(icon)
    button.setIconSize(QSize(size + gap, size))


def _icon_label(name: str, color: str, size: int = 16) -> QLabel:
    label = QLabel()
    label.setPixmap(_render_svg(name, color, size))
    label.setFixedSize(size, size)
    return label


def _checkbox_stylesheet() -> str:
    """Draw checkboxes ourselves so the Windows accent color never leaks in."""
    rules = """
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #53606D;
    background: #17191C;
}
QCheckBox::indicator:hover {
    border-color: #8BE0D2;
}
QCheckBox::indicator:checked {
    background: #46C7B5;
    border-color: #46C7B5;
    %s
}
QCheckBox::indicator:disabled {
    border-color: #2E2E2E;
    background: #1A1A1A;
}
"""
    check_svg = _SVG_TEMPLATE.format(fill="none", stroke="#10201E", body='<path d="M5 12.5l4.5 4.5L19 7.5"/>')
    try:
        path = Path(tempfile.gettempdir()) / "teams-lite-ui" / "check.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != check_svg:
            path.write_text(check_svg, encoding="utf-8")
        image = f'image: url("{path.as_posix()}");'
    except OSError:
        image = ""
    return rules % image


def _dark_palette() -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: COLORS["background"],
        QPalette.ColorRole.WindowText: COLORS["text"],
        QPalette.ColorRole.Base: COLORS["surface"],
        QPalette.ColorRole.AlternateBase: COLORS["surface_raised"],
        QPalette.ColorRole.Text: COLORS["text"],
        QPalette.ColorRole.Button: COLORS["surface"],
        QPalette.ColorRole.ButtonText: COLORS["text"],
        QPalette.ColorRole.Highlight: "#E6E6E6",
        QPalette.ColorRole.Accent: "#E6E6E6",
        QPalette.ColorRole.HighlightedText: COLORS["background"],
        QPalette.ColorRole.ToolTipBase: COLORS["surface_raised"],
        QPalette.ColorRole.ToolTipText: COLORS["text"],
        QPalette.ColorRole.PlaceholderText: COLORS["text_subtle"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    return palette


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return card, layout


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def format_meeting_date(started_utc: str) -> str:
    try:
        moment = datetime.fromisoformat(started_utc)
    except (TypeError, ValueError):
        return str(started_utc)
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year} · {moment:%H:%M:%S} UTC"


def short_id(value: str, head: int = 8, tail: int = 8) -> str:
    return value if len(value) <= head + tail + 1 else f"{value[:head]}…{value[-tail:]}"


def _parse_clock(value: str) -> float:
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def format_clock(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    clock = f"{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours}:{clock}" if hours else clock


def load_transcript_turns(meeting_dir: Path) -> list[dict]:
    """Return transcript turns from transcript.json, falling back to transcript.txt."""
    json_path = meeting_dir / "transcript.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return [
                {
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "speaker": str(item["speaker"]),
                    "text": str(item["text"]),
                }
                for item in data
            ]
        except (ValueError, KeyError, TypeError):
            pass
    turns = []
    for raw_line in (meeting_dir / "transcript.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TRANSCRIPT_LINE.match(line)
        if match is None:
            turns.append({"start": None, "end": None, "speaker": "", "text": line})
            continue
        turns.append({
            "start": _parse_clock(match["start"]),
            "end": _parse_clock(match["end"]) if match["end"] else None,
            "speaker": match["speaker"].strip(),
            "text": match["text"],
        })
    return turns


class AppSignals(QObject):
    status = Signal(str)
    recording_stopped = Signal(str)
    transcript_ready = Signal(str)
    failure = Signal(str)
    ocr_ready = Signal(str)
    visual_warning = Signal(str)
    minutes_ready = Signal(str)
    minutes_failed = Signal(str, str)
    moment_marked = Signal(object)
    transcription_progress = Signal(str, float)
    transcription_finished = Signal(str)
    update_available = Signal(object)
    library_task_done = Signal(str)


_MEETING_ROLE = Qt.ItemDataRole.UserRole + 1
_BADGE_COLORS = {
    "completed": ("#12301F", "#4ADE80"),
    "recording": ("#3A1717", "#F87171"),
    "interrupted": ("#3A2C12", "#F5B544"),
}
_PROGRESS_COLORS = ("#14283D", "#60A5FA")


class MeetingDelegate(QStyledItemDelegate):
    """Paints one recent-recording card per row.

    It replaces a QWidget per meeting: each styled widget took ~13 ms to create, so 300
    meetings added ~4 s to startup. Painting is a few milliseconds for the whole list.
    """

    ROW_HEIGHT = 104

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_font = QFont()
        self.title_font.setPixelSize(13)
        self.title_font.setBold(True)
        self.meta_font = QFont()
        self.meta_font.setPixelSize(12)
        self.mono_font = QFont(mono_font_family())
        self.mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.mono_font.setPixelSize(11)

    def sizeHint(self, option, index):  # noqa: N802 - Qt API
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        meeting = index.data(_MEETING_ROLE)
        if not meeting:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(COLORS["surface_raised"]))
            painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()), QColor(COLORS["text"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor("#1F1F1F"))

        left, top = rect.left() + 14, rect.top() + 12
        status = str(meeting["status"])
        progress = meeting.get("transcription_progress")
        badge_text = (
            f"Transcribiendo {int(progress * 100)} %" if progress is not None
            else _STATUS_LABELS.get(status, status)
        )
        badge_metrics = QFontMetrics(self.meta_font)
        badge_width = badge_metrics.horizontalAdvance(badge_text) + 16
        title_metrics = QFontMetrics(self.title_font)
        title = title_metrics.elidedText(
            str(meeting["title"]), Qt.TextElideMode.ElideRight, max(40, rect.width() - 36 - badge_width)
        )
        painter.setFont(self.title_font)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(left, top + title_metrics.ascent() + 2, title)

        badge = QRect(left + title_metrics.horizontalAdvance(title) + 8, top, badge_width, 22)
        background, foreground = (
            _PROGRESS_COLORS if progress is not None
            else _BADGE_COLORS.get(status, ("#232323", COLORS["text_muted"]))
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setFont(self.meta_font)
        painter.setPen(QColor(foreground))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)

        painter.setPen(QColor(COLORS["text_muted"]))
        painter.drawText(left, top + 46, format_meeting_date(meeting["started_utc"]))
        painter.setFont(self.mono_font)
        painter.drawText(left, top + 74, short_id(str(meeting["id"])))
        if progress is not None:
            track = QRect(left, rect.bottom() - 9, max(0, rect.width() - 28), 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["border"]))
            painter.drawRoundedRect(track, 2, 2)
            painter.setBrush(QColor(_PROGRESS_COLORS[1]))
            painter.drawRoundedRect(QRect(track.left(), track.top(), int(track.width() * progress), 4), 2, 2)
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.store = Storage(config.data_dir)
        self.meeting_id: str | None = None
        self.recorder: Recorder | None = None
        self.visual_sampler: FrameSampler | None = None
        self.ocr_sampler: FrameSampler | None = None
        self._busy = False
        self._meetings_signature: tuple | None = None
        self._transcript_raw = ""
        self._attendees: list[str] = []
        self._attendee_lock = threading.Lock()
        self._minutes: str | None = None
        self._minutes_jobs: set[str] = set()
        self._view = "transcript"
        self._moments: list[dict] = []
        self._recording_origin: float | None = None
        self._moment_count = 0
        self._audio_warnings_shown = 0
        self._hotkey_registered = False
        self._ocr_window_warned = False
        self._hotkey_unavailable_logged = False
        self._hotkey_retry_timer = QTimer(self)
        self._hotkey_retry_timer.setInterval(HOTKEY_RETRY_SECONDS * 1000)
        self._hotkey_retry_timer.timeout.connect(self._acquire_moment_hotkey)
        self._transcription_progress: dict[str, float] = {}
        self._update: UpdateInfo | None = None
        self.signals = AppSignals()
        self.signals.status.connect(self._set_status)
        self.signals.recording_stopped.connect(self._after_stop)
        self.signals.transcript_ready.connect(self._transcript_done)
        self.signals.failure.connect(self._show_error)
        self.signals.ocr_ready.connect(self._ocr_done)
        self.signals.minutes_ready.connect(self._minutes_done)
        self.signals.minutes_failed.connect(self._minutes_failed)
        self.signals.moment_marked.connect(self._moment_done)
        self.signals.visual_warning.connect(lambda message: self._set_status(message, tone="warning"))
        self.signals.transcription_progress.connect(self._on_transcription_progress)
        self.signals.transcription_finished.connect(self._on_transcription_finished)
        self.signals.update_available.connect(self.show_update)
        self.signals.library_task_done.connect(self._library_task_done)
        self.setWindowTitle("Teams Lite")
        # The 190 px side menu sits next to the original 1000 x 780 page.
        self.resize(1190, 780)
        self.setMinimumSize(1010, 600)
        self.setPalette(_dark_palette())
        self.setStyleSheet(APP_STYLESHEET.replace("{mono_family}", mono_font_family()) + _checkbox_stylesheet())
        self._build_ui()
        self._refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2_000)

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("pageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(18)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_update_banner())
        layout.addWidget(self._build_notice())
        # Two halves: recording and recent recordings on the left, the transcript over the
        # full height on the right. Narrow windows stack them (see resizeEvent).
        self.recording_card = self._build_recording_card()
        self.history_card = self._build_history_card()
        self.transcript_card = self._build_transcript_card()
        left_column = QWidget()
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(16)
        left_layout.addWidget(self.recording_card)
        left_layout.addWidget(self.history_card, 1)
        self.overview_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.overview_columns.setSpacing(16)
        for half in (left_column, self.transcript_card):
            policy = half.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            half.setSizePolicy(policy)
        self.overview_columns.addWidget(left_column, 1)
        self.overview_columns.addWidget(self.transcript_card, 1)
        layout.addLayout(self.overview_columns, 1)
        scroll.setWidget(content)
        self.recording_page = scroll
        self.preview_page = self._build_preview_page()
        self.admin_page = self._build_admin_page()
        self.mascot_page = self._build_mascot()

        self.pages = QStackedWidget()
        self.side_nav = QListWidget()
        self.side_nav.setObjectName("sideNav")
        self.side_nav.setFrameShape(QFrame.Shape.NoFrame)
        self.side_nav.setIconSize(QSize(16, 16))
        for label, icon, page in (
            ("Grabar", "app", self.recording_page),
            ("Biblioteca", "image", self.preview_page),
            ("Organización", "folder", self.admin_page),
            ("Mascota", "smile", self.mascot_page),
        ):
            item = QListWidgetItem(QIcon(_render_svg(icon, COLORS["accent"], 16)), label)
            item.setSizeHint(QSize(0, 44))
            self.side_nav.addItem(item)
            self.pages.addWidget(page)
        self.side_nav.currentRowChanged.connect(self._show_section)
        self.side_nav.setCurrentRow(0)

        sidebar = QFrame()
        sidebar.setObjectName("sideRail")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 14)
        sidebar_layout.setSpacing(12)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand_mark = QFrame()
        brand_mark.setObjectName("appMark")
        brand_mark.setFixedSize(36, 36)
        brand_mark_layout = QVBoxLayout(brand_mark)
        brand_mark_layout.setContentsMargins(0, 0, 0, 0)
        brand_mark_layout.addWidget(
            _icon_label("app", COLORS["accent"], 16), 0, Qt.AlignmentFlag.AlignCenter
        )
        brand.addWidget(brand_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
        self.brand_title = QLabel("Teams Lite")
        self.brand_title.setObjectName("brandTitle")
        self.brand_tagline = QLabel("Memoria de tus reuniones")
        self.brand_tagline.setObjectName("brandTagline")
        brand_copy.addWidget(self.brand_title)
        brand_copy.addWidget(self.brand_tagline)
        brand.addLayout(brand_copy, 1)
        sidebar_layout.addLayout(brand)
        sidebar_layout.addWidget(self.side_nav, 1)
        footer = QLabel(f"v{__version__} · datos locales")
        footer.setObjectName("sideFooter")
        sidebar_layout.addWidget(footer)

        shell = QWidget()
        shell.setObjectName("pageContent")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(self.pages, 1)
        self.setCentralWidget(shell)

        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.ocr_button.clicked.connect(self.capture_ocr)
        self.ocr_checkbox.toggled.connect(self._sync_ocr_button)
        self.table.itemSelectionChanged.connect(self.show_selected_transcript)
        self.delete_button.clicked.connect(self.delete_selected_meeting)
        self.rename_button.clicked.connect(lambda: self.rename_meeting())
        self.placement_picker.organization_combo.currentIndexChanged.connect(lambda _index: self._refresh())
        self.placement_picker.project_combo.currentIndexChanged.connect(lambda _index: self._refresh())
        self.move_button.clicked.connect(lambda: self.move_meeting())
        self.export_button.clicked.connect(lambda: self.export_meeting())
        self.copy_button.clicked.connect(self.copy_transcript)
        self.download_button.clicked.connect(self.download_transcript)
        self.generate_minutes_button.clicked.connect(self.generate_minutes_for_selected)
        self.mark_moment_button.clicked.connect(lambda: self.mark_moment())
        self.moment_note_input.returnPressed.connect(lambda: self.mark_moment())
        self.transcript_table.cellDoubleClicked.connect(self._open_moment_image)
        self.view_transcript_button.clicked.connect(lambda: self._set_view("transcript"))
        self.view_minutes_button.clicked.connect(lambda: self._set_view("minutes"))
        self.view_chat_button.clicked.connect(lambda: self._set_view("chat"))
        self.view_gallery_button.clicked.connect(lambda: self._set_view("gallery"))

    def resizeEvent(self, event) -> None:
        """Stack overview cards when the window is too narrow to keep both readable."""
        super().resizeEvent(event)
        if hasattr(self, "overview_columns"):
            direction = (
                QBoxLayout.Direction.TopToBottom
                if event.size().width() < 1120
                else QBoxLayout.Direction.LeftToRight
            )
            self.overview_columns.setDirection(direction)

    def _build_preview_page(self) -> QWidget:
        placeholder = QPixmap(THUMBNAIL_SIZE)
        placeholder.fill(QColor(COLORS["surface_raised"]))
        painter = QPainter(placeholder)
        icon = _render_svg("image", COLORS["text_subtle"], 28)
        painter.drawPixmap((placeholder.width() - icon.width()) // 2, (placeholder.height() - icon.height()) // 2, icon)
        painter.end()
        page = PreviewPage(
            self.store,
            folder_icon=_render_svg("folder", COLORS["text_muted"], 72),
            placeholder=placeholder,
            describe=lambda meeting: (
                f"{format_meeting_date(meeting['started_utc'])} · "
                f"{_STATUS_LABELS.get(str(meeting['status']), str(meeting['status']))}"
            ),
            default_cli=self.config.minutes_cli,
        )
        page.open_meeting.connect(self.open_meeting)
        page.rename_meeting.connect(self.rename_meeting)
        page.move_meeting.connect(self.move_meeting)
        page.export_meeting.connect(self.export_meeting)
        page.import_requested.connect(self.import_meeting_package)
        return page

    def _build_admin_page(self) -> QWidget:
        page = AdminPage(self.store, icons={
            "organization": _render_svg("folder", COLORS["accent"], 16),
            "project": _render_svg("layers", COLORS["text_soft"], 16),
            "add": _render_svg("plus", COLORS["background"], 14),
            "rename": _render_svg("pencil", COLORS["text_soft"], 14),
            "delete": _render_svg("trash", COLORS["danger"], 14),
        })
        page.changed.connect(self._library_changed)
        return page

    def _build_mascot(self) -> QWidget:
        """The floating mascot and its Mascota section, both driven by data/mascot.json."""
        settings = load_mascot_settings(self.store.root)
        self.mascot = MascotWidget()
        self.mascot.setStyleSheet(self.styleSheet())  # a separate top-level window: it does not inherit it
        self.mascot.record_requested.connect(self._toggle_recording_from_mascot)
        self.mascot.capture_requested.connect(lambda: self.mark_moment(from_hotkey=True))
        self.mascot.ocr_requested.connect(lambda: self.capture_ocr())
        self.mascot.open_requested.connect(self._open_from_mascot)
        self.mascot.moved.connect(self._mascot_moved)
        self.mascot.adjustSize()
        if settings.position is not None:
            self.mascot.move(*settings.position)
        else:
            self._place_mascot_by_default()
        self.mascot.setVisible(settings.enabled)
        page = MascotSettingsPage(self.store, settings)
        page.changed.connect(self._mascot_settings_changed)
        return page

    def _place_mascot_by_default(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.mascot.move(area.right() - self.mascot.width() - 24, area.bottom() - self.mascot.height() - 24)

    def _toggle_recording_from_mascot(self) -> None:
        if self.recorder is not None:
            self.stop_recording()
        else:
            self.quick_start_recording()

    def _open_from_mascot(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _mascot_moved(self, x: int, y: int) -> None:
        settings = self.mascot_page.settings()
        settings.position = (x, y)
        save_mascot_settings(self.store.root, settings)

    def _mascot_settings_changed(self, settings) -> None:
        save_mascot_settings(self.store.root, settings)
        self.mascot.setVisible(settings.enabled)
        if settings.position is None:
            self._place_mascot_by_default()

    def _library_changed(self) -> None:
        self.mascot_page.reload_placements()
        self.placement_picker.reload()
        self.preview_page.refresh()
        self._refresh()

    def _ask_meeting_title(self, current: str) -> str | None:
        title, accepted = QInputDialog.getText(self, "Renombrar reunión", "Nuevo nombre de la reunión:", text=current)
        return title if accepted else None

    def rename_meeting(self, meeting_id: str | None = None) -> None:
        """Changes the meeting title; its folder on disk keeps its name."""
        meeting = self.store.get_meeting(meeting_id or self._selected_meeting_id() or "")
        if meeting is None:
            return
        title = self._ask_meeting_title(meeting["title"])
        if title is None:
            return
        try:
            self.store.rename_meeting(meeting["id"], title)
        except ValueError as exc:
            self._show_error(f"No se pudo renombrar la reunión: {exc}")
            return
        self.preview_page.refresh()
        self._refresh()
        self._sync_chat_scope()
        self._set_status("Reunión renombrada.", tone="success")

    def _choose_placement(self, meeting: dict) -> tuple[str | None, str | None] | None:
        dialog = MoveMeetingDialog(self.store, meeting["organization_id"], meeting["project_id"], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.placement()

    def move_meeting(self, meeting_id: str | None = None) -> None:
        """Files a meeting in another organization or project, or back in the vault."""
        meeting_id = meeting_id or self._selected_meeting_id()
        meeting = next((row for row in self.store.list_meetings() if row["id"] == meeting_id), None)
        if meeting is None:
            return
        placement = self._choose_placement(meeting)
        if placement is None:
            return
        try:
            self.store.move_meeting(meeting_id, *placement)
        except ValueError as exc:
            self._show_error(f"No se pudo mover la reunión: {exc}")
            return
        was_selected = meeting_id == self._selected_meeting_id()
        self.preview_page.refresh()
        if was_selected:
            self._show_placement_of(meeting_id)
        self._refresh()
        if was_selected:
            self._select_meeting_row(meeting_id)
        self._set_status("Reunión movida.", tone="success")

    def _choose_export(self, meeting: dict) -> tuple[Path, bool] | None:
        options = ExportOptionsDialog(self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return None
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar reunión", str(Path.home() / f"{meeting['id']}.zip"), "Paquete de reunión (*.zip)"
        )
        return (Path(path), options.include_audio()) if path else None

    def export_meeting(self, meeting_id: str | None = None) -> None:
        """Writes a shareable .zip package of a meeting in the background."""
        meeting = self.store.get_meeting(meeting_id or self._selected_meeting_id() or "")
        if meeting is None:
            return
        if meeting["status"] == "recording":
            self._show_error("Detén la grabación antes de exportar esta reunión.")
            return
        choice = self._choose_export(meeting)
        if choice is None:
            return
        path, include_audio = choice
        self._set_status("Exportando la reunión…")

        def work() -> None:
            try:
                written = export_meeting(self.store, meeting["id"], path, include_audio=include_audio)
            except Exception as exc:
                self.signals.failure.emit(f"No se pudo exportar la reunión: {exc}")
                return
            self.signals.library_task_done.emit(f"Exportada · {written}")

        threading.Thread(target=work, name="meeting-export", daemon=True).start()

    def _choose_import_file(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar reunión", str(Path.home()), "Paquete de reunión (*.zip)"
        )
        return Path(path) if path else None

    def _choose_import_placement(
        self, organization_id: str | None, project_id: str | None, manifest: dict
    ) -> tuple[str | None, str | None] | None:
        origin = " › ".join(name for name in (manifest.get("organization"), manifest.get("project")) if name)
        dialog = MoveMeetingDialog(
            self.store, organization_id, project_id, self,
            title="Importar reunión", action="Importar",
            note=f'"{manifest["meeting"]["title"]}"' + (f" venía de {origin}." if origin else "."),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.placement()

    def import_meeting_package(self) -> None:
        """Imports a meeting package into the place the user picks, in the background."""
        path = self._choose_import_file()
        if path is None:
            return
        try:
            manifest = read_package(path)
        except PackageError as exc:
            self._show_error(f"No se pudo importar: {exc}")
            return
        browsing = self.preview_page.current_placement()
        organization_id, project_id = browsing if browsing is not None else suggest_placement(self.store, manifest)
        placement = self._choose_import_placement(organization_id, project_id, manifest)
        if placement is None:
            return
        self._set_status("Importando la reunión…")

        def work() -> None:
            try:
                import_meeting(self.store, path, *placement)
            except Exception as exc:
                self.signals.failure.emit(f"No se pudo importar la reunión: {exc}")
                return
            self.signals.library_task_done.emit(f'Reunión importada · {manifest["meeting"]["title"]}')

        threading.Thread(target=work, name="meeting-import", daemon=True).start()

    def _library_task_done(self, message: str) -> None:
        self.preview_page.refresh()
        self._refresh()
        self._set_status(message, tone="success")

    def _placement_label(self) -> str:
        picker = self.placement_picker
        if picker.organization_id() is None:
            return picker.organization_combo.currentText()
        return f"{picker.organization_combo.currentText()} › {picker.project_combo.currentText()}"

    def _show_placement_of(self, meeting_id: str) -> None:
        """Recent recordings follow the meeting: switch to the project it is filed in."""
        meeting = self.store.get_meeting(meeting_id)
        if meeting is not None:
            self.placement_picker.set_placement(meeting["organization_id"], meeting["project_id"])

    def _select_meeting_row(self, meeting_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == meeting_id:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                return

    def open_meeting(self, meeting_id: str) -> None:
        """Shows a meeting picked in another section on the Teams Lite page."""
        self.side_nav.setCurrentRow(0)
        self._show_placement_of(meeting_id)
        self._refresh()
        self._select_meeting_row(meeting_id)

    def _show_section(self, row: int) -> None:
        if row < 0:
            return
        self.pages.setCurrentIndex(row)
        if self.pages.currentWidget() is self.preview_page:
            self.preview_page.refresh()  # screenshots may have been added since the last visit

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(14)
        mark = QFrame()
        mark.setObjectName("appMark")
        mark.setFixedSize(40, 40)
        mark_layout = QVBoxLayout(mark)
        mark_layout.setContentsMargins(0, 0, 0, 0)
        mark_layout.addWidget(_icon_label("app", COLORS["accent"], 18), 0, Qt.AlignmentFlag.AlignCenter)
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        eyebrow = QLabel("GRABACIÓN Y MEMORIA DE REUNIONES")
        eyebrow.setObjectName("eyebrow")
        page_title = QLabel("Grabar reunión")
        page_title.setObjectName("pageTitle")
        titles.addWidget(eyebrow)
        titles.addWidget(page_title)
        header.addLayout(titles)
        header.addStretch(1)

        self.state_pill = QLabel()
        self.state_pill.setObjectName("statePill")
        self.state_pill.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.state_pill, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def _build_update_banner(self) -> QFrame:
        self.update_banner = QFrame()
        self.update_banner.setObjectName("updateCard")
        banner_layout = QHBoxLayout(self.update_banner)
        banner_layout.setContentsMargins(16, 10, 12, 10)
        banner_layout.setSpacing(12)
        banner_layout.addWidget(_icon_label("info", "#60A5FA"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_text = QLabel()
        self.update_text.setObjectName("updateText")
        self.update_text.setTextFormat(Qt.TextFormat.RichText)
        self.update_text.setWordWrap(True)
        banner_layout.addWidget(self.update_text, 1)
        self.update_button = QPushButton("Descargar actualización")
        self.update_button.setObjectName("primaryButton")
        set_button_icon(self.update_button, "download", "#0B0B0B", gap=6)
        self.update_dismiss_button = QPushButton("Ahora no")
        self.update_dismiss_button.setObjectName("ghostButton")
        banner_layout.addWidget(self.update_button)
        banner_layout.addWidget(self.update_dismiss_button)
        self.update_button.clicked.connect(self._open_update)
        self.update_dismiss_button.clicked.connect(self.update_banner.hide)
        self.update_banner.hide()
        return self.update_banner

    def check_for_updates_async(self) -> None:
        """Ask GitHub for a newer release off the UI thread; silent when offline."""
        def work() -> None:
            info = check_for_update(__version__)
            if info is not None:
                self.signals.update_available.emit(info)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def show_update(self, info: UpdateInfo) -> None:
        self._update = info
        self.update_text.setText(
            f"<b style='color:#EAF2FC'>Hay una nueva versión de Teams Lite: {info.version}.</b> "
            f"Tienes la {__version__}. El instalador actualiza encima y conserva tus reuniones, "
            "la configuración y el modelo."
        )
        self.update_banner.show()

    def _open_update(self) -> None:
        if self._update is not None:
            QDesktopServices.openUrl(QUrl(self._update.installer_url or self._update.page_url))
            self._set_status("Descargando el instalador en el navegador. Cierra Teams Lite antes de ejecutarlo.")

    def _build_notice(self) -> QFrame:
        notice_card = QFrame()
        notice_card.setObjectName("noticeCard")
        notice_layout = QHBoxLayout(notice_card)
        notice_layout.setContentsMargins(16, 12, 16, 12)
        notice_layout.setSpacing(12)
        notice_layout.addWidget(_icon_label("warning", COLORS["warning"]), 0, Qt.AlignmentFlag.AlignTop)
        notice = QLabel(
            "<b style='color:#F2E6D3'>Antes de grabar.</b> "
            "La grabación captura el micrófono predeterminado y la salida de Windows en pistas separadas. "
            "Avisa a los participantes y respeta las normas de grabación aplicables. "
            "Usa auriculares para evitar que el micrófono vuelva a captar las voces remotas."
        )
        notice.setObjectName("noticeText")
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setWordWrap(True)
        notice_layout.addWidget(notice, 1)
        return notice_card

    def _build_recording_card(self) -> QFrame:
        card, layout = _card()
        title = QLabel("Nueva grabación")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addSpacing(4)

        field_hint = QLabel("Título de la reunión")
        field_hint.setObjectName("fieldHint")
        layout.addWidget(field_hint)
        self.title_input = QLineEdit("Reunión de Teams")
        self.title_input.setPlaceholderText("Escribe un título para la reunión")
        self.title_input.setAccessibleName("Título de la reunión")
        layout.addWidget(self.title_input)
        self.placement_picker = PlacementPicker(self.store)
        layout.addWidget(self.placement_picker)

        self.start_button = QPushButton("Iniciar grabación")
        self.start_button.setObjectName("primaryButton")
        set_button_icon(self.start_button, "play", COLORS["background"])
        self.stop_button = QPushButton("Detener y transcribir")
        self.stop_button.setObjectName("dangerButton")
        set_button_icon(self.stop_button, "stop", COLORS["danger"], size=12)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._set_recording_controls(recording=False)

        layout.addSpacing(4)
        self.ocr_checkbox = QCheckBox("Detectar asistentes con OCR")
        self.ocr_checkbox.setToolTip("Lee los nombres visibles en la ventana de Teams durante la grabación")
        self.ocr_checkbox.setChecked(self.config.ocr_enabled)
        layout.addWidget(self.ocr_checkbox)
        self.ocr_button = QPushButton("Capturar asistentes ahora")
        self.ocr_button.setObjectName("ghostButton")
        set_button_icon(self.ocr_button, "app", COLORS["text_soft"])
        self.ocr_button.setEnabled(False)
        layout.addWidget(self.ocr_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.visual_speaker_checkbox = QCheckBox("Identificar al hablante activo")
        self.visual_speaker_checkbox.setToolTip("Usa WeVisOne-VL cuando el modelo visual está disponible")
        self.visual_speaker_checkbox.setChecked(self.config.visual_speaker_enabled)
        layout.addWidget(self.visual_speaker_checkbox)
        # Only offer the heavy visual model when it is configured or actually installed.
        self.visual_speaker_checkbox.setVisible(
            self.config.visual_speaker_enabled
            or visual_model_problem(self.config.wevisone_model_dir) is None
        )

        moment_row = QHBoxLayout()
        moment_row.setSpacing(8)
        self.moment_note_input = QLineEdit()
        self.moment_note_input.setPlaceholderText("Nota del momento (opcional)")
        self.moment_note_input.setAccessibleName("Nota del momento importante")
        self.mark_moment_button = QPushButton("Marcar momento")
        set_button_icon(self.mark_moment_button, "flag", COLORS["text"])
        moment_row.addWidget(self.moment_note_input, 1)
        moment_row.addWidget(self.mark_moment_button)
        layout.addLayout(moment_row)
        self.moment_hint = QLabel()
        self.moment_hint.setObjectName("fieldHint")
        self.moment_hint.setWordWrap(True)
        layout.addWidget(self.moment_hint)
        self._set_moment_controls(recording=False)

        layout.addStretch(1)
        layout.addWidget(_divider())
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(14, 14)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusText")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)
        return card

    def _build_history_card(self) -> QFrame:
        card, layout = _card()
        layout.setContentsMargins(12, 18, 12, 12)
        heading = QHBoxLayout()
        heading.setContentsMargins(8, 0, 8, 0)
        title = QLabel("Grabaciones recientes")
        title.setObjectName("sectionTitle")
        self.history_count = QLabel("0")
        self.history_count.setObjectName("sectionMeta")
        self.history_scope = QLabel()
        self.history_scope.setObjectName("historyScope")
        self.history_scope.setToolTip("Se muestran las reuniones de la organización y el proyecto elegidos para grabar")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(title)
        titles.addWidget(self.history_scope)
        heading.addLayout(titles)
        heading.addStretch(1)
        heading.addWidget(self.history_count, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)

        # Columns 1-3 keep title/status/id as item data; column 0 renders the card row.
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("meetingList")
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(MeetingDelegate.ROW_HEIGHT)
        self.table.setItemDelegateForColumn(0, MeetingDelegate(self.table))
        self.table.setMouseTracking(True)  # hover highlight
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.table.setColumnHidden(column, True)
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table, 1)

        self.history_empty = QLabel("Todavía no hay grabaciones en este proyecto.")
        self.history_empty.setObjectName("emptyState")
        self.history_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.history_empty, 1)
        layout.addWidget(_divider())
        layout.addLayout(self._build_meeting_actions())
        return card

    def _build_meeting_actions(self) -> QVBoxLayout:
        """Actions on the meeting selected in the list, right below it."""
        self.rename_button = QPushButton("Renombrar")
        self.move_button = QPushButton("Mover")
        self.export_button = QPushButton("Exportar")
        for button, icon, label, tip in (
            (self.rename_button, "pencil", "Renombrar…", "Cambiar el nombre de la reunión"),
            (self.move_button, "folder", "Mover a…", "Mover a otra organización o proyecto"),
            (self.export_button, "share", "Exportar…", "Exportar la reunión en un paquete .zip para compartirla"),
        ):
            button.setObjectName("ghostButton")
            set_button_icon(button, icon, COLORS["text_soft"], gap=6)
            button.setAccessibleName(label)
            button.setToolTip(tip)
        self.delete_button = QPushButton("Eliminar reunión")
        self.delete_button.setObjectName("deleteButton")
        set_button_icon(self.delete_button, "trash", COLORS["danger"], gap=6)
        actions = QVBoxLayout()
        actions.setContentsMargins(8, 0, 8, 0)
        actions.setSpacing(6)
        first, second = QHBoxLayout(), QHBoxLayout()
        first.setSpacing(6)
        for button in (self.rename_button, self.move_button, self.export_button):
            button.setEnabled(False)
            first.addWidget(button)
        first.addStretch(1)
        self.delete_button.setEnabled(False)
        second.addStretch(1)
        second.addWidget(self.delete_button)
        actions.addLayout(first)
        actions.addLayout(second)
        return actions

    def _build_transcript_card(self) -> QFrame:
        card, layout = _card()
        card.setMinimumHeight(340)
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Transcripción")
        title.setObjectName("sectionTitle")
        self.transcript_meta = QLabel()
        self.transcript_meta.setObjectName("monoMuted")
        header.addWidget(title, 0, Qt.AlignmentFlag.AlignBaseline)
        header.addWidget(self.transcript_meta, 0, Qt.AlignmentFlag.AlignBaseline)
        header.addStretch(1)
        layout.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.copy_button = QPushButton("Copiar")
        self.copy_button.setObjectName("ghostButton")
        set_button_icon(self.copy_button, "copy", COLORS["text_soft"], gap=6)
        self.download_button = QPushButton("Descargar")
        self.download_button.setObjectName("ghostButton")
        set_button_icon(self.download_button, "download", COLORS["text_soft"], gap=6)
        for button in (self.copy_button, self.download_button):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.transcript_attendees = QLabel()
        self.transcript_attendees.setObjectName("sectionMeta")
        self.transcript_attendees.setWordWrap(True)
        self.transcript_attendees.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.transcript_attendees.hide()
        layout.addWidget(self.transcript_attendees)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.view_transcript_button = QPushButton("Transcripción")
        self.view_minutes_button = QPushButton("Minuta")
        self.view_chat_button = QPushButton("Chat con IA")
        self.view_chat_button.setToolTip("Conversa con la IA sobre la minuta y la transcripción de esta reunión")
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self.view_gallery_button = QPushButton("Capturas")
        self.view_gallery_button.setToolTip("Todas las capturas de pantalla tomadas en esta reunión")
        for button in (self.view_transcript_button, self.view_minutes_button, self.view_chat_button,
                       self.view_gallery_button):
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            self._view_group.addButton(button)
            toolbar.addWidget(button)
        self.view_transcript_button.setChecked(True)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        minutes_row = QHBoxLayout()
        minutes_row.setSpacing(8)
        minutes_row.addStretch(1)
        self.minutes_cli_combo = QComboBox()
        self.minutes_cli_combo.setAccessibleName("CLI para la minuta y el chat")
        for cli, label in _CLI_LABELS.items():
            self.minutes_cli_combo.addItem(label, cli)
        self.minutes_cli_combo.setCurrentIndex(max(0, self.minutes_cli_combo.findData(self.config.minutes_cli)))
        self.generate_minutes_button = QPushButton("Generar minuta")
        self.generate_minutes_button.setObjectName("minutesButton")
        self.generate_minutes_button.setToolTip(
            "Envía la transcripción a la CLI elegida (sin herramientas ni acceso a archivos) "
            "y guarda la respuesta en minutes.md."
        )
        minutes_row.addWidget(self.minutes_cli_combo)
        minutes_row.addWidget(self.generate_minutes_button)
        layout.addLayout(minutes_row)
        layout.addWidget(_divider())

        self.transcript_stack = QStackedWidget()
        self.transcript_placeholder = QLabel()
        self.transcript_placeholder.setObjectName("emptyState")
        self.transcript_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.transcript_placeholder.setWordWrap(True)
        self.transcript_table = QTableWidget(0, 3)
        self.transcript_table.setObjectName("transcriptTable")
        self.transcript_table.setShowGrid(False)
        self.transcript_table.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript_table.setWordWrap(True)
        self.transcript_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.transcript_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.transcript_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.transcript_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.transcript_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_table.verticalHeader().setVisible(False)
        # Row heights are computed by _fit_transcript_rows: ResizeToContents measured every cell
        # of every row on each resize step (~0.9 s per step with a 2-hour transcript).
        self.transcript_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._row_fit_timer = QTimer(self)
        self._row_fit_timer.setSingleShot(True)
        self._row_fit_timer.setInterval(80)
        self._row_fit_timer.timeout.connect(self._fit_transcript_rows)
        # Kept as a plain reference: the filter must not touch the table while the window is torn down.
        self._transcript_viewport = self.transcript_table.viewport()
        self._transcript_viewport.installEventFilter(self)
        transcript_header = self.transcript_table.horizontalHeader()
        transcript_header.setVisible(False)
        transcript_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        transcript_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        transcript_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.transcript_table.setColumnWidth(0, 130)
        self.transcript_table.setColumnWidth(1, 120)
        self.transcript_stack.addWidget(self.transcript_placeholder)
        self.transcript_stack.addWidget(self.transcript_table)
        self.minutes_view = QTextBrowser()
        self.minutes_view.setObjectName("minutesView")
        self.minutes_view.setOpenExternalLinks(False)
        self.minutes_view.document().setDefaultStyleSheet(MINUTES_DOCUMENT_CSS)
        self.transcript_stack.addWidget(self.minutes_view)
        self.chat_panel = ChatPanel(
            self.store.root, cli_provider=lambda: str(self.minutes_cli_combo.currentData())
        )
        self.transcript_stack.addWidget(self.chat_panel)
        self.transcript_stack.addWidget(self._build_gallery())
        # Copiar and Descargar follow the conversation while the Chat tab is visible.
        self.chat_panel.conversation_changed.connect(lambda: self._view == "chat" and self._render_view())
        layout.addWidget(self.transcript_stack, 1)
        self._show_transcript_placeholder("Selecciona una reunión para ver su transcripción.")
        self._sync_minutes_controls()
        self._set_status("Listo para grabar.")
        return card

    def _show_new_audio_warnings(self) -> None:
        """Surface recovered audio problems (device lost or switched) while still recording."""
        warnings = getattr(self.recorder, "warnings", None) if self.recorder is not None else None
        if not warnings or len(warnings) <= self._audio_warnings_shown:
            return
        self._audio_warnings_shown = len(warnings)
        self._set_status(f"Audio: {warnings[-1]}", tone="warning")

    def _set_moment_controls(self, *, recording: bool) -> None:
        self.mark_moment_button.setEnabled(recording)
        self.moment_note_input.setEnabled(recording)
        if not recording:
            self.moment_hint.setText(
                f"Durante la grabación, {MOMENT_HOTKEY_LABEL} marca un momento aunque estés en Teams."
            )
        elif self._hotkey_registered:
            self.moment_hint.setText(f"Atajo global activo: {MOMENT_HOTKEY_LABEL} (funciona aunque estés en Teams).")
        else:
            self.moment_hint.setText(
                f"El atajo {MOMENT_HOTKEY_LABEL} está ocupado por otra aplicación; reintentando cada "
                f"{HOTKEY_RETRY_SECONDS} s. Mientras tanto usa el botón o la mascota."
            )

    def _acquire_moment_hotkey(self) -> None:
        """Register Ctrl+Alt+M for this recording; when another app holds it, keep retrying.

        Each outcome goes to the meeting's events, so a press that never arrives can be explained.
        """
        if self.recorder is None or self.meeting_id is None:
            self._hotkey_retry_timer.stop()
            return
        self._hotkey_registered = register_moment_hotkey(int(self.winId()))
        if self._hotkey_registered:
            self._hotkey_retry_timer.stop()
            self.store.add_event(self.meeting_id, "moment_hotkey_ready", MOMENT_HOTKEY_LABEL)
            self._set_moment_controls(recording=True)
            return
        if not self._hotkey_unavailable_logged:
            self._hotkey_unavailable_logged = True
            self.store.add_event(
                self.meeting_id, "moment_hotkey_unavailable", f"{MOMENT_HOTKEY_LABEL} lo usa otra aplicación"
            )
        self._hotkey_retry_timer.start()

    def _release_moment_hotkey(self) -> None:
        self._hotkey_retry_timer.stop()
        if self._hotkey_registered:
            unregister_moment_hotkey(int(self.winId()))
            self._hotkey_registered = False

    def mark_moment(self, *, from_hotkey: bool = False) -> bool:
        """Mark the current instant; the screenshot runs off the UI thread."""
        if self.recorder is None or self.meeting_id is None or self._recording_origin is None:
            return False
        note = "" if from_hotkey else self.moment_note_input.text()
        if not from_hotkey:
            self.moment_note_input.clear()
        meeting_dir = Path(self.config.data_dir) / self.meeting_id
        origin = self._recording_origin
        title_hint = self.config.teams_window_title

        def work() -> None:
            try:
                moment = capture_moment(meeting_dir, origin, note=note, title_hint=title_hint)
            except Exception as exc:
                self.signals.failure.emit(f"No se pudo marcar el momento: {exc}")
                return
            self.signals.moment_marked.emit(moment)

        threading.Thread(target=work, name="mark-moment", daemon=True).start()
        if from_hotkey:
            _confirmation_beep()  # the user is looking at Teams, not at this window
        return True

    def _moment_done(self, moment: dict) -> None:
        self._moment_count += 1
        when = format_offset(moment["offset_seconds"])
        if moment.get("source") == "screen":
            self._set_status(
                f"Momento {self._moment_count} marcado a las {when} con captura de tu pantalla "
                "(Teams no estaba visible).",
                tone="success",
            )
        elif moment.get("problem"):
            self._set_status(
                f"Momento {self._moment_count} marcado a las {when}, sin captura: {moment['problem']}",
                tone="warning",
            )
        else:
            self._set_status(f"Momento {self._moment_count} marcado a las {when} con captura.", tone="success")

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt API
        if hotkey_id_from_native_event(event_type, message) == MOMENT_HOTKEY_ID:
            if self.meeting_id is not None:
                self.store.add_event(self.meeting_id, "moment_hotkey_pressed", MOMENT_HOTKEY_LABEL)
            return self.mark_moment(from_hotkey=True), 0
        return super().nativeEvent(event_type, message)

    def _open_moment_image(self, row: int, _column: int) -> None:
        item = self.transcript_table.item(row, 2)
        image = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if image and Path(image).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(image)))

    def _sync_ocr_button(self) -> None:
        self.ocr_button.setEnabled(self.recorder is not None and self.ocr_checkbox.isChecked())

    def _set_recording_controls(self, *, recording: bool, finalizing: bool = False) -> None:
        self.start_button.setVisible(not recording)
        self.start_button.setEnabled(not recording)
        self.stop_button.setVisible(recording)
        self.stop_button.setEnabled(recording and not finalizing)
        mascot = getattr(self, "mascot", None)
        if mascot is not None:
            mascot.set_recording(recording, getattr(self, "_recording_origin", None))
            mascot.record_button.setEnabled(not finalizing)

    def start_recording(self) -> None:
        """Record with the title, organization and project chosen in the recording card."""
        self._start_recording(
            self.title_input.text().strip() or "Reunión de Teams",
            self.placement_picker.organization_id(),
            self.placement_picker.project_id(),
        )

    def quick_start_recording(self) -> None:
        """Record at once with the mascot defaults: no title, organization or project to choose."""
        if self.recorder is not None:
            return
        settings = load_mascot_settings(self.store.root)
        organization_id, project_id, problem = resolve_placement(self.store, settings)
        # Recent recordings follow the project being recorded.
        self.placement_picker.set_placement(organization_id, project_id)
        if self._start_recording(settings.title, organization_id, project_id) and problem:
            self._set_status(f"Grabando. {problem}", tone="warning")

    def _start_recording(self, title: str, organization_id: str | None, project_id: str | None) -> bool:
        if self.recorder is not None:
            return False
        try:
            meeting_id = self.store.create_meeting(title, organization_id=organization_id, project_id=project_id)
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
            return False
        self.meeting_id = meeting_id
        self.recorder = recorder
        self._recording_origin = recording_origin
        self._audio_warnings_shown = 0
        self._moment_count = 0
        self._ocr_window_warned = False
        self._hotkey_unavailable_logged = False
        self._acquire_moment_hotkey()
        self._set_moment_controls(recording=True)
        visual_problem = None
        if self.visual_speaker_checkbox.isChecked():
            # Cheap file check: never start sampling frames for a model that cannot load.
            visual_problem = visual_model_problem(self.config.wevisone_model_dir)
            if visual_problem is None:
                self._start_visual_tracking(meeting_id, recording_origin)
            else:
                self.store.add_event(meeting_id, "visual_tracking_disabled", visual_problem)
        if self.ocr_checkbox.isChecked():
            self._start_attendee_ocr(meeting_id)
        self._set_recording_controls(recording=True)
        self.ocr_button.setEnabled(self.ocr_checkbox.isChecked())
        if visual_problem is None:
            self._set_status("Grabando el micrófono y el audio del sistema…")
        else:
            self._set_status(f"Grabando solo audio, sin hablante visual. {visual_problem}", tone="warning")
        self._refresh()
        return True

    def stop_recording(self) -> None:
        if self.recorder is None or self.meeting_id is None:
            return
        recorder, meeting_id = self.recorder, self.meeting_id
        visual_sampler, self.visual_sampler = self.visual_sampler, None
        ocr_sampler, self.ocr_sampler = self.ocr_sampler, None
        self._release_moment_hotkey()
        self._recording_origin = None
        self._set_moment_controls(recording=False)
        self._set_recording_controls(recording=True, finalizing=True)
        self.ocr_button.setEnabled(False)
        self._busy = True
        self._set_status("Finalizando los segmentos de audio recuperables…")

        def work() -> None:
            try:
                if ocr_sampler is not None:
                    ocr_sampler.stop()
                    if ocr_sampler.errors and not load_participants(self.store.root / meeting_id):
                        self.store.add_event(meeting_id, "attendee_ocr_warning", ocr_sampler.errors[-1])
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
        self.ocr_button.setEnabled(False)
        if not self.config.auto_transcribe:
            self._busy = False
            self._set_status("Grabación finalizada. La transcripción automática está desactivada.", tone="success")
            return
        self._set_status("Transcribiendo los segmentos finalizados mediante CPU…")
        threading.Thread(
            target=self._transcribe_meeting,
            args=(meeting_id,),
            name="offline-transcription",
            daemon=True,
        ).start()

    def _transcribe_meeting(self, meeting_id: str) -> None:
        last_percent = -1

        def progress(fraction: float) -> None:
            # Runs on the transcription thread: one signal per whole percent, not per chunk.
            nonlocal last_percent
            percent = int(fraction * 100)
            if percent != last_percent:
                last_percent = percent
                self.signals.transcription_progress.emit(meeting_id, fraction)

        try:
            self._transcribe_meeting_now(meeting_id, progress)
        finally:
            self.signals.transcription_finished.emit(meeting_id)

    def _transcribe_meeting_now(self, meeting_id: str, progress) -> None:
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
                skip_silence=True,  # each track is mostly silence while the other side talks
                progress=progress,
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

    def _read_attendees(self, meeting_id: str) -> dict:
        return read_teams_names(
            Path(self.config.data_dir) / meeting_id / "ocr",
            title_hint=self.config.teams_window_title,
            tesseract_cmd=self.config.tesseract_cmd,
        )

    def _store_attendees(self, meeting_id: str, result: dict) -> list[str]:
        # The periodic sampler and the manual button run on different threads.
        with self._attendee_lock:
            return merge_participants(Path(self.config.data_dir) / meeting_id, result.get("names", []))

    def _start_attendee_ocr(self, meeting_id: str) -> None:
        def on_result(result: dict) -> None:
            participants = self._store_attendees(meeting_id, result)
            if result.get("names"):
                self.signals.ocr_ready.emit(f"Grabando · {len(participants)} asistentes detectados por OCR.")

        def read() -> dict:
            try:
                result = self._read_attendees(meeting_id)
            except TeamsWindowUnavailable as exc:
                # Minimized or hidden Teams: pause quietly, but tell the user once per streak.
                if not self._ocr_window_warned:
                    self._ocr_window_warned = True
                    self.store.add_event(meeting_id, "attendee_ocr_paused", str(exc))
                    self.signals.visual_warning.emit(f"Asistentes por OCR en pausa. {exc}")
                return {"names": []}
            self._ocr_window_warned = False
            return result

        self.ocr_sampler = FrameSampler(
            read,
            on_result,
            interval_seconds=OCR_INTERVAL_SECONDS,
        )
        self.ocr_sampler.start()

    def capture_ocr(self) -> None:
        if self.meeting_id is None:
            return
        meeting_id = self.meeting_id
        self._set_status("Leyendo los asistentes visibles en Teams…")

        def work() -> None:
            try:
                result = self._read_attendees(meeting_id)
                participants = self._store_attendees(meeting_id, result)
                self.signals.ocr_ready.emit(
                    f"OCR: {len(result['names'])} nombres en pantalla · {len(participants)} asistentes en total."
                )
            except Exception as exc:
                self.signals.failure.emit(f"Falló la captura OCR: {exc}")

        threading.Thread(target=work, name="attendee-ocr", daemon=True).start()

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

        sampler: FrameSampler | None = None

        def detect(frame) -> None:
            try:
                observation = detector.detect(frame)
            except VisualModelUnavailable as exc:
                # The model will not recover mid-recording: stop capturing frames, keep the audio.
                if sampler is not None:
                    sampler.stop()
                self.store.add_event(meeting_id, "visual_tracking_disabled", str(exc))
                self.signals.visual_warning.emit(f"Seguimiento visual detenido; el audio sigue grabando. {exc}")
                return
            if observation is not None:
                append_observation(meeting_dir / "speaker-observations.jsonl", observation)

        sampler = FrameSampler(
            capture,
            detect,
            interval_seconds=self.config.visual_sample_seconds,
        )
        self.visual_sampler = sampler
        sampler.start()

    def _refresh(self) -> None:
        self._show_new_audio_warnings()
        all_meetings = self.store.list_meetings()
        # Recent recordings belong to the organization/project chosen for recording.
        placement = (self.placement_picker.organization_id(), self.placement_picker.project_id())
        meetings = [m for m in all_meetings if (m["organization_id"], m["project_id"]) == placement]
        signature = (placement, tuple(
            (meeting["id"], meeting["title"], meeting["status"], meeting["started_utc"],
             meeting["organization_id"], meeting["project_id"])
            for meeting in all_meetings
        ))
        self.history_scope.setText(self._placement_label())
        if signature == self._meetings_signature:
            return
        self._meetings_signature = signature
        if self.pages.currentWidget() is self.preview_page:
            self.preview_page.refresh()
        selected_id = self._selected_meeting_id()
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(meetings))
        selected_row = -1
        for row_number, meeting in enumerate(meetings):
            for column, key in enumerate(("started_utc", "title", "status", "id")):
                value = meeting[key]
                if key == "status":
                    value = _STATUS_LABELS.get(str(value), str(value))
                item = QTableWidgetItem("" if column == 0 else str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
                    item.setData(_MEETING_ROLE, dict(
                        meeting, transcription_progress=self._transcription_progress.get(meeting["id"])
                    ))
                    item.setToolTip(str(meeting["id"]))
                if column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, meeting["status"])
                self.table.setItem(row_number, column, item)
            if meeting["id"] == selected_id:
                selected_row = row_number
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self.history_count.setText(str(len(meetings)))
        self.table.setVisible(bool(meetings))
        self.history_empty.setVisible(not meetings)
        if selected_id is not None and selected_row < 0:
            self.show_selected_transcript()

    def _selected_meeting_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.selectionModel().isRowSelected(row):
            return None
        item = self.table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _show_transcript_placeholder(self, message: str) -> None:
        self._transcript_raw = ""
        self.transcript_table.setRowCount(0)
        self.transcript_placeholder.setText(message)
        self.transcript_stack.setCurrentWidget(self.transcript_placeholder)
        self.copy_button.setEnabled(False)
        self.download_button.setEnabled(False)

    def show_selected_transcript(self) -> None:
        meeting_id = self._selected_meeting_id()
        self.delete_button.setEnabled(meeting_id is not None)
        self.rename_button.setEnabled(meeting_id is not None)
        self.move_button.setEnabled(meeting_id is not None)
        self.export_button.setEnabled(meeting_id is not None)
        self._show_attendees(meeting_id)
        self._moments = load_moments(self.store.root / meeting_id) if meeting_id else []
        self._load_transcript(meeting_id)
        self._minutes = load_minutes(self.store.root / meeting_id) if meeting_id else None
        self._sync_chat_scope()
        self._render_view()

    def _sync_chat_scope(self) -> None:
        """The Chat tab talks about the selected meeting; each meeting keeps its own conversation."""
        meeting_id = self._selected_meeting_id()
        meeting = self.store.get_meeting(meeting_id) if meeting_id is not None else None
        if meeting is None:
            self.chat_panel.set_scope(None, "", [])
        else:
            self.chat_panel.set_scope(f"meeting-{meeting_id}", meeting["title"], [meeting])

    def _load_transcript(self, meeting_id: str | None) -> None:
        if meeting_id is None:
            self.transcript_meta.clear()
            self._show_transcript_placeholder("Selecciona una reunión para ver su transcripción.")
            return
        meeting_dir = self.store.root / meeting_id
        transcript = meeting_dir / "transcript.txt"
        if not transcript.is_file():
            self.transcript_meta.setText(f"reunión {meeting_id[:8]}…")
            self._show_transcript_placeholder("La transcripción todavía no está disponible para esta reunión.")
            return
        content = transcript.read_text(encoding="utf-8").strip()
        turns = load_transcript_turns(meeting_dir) if content else []
        if not turns:
            self.transcript_meta.setText(f"0 turnos · reunión {meeting_id[:8]}…")
            self._show_transcript_placeholder("La transcripción está vacía.")
            return
        self._populate_transcript(turns, self._moments)
        self._transcript_raw = content
        self.copy_button.setEnabled(True)
        self.download_button.setEnabled(True)
        ends = [turn["end"] if turn["end"] is not None else turn["start"] for turn in turns]
        duration = max((value for value in ends if value is not None), default=0.0)
        noun = "turno" if len(turns) == 1 else "turnos"
        moments = ""
        if self._moments:
            moments = f" · {len(self._moments)} momento" + ("" if len(self._moments) == 1 else "s")
        self.transcript_meta.setText(f"{len(turns)} {noun} · {round(duration)} s{moments} · reunión {meeting_id[:8]}…")

    def _build_gallery(self) -> QWidget:
        self.gallery_page = QWidget()
        layout = QVBoxLayout(self.gallery_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.gallery_summary = QLabel()
        self.gallery_summary.setObjectName("fieldHint")
        self.gallery_summary.setWordWrap(True)
        layout.addWidget(self.gallery_summary)
        self.gallery = QListWidget()
        self.gallery.setObjectName("previewGrid")
        self.gallery.setViewMode(QListView.ViewMode.IconMode)
        self.gallery.setResizeMode(QListView.ResizeMode.Adjust)
        self.gallery.setMovement(QListView.Movement.Static)
        self.gallery.setIconSize(THUMBNAIL_SIZE)
        self.gallery.setGridSize(QSize(THUMBNAIL_SIZE.width() + 36, THUMBNAIL_SIZE.height() + 58))
        self.gallery.setSpacing(8)
        self.gallery.setWordWrap(True)
        self.gallery.setFrameShape(QFrame.Shape.NoFrame)
        self.gallery.itemActivated.connect(self._open_gallery_image)
        layout.addWidget(self.gallery, 1)
        self._gallery_pending: list[QListWidgetItem] = []
        self._gallery_timer = QTimer(self)
        self._gallery_timer.setInterval(0)
        self._gallery_timer.timeout.connect(self._load_gallery_batch)
        return self.gallery_page

    def _populate_gallery(self) -> None:
        """List every screenshot of the selected meeting; thumbnails load a few at a time."""
        meeting_id = self._selected_meeting_id()  # re-listed each time: new moments appear while recording
        self._gallery_timer.stop()
        self.gallery.clear()
        shots = meeting_screenshots(self.store.root / meeting_id) if meeting_id else []
        placeholder = QIcon(self.preview_page.thumbnails.placeholder)
        for shot in shots:
            item = QListWidgetItem(placeholder, shot["label"])
            item.setData(Qt.ItemDataRole.UserRole, str(shot["path"]))
            item.setToolTip(f"{shot['label']} · doble clic para abrir")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.gallery.addItem(item)
        self._gallery_pending = [self.gallery.item(row) for row in range(self.gallery.count())]
        if meeting_id is None:
            self.gallery_summary.setText("Selecciona una reunión para ver sus capturas.")
        elif not shots:
            self.gallery_summary.setText(
                "Esta reunión no tiene capturas. Usa \"Marcar momento\" mientras grabas para guardar la pantalla."
            )
        else:
            moments = sum(1 for shot in shots if shot["kind"] == "moment")
            noun = "captura" if len(shots) == 1 else "capturas"
            self.gallery_summary.setText(
                f"{len(shots)} {noun} · {moments} de momentos marcados · doble clic para abrir a tamaño completo"
            )
        if self._gallery_pending:
            self._gallery_timer.start()

    def _load_gallery_batch(self) -> None:
        cache = self.preview_page.thumbnails
        for _ in range(6):  # a few per tick keeps the window responsive with hundreds of frames
            if not self._gallery_pending:
                self._gallery_timer.stop()
                return
            item = self._gallery_pending.pop(0)
            item.setIcon(QIcon(cache.pixmap(Path(item.data(Qt.ItemDataRole.UserRole)))))

    def _open_gallery_image(self, item: QListWidgetItem) -> None:
        path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _set_view(self, view: str) -> None:
        self._view = view
        self.view_transcript_button.setChecked(view == "transcript")
        self.view_minutes_button.setChecked(view == "minutes")
        self.view_chat_button.setChecked(view == "chat")
        self.view_gallery_button.setChecked(view == "gallery")
        self._render_view()

    def _render_view(self) -> None:
        """Show the transcript, the minutes or the chat; Copiar and Descargar act on what is visible."""
        if self._view == "chat":
            self.transcript_stack.setCurrentWidget(self.chat_panel)
        elif self._view == "gallery":
            self._populate_gallery()
            self.transcript_stack.setCurrentWidget(self.gallery_page)
        elif self._view == "minutes":
            if self._minutes:
                self.minutes_view.setMarkdown(self._minutes)
                self.transcript_stack.setCurrentWidget(self.minutes_view)
            else:
                if self._selected_meeting_id() is None:
                    message = "Selecciona una reunión para ver su minuta."
                elif self._transcript_raw:
                    message = "Esta reunión todavía no tiene minuta. Usa \"Generar minuta\"."
                else:
                    message = "La minuta se genera a partir de la transcripción, que todavía no está disponible."
                self.transcript_placeholder.setText(message)
                self.transcript_stack.setCurrentWidget(self.transcript_placeholder)
        elif self._transcript_raw:
            self.transcript_stack.setCurrentWidget(self.transcript_table)
        else:
            self.transcript_stack.setCurrentWidget(self.transcript_placeholder)
        has_content = bool(self._current_export())
        self.copy_button.setEnabled(has_content)
        self.download_button.setEnabled(has_content)
        self._sync_minutes_controls()

    def _sync_minutes_controls(self) -> None:
        meeting_id = self._selected_meeting_id()
        generating = meeting_id is not None and meeting_id in self._minutes_jobs
        can_generate = meeting_id is not None and bool(self._transcript_raw) and not generating
        self.generate_minutes_button.setEnabled(can_generate)
        self.minutes_cli_combo.setEnabled(not generating)
        if generating:
            self.generate_minutes_button.setText("Generando…")
        elif self._minutes:
            self.generate_minutes_button.setText("Regenerar minuta")
        else:
            self.generate_minutes_button.setText("Generar minuta")

    def _current_export(self) -> str:
        if self._view == "gallery":
            return ""  # images: open them with a double click instead
        if self._view == "chat":
            return self.chat_panel.export_text()
        if self._view == "minutes":
            return self._minutes or ""
        return self.export_text()

    def generate_minutes_for_selected(self) -> None:
        meeting_id = self._selected_meeting_id()
        if meeting_id is None or not self._transcript_raw or meeting_id in self._minutes_jobs:
            return
        cli = str(self.minutes_cli_combo.currentData())
        transcript = self.export_text()
        meeting_dir = self.store.root / meeting_id
        self._minutes_jobs.add(meeting_id)
        self._sync_minutes_controls()
        self._set_status(f"Generando la minuta con {_CLI_LABELS[cli]}… puede tardar 1–2 minutos.")

        def work() -> None:
            try:
                generate_minutes(transcript, meeting_dir, cli=cli)
                self.signals.minutes_ready.emit(meeting_id)
            except MinutesError as exc:
                self.signals.minutes_failed.emit(meeting_id, str(exc))
            except Exception as exc:
                self.signals.minutes_failed.emit(meeting_id, f"No se pudo generar la minuta: {exc}")

        threading.Thread(target=work, name="meeting-minutes", daemon=True).start()

    def _minutes_done(self, meeting_id: str) -> None:
        self._minutes_jobs.discard(meeting_id)
        self._set_status(f"Minuta lista · {meeting_id[:8]}…/minutes.md", tone="success")
        if self._selected_meeting_id() == meeting_id:
            self._minutes = load_minutes(self.store.root / meeting_id)
            self._sync_chat_scope()  # the chat becomes available once the minutes exist
            self._set_view("minutes")
        else:
            self._sync_minutes_controls()

    def _minutes_failed(self, meeting_id: str, message: str) -> None:
        # Generation runs in the background for minutes: report on the status line, no modal dialog.
        self._minutes_jobs.discard(meeting_id)
        self._set_status(message, tone="error")
        self._sync_minutes_controls()

    def _show_attendees(self, meeting_id: str | None) -> None:
        self._attendees = load_participants(self.store.root / meeting_id) if meeting_id else []
        self.transcript_attendees.setText(f"Asistentes (OCR): {', '.join(self._attendees)}")
        self.transcript_attendees.setVisible(bool(self._attendees))

    def _populate_transcript(self, turns: list[dict], moments: list[dict] = ()) -> None:
        mono = QFont(mono_font_family())
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(8.5)
        bold = QFont()
        bold.setBold(True)
        muted = QColor(COLORS["text_muted"])
        entries = [("turn", turn["start"] if turn["start"] is not None else 0.0, turn) for turn in turns]
        entries += [("moment", moment["offset_seconds"], moment) for moment in moments]
        entries.sort(key=lambda entry: entry[1])  # stable: turns keep their order
        self.transcript_table.setRowCount(len(entries))
        meeting_dir = self.store.root / (self._selected_meeting_id() or "")
        for row, (kind, _, turn) in enumerate(entries):
            if kind == "moment":
                self._set_moment_row(row, turn, meeting_dir, mono, bold)
                continue
            if turn["start"] is None:
                clock = ""
            elif turn["end"] is None:
                clock = format_clock(turn["start"])
            else:
                clock = f"{format_clock(turn['start'])} –\n{format_clock(turn['end'])}"
            time_item = QTableWidgetItem(clock)
            time_item.setFont(mono)
            time_item.setForeground(muted)
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            speaker_item = QTableWidgetItem(turn["speaker"])
            speaker_item.setFont(bold)
            speaker_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            text_item = QTableWidgetItem(turn["text"])
            text_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.transcript_table.setItem(row, 0, time_item)
            self.transcript_table.setItem(row, 1, speaker_item)
            self.transcript_table.setItem(row, 2, text_item)
        self._fit_transcript_rows()
        self.transcript_stack.setCurrentWidget(self.transcript_table)
        self.transcript_table.scrollToTop()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched is getattr(self, "_transcript_viewport", None) and event.type() == QEvent.Type.Resize:
            self._row_fit_timer.start()  # recompute once the user stops resizing
        return super().eventFilter(watched, event)

    def _fit_transcript_rows(self) -> None:
        """Size each transcript row to its wrapped text with font metrics (fast, no per-cell sizeHint)."""
        table = self.transcript_table
        if not table.rowCount():
            return
        cell_padding, row_padding = 14, 22  # item padding 6 px per side + borders; 10 px top/bottom
        widths = {1: max(40, table.columnWidth(1) - cell_padding), 2: max(80, table.columnWidth(2) - cell_padding)}
        wrap = int(Qt.TextFlag.TextWordWrap)
        metrics: dict[str, QFontMetrics] = {}

        def text_height(item, width):
            font = item.font()
            key = font.toString()
            if key not in metrics:
                metrics[key] = QFontMetrics(font)
            if width is None:
                return metrics[key].lineSpacing() * (item.text().count("\n") + 1)
            return metrics[key].boundingRect(QRect(0, 0, width, 1_000_000), wrap, item.text()).height()

        table.setUpdatesEnabled(False)
        try:
            for row in range(table.rowCount()):
                height = 0
                for column, width in ((0, None), (1, widths[1]), (2, widths[2])):
                    item = table.item(row, column)
                    if item is not None and item.text():
                        height = max(height, text_height(item, width))
                table.setRowHeight(row, height + row_padding)
        finally:
            table.setUpdatesEnabled(True)

    def _set_moment_row(self, row: int, moment: dict, meeting_dir: Path, mono: QFont, bold: QFont) -> None:
        accent = QColor(COLORS["warning"])
        time_item = QTableWidgetItem(format_clock(moment["offset_seconds"]))
        time_item.setFont(mono)
        time_item.setForeground(accent)
        label_item = QTableWidgetItem("📌 Momento")
        label_item.setFont(bold)
        label_item.setForeground(accent)
        detail = moment.get("note") or "(sin nota)"
        text_item = QTableWidgetItem()
        if moment.get("image"):
            image = meeting_dir / moment["image"]
            text_item.setData(Qt.ItemDataRole.UserRole, str(image))
            text_item.setToolTip(str(image))
            where = "de tu pantalla" if moment.get("source") == "screen" else "de Teams"
            detail += f" · captura {where} (doble clic para abrir)"
        elif moment.get("problem"):
            detail += f" · sin captura: {moment['problem']}"
        text_item.setText(detail)
        text_item.setForeground(accent)
        for column, item in enumerate((time_item, label_item, text_item)):
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.transcript_table.setItem(row, column, item)

    def transcript_text(self) -> str:
        return self._transcript_raw

    def export_text(self) -> str:
        """Transcript as copied or downloaded: attendees first, so the minutes know who attended."""
        if not self._transcript_raw:
            return ""
        sections = []
        if self._attendees:
            sections.append(f"Asistentes (detectados por OCR): {', '.join(self._attendees)}")
        if self._moments:
            sections.append(moments_for_minutes(self._moments))
        sections.append(self._transcript_raw)
        return "\n\n".join(sections)

    def copy_transcript(self) -> None:
        content = self._current_export()
        if not content:
            return
        QApplication.clipboard().setText(content)
        copied = {"minutes": "Minuta copiada", "chat": "Conversación copiada"}.get(self._view, "Transcripción copiada")
        self._set_status(f"{copied} al portapapeles.", tone="success")

    def download_transcript(self) -> None:
        meeting_id = self._selected_meeting_id()
        content = self._current_export()
        if not content or meeting_id is None:
            return
        row = self.table.currentRow()
        title_item = self.table.item(row, 1)
        title = title_item.text() if title_item is not None else "transcripcion"
        safe_title = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "transcripcion"
        if self._view == "minutes":
            caption, filename, file_filter = "Descargar minuta", f"{safe_title}-minuta.md", "Markdown (*.md)"
        elif self._view == "chat":
            caption, filename, file_filter = "Descargar conversación", f"{safe_title}-chat.md", "Markdown (*.md)"
        else:
            caption, filename, file_filter = "Descargar transcripción", f"{safe_title}.txt", "Texto (*.txt)"
        path, _ = QFileDialog.getSaveFileName(self, caption, str(Path.home() / filename), file_filter)
        if not path:
            return
        try:
            Path(path).write_text(content.rstrip("\n") + "\n", encoding="utf-8")
        except OSError as exc:
            self._show_error(f"No se pudo guardar la transcripción: {exc}")
            return
        self._set_status(f"Descargado · {path}", tone="success")

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
        if meeting_id in self._minutes_jobs:
            self._show_error("Espera a que termine de generarse la minuta antes de eliminar esta reunión.")
            return
        if not self._confirm_delete(title):
            return
        try:
            self.store.delete_meeting(meeting_id)
        except Exception as exc:
            self._show_error(f"No se pudo eliminar la reunión: {exc}")
            return
        self.table.clearSelection()
        self.delete_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.transcript_meta.clear()
        self._show_transcript_placeholder("Selecciona una reunión para ver su transcripción.")
        self._set_status("Reunión eliminada.", tone="success")
        self._refresh()

    def _set_status(self, message: str, tone: str = "info") -> None:
        icon, color = {
            "success": ("check", COLORS["success"]),
            "error": ("alert", COLORS["danger"]),
            "warning": ("warning", COLORS["warning"]),
        }.get(tone, ("info", COLORS["text_muted"]))
        self.status_label.setText(message)
        self.status_label.setToolTip(message)
        self.status_icon.setPixmap(_render_svg(icon, color, 14))
        self._update_state_pill()

    def _update_state_pill(self) -> None:
        if self.recorder is not None and not self._busy:
            tone, label, color = "recording", "Grabando", COLORS["danger"]
        elif self._busy:
            tone, label, color = "busy", "Procesando", COLORS["warning"]
        else:
            tone, label, color = "ready", "Listo", COLORS["success"]
        self.state_pill.setText(f"<span style='color:{color}'>●</span>&nbsp;{label}")
        if self.state_pill.property("tone") != tone:
            self.state_pill.setProperty("tone", tone)
            _repolish(self.state_pill)

    def _set_card_progress(self, meeting_id: str, fraction: float | None) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == meeting_id:
                item.setData(_MEETING_ROLE, dict(item.data(_MEETING_ROLE), transcription_progress=fraction))
                self.table.viewport().update()
                return

    def _on_transcription_progress(self, meeting_id: str, fraction: float) -> None:
        self._transcription_progress[meeting_id] = fraction
        self._set_card_progress(meeting_id, fraction)
        self._set_status(f"Transcribiendo mediante CPU… {int(fraction * 100)} %")

    def _on_transcription_finished(self, meeting_id: str) -> None:
        self._transcription_progress.pop(meeting_id, None)
        self._set_card_progress(meeting_id, None)

    def _transcript_done(self, path: str) -> None:
        self._busy = False
        self._set_status(f"Transcripción guardada · {path}", tone="success")
        self._refresh()
        self.show_selected_transcript()

    def _ocr_done(self, message: str) -> None:
        self._set_status(message, tone="success")

    def _show_error(self, message: str) -> None:
        self._busy = False
        self._set_status(message, tone="error")
        QMessageBox.warning(self, "Teams Lite", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.recorder is not None:
            QMessageBox.warning(self, "Teams Lite", "Detén la grabación antes de cerrar Teams Lite.")
            event.ignore()
            return
        self._release_moment_hotkey()
        self.mascot.close()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grabador recuperable Teams Lite")
    parser.add_argument(
        "--config",
        default=None,
        help="Ruta del archivo de configuración JSON (por defecto, config.json junto al ejecutable)",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("La captura de audio de Teams Lite solo es compatible con Windows.", file=sys.stderr)
        return 2
    config = load_config(args.config or default_config_path())
    application = QApplication(sys.argv[:1])
    application.setStyle("Fusion")
    application.setPalette(_dark_palette())
    application.setFont(QFont("Segoe UI", 10))
    window = MainWindow(config)
    window.show()
    window.check_for_updates_async()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

