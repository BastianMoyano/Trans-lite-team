"""Widgets that file meetings by organization and project."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .chat_ui import ChatPanel
from .previews import meeting_thumbnail
from .storage import ANY, Storage

VAULT_LABEL = "Cofre de reuniones"
NO_PROJECT_LABEL = "Sin proyecto"
THUMBNAIL_ROLE = Qt.ItemDataRole.UserRole + 10
THUMBNAIL_SIZE = QSize(160, 90)


def _count_label(count: int) -> str:
    return f"{count} reunión" if count == 1 else f"{count} reuniones"


class ThumbnailCache:
    """Scaled, center-cropped screenshots, re-read only when the file changes on disk."""

    def __init__(self, placeholder: QPixmap):
        self.placeholder = placeholder
        self._entries: dict[str, tuple[int, QPixmap]] = {}

    def pixmap(self, path: Path | None) -> QPixmap:
        if path is None:
            return self.placeholder
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return self.placeholder
        cached = self._entries.get(str(path))
        if cached is not None and cached[0] == stamp:
            return cached[1]
        image = QPixmap(str(path))
        if image.isNull():
            return self.placeholder
        scaled = image.scaled(
            THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        origin = QPoint((scaled.width() - THUMBNAIL_SIZE.width()) // 2, (scaled.height() - THUMBNAIL_SIZE.height()) // 2)
        cropped = scaled.copy(QRect(origin, THUMBNAIL_SIZE))
        self._entries[str(path)] = (stamp, cropped)
        return cropped


class PreviewPage(QWidget):
    """Browses organizations, then projects, then their meetings newest first with a screenshot."""

    open_meeting = Signal(str)
    move_meeting = Signal(str)
    export_meeting = Signal(str)
    import_requested = Signal()
    def __init__(
        self,
        store: Storage,
        *,
        folder_icon: QPixmap,
        placeholder: QPixmap,
        describe: Callable[[dict], str],
        default_cli: str = "claude",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.folder_icon = folder_icon
        self.describe = describe
        self.thumbnails = ThumbnailCache(placeholder)
        # Each entry is (kind, id, label): kind is "vault", "organization" or "project".
        self._path: list[tuple[str, str | None, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        title = QLabel("Vista previa")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        header = QHBoxLayout()
        self.breadcrumb_bar = QHBoxLayout()
        self.breadcrumb_bar.setSpacing(4)
        header.addLayout(self.breadcrumb_bar)
        header.addStretch(1)
        self.chat_button = QPushButton("Conversar con IA")
        self.chat_button.setObjectName("ghostButton")
        self.chat_button.setCheckable(True)
        self.chat_button.setToolTip("Muestra u oculta el chat sobre las minutas de estas reuniones")
        header.addWidget(self.chat_button)
        self.move_button = QPushButton("Mover a…")
        self.move_button.setObjectName("ghostButton")
        self.move_button.setEnabled(False)
        header.addWidget(self.move_button)
        self.import_button = QPushButton("Importar…")
        self.import_button.setObjectName("ghostButton")
        self.import_button.setToolTip("Importa un paquete .zip de reunión exportado desde otro Teams Lite")
        header.addWidget(self.import_button)
        layout.addLayout(header)

        self.grid = QListWidget()
        self.grid.setObjectName("previewGrid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setIconSize(THUMBNAIL_SIZE)
        self.grid.setGridSize(QSize(200, 160))
        self.grid.setSpacing(12)
        self.grid.setWordWrap(True)
        self.grid.setFrameShape(QFrame.Shape.NoFrame)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.meeting_list = QListWidget()
        self.meeting_list.setObjectName("previewMeetings")
        self.meeting_list.setIconSize(THUMBNAIL_SIZE)
        self.meeting_list.setSpacing(4)
        self.meeting_list.setFrameShape(QFrame.Shape.NoFrame)
        self.meeting_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        for widget in (self.grid, self.meeting_list, self.empty_label):
            self.stack.addWidget(widget)
        # The project chat sits to the right of its meetings, inside the same section.
        self.chat_panel = ChatPanel(store.root, default_cli=default_cli)
        self.chat_panel.setMinimumWidth(320)
        self.chat_panel.hide()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.chat_panel)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)
        self.chat_button.toggled.connect(self.chat_panel.setVisible)

        self.grid.itemClicked.connect(self._enter)
        self.meeting_list.itemActivated.connect(
            lambda item: self.open_meeting.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.meeting_list.itemSelectionChanged.connect(
            lambda: self.move_button.setEnabled(self.selected_meeting_id() is not None)
        )
        self.meeting_list.customContextMenuRequested.connect(self._meeting_menu)
        self.move_button.clicked.connect(self._request_move)
        self.import_button.clicked.connect(self.import_requested.emit)
        self.refresh()

    def current_placement(self) -> tuple[str | None, str | None] | None:
        """Where the user is browsing, or None at the top level where nothing is chosen yet."""
        if not self._path:
            return None
        if self._path[0][0] == "vault":
            return None, None
        project_id = self._path[1][1] if len(self._path) > 1 else None
        return self._path[0][1], project_id

    def breadcrumb_labels(self) -> list[str]:
        return ["Organizaciones"] + [label for _, _, label in self._path]

    def visible_meetings(self) -> list[dict] | None:
        """The meetings of the vault or project being browsed, or None while choosing one."""
        if not self._path:
            return None
        if self._path[0][0] == "vault":
            return self.store.list_meetings(organization_id=None)
        if len(self._path) == 1:
            return None
        return self.store.list_meetings(organization_id=self._path[0][1], project_id=self._path[1][1])

    def chat_scope(self) -> tuple[str, str]:
        """A stable key and a readable title for the meetings in view."""
        placement = self.current_placement()
        if placement is None or self._path[0][0] == "vault":
            return "vault", VAULT_LABEL
        organization_id, project_id = placement
        return f"project-{organization_id}-{project_id or 'none'}", " › ".join(label for _, _, label in self._path)

    def go_to_level(self, depth: int) -> None:
        self._path = self._path[:depth]
        self.refresh()

    def selected_meeting_id(self) -> str | None:
        if self.stack.currentWidget() is not self.meeting_list:
            return None
        items = self.meeting_list.selectedItems()
        return str(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def refresh(self) -> None:
        self._drop_missing_levels()
        self._render_breadcrumb()
        meetings = self.visible_meetings()
        if meetings is not None:
            self._show_meetings(meetings)
            scope_key, title = self.chat_scope()
            self.chat_panel.set_scope(scope_key, title, meetings)
        else:
            self.chat_button.setChecked(False)  # the chat belongs to a project: hide it while choosing one
            self.chat_panel.set_scope(None, "", [])
            if not self._path:
                self._show_organizations()
            else:
                self._show_projects(self._path[0][1])

    def _drop_missing_levels(self) -> None:
        """An organization or project deleted or renamed elsewhere must not leave a stale view."""
        if not self._path or self._path[0][0] == "vault":
            return
        organizations = {org["id"]: org["name"] for org in self.store.list_organizations()}
        organization_id = self._path[0][1]
        if organization_id not in organizations:
            self._path = []
            return
        self._path[0] = ("organization", organization_id, organizations[organization_id])
        if len(self._path) > 1 and self._path[1][1] is not None:
            projects = {project["id"]: project["name"] for project in self.store.list_projects(organization_id)}
            project_id = self._path[1][1]
            if project_id not in projects:
                self._path = self._path[:1]
            else:
                self._path[1] = ("project", project_id, projects[project_id])

    def _render_breadcrumb(self) -> None:
        while self.breadcrumb_bar.count():
            widget = self.breadcrumb_bar.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        labels = self.breadcrumb_labels()
        for depth, label in enumerate(labels):
            if depth:
                separator = QLabel("›")
                separator.setObjectName("sectionMeta")
                self.breadcrumb_bar.addWidget(separator)
            crumb = QPushButton(label)
            crumb.setObjectName("crumb")
            crumb.setFlat(True)
            current = depth == len(labels) - 1
            crumb.setProperty("current", current)
            crumb.setEnabled(not current)
            crumb.clicked.connect(lambda _checked=False, level=depth: self.go_to_level(level))
            self.breadcrumb_bar.addWidget(crumb)

    def _card(self, label: str, count: int, meetings: list[dict], target: tuple[str, str | None, str]) -> None:
        item = QListWidgetItem(QIcon(self._cover(meetings)), f"{label}\n{_count_label(count)}")
        item.setData(Qt.ItemDataRole.UserRole, target)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.grid.addItem(item)

    def _cover(self, meetings: list[dict]) -> QPixmap:
        """The newest meeting with a screenshot represents the group; otherwise a folder."""
        for meeting in meetings:
            path = meeting_thumbnail(self.store.root / meeting["id"])
            if path is not None:
                pixmap = self.thumbnails.pixmap(path)
                if pixmap is not self.thumbnails.placeholder:
                    return pixmap
        return self.folder_icon

    def _show_organizations(self) -> None:
        self.grid.clear()
        vault = self.store.list_meetings(organization_id=None)
        self._card(VAULT_LABEL, len(vault), vault, ("vault", None, VAULT_LABEL))
        for organization in self.store.list_organizations():
            meetings = self.store.list_meetings(organization_id=organization["id"])
            self._card(organization["name"], organization["meeting_count"], meetings,
                       ("organization", organization["id"], organization["name"]))
        self.stack.setCurrentWidget(self.grid)
        self.move_button.hide()
        self.chat_button.hide()

    def _show_projects(self, organization_id: str) -> None:
        self.grid.clear()
        for project in self.store.list_projects(organization_id):
            meetings = self.store.list_meetings(project_id=project["id"])
            self._card(project["name"], project["meeting_count"], meetings,
                       ("project", project["id"], project["name"]))
        loose = self.store.list_meetings(organization_id=organization_id, project_id=None)
        self._card(NO_PROJECT_LABEL, len(loose), loose, ("project", None, NO_PROJECT_LABEL))
        self.stack.setCurrentWidget(self.grid)
        self.move_button.hide()
        self.chat_button.hide()

    def _show_meetings(self, meetings: list[dict]) -> None:
        selected = self.selected_meeting_id()
        self.meeting_list.clear()
        for meeting in meetings:
            path = meeting_thumbnail(self.store.root / meeting["id"])
            item = QListWidgetItem(
                QIcon(self.thumbnails.pixmap(path)), f"{meeting['title']}\n{self.describe(meeting)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
            item.setData(THUMBNAIL_ROLE, str(path) if path is not None else None)
            item.setToolTip(str(meeting["id"]))
            item.setSizeHint(QSize(0, THUMBNAIL_SIZE.height() + 16))
            self.meeting_list.addItem(item)
            if meeting["id"] == selected:
                item.setSelected(True)
        if meetings:
            self.stack.setCurrentWidget(self.meeting_list)
        else:
            self.empty_label.setText("No hay reuniones aquí todavía.")
            self.stack.setCurrentWidget(self.empty_label)
        self.move_button.show()
        self.move_button.setEnabled(self.selected_meeting_id() is not None)
        self.chat_button.show()
        self.chat_button.setEnabled(bool(meetings))

    def _enter(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target:
            self._path.append(tuple(target))
            self.refresh()

    def _request_move(self) -> None:
        meeting_id = self.selected_meeting_id()
        if meeting_id is not None:
            self.move_meeting.emit(meeting_id)

    def _meeting_menu(self, position: QPoint) -> None:
        item = self.meeting_list.itemAt(position)
        if item is None:
            return
        meeting_id = str(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Abrir", lambda: self.open_meeting.emit(meeting_id))
        menu.addAction("Mover a…", lambda: self.move_meeting.emit(meeting_id))
        menu.addAction("Exportar…", lambda: self.export_meeting.emit(meeting_id))
        menu.exec(self.meeting_list.viewport().mapToGlobal(position))


class MoveMeetingDialog(QDialog):
    """Chooses where a meeting is filed: the vault, an organization, or one of its projects."""

    def __init__(self, store: Storage, organization_id: str | None, project_id: str | None,
                 parent: QWidget | None = None, *, title: str = "Mover reunión", action: str = "Mover",
                 note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint = QLabel(
            "Elige dónde guardar la reunión. Sin organización queda en el Cofre de reuniones."
            + (f"\n{note}" if note else "")
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.picker = PlacementPicker(store)
        self.picker.set_placement(organization_id, project_id)
        layout.addWidget(self.picker)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(action)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def placement(self) -> tuple[str | None, str | None]:
        return self.picker.organization_id(), self.picker.project_id()


class ExportOptionsDialog(QDialog):
    """Asks whether the package carries the audio; it is off so packages stay light."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Exportar reunión")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint = QLabel(
            "El paquete .zip lleva la transcripción, la minuta, los momentos con sus pantallazos "
            "y los asistentes. Quien lo reciba lo importa desde Vista previa."
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.include_audio_checkbox = QCheckBox("Incluir audio (permite volver a transcribir; pesa mucho más)")
        layout.addWidget(self.include_audio_checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Elegir dónde guardar…")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def include_audio(self) -> bool:
        return self.include_audio_checkbox.isChecked()


_FAILED = object()


class AdminPage(QWidget):
    """Creates, renames and deletes organizations and their projects."""

    changed = Signal()

    def __init__(self, store: Storage, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        title = QLabel("Administrar")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Las reuniones sin organización quedan en el Cofre de reuniones. "
            "Eliminar una organización o un proyecto nunca elimina sus reuniones."
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        organization_card, self.organization_list, organization_buttons = self._column("Organizaciones", "Nueva")
        self.new_organization_button, self.rename_organization_button, self.delete_organization_button = (
            organization_buttons
        )
        project_card, self.project_list, project_buttons = self._column("Proyectos", "Nuevo")
        self.new_project_button, self.rename_project_button, self.delete_project_button = project_buttons
        columns.addWidget(organization_card, 1)
        columns.addWidget(project_card, 1)
        layout.addLayout(columns, 1)

        self.organization_list.itemSelectionChanged.connect(self._reload_projects)
        self.project_list.itemSelectionChanged.connect(self._sync_buttons)
        self.new_organization_button.clicked.connect(self._new_organization)
        self.rename_organization_button.clicked.connect(self._rename_organization)
        self.delete_organization_button.clicked.connect(self._delete_organization)
        self.new_project_button.clicked.connect(self._new_project)
        self.rename_project_button.clicked.connect(self._rename_project)
        self.delete_project_button.clicked.connect(self._delete_project)
        self.reload()

    # Dialog hooks: plain methods so the flows can be exercised without modal windows.
    def ask_name(self, title: str, label: str, current: str = "") -> str | None:
        text, accepted = QInputDialog.getText(self, title, label, text=current)
        return text if accepted else None

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Administrar", message)

    @staticmethod
    def _column(heading: str, new_label: str):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        label = QLabel(heading)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        items = QListWidget()
        items.setObjectName("adminList")
        layout.addWidget(items, 1)
        row = QHBoxLayout()
        buttons = (QPushButton(new_label), QPushButton("Renombrar"), QPushButton("Eliminar"))
        for button, name in zip(buttons, ("ghostButton", "ghostButton", "deleteButton")):
            button.setObjectName(name)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return card, items, buttons

    @staticmethod
    def _selected(items: QListWidget) -> tuple[str, str] | None:
        """(id, name) of the selected row, or None."""
        chosen = items.selectedItems()
        if not chosen:
            return None
        return str(chosen[0].data(Qt.ItemDataRole.UserRole)), chosen[0].text()

    @staticmethod
    def _fill(items: QListWidget, rows: list[dict], selected_id: str | None) -> None:
        items.blockSignals(True)
        items.clear()
        for row in rows:
            item = QListWidgetItem(row["name"])
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(_count_label(row["meeting_count"]))
            items.addItem(item)
            if row["id"] == selected_id:
                items.setCurrentItem(item)
                item.setSelected(True)
        items.blockSignals(False)

    def reload(self, organization_id: object = ANY, project_id: object = ANY) -> None:
        """Re-read both lists; ANY keeps the current selection."""
        if organization_id is ANY:
            current = self._selected(self.organization_list)
            organization_id = current[0] if current else None
        self._fill(self.organization_list, self.store.list_organizations(), organization_id)
        self._reload_projects(project_id)

    def _reload_projects(self, project_id: object = ANY) -> None:
        if project_id is ANY:
            current = self._selected(self.project_list)
            project_id = current[0] if current else None
        organization = self._selected(self.organization_list)
        rows = self.store.list_projects(organization[0]) if organization else []
        self._fill(self.project_list, rows, project_id)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has_organization = self._selected(self.organization_list) is not None
        has_project = self._selected(self.project_list) is not None
        self.rename_organization_button.setEnabled(has_organization)
        self.delete_organization_button.setEnabled(has_organization)
        self.new_project_button.setEnabled(has_organization)
        self.rename_project_button.setEnabled(has_project)
        self.delete_project_button.setEnabled(has_project)

    def _run(self, action: Callable[[], object]) -> object:
        try:
            result = action()
        except ValueError as exc:
            self.show_error(str(exc))
            return _FAILED
        self.changed.emit()
        return result

    def _new_organization(self) -> None:
        name = self.ask_name("Nueva organización", "Nombre de la organización:")
        if name is None:
            return
        created = self._run(lambda: self.store.create_organization(name))
        if created is not _FAILED:
            self.reload(created, None)

    def _rename_organization(self) -> None:
        selected = self._selected(self.organization_list)
        if selected is None:
            return
        name = self.ask_name("Renombrar organización", "Nuevo nombre:", selected[1])
        if name is not None and self._run(lambda: self.store.rename_organization(selected[0], name)) is not _FAILED:
            self.reload()

    def _delete_organization(self) -> None:
        selected = self._selected(self.organization_list)
        if selected is None or not self.confirm(
            "Eliminar organización",
            f'¿Eliminar "{selected[1]}" y sus proyectos? Sus reuniones pasarán al Cofre de reuniones.',
        ):
            return
        if self._run(lambda: self.store.delete_organization(selected[0])) is not _FAILED:
            self.reload(None, None)

    def _new_project(self) -> None:
        organization = self._selected(self.organization_list)
        if organization is None:
            return
        name = self.ask_name("Nuevo proyecto", f"Nombre del proyecto en {organization[1]}:")
        if name is None:
            return
        created = self._run(lambda: self.store.create_project(organization[0], name))
        if created is not _FAILED:
            self.reload(organization[0], created)

    def _rename_project(self) -> None:
        selected = self._selected(self.project_list)
        if selected is None:
            return
        name = self.ask_name("Renombrar proyecto", "Nuevo nombre:", selected[1])
        if name is not None and self._run(lambda: self.store.rename_project(selected[0], name)) is not _FAILED:
            self.reload()

    def _delete_project(self) -> None:
        organization, selected = self._selected(self.organization_list), self._selected(self.project_list)
        if organization is None or selected is None or not self.confirm(
            "Eliminar proyecto",
            f'¿Eliminar el proyecto "{selected[1]}"? Sus reuniones quedarán en {organization[1]} sin proyecto.',
        ):
            return
        if self._run(lambda: self.store.delete_project(selected[0])) is not _FAILED:
            self.reload(organization[0], None)


class PlacementPicker(QWidget):
    """Organization and project combos; the first organization entry is the meeting vault."""

    def __init__(self, store: Storage, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.organization_combo = QComboBox()
        self.organization_combo.setAccessibleName("Organización")
        self.project_combo = QComboBox()
        self.project_combo.setAccessibleName("Proyecto")
        for label, combo in (("Organización", self.organization_combo), ("Proyecto", self.project_combo)):
            column = QVBoxLayout()
            column.setSpacing(4)
            hint = QLabel(label)
            hint.setObjectName("fieldHint")
            column.addWidget(hint)
            column.addWidget(combo)
            layout.addLayout(column, 1)
        self.organization_combo.currentIndexChanged.connect(lambda _index: self._reload_projects())
        self.reload()

    def organization_id(self) -> str | None:
        return self.organization_combo.currentData()

    def project_id(self) -> str | None:
        return self.project_combo.currentData() if self.organization_id() is not None else None

    def set_placement(self, organization_id: str | None, project_id: str | None) -> None:
        index = self.organization_combo.findData(organization_id)
        self.organization_combo.setCurrentIndex(max(0, index))
        index = self.project_combo.findData(project_id)
        self.project_combo.setCurrentIndex(max(0, index))

    def reload(self) -> None:
        """Re-read organizations and projects, keeping the current choice when it still exists."""
        organization_id, project_id = self.organization_id(), self.project_combo.currentData()
        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        self.organization_combo.addItem(VAULT_LABEL, None)
        for organization in self.store.list_organizations():
            self.organization_combo.addItem(organization["name"], organization["id"])
        self.organization_combo.setCurrentIndex(max(0, self.organization_combo.findData(organization_id)))
        self.organization_combo.blockSignals(False)
        self._reload_projects(project_id)

    def _reload_projects(self, project_id: str | None = None) -> None:
        organization_id = self.organization_id()
        self.project_combo.clear()
        self.project_combo.addItem(NO_PROJECT_LABEL, None)
        if organization_id is not None:
            for project in self.store.list_projects(organization_id):
                self.project_combo.addItem(project["name"], project["id"])
        self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(project_id)))
        self.project_combo.setEnabled(organization_id is not None)
