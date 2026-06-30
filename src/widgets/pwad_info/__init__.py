import datetime
import json
import os
import struct
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QGroupBox, QPlainTextEdit, QVBoxLayout


def _wad_details(path: str) -> str:
    info = []
    try:
        with open(path, 'rb') as fh:
            ident = fh.read(4).decode('ascii', 'ignore')
            if ident not in {'IWAD', 'PWAD'}:
                return ''
            num = struct.unpack('<I', fh.read(4))[0]
            offset = struct.unpack('<I', fh.read(4))[0]
            info.append(f'Type: {ident}')
            info.append(f'Lumps: {num}')
            fh.seek(offset)
            for i in range(min(num, 20)):
                pos, size = struct.unpack('<II', fh.read(8))
                name = fh.read(8).decode('ascii', 'ignore').rstrip('\0')
                info.append(f' {i:03d}: {name} ({size} bytes)')
        if num > 20:
            info.append(' ...')
    except OSError:
        pass
    return '\n'.join(info)


def _pk3_details(path: str) -> str:
    info = []
    try:
        with zipfile.ZipFile(path) as zf:
            info.append(f'ZIP entries: {len(zf.infolist())}')
            for zi in zf.infolist()[:20]:
                info.append(f' {zi.filename} ({zi.file_size} bytes)')
            if len(zf.infolist()) > 20:
                info.append(' ...')
    except (OSError, zipfile.BadZipFile):
        pass
    return '\n'.join(info)


def describe(path: str) -> str:
    lines = [f'Path: {path}']
    try:
        stat = os.stat(path)
        lines.append(f'Size: {stat.st_size} bytes')
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        lines.append(f'Modified: {mtime:%Y-%m-%d %H:%M:%S}')
    except OSError:
        lines.append('Missing file')

    if path.lower().endswith('.pk3') or zipfile.is_zipfile(path):
        detail = _pk3_details(path)
        if detail:
            lines.append(detail)
    else:
        detail = _wad_details(path)
        if detail:
            lines.append(detail)
    return '\n'.join(lines)


class _ModInfoCache:
    """Simple bounded cache for mod metadata with optional on-disk fallback."""

    _MAX_ENTRIES = 300
    _cache: "OrderedDict[str, str]" = OrderedDict()
    _cache_file = Path(".bfg_mod_info_cache.json")

    @classmethod
    def _cache_key(cls, path: str) -> str:
        try:
            stat = os.stat(path)
            return f"{Path(path).resolve()}::{stat.st_size}::{stat.st_mtime_ns}"
        except OSError:
            return f"{Path(path).resolve()}::missing"

    @classmethod
    def load(cls):
        if not cls._cache_file.exists():
            return
        try:
            with cls._cache_file.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
            if not isinstance(payload, dict):
                return
            for key in list(payload.keys())[: cls._MAX_ENTRIES]:
                if isinstance(payload[key], str):
                    cls._cache[key] = payload[key]
        except (OSError, json.JSONDecodeError):
            return

    @classmethod
    def _flush(cls):
        try:
            with cls._cache_file.open("w", encoding="utf-8") as fp:
                json.dump(cls._cache, fp)
        except OSError:
            pass

    @classmethod
    def get(cls, path: str) -> Optional[str]:
        key = cls._cache_key(path)
        if key not in cls._cache:
            return None
        value = cls._cache.pop(key)
        cls._cache[key] = value
        return value

    @classmethod
    def set(cls, path: str, text: str):
        key = cls._cache_key(path)
        cls._cache[key] = text
        if len(cls._cache) > cls._MAX_ENTRIES:
            cls._cache.popitem(last=False)
        cls._flush()


_ModInfoCache.load()


class _ModInfoSignals(QObject):
    finished = pyqtSignal(int, dict)
    error = pyqtSignal(int, str)
    progress = pyqtSignal(int, int, int, str, str)


class ModInfoWorker(QRunnable):
    def __init__(self, request_id: int, paths: List[str]):
        super().__init__()
        self.request_id = request_id
        self.paths = paths
        self.signals = _ModInfoSignals()

    @pyqtSlot()
    def run(self):
        payload: Dict[str, str] = {}
        total = len(self.paths)
        try:
            for index, path in enumerate(self.paths, start=1):
                text = describe(path)
                payload[path] = text
                self.signals.progress.emit(self.request_id, index, total, path, text)
            self.signals.finished.emit(self.request_id, payload)
        except Exception as exc:
            self.signals.error.emit(self.request_id, str(exc))


class PWadInfo(QGroupBox):
    """Widget showing detailed information about selected mods."""

    def __init__(self, title='📁 Mod Info'):
        super().__init__(title)
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setObjectName("doomModInfo")
        self.text.setMinimumHeight(100)
        self.text.setMaximumBlockCount(3000)
        from PyQt5.QtWidgets import QSizePolicy
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout.addWidget(self.text)
        self.setLayout(layout)

        # Keep bounded per-session cache and use a global worker pool for scanning.
        self._worker_pool = QThreadPool.globalInstance()
        self._request_id = 0
        self._active_request_id = 0
        self._last_paths: List[str] = []
        self._active_payload: Dict[str, str] = {}

    def showInfo(self, paths):
        """Display information for selected mod paths asynchronously."""
        self._request_id += 1
        request_id = self._request_id
        self._active_request_id = request_id
        self._last_paths = list(paths)
        self._active_payload = {}

        if not self._last_paths:
            self.text.setPlainText("Select a mod to inspect file details")
            return

        cached = []
        missing = []
        for path in self._last_paths:
            cached_text = _ModInfoCache.get(path)
            if cached_text is None:
                missing.append(path)
            else:
                cached.append((path, cached_text))

        if not missing:
            self._render_result(len(self._last_paths), len(self._last_paths))
            return

        for path, text in cached:
            self._active_payload[path] = text
        self.text.setPlainText(
            f"Loading mod metadata ({len(self._last_paths)} file(s))..."
        )
        worker = ModInfoWorker(request_id, missing)
        worker.signals.progress.connect(self._on_worker_progress)
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.error.connect(self._on_worker_error)
        self._worker_pool.start(worker)

    def _render_result(self, done: int, total: int) -> None:
        ordered = []
        for path in self._last_paths:
            cached = _ModInfoCache.get(path)
            if cached is not None:
                ordered.append(cached)
                continue

            cached = self._active_payload.get(path)
            if cached is not None:
                ordered.append(cached)
            elif done >= total:
                ordered.append(f"Path: {path}\nMissing or unreadable file")
            else:
                ordered.append(f"Path: {path}\n(Scanning...)")

        text = "\n\n".join(ordered)
        if total:
            text += f"\n\nProgress: {done}/{total}"
        self.text.setPlainText(text)

    def _on_worker_progress(self, request_id: int, done: int, total: int, path: str, text: str):
        if request_id != self._active_request_id:
            return
        self._active_payload[path] = text
        _ModInfoCache.set(path, text)
        self._render_result(done, total)

    def _on_worker_finished(self, request_id: int, payload: Dict[str, str]):
        if request_id != self._active_request_id:
            return
        for path, text in payload.items():
            self._active_payload[path] = text
            _ModInfoCache.set(path, text)
        self._render_result(len(self._last_paths), len(self._last_paths))

    def _on_worker_error(self, request_id: int, error_message: str):
        if request_id != self._active_request_id:
            return
        if not self._last_paths:
            self.text.setPlainText("No mod files selected.")
            return
        self.text.setPlainText(
            f"Failed to read mod metadata.\n{error_message}"
        )
