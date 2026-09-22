import os
from pathlib import Path
from PyQt5.Qt import Qt
from PyQt5.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QAbstractItemView,
)
from PyQt5.QtCore import pyqtSignal


def _file_size(path: str) -> str:
    """Return a compact, human-readable file size."""
    try:
        size = float(os.path.getsize(path))
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit in {"B", "KB"} else f"{size:.1f} {unit}"
            size /= 1024
    except OSError:
        return "—"


def _mod_kind(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix.upper() if suffix else "FILE"


class PWadList(QTreeWidget):
    """Tree widget listing PWAD/PK3 files."""

    orderChanged = pyqtSignal()
    filesDropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("loadoutTree")
        self.setColumnCount(5)
        self.setHeaderLabels(["#", "Mod / package", "Type", "Size", "Status"])
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setRootIsDecorated(False)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setToolTip('Launch order runs top to bottom. Drag files in, drag rows to reorder, or press Delete to remove.')
        self.setColumnWidth(0, 42)
        self.setColumnWidth(1, 210)
        self.setColumnWidth(2, 58)
        self.setColumnWidth(3, 76)

    def moveUp(self):
        """Move the selected items up by one position."""
        selected = self.selectedItems()
        if not selected:
            return
        for item in selected:
            idx = self.indexOfTopLevelItem(item)
            if idx > 0:
                self.takeTopLevelItem(idx)
                self.insertTopLevelItem(idx - 1, item)
                self.setCurrentItem(item)
        if selected:
            self._renumber()
            self.orderChanged.emit()

    def moveDown(self):
        """Move the selected items down by one position."""
        selected = self.selectedItems()
        if not selected:
            return
        # process in reverse to avoid leapfrogging
        for item in reversed(selected):
            idx = self.indexOfTopLevelItem(item)
            if idx < self.topLevelItemCount() - 1:
                self.takeTopLevelItem(idx)
                self.insertTopLevelItem(idx + 1, item)
                self.setCurrentItem(item)
        if selected:
            self._renumber()
            self.orderChanged.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            deleted = False
            for item in self.selectedItems():
                index = self.indexOfTopLevelItem(item)
                self.takeTopLevelItem(index)
                deleted = True
            if deleted:
                self._renumber()
                self.orderChanged.emit()
        else:
            super().keyPressEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls():
            supported = {".wad", ".pk3", ".ipk3", ".pk7", ".pke", ".zip"}
            paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in supported
            ]
            if paths:
                event.acceptProposedAction()
                self.filesDropped.emit(paths)
                return
        super().dropEvent(event)
        self._renumber()
        self.orderChanged.emit()

    def getItems(self):
        items = []
        for n in range(self.topLevelItemCount()):
            items.append(self.topLevelItem(n))
        return items

    def addWad(self, path: str, *, emit_change: bool = True):
        """Add a wad entry with size information if not already present."""
        paths = [i.data(0, Qt.UserRole) for i in self.getItems()]
        if path in paths:
            return False
        exists = os.path.isfile(path)
        item = QTreeWidgetItem([
            "",
            os.path.basename(path),
            _mod_kind(path),
            _file_size(path),
            "READY" if exists else "MISSING",
        ])
        item.setToolTip(1, f"{path}\nFolder: {os.path.dirname(path) or '.'}")
        item.setData(0, Qt.UserRole, path)
        if not exists:
            item.setForeground(4, Qt.red)
        self.addTopLevelItem(item)
        self._renumber()
        if emit_change:
            self.orderChanged.emit()
        return True

    def refresh_statuses(self):
        """Re-stat every row so READY/MISSING reflects the current filesystem
        (files may be downloaded into place or deleted externally)."""
        changed = False
        for item in self.getItems():
            path = item.data(0, Qt.UserRole)
            if not path:
                continue
            exists = os.path.isfile(path)
            label = "READY" if exists else "MISSING"
            if item.text(4) != label:
                item.setText(4, label)
                changed = True
            if exists:
                item.setData(4, Qt.ForegroundRole, None)
            else:
                item.setForeground(4, Qt.red)
        if changed:
            self.orderChanged.emit()

    def _renumber(self):
        for index, item in enumerate(self.getItems(), start=1):
            item.setText(0, f"{index:02d}")
