import sys
from pathlib import Path
import os

from src.launch_controller import resolve_source_port

from PyQt5.QtWidgets import QAction, QFileDialog
from PyQt5.QtWidgets import QMessageBox


class OpenSourcePortAction(QAction):
    def __init__(self, widget, setSourcePort, config, saveSourcePortPath, browse_handler=None):
        super().__init__("&Browse Source Port...", widget)
        self.widget = widget
        self.setShortcut("Ctrl+O")
        self.setStatusTip("Browse a source port executable")
        self.triggered.connect(self._open)
        self.setSourcePort = setSourcePort
        self.config = config
        self.saveSourcePortPath = saveSourcePortPath
        self.browse_handler = browse_handler

    def _open(self):
        if self.browse_handler is not None:
            filename = self.browse_handler()
        else:
            filename = self.browse_source_port(self.widget)
        if not filename:
            return
        self._apply_source_port_selection(filename)

    def browse_source_port(self, parent=None) -> str | None:
        selected = self._open_file_dialog(parent=parent)
        if not selected:
            return None
        return selected

    def browse_source_port_directory(self, parent=None) -> str | None:
        selected_dir = self._open_directory_dialog(parent=parent)
        if not selected_dir:
            return None
        return self._resolve_source_port_directory(Path(selected_dir))

    def _open_file_dialog(self, parent=None) -> str | None:
        start_dir = self._source_port_start_dir()
        file_filter = self._source_port_filter()
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog

        filename, _ = QFileDialog.getOpenFileName(
            parent or self.widget,
            "Select a source port",
            start_dir,
            file_filter,
            options=options,
        )
        if not filename:
            return None
        return str(Path(filename).expanduser())

    def _open_directory_dialog(self, parent=None) -> str | None:
        start_dir = self._source_port_start_dir()
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        selected = QFileDialog.getExistingDirectory(
            parent or self.widget,
            "Select source port folder",
            start_dir,
            options=options,
        )
        if not selected:
            return None
        return str(Path(selected).expanduser())

    def open(self):
        self._open()

    def _source_port_filter(self) -> str:
        if sys.platform.startswith("win"):
            return (
                "Source port executables (*.exe *.bat *.cmd *.com);;"
                "Executable files (*);;"
                "All files (*)"
            )
        if sys.platform == "darwin":
            return (
                "Source port executables (*.app);;"
                "Executable files (*);;"
                "All files (*)"
            )
        return (
            "Executable files (*);;"
            "All files (*)"
        )

    def _source_port_start_dir(self) -> str:
        source_port = str(getattr(self.config, "source_port_path", "") or "").strip()
        configured = Path(source_port).expanduser() if source_port else None

        start_dir = str(getattr(self.config, "source_port_dir", "") or "").strip()
        if start_dir:
            candidate = Path(start_dir).expanduser()
            if candidate.is_dir() and candidate.exists():
                return str(candidate)

        try:
            legacy_dir = self.config.get("sourcePortDir")
            if isinstance(legacy_dir, str):
                candidate = Path(legacy_dir).expanduser()
                if candidate.is_dir() and candidate.exists():
                    return str(candidate)
        except Exception:
            pass

        if configured:
            if configured.is_file():
                return str(configured.parent)
            if configured.is_dir():
                return str(configured)

        return str(Path.home())

    def _apply_source_port_selection(self, filename: str) -> bool:
        resolved = self._resolve_source_port_path(filename)
        if resolved:
            self.config.source_port_dir = str(Path(resolved).expanduser().parent)
            self.saveSourcePortPath(resolved)
            self.setSourcePort(resolved)
            return True

        selected = str(Path(filename).expanduser())
        should_use_raw = QMessageBox.question(
            self.widget,
            "Source Port",
            f"Could not verify executable status for:\n{selected}\n\n"
            "Use this path anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if should_use_raw == QMessageBox.Yes:
            self.config.source_port_dir = str(Path(selected).parent)
            self.saveSourcePortPath(selected)
            self.setSourcePort(selected)
            return True

        return False

    def _resolve_source_port_path(self, filename: str) -> str | None:
        selected = Path(filename).expanduser()
        if selected.is_dir():
            return self._resolve_source_port_directory(selected)

        resolved, failure = resolve_source_port(selected, discover=False)
        if not resolved:
            if failure:
                QMessageBox.warning(self.widget, "Source Port", failure)
            else:
                QMessageBox.warning(self.widget, "Source Port", "The selected source port path is invalid.")
            return None
        return str(Path(resolved).resolve())

    def _resolve_source_port_directory(self, folder: Path) -> str | None:
        try:
            entries = [entry for entry in folder.iterdir()]
        except OSError:
            QMessageBox.warning(
                self.widget,
                "Source Port",
                f"Could not scan directory: {folder}",
            )
            return None

        # Prefer common executable names first.
        preferred_names = (
            "gzdoom",
            "gzdoom-sdl",
            "gzdoom-sdl2",
            "zandronum",
            "prboom-plus",
            "prboom",
            "lzdoom",
        )

        if sys.platform == "darwin":
            for entry in entries:
                if entry.is_dir() and entry.suffix.lower() == ".app":
                    resolved, failure = resolve_source_port(entry, discover=False)
                    if resolved:
                        return str(Path(resolved).resolve())

        file_entries = [entry for entry in sorted(entries, key=lambda path: path.name.lower()) if entry.is_file()]
        for candidate in file_entries:
            if not os.access(str(candidate), os.X_OK):
                continue
            name = candidate.name.lower()
            for base_name in preferred_names:
                if name == base_name:
                    resolved, failure = resolve_source_port(candidate, discover=False)
                    if resolved:
                        return str(Path(resolved).resolve())
                if name == f"{base_name}.exe" or name == f"{base_name}.bat" or name == f"{base_name}.cmd" or name == f"{base_name}.com":
                    resolved, failure = resolve_source_port(candidate, discover=False)
                    if resolved:
                        return str(Path(resolved).resolve())

        # Fallback: first executable in folder.
        for candidate in file_entries:
            if os.access(str(candidate), os.X_OK):
                resolved, failure = resolve_source_port(candidate, discover=False)
                if resolved:
                    return str(Path(resolved).resolve())

        QMessageBox.warning(
            self.widget,
            "Source Port",
            f"No executable was found in: {folder}",
        )
        return None
