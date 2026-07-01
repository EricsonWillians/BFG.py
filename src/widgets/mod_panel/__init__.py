from __future__ import annotations

from pathlib import Path
from typing import List

from PyQt5.Qt import Qt
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from src.widgets.pwad_info import PWadInfo
from src.widgets.pwad_list import PWadList


def _looks_like_match(path: str, query: str) -> bool:
    if not query:
        return True
    query = query.lower()
    p = path.lower()
    basename = Path(path).name.lower()
    return query in basename or query in p


class ModPanel(QGroupBox):
    """Single, focused workspace for PWAD/PK3 list and metadata.

    This replaces the previous spread-out controls with one compact section:
    search + toolbar + list + inline metadata.
    """

    addRequested = pyqtSignal()
    selectedPathsChanged = pyqtSignal(list)
    modsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Mods", parent)
        self._all_items = []

        root = QVBoxLayout()
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._searchInput = QLineEdit()
        self._searchInput.setPlaceholderText("Filter mods by filename or path...")
        self._searchInput.textChanged.connect(self._apply_filter)
        header.addWidget(self._searchInput, 1)

        self._countLabel = QLabel("0 mods loaded")
        self._countLabel.setObjectName("modCount")
        header.addWidget(self._countLabel)
        root.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.addButton = QPushButton("＋ Add Mods…")
        self.addButton.setToolTip("Open file dialog to add .wad/.pk3 files")
        self.addButton.clicked.connect(self.addRequested.emit)

        self.removeButton = QPushButton("− Remove")
        self.removeButton.setToolTip("Remove selected mods")
        self.removeButton.clicked.connect(self.removeSelected)

        self.pwadList = PWadList()
        self.pwadList.setMinimumHeight(140)
        self.pwadList.itemSelectionChanged.connect(self._handle_selection_change)
        self.pwadList.orderChanged.connect(self._on_model_changed)

        self.moveUpButton = QPushButton("↑")
        self.moveUpButton.setToolTip("Move selected mods up in launch order")
        self.moveUpButton.setMaximumWidth(34)
        self.moveUpButton.clicked.connect(self.pwadList.moveUp)

        self.moveDownButton = QPushButton("↓")
        self.moveDownButton.setToolTip("Move selected mods down in launch order")
        self.moveDownButton.setMaximumWidth(34)
        self.moveDownButton.clicked.connect(self.pwadList.moveDown)

        toolbar.addWidget(self.addButton)
        toolbar.addWidget(self.removeButton)
        toolbar.addStretch(1)
        toolbar.addWidget(self.moveUpButton)
        toolbar.addWidget(self.moveDownButton)
        root.addLayout(toolbar)

        self.pwadInfo = PWadInfo()

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.pwadList)
        splitter.addWidget(self.pwadInfo)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.setLayout(root)

    def setMods(self, paths: List[str]):
        self._all_items = []
        self.pwadList.clear()
        for path in paths:
            if self.pwadList.addWad(path):
                self._all_items.append(path)
        self._refresh_count()
        self._apply_filter()
        self._handle_selection_change()
        self._emit_selection()

    def addMods(self, paths: List[str]):
        added = 0
        for path in paths:
            if self.pwadList.addWad(path):
                self._all_items.append(path)
                added += 1
        if added:
            self._refresh_count()
            self._emit_selection()
            self._on_model_changed()

    def selectedPaths(self) -> List[str]:
        return [
            item.data(0, Qt.UserRole)
            for item in self.pwadList.selectedItems()
            if item.data(0, Qt.UserRole)
        ]

    def allPaths(self) -> List[str]:
        return [
            item.data(0, Qt.UserRole)
            for item in self.pwadList.getItems()
            if item.data(0, Qt.UserRole)
        ]

    def removeSelected(self):
        selected = self.pwadList.selectedItems()
        if not selected:
            return
        for item in reversed(selected):
            path = item.data(0, Qt.UserRole)
            if path in self._all_items:
                self._all_items.remove(path)
            self.pwadList.takeTopLevelItem(self.pwadList.indexOfTopLevelItem(item))
        self._emit_selection()
        self._on_model_changed()

    def removePaths(self, paths: list[str]):
        if not paths:
            return

        wanted = set(paths)
        removed = 0
        for item in reversed(self.pwadList.getItems()):
            path = item.data(0, Qt.UserRole)
            if path in wanted:
                self._all_items = [entry for entry in self._all_items if entry != path]
                removed += 1
                self.pwadList.takeTopLevelItem(self.pwadList.indexOfTopLevelItem(item))

        if removed:
            self._emit_selection()
            self._on_model_changed()

    def _handle_selection_change(self):
        selected = len(self.pwadList.selectedItems())
        has_selection = selected > 0
        self.removeButton.setEnabled(has_selection)
        self.moveUpButton.setEnabled(has_selection)
        self.moveDownButton.setEnabled(has_selection)
        self.selectedPathsChanged.emit(self.selectedPaths())
        self.pwadInfo.showInfo(self.selectedPaths())

    def _on_model_changed(self):
        # Keep internal ordered list in sync with the tree ordering.
        self._all_items = self.allPaths()
        self._refresh_count()
        self._emit_selection()
        self.modsChanged.emit()

    def _emit_selection(self):
        self.selectedPathsChanged.emit(self.selectedPaths())

    def _refresh_count(self):
        visible = 0
        for i in range(self.pwadList.topLevelItemCount()):
            item = self.pwadList.topLevelItem(i)
            if not item.isHidden():
                visible += 1
        total = self.pwadList.topLevelItemCount()
        self._countLabel.setText(f"{total} mods • {visible} visible")

    def _apply_filter(self):
        query = self._searchInput.text().strip()
        for i in range(self.pwadList.topLevelItemCount()):
            item = self.pwadList.topLevelItem(i)
            path = item.data(0, Qt.UserRole)
            item.setHidden(not _looks_like_match(path, query))
        self._refresh_count()
