import datetime
import hashlib
import json
import os
import re
import struct
import tempfile
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QGroupBox, QPlainTextEdit, QVBoxLayout

from src.config import LauncherConfig


def _asset_cache_path() -> Path:
    base = os.getenv("BFG_CACHE_DIR")
    if not base:
        base = os.path.join(Path.home(), ".cache", "bfg.py")
    path = Path(base) / "mod_metadata_cache.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _state_label(state: str) -> str:
    if state == "cached":
        return "CACHED"
    if state == "pending":
        return "LOADING"
    if state == "missing":
        return "MISSING"
    if state == "error":
        return "ERROR"
    return "UNKNOWN"


def _human_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _sidecar_metadata(path: str) -> Dict:
    candidate = Path(path).with_suffix(Path(path).suffix + ".bfg-meta.json")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _wad_details(path: str) -> str:
    info = []
    try:
        with open(path, 'rb') as fh:
            ident = fh.read(4).decode('ascii', 'ignore')
            if ident not in {'IWAD', 'PWAD'}:
                return ''
            num = struct.unpack('<I', fh.read(4))[0]
            offset = struct.unpack('<I', fh.read(4))[0]
            info.append(f'Format       : {ident}')
            info.append(f'Lump count   : {num:,}')
            fh.seek(offset)
            names = []
            maps = []
            for i in range(num):
                pos, size = struct.unpack('<II', fh.read(8))
                name = fh.read(8).decode('ascii', 'ignore').rstrip('\0')
                if len(names) < 12:
                    names.append(name)
                if re.match(r"^(E\dM\d|MAP\d\d)$", name, re.IGNORECASE):
                    maps.append(name)
            info.append(f'Maps         : {", ".join(maps[:16]) if maps else "none detected"}')
            if len(maps) > 16:
                info[-1] += f" (+{len(maps) - 16} more)"
            info.append(f'First lumps  : {", ".join(names)}')
    except (OSError, struct.error):
        return "File structure could not be read."
    return '\n'.join(info)


def _pk3_details(path: str) -> str:
    info = []
    try:
        with zipfile.ZipFile(path) as zf:
            entries = zf.infolist()
            names = [zi.filename for zi in entries if not zi.is_dir()]
            maps = sorted({
                match.group(1).upper()
                for name in names
                for match in [re.search(r"(?:^|/)(E\dM\d|MAP\d\d)(?:\.|/|$)", name, re.IGNORECASE)]
                if match
            })
            info.append(f'Format       : ZIP package')
            info.append(f'File count   : {len(names):,}')
            info.append(f'Maps         : {", ".join(maps[:16]) if maps else "none detected"}')
            info.append(f'Contents     : {", ".join(names[:10])}')
            if len(names) > 10:
                info[-1] += f" (+{len(names) - 10} more)"
    except (OSError, zipfile.BadZipFile):
        pass
    return '\n'.join(info)


def describe(path: str) -> str:
    target = Path(path)
    lines = [
        f'FILE         : {target.name}',
        f'LOCATION     : {target.parent}',
    ]
    try:
        stat = os.stat(path)
        lines.append(f'SIZE         : {_human_size(stat.st_size)} ({stat.st_size:,} bytes)')
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        lines.append(f'MODIFIED     : {mtime:%Y-%m-%d %H:%M}')
    except OSError:
        lines.append('STATUS       : MISSING FILE')

    remote = _sidecar_metadata(path)
    if remote:
        downloaded_at = remote.get("downloaded_at")
        downloaded = ""
        try:
            downloaded = datetime.datetime.fromtimestamp(int(downloaded_at)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            pass
        lines.extend([
            "",
            "-- IDGAMES RECORD --------------------------------",
            f"TITLE        : {remote.get('title') or target.name}",
            f"SOURCE       : {remote.get('source_name') or remote.get('source_id') or 'unknown'}",
            f"REMOTE PATH  : {remote.get('remote_path') or 'unknown'}",
        ])
        if downloaded:
            lines.append(f"DOWNLOADED   : {downloaded}")
        description = str(remote.get("description") or remote.get("metadata_text") or "").strip()
        if description:
            lines.extend(["", "DESCRIPTION", description])

    if path.lower().endswith('.pk3') or zipfile.is_zipfile(path):
        detail = _pk3_details(path)
        if detail:
            lines.extend(["", "-- PACKAGE CONTENTS ------------------------------", detail])
    else:
        detail = _wad_details(path)
        if detail:
            lines.extend(["", "-- WAD DIRECTORY ---------------------------------", detail])
    return '\n'.join(lines)


class _ModInfoCache:
    """Persistent bounded metadata cache with integrity-safe writes."""

    _MAX_ENTRIES = 500
    _MAX_BYTES = 4_000_000
    _TTL_SECONDS = 24 * 60 * 60
    _cache_file = _asset_cache_path()
    _cache: OrderedDict[str, Dict] = OrderedDict()

    @classmethod
    def _cache_key(cls, path: str) -> str:
        try:
            stat = os.stat(path)
            digest = f"{Path(path).resolve()}::{stat.st_size}::{stat.st_mtime_ns}"
        except OSError:
            digest = f"{Path(path).resolve()}::missing"
        return hashlib.sha256(digest.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls):
        if not cls._cache_file.exists():
            return
        try:
            with cls._cache_file.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
            if not isinstance(payload, dict):
                return

            now = int(datetime.datetime.now().timestamp())
            for key, raw in list(payload.items()):
                if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                    continue
                if now - int(raw.get("cached_at", 0)) > cls._TTL_SECONDS:
                    continue
                cls._cache[key] = {
                    "text": raw["text"],
                    "cached_at": int(raw.get("cached_at", now)),
                    "size": len(raw["text"]) if raw.get("text") else 0,
                }

            cls._cache = OrderedDict(
                sorted(cls._cache.items(), key=lambda pair: pair[1]["cached_at"])  
            )
        except (OSError, json.JSONDecodeError):
            return

    @classmethod
    def _flush(cls):
        payload = {
            key: {
                "text": value["text"],
                "cached_at": value.get("cached_at", int(datetime.datetime.now().timestamp())),
                "size": value.get("size", len(value.get("text", ""))),
            }
            for key, value in cls._cache.items()
            if isinstance(value, dict) and isinstance(value.get("text"), str)
        }
        if not payload:
            try:
                if cls._cache_file.exists():
                    cls._cache_file.unlink()
            except OSError:
                pass
            return

        tmp = cls._cache_file.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp)
            tmp.replace(cls._cache_file)
        except OSError:
            return

    @classmethod
    def _enforce_limits(cls):
        max_bytes = max(256_000, int(LauncherConfig().performance.mod_cache_bytes))
        max_entries = max(1, int(LauncherConfig().performance.mod_cache_entries))
        total_bytes = 0
        for value in cls._cache.values():
            total_bytes += int(value.get("size", 0))

        now = int(datetime.datetime.now().timestamp())
        for key in list(cls._cache.keys()):
            entry = cls._cache[key]
            if now - int(entry.get("cached_at", 0)) > cls._TTL_SECONDS:
                cls._cache.pop(key)

        while len(cls._cache) > max_entries or total_bytes > max_bytes:
            if not cls._cache:
                break
            _, entry = cls._cache.popitem(last=False)
            total_bytes -= int(entry.get("size", 0))

    @classmethod
    def get(cls, path: str) -> Optional[str]:
        key = cls._cache_key(path)
        entry = cls._cache.get(key)
        if not entry:
            return None
        entry = dict(entry)
        cached = entry.get("text")
        if not isinstance(cached, str):
            return None

        cls._cache.pop(key)
        cls._cache[key] = entry
        return cached

    @classmethod
    def set(cls, path: str, text: str) -> None:
        if not text:
            return
        key = cls._cache_key(path)
        cls._cache[key] = {
            "text": text,
            "cached_at": int(datetime.datetime.now().timestamp()),
            "size": len(text),
        }
        cls._cache.move_to_end(key)
        cls._enforce_limits()
        cls._flush()


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
                if not os.path.isfile(path):
                    self.signals.progress.emit(self.request_id, index, total, path, "")
                    continue
                text = describe(path)
                payload[path] = text
                self.signals.progress.emit(self.request_id, index, total, path, text)
            self.signals.finished.emit(self.request_id, payload)
        except Exception as exc:  # pragma: no cover - defensive
            self.signals.error.emit(self.request_id, str(exc))


class ModMetadataService(QObject):
    """Worker service with explicit request IDs and cancellation."""

    result_ready = pyqtSignal(int, dict)
    progress = pyqtSignal(int, int, int, str, str)
    failed = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker_pool = QThreadPool.globalInstance()
        self._last_token = 0
        self._cancelled = set()

    def request(self, paths: List[str], token: int) -> None:
        self._last_token = token
        if token in self._cancelled:
            self._cancelled.remove(token)

        worker = ModInfoWorker(token, list(paths))
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_failed)
        self._worker_pool.start(worker)

    def cancel(self, token: int) -> None:
        self._cancelled.add(token)

    def _is_canceled(self, token: int) -> bool:
        return token in self._cancelled

    def _on_progress(self, token: int, done: int, total: int, path: str, text: str):
        if self._is_canceled(token):
            return
        self.progress.emit(token, done, total, path, text)

    def _on_finished(self, token: int, payload: dict):
        if self._is_canceled(token):
            return
        for path, text in payload.items():
            if text:
                _ModInfoCache.set(path, text)
        self.result_ready.emit(token, payload)

    def _on_failed(self, token: int, error: str):
        if self._is_canceled(token):
            return
        self.failed.emit(token, error)


class PWadInfo(QGroupBox):
    """Widget showing detailed information about selected mods."""

    def __init__(self, title='MOD INTELLIGENCE'):
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

        self._worker = ModMetadataService(self)
        self._request_id = 0
        self._active_request_id = 0
        self._last_paths: List[str] = []
        self._last_state: Dict[str, str] = {}

        self._worker.result_ready.connect(self._on_worker_result)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.failed.connect(self._on_worker_failed)

        _ModInfoCache.load()

    def showInfo(self, paths):
        self._request_id += 1
        request_id = self._request_id

        if self._active_request_id:
            self._worker.cancel(self._active_request_id)

        self._active_request_id = request_id
        self._last_paths = list(paths)
        self._last_state = {}

        if not self._last_paths:
            self.text.setPlainText(
                "SELECT A MOD TO INSPECT\n\n"
                "BFG will show file health, map/package contents, and preserved\n"
                "idgames provenance for mods downloaded through the browser."
            )
            return

        pending = []
        for path in self._last_paths:
            cached = _ModInfoCache.get(path)
            if cached is None:
                if os.path.exists(path):
                    self._last_state[path] = "pending"
                    pending.append(path)
                else:
                    self._last_state[path] = "missing"
            else:
                self._last_state[path] = "cached"
                continue

        self._render()
        if not pending:
            return

        self._worker.request(pending, request_id)

    def _on_worker_progress(self, request_id: int, done: int, total: int, path: str, text: str):
        if request_id != self._active_request_id:
            return
        self._last_state[path] = "cached" if text else "error"
        self._render(done=done, total=total)

    def _on_worker_result(self, request_id: int, payload: Dict[str, str]):
        if request_id != self._active_request_id:
            return
        for path, text in payload.items():
            if text:
                self._last_state[path] = "cached"
            else:
                self._last_state[path] = "error"
            _ModInfoCache.set(path, text)
        self._render(done=len(self._last_paths), total=len(self._last_paths))

    def _on_worker_failed(self, request_id: int, error_message: str):
        if request_id != self._active_request_id:
            return
        self.text.setPlainText(f"Failed to read mod metadata: {error_message}")

    def _render(self, done: int = 0, total: int = 0) -> None:
        blocks = []
        for path in self._last_paths:
            state = self._last_state.get(path, "missing")
            cached = _ModInfoCache.get(path)
            if cached is not None:
                state = "cached"
                blocks.append(f"[{_state_label(state)}] {path}\n{cached}")
            elif state == "pending":
                blocks.append(f"[{_state_label('pending')}] {path}\nScanning...")
            elif state == "missing":
                blocks.append(f"[{_state_label('missing')}] {path}\nPath is missing or invalid.")
            else:
                blocks.append(f"[{_state_label('error')}] {path}\nUnable to read metadata.")

        selected_count = len(self._last_paths)
        heading = f"SELECTED: {selected_count} MOD{'S' if selected_count != 1 else ''}\n" + ("=" * 54)
        text = heading + "\n\n" + "\n\n".join(blocks)
        if done and total:
            text += f"\n\nProgress: {done}/{total}"
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(0)
        self.text.moveCursor(QTextCursor.Start)
