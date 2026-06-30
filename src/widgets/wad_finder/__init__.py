from __future__ import annotations

import gzip
import json
import os
import re
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

UA = "BFG.py wad browser/1.0 (+https://github.com)"
DEFAULT_EXTENSIONS = (".wad", ".pk3", ".ipk3", ".pke", ".zip")
INDEX_TTL_SECONDS = 60 * 60 * 6
DEFAULT_SOURCE_LIMIT = 300

DEFAULT_SOURCES = {
    "cyberd": {
        "id": "cyberd",
        "name": "Doomworld / idgames mirror (cyberd)",
        "base": "https://idgames.cyberd.org",
        "index": "fullsort.gz",
        "browser": "https://idgames.cyberd.org/",
    },
    "youfailit": {
        "id": "youfailit",
        "name": "Doomworld / idgames mirror (youfailit)",
        "base": "https://youfailit.net/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://youfailit.net/pub/idgames",
    },
}


@dataclass(frozen=True)
class WadBrowserResult:
    title: str
    source_id: str
    source_name: str
    size_bytes: int
    remote_path: str
    download_url: str
    browser_url: str


@dataclass
class _IndexCacheEntry:
    source_id: str
    source_name: str
    fetched_at: float
    entries: List[WadBrowserResult]


class _SearchSignals(QObject):
    finished = pyqtSignal(int, str, list)
    failed = pyqtSignal(int, str, str)


class _DownloadSignals(QObject):
    progress = pyqtSignal(int, int, int, str)
    finished = pyqtSignal(int, str, str)
    failed = pyqtSignal(int, str, str)


class _SearchWorker(QRunnable):
    def __init__(self, token: int, source_id: str, source: Dict[str, str]):
        super().__init__()
        self.token = token
        self.source_id = source_id
        self.source = source
        self.signals = _SearchSignals()

    @staticmethod
    def _fetch_index(url: str) -> str:
        response = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=30,
        )
        response.raise_for_status()

        data = response.content
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _parse_fullsort(text: str) -> List[tuple[int, str]]:
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("fullpath") or line.startswith("Control switch"):
                continue
            if not re.match(r"^\d{4}/\d{2}/\d{2}", line):
                continue
            chunks = line.split(None, 2)
            if len(chunks) < 3:
                continue
            try:
                size = int(chunks[1])
            except (ValueError, TypeError):
                continue
            path = _normalize_remote_path(chunks[2])
            if not _looks_like_mod_file(path):
                continue
            entries.append((size, path))
        return entries

    def run(self):
        try:
            index_url = urljoin(self.source["base"], self.source["index"])
            text = self._fetch_index(index_url)
            raw_items = self._parse_fullsort(text)

            parsed: List[WadBrowserResult] = []
            for size, path in raw_items:
                parsed.append(
                    WadBrowserResult(
                        title=Path(path).name,
                        source_id=self.source_id,
                        source_name=self.source["name"],
                        size_bytes=size,
                        remote_path=path,
                        download_url=urljoin(self.source["base"].rstrip("/") + "/", path),
                        browser_url=urljoin(self.source["browser"].rstrip("/") + "/", path),
                    )
                )
            self.signals.finished.emit(self.token, self.source_id, parsed)
        except Exception as exc:  # pragma: no cover - network path
            self.signals.failed.emit(self.token, self.source_id, str(exc))


class _DownloadWorker(QRunnable):
    def __init__(self, token: int, url: str, destination: str):
        super().__init__()
        self.token = token
        self.url = url
        self.destination = destination
        self.signals = _DownloadSignals()

    @pyqtSlot()
    def run(self):
        try:
            with requests.get(
                self.url,
                stream=True,
                headers={"User-Agent": UA},
                timeout=120,
            ) as response:
                response.raise_for_status()

                total = int(response.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                tmp = f"{self.destination}.part"
                with open(tmp, "wb") as fp:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        fp.write(chunk)
                        downloaded += len(chunk)
                        self.signals.progress.emit(self.token, downloaded, total, self.url)
                os.replace(tmp, self.destination)
            self.signals.finished.emit(self.token, self.destination, self.url)
        except Exception as exc:  # pragma: no cover - network path
            tmp = f"{self.destination}.part"
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.signals.failed.emit(self.token, self.url, str(exc))


def _looks_like_mod_file(path: str) -> bool:
    path = path.lower()
    return path.endswith(DEFAULT_EXTENSIONS)


def _normalize_remote_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    return path.lstrip("/")


def _normalize_source_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")


def _safe_library_name(path: str, source_id: str, target_dir: Path) -> Path:
    file_name = os.path.basename(path)
    if not file_name:
        file_name = f"mod_{source_id}.pkg"
    candidate = target_dir / file_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    idx = 1
    while True:
        alt = target_dir / f"{stem}_{idx}__{source_id}{suffix}"
        if not alt.exists():
            return alt
        idx += 1


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "—"
    units = [(1024**4, "TB"), (1024**3, "GB"), (1024**2, "MB"), (1024, "KB")]
    for divisor, name in units:
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.1f} {name}"
    return f"{size_bytes} B"


class WadFinder(QWidget):
    addRequested = pyqtSignal(list)
    statusChanged = pyqtSignal(str)

    def __init__(self, parent=None, library_dir: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Mod Browser")
        self.setToolTip("Search and download community PWADs and add them to launch order")

        self.searchSources: Dict[str, Dict[str, str]] = dict(DEFAULT_SOURCES)
        self.sourceOrder: List[str] = list(self.searchSources.keys())

        cache_root = Path(os.getenv("BFG_CACHE_DIR", Path.home() / ".cache" / "bfg.py"))
        self.cache_root = cache_root
        self.library_dir = Path(library_dir).expanduser() if library_dir else cache_root / "mods"
        self.library_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_root / "source_index").mkdir(parents=True, exist_ok=True)

        self._thread_pool = QThreadPool.globalInstance()
        self._search_token = 0
        self._download_token = 0
        self._search_sessions: Dict[int, Dict[str, Any]] = {}
        self._download_sessions: Dict[int, Dict[str, Any]] = {}
        self._index_cache: Dict[str, _IndexCacheEntry] = {}

        self._load_index_cache()
        self.initUi()

    def initUi(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        title = QLabel("PWAD Browser")
        title.setFrameStyle(QFrame.Box | QFrame.Raised)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # Search controls.
        searchRow = QHBoxLayout()
        self.sourceCombo = QComboBox()
        self.sourceCombo.setMinimumWidth(220)
        self.sourceCombo.currentIndexChanged.connect(self._on_search_source_changed)
        self._refresh_source_combo()

        self.searchInput = QLineEdit()
        self.searchInput.setPlaceholderText("Search by filename, map, author, or mod name")
        self.searchInput.returnPressed.connect(self._on_search)

        self.searchButton = QPushButton("Search")
        self.searchButton.clicked.connect(self._on_search)
        self.clearSearchButton = QPushButton("Clear")
        self.clearSearchButton.clicked.connect(self._on_clear_search)
        searchRow.addWidget(self.sourceCombo, 1)
        searchRow.addWidget(self.searchInput, 3)
        searchRow.addWidget(self.searchButton)
        searchRow.addWidget(self.clearSearchButton)
        root.addLayout(searchRow)

        # Source management for priority and optional disable.
        sourceHeader = QLabel("Sources & priority")
        sourceHeader.setStyleSheet("font-weight: 600;")
        root.addWidget(sourceHeader)

        self.sourceOrderList = QListWidget()
        self.sourceOrderList.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sourceOrderList.itemSelectionChanged.connect(self._on_selection_changed)
        self._rebuild_source_list()

        sourceButtons = QHBoxLayout()
        self.sourceUpButton = QPushButton("Move Up")
        self.sourceUpButton.clicked.connect(lambda: self._reorder_source(-1))
        self.sourceDownButton = QPushButton("Move Down")
        self.sourceDownButton.clicked.connect(lambda: self._reorder_source(1))
        self.sourceRemoveButton = QPushButton("Remove")
        self.sourceRemoveButton.clicked.connect(self._remove_selected_custom_sources)
        self.sourceToggleButton = QPushButton("Enable/Disable")
        self.sourceToggleButton.clicked.connect(self._toggle_selected_source)

        sourceButtons.addWidget(self.sourceUpButton)
        sourceButtons.addWidget(self.sourceDownButton)
        sourceButtons.addWidget(self.sourceToggleButton)
        sourceButtons.addWidget(self.sourceRemoveButton)

        sourceAddRow = QHBoxLayout()
        self.customSourceInput = QLineEdit()
        self.customSourceInput.setPlaceholderText(
            "Custom source: URL | index (or Name | URL | index)"
        )
        self.customSourceButton = QPushButton("Add Source")
        self.customSourceButton.clicked.connect(self._add_custom_source)
        self.customSourceListReset = QPushButton("Clear Custom")
        self.customSourceListReset.clicked.connect(self._clear_custom_source)
        sourceAddRow.addWidget(self.customSourceInput, 2)
        sourceAddRow.addWidget(self.customSourceButton)
        sourceAddRow.addWidget(self.customSourceListReset)

        root.addWidget(self.sourceOrderList)
        root.addLayout(sourceButtons)
        root.addLayout(sourceAddRow)

        # Search + local library.
        self.resultsTree = QTreeWidget()
        self.resultsTree.setHeaderLabels(["Source", "Mod File", "Size", "Path"])
        self.resultsTree.setSelectionMode(self.resultsTree.ExtendedSelection)
        self.resultsTree.itemSelectionChanged.connect(self._on_selection_changed)
        self.resultsTree.setMinimumHeight(150)

        self.localTree = QTreeWidget()
        self.localTree.setHeaderLabels(["Local Mod", "Size", "Source", "Source Path"])
        self.localTree.setSelectionMode(self.localTree.ExtendedSelection)
        self.localTree.itemSelectionChanged.connect(self._on_selection_changed)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.resultsTree)
        splitter.addWidget(self.localTree)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 2)

        actions = QHBoxLayout()
        self.openButton = QPushButton("Open in Browser")
        self.openButton.clicked.connect(self._open_selected_remote)

        self.downloadButton = QPushButton("Download")
        self.downloadButton.clicked.connect(self._download_selected)

        self.addButton = QPushButton("Add to Launch List")
        self.addButton.clicked.connect(self._add_selected)

        self.deleteButton = QPushButton("Delete Downloaded")
        self.deleteButton.clicked.connect(self._delete_selected_local)

        self.openLibraryButton = QPushButton("Open Library Folder")
        self.openLibraryButton.clicked.connect(self._open_library_dir)

        actions.addWidget(self.openButton)
        actions.addWidget(self.downloadButton)
        actions.addWidget(self.addButton)
        actions.addWidget(self.deleteButton)
        actions.addWidget(self.openLibraryButton)
        root.addLayout(actions)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setWordWrap(True)
        root.addWidget(self.statusLabel)

        self._refresh_local_library()
        self._set_controls_enabled(True)
        self._on_selection_changed()

    def _load_index_cache(self):
        cache_dir = self.cache_root / "source_index"
        for source_id, source in self.searchSources.items():
            cache_file = cache_dir / f"{source_id}.json"
            if not cache_file.exists():
                continue
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                entries = payload.get("entries", [])
                parsed: List[WadBrowserResult] = []
                for item in entries:
                    parsed.append(
                        WadBrowserResult(
                            title=item["title"],
                            source_id=item["source_id"],
                            source_name=item["source_name"],
                            size_bytes=int(item["size_bytes"]),
                            remote_path=item["remote_path"],
                            download_url=item["download_url"],
                            browser_url=item["browser_url"],
                        )
                    )
                fetched_at = float(payload.get("fetched_at", 0.0))
                self._index_cache[source_id] = _IndexCacheEntry(
                    source_id=source_id,
                    source_name=source["name"],
                    fetched_at=fetched_at,
                    entries=parsed,
                )
            except Exception:
                continue

    def _store_index_cache(self, source_id: str, source: Dict[str, str], entries: List[WadBrowserResult]):
        cache_file = self.cache_root / "source_index" / f"{source_id}.json"
        payload = {
            "source_id": source_id,
            "source_name": source["name"],
            "fetched_at": time.time(),
            "entries": [
                {
                    "title": item.title,
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "size_bytes": item.size_bytes,
                    "remote_path": item.remote_path,
                    "download_url": item.download_url,
                    "browser_url": item.browser_url,
                }
                for item in entries
            ],
        }
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _is_cache_valid(self, source_id: str) -> bool:
        entry = self._index_cache.get(source_id)
        if not entry:
            return False
        return (time.time() - entry.fetched_at) <= INDEX_TTL_SECONDS

    def _refresh_source_list(self):
        self._rebuild_source_list()
        self._refresh_source_combo()

    def _rebuild_source_list(self):
        checked_map = {}
        selected = self.sourceOrderList.currentItem().data(Qt.UserRole) if self.sourceOrderList.currentItem() else None
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            checked_map[source_id] = item.checkState() == Qt.Checked
        selected_idx = 0
        selected_found = -1

        self.sourceOrderList.blockSignals(True)
        self.sourceOrderList.clear()
        for source_id in list(self.sourceOrder):
            source = self.searchSources.get(source_id)
            if not source:
                continue
            item = QListWidgetItem(f"{source['name']} ({source['base']})")
            item.setData(Qt.UserRole, source_id)
            item.setToolTip(source.get("browser", source.get("base", "")))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked_map.get(source_id, True) else Qt.Unchecked)
            item.setCheckable(True)
            self.sourceOrderList.addItem(item)
            if selected == source_id and selected_found < 0:
                selected_found = selected_idx
            selected_idx += 1
        self.sourceOrderList.blockSignals(False)

        if selected_found >= 0:
            self.sourceOrderList.setCurrentRow(selected_found)

    def _refresh_source_combo(self):
        previous_data = self.sourceCombo.currentData()
        self.sourceCombo.blockSignals(True)
        self.sourceCombo.clear()
        self.sourceCombo.addItem("All active sources", "all")
        for source_id in self.sourceOrder:
            source = self.searchSources.get(source_id)
            if not source:
                continue
            self.sourceCombo.addItem(source["name"], source_id)
        self.sourceCombo.blockSignals(False)

        idx = self.sourceCombo.findData(previous_data)
        self.sourceCombo.setCurrentIndex(0 if idx < 0 else idx)

    def _on_search_source_changed(self, *_):
        self._on_search()

    def _set_controls_enabled(self, enabled: bool):
        self.searchButton.setEnabled(enabled)
        self.clearSearchButton.setEnabled(enabled)
        self.customSourceButton.setEnabled(enabled)
        self.customSourceInput.setEnabled(enabled)
        self.customSourceListReset.setEnabled(enabled)
        self.sourceUpButton.setEnabled(enabled)
        self.sourceDownButton.setEnabled(enabled)
        self.sourceToggleButton.setEnabled(enabled)
        self.sourceRemoveButton.setEnabled(enabled)
        self.sourceOrderList.setEnabled(enabled)
        self.searchInput.setEnabled(enabled)
        self.openButton.setEnabled(enabled)
        self.downloadButton.setEnabled(enabled)
        self.addButton.setEnabled(enabled)
        self.deleteButton.setEnabled(enabled)
        self.openLibraryButton.setEnabled(enabled)
        self.resultsTree.setEnabled(enabled)
        self.localTree.setEnabled(enabled)
        self.sourceCombo.setEnabled(enabled)

    def _set_status(self, text: str):
        self.statusLabel.setText(text)
        self.statusChanged.emit(text)

    def _selected_source_ids(self) -> List[str]:
        selected_source = self.sourceCombo.currentData()
        if selected_source is None:
            return []

        if selected_source != "all":
            source = self.searchSources.get(selected_source)
            if source:
                return [selected_source]
            return []

        ordered = []
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked and source_id in self.searchSources:
                ordered.append(source_id)
        return ordered

    def _iter_source_rows(self, ids: Iterable[str]):
        for source_id in self.sourceOrder:
            if source_id in ids:
                yield source_id

    def _add_custom_source(self):
        text = self.customSourceInput.text().strip()
        if not text:
            self._set_status("Enter a mirror URL first.")
            return

        parts = [chunk.strip() for chunk in text.split("|")]
        if len(parts) >= 3:
            source_name, url, index = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            if parts[0].startswith("http://") or parts[0].startswith("https://"):
                source_name = None
                url = parts[0]
                index = parts[1]
            else:
                source_name = parts[0]
                url = parts[1]
                index = "fullsort.gz"
        else:
            source_name = None
            url = parts[0]
            index = "fullsort.gz"

        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url}"

        source_id = f"custom:{_normalize_source_id(url)}"
        if source_id in self.searchSources:
            self._set_status("This source is already added.")
            return

        self.searchSources[source_id] = {
            "id": source_id,
            "name": source_name or f"Custom source ({urlparse(url).netloc})",
            "base": url.rstrip("/"),
            "index": index,
            "browser": url.rstrip("/"),
        }
        self.sourceOrder.append(source_id)
        self._rebuild_source_list()
        self._refresh_source_combo()
        self.customSourceInput.clear()
        self._set_status(f"Added source: {url}")

    def _clear_custom_source(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if source_id.startswith("custom:"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No custom source to remove.")
        else:
            self._set_status(f"Removed {removed} custom source(s).")
        self._refresh_source_list()

    def _remove_selected_custom_sources(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a custom source in the list.")
            return

        removed = 0
        for item in selected:
            source_id = item.data(Qt.UserRole)
            if source_id and source_id.startswith("custom:"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                removed += 1
        if removed == 0:
            self._set_status("Only custom sources can be removed this way.")
        else:
            self._set_status(f"Removed {removed} source(s).")

        self._refresh_source_list()

    def _toggle_selected_source(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source in the list.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        if source_id not in self.searchSources:
            return
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self._set_status(f"Toggled source: {self.searchSources[source_id]['name']}")

    def _reorder_source(self, delta: int):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        idx = self.sourceOrder.index(source_id)
        target = idx + delta
        if target < 0 or target >= len(self.sourceOrder):
            return

        self.sourceOrder[idx], self.sourceOrder[target] = self.sourceOrder[target], self.sourceOrder[idx]
        self._rebuild_source_list()
        self._refresh_source_combo()

        self.sourceOrderList.setCurrentRow(target)
        self._set_status(f"Moved source: {self.searchSources[source_id]['name']}")

    def _on_clear_search(self):
        self.searchInput.clear()
        self.resultsTree.clear()
        self._set_status("Search cleared.")

    def _on_search(self):
        query = self.searchInput.text().strip().lower()
        source_ids = self._selected_source_ids()

        self.resultsTree.clear()
        if not source_ids:
            self._set_status("No source selected.")
            return

        self._search_token += 1
        token = self._search_token
        self._search_sessions[token] = {
            "query": query,
            "sources": list(self._iter_source_rows(source_ids)),
            "remaining": set(self._iter_source_rows(source_ids)),
            "results": {},
            "errors": {},
            "ready": False,
        }

        self._set_status("Searching...")
        self._set_controls_enabled(False)

        started = 0
        for source_id in self._search_sessions[token]["sources"]:
            source = self.searchSources.get(source_id)
            if not source:
                self._search_sessions[token]["remaining"].discard(source_id)
                continue

            cached = self._index_cache.get(source_id)
            if self._is_cache_valid(source_id) and cached is not None:
                self._on_search_ready(token, source_id, cached.entries)
                continue

            started += 1
            worker = _SearchWorker(token, source_id, source)
            worker.signals.finished.connect(self._on_search_ready)
            worker.signals.failed.connect(self._on_search_failed)
            self._thread_pool.start(worker)

        if not started:
            self._finalize_search_session(token)

    def _on_search_ready(self, token: int, source_id: str, payload: List[WadBrowserResult]):
        if token != self._search_token:
            return
        session = self._search_sessions.get(token)
        if not session:
            return

        session["remaining"].discard(source_id)

        query = session["query"]
        filtered = self._filter_search_results(payload, query)
        source = self.searchSources.get(source_id)
        if source:
            self._index_cache[source_id] = _IndexCacheEntry(
                source_id=source_id,
                source_name=source["name"],
                fetched_at=time.time(),
                entries=payload,
            )
            self._store_index_cache(source_id, source, payload)

        session["results"][source_id] = filtered
        self._render_search_results(token)

        if not session["remaining"]:
            self._finalize_search_session(token)

    def _on_search_failed(self, token: int, source_id: str, error: str):
        if token != self._search_token:
            return
        session = self._search_sessions.get(token)
        if not session:
            return
        session["remaining"].discard(source_id)
        source = self.searchSources.get(source_id, {})
        session["errors"][source_id] = f"{source.get('name', source_id)}: {error}"
        self._render_search_results(token)
        if not session["remaining"]:
            self._finalize_search_session(token)

    def _filter_search_results(self, entries: List[WadBrowserResult], query: str) -> List[WadBrowserResult]:
        if not query:
            # Show newest/popular-ish items first by file size while avoiding overload.
            filtered = sorted(entries, key=lambda item: item.size_bytes, reverse=True)
            return filtered[:DEFAULT_SOURCE_LIMIT]

        q = query
        filtered = []
        for item in entries:
            if q in item.title.lower() or q in item.remote_path.lower():
                filtered.append(item)
        return filtered[:DEFAULT_SOURCE_LIMIT]

    def _render_search_results(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        self.resultsTree.clear()
        source_order = session["sources"]
        all_results: List[WadBrowserResult] = []
        for source_id in source_order:
            all_results.extend(session["results"].get(source_id, []))

        # Keep order consistent and avoid duplicate remote entries for stability.
        merged: List[WadBrowserResult] = []
        seen = set()
        for item in all_results:
            key = item.download_url
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        for item in sorted(
            merged,
            key=lambda result: self._source_order_key(session["sources"], result.source_id),
        ):
            row = QTreeWidgetItem([
                item.source_name,
                item.title,
                _human_size(item.size_bytes),
                item.remote_path,
            ])
            row.setData(0, Qt.UserRole, item)
            row.setData(2, Qt.UserRole, item.size_bytes)
            self.resultsTree.addTopLevelItem(row)

    @staticmethod
    def _source_order_key(source_order: List[str], source_id: str) -> tuple[int, int]:
        try:
            return (source_order.index(source_id), 0)
        except ValueError:
            return (len(source_order), 0)

    def _finalize_search_session(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        count = sum(len(items) for items in session["results"].values())
        status = f"Found {count} result(s)."
        if session["errors"]:
            status += " " + "; ".join(sorted(session["errors"].values()))
        if count == 0:
            status = "No matches. Try a broader query or another source."

        self._set_controls_enabled(True)
        self._set_status(status)
        self._search_sessions.pop(token, None)

    def _on_selection_changed(self):
        search_count = len(self.resultsTree.selectedItems())
        local_count = len(self.localTree.selectedItems())
        open_enabled = bool(search_count)
        download_enabled = bool(search_count)
        add_enabled = bool(search_count or local_count)
        delete_enabled = bool(local_count)

        self.openButton.setEnabled(open_enabled)
        self.downloadButton.setEnabled(download_enabled)
        self.addButton.setEnabled(add_enabled)
        self.deleteButton.setEnabled(delete_enabled)

        # Keep search button available if query is empty or results are visible.
        if self.searchInput.text().strip():
            self.searchButton.setEnabled(True)

    def _selected_search_results(self) -> List[WadBrowserResult]:
        ordered = []
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            if item.isSelected():
                payload = item.data(0, Qt.UserRole)
                if isinstance(payload, WadBrowserResult):
                    ordered.append(payload)
        return ordered

    def _selected_local_files(self) -> List[str]:
        selected: List[str] = []
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            if item.isSelected():
                payload = item.data(0, Qt.UserRole)
                if payload:
                    selected.append(str(payload))
        return selected

    def _add_selected(self):
        selected_files = self._selected_local_files()
        if selected_files:
            self.addRequested.emit(selected_files)
            return

        selected_results = self._selected_search_results()
        if not selected_results:
            self._set_status("No selected item to add.")
            return

        ready = []
        missing = []
        for result in selected_results:
            candidate = self._safe_library_path(result)
            if candidate.exists():
                ready.append(str(candidate))
            else:
                missing.append(result)

        if ready:
            self.addRequested.emit(ready)

        if missing:
            self._start_download_session(missing, auto_add=True)
        else:
            self._set_status("All selected mods are already downloaded and added.")

    def _download_selected(self):
        selected_results = self._selected_search_results()
        if not selected_results:
            self._set_status("No remote selection to download.")
            return
        self._start_download_session(selected_results, auto_add=False)

    def _safe_library_path(self, result: WadBrowserResult) -> Path:
        return _safe_library_name(result.remote_path, result.source_id, self.library_dir)

    def _metadata_path(self, destination: str) -> Path:
        destination_path = Path(destination)
        return destination_path.with_suffix(destination_path.suffix + ".bfg-meta.json")

    def _write_metadata(self, destination: str, result: WadBrowserResult):
        payload = {
            "title": result.title,
            "source_id": result.source_id,
            "source_name": result.source_name,
            "remote_path": result.remote_path,
            "download_url": result.download_url,
            "browser_url": result.browser_url,
            "downloaded_at": int(time.time()),
            "size_bytes": result.size_bytes,
        }
        try:
            self._metadata_path(destination).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _read_metadata(self, destination: str) -> Dict[str, Any]:
        path = self._metadata_path(destination)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _start_download_session(self, results: List[WadBrowserResult], auto_add: bool):
        if not results:
            return

        deduped: List[WadBrowserResult] = []
        seen = set()
        for result in results:
            if result.download_url in seen:
                continue
            seen.add(result.download_url)
            deduped.append(result)

        self._download_token += 1
        token = self._download_token
        by_url = {r.download_url: r for r in deduped}
        ordered_urls = [r.download_url for r in deduped]
        self._download_sessions[token] = {
            "auto_add": bool(auto_add),
            "pending": 0,
            "paths": [],
            "failures": [],
            "by_url": by_url,
            "ordered_urls": ordered_urls,
            "path_by_url": {},
        }
        self._set_controls_enabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        for result in deduped:
            target = self._safe_library_path(result)
            if target.exists():
                # Avoid duplicate downloads.
                existing = self._download_sessions[token]
                existing["paths"].append(str(target))
                existing["path_by_url"][result.download_url] = str(target)
                self._write_metadata(str(target), result)
                continue

            self._download_sessions[token]["pending"] += 1
            worker = _DownloadWorker(token, result.download_url, str(target))
            worker.signals.finished.connect(self._on_download_finished)
            worker.signals.failed.connect(self._on_download_failed)
            worker.signals.progress.connect(self._on_download_progress)
            self._thread_pool.start(worker)

        if self._download_sessions[token]["pending"] == 0:
            self._finish_download_session(token)

    def _on_download_progress(self, token: int, received: int, total: int, url: str):
        if token not in self._download_sessions:
            return
        if total:
            self._set_status(f"Downloading ({Path(url).name}) {received}/{total} bytes ({received / total:.0%})")

    def _finish_download_session(self, session_id: int):
        payload = self._download_sessions.pop(session_id, None)
        if payload is None:
            return

        QApplication.restoreOverrideCursor()
        self._set_controls_enabled(True)
        self._refresh_local_library()

        ordered_urls = payload.get("ordered_urls", [])
        path_by_url = payload.get("path_by_url", {})
        ordered_paths = [path_by_url.get(url) for url in ordered_urls if path_by_url.get(url)]
        payload["paths"] = ordered_paths
        count = len(payload.get("paths", []))
        failures = payload.get("failures", [])

        if payload.get("auto_add") and payload.get("paths"):
            self.addRequested.emit(list(payload["paths"]))

        if failures:
            self._set_status(f"Downloads complete. {count} added. {len(failures)} failed.")
        elif count:
            if payload.get("auto_add"):
                self._set_status(f"Downloaded and added {count} mod(s).")
            else:
                self._set_status(f"Downloaded {count} mod(s) to {self.library_dir}")
        else:
            self._set_status(f"No files needed download. Added local files if available.")

    def _on_download_finished(self, token: int, destination: str, source_url: str):
        session = self._download_sessions.get(token)
        if not session:
            return

        session["pending"] -= 1
        session["paths"].append(destination)
        session["path_by_url"][source_url] = destination

        result = session.get("by_url", {}).get(source_url)
        if result is None:
            result = WadBrowserResult(
                title=Path(destination).name,
                source_id="direct",
                source_name="Downloaded",
                size_bytes=0,
                remote_path=Path(destination).name,
                download_url=source_url,
                browser_url=source_url,
            )
        self._write_metadata(destination, result)

        if session["pending"] <= 0:
            self._finish_download_session(token)

    def _on_download_failed(self, token: int, source_url: str, error: str):
        session = self._download_sessions.get(token)
        if not session:
            return
        session["pending"] -= 1
        session["failures"].append(f"{source_url}: {error}")
        if session["pending"] <= 0:
            self._finish_download_session(token)

    def _open_selected_remote(self):
        selected = self._selected_search_results()
        if not selected:
            self._set_status("No search item selected to open.")
            return
        webbrowser.open(selected[0].browser_url)

    def _delete_selected_local(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        deleted = 0
        for file_path in selected:
            p = Path(file_path)
            try:
                if p.is_file():
                    p.unlink()
                    meta = self._metadata_path(str(p))
                    if meta.exists():
                        meta.unlink()
                    deleted += 1
            except OSError as exc:
                self._set_status(f"Could not delete {file_path}: {exc}")
        self._refresh_local_library()
        if deleted:
            self._set_status(f"Deleted {deleted} file(s).")

    def _refresh_local_library(self):
        self.localTree.clear()
        count = 0
        for path in sorted(self.library_dir.glob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in DEFAULT_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            metadata = self._read_metadata(str(path))
            source = metadata.get("source_name") or "local"
            source_path = metadata.get("remote_path") or "-"
            item = QTreeWidgetItem([path.name, _human_size(size), source, source_path])
            item.setData(0, Qt.UserRole, str(path))
            item.setToolTip(0, str(path))
            self.localTree.addTopLevelItem(item)
            count += 1

        self._set_status(f"Library: {count} managed files in {self.library_dir}")

    def _open_library_dir(self):
        self.library_dir.mkdir(parents=True, exist_ok=True)
        webbrowser.open(self.library_dir.as_uri())
