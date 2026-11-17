"""Widgets that file meetings by organization and project."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .storage import Storage

VAULT_LABEL = "Cofre de reuniones"
NO_PROJECT_LABEL = "Sin proyecto"


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
