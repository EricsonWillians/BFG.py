"""The WadFinder QWidget: mod browser UI and search/download orchestration.

Pure logic lives in the sibling modules (``text``/``sources``/``parsing``/
``library``); network work runs in the QRunnable classes from ``workers``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import webbrowser
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from PyQt5.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    DEFAULT_SOURCE_LIMIT,
    DEFAULT_SOURCES,
    INDEX_TTL_SECONDS,
    MAX_CACHED_INDEX_ENTRIES,
    SOURCE_STATUS_CACHED,
    SOURCE_STATUS_CHECKING,
    SOURCE_STATUS_DISABLED,
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_OK,
    SOURCE_STATUS_UNKNOWN,
    SOURCE_STATUS_UNREACHABLE,
)
from .library import (
    build_library_identity_map,
    collect_library_rows,
    library_row_matches,
    metadata_path,
    read_metadata,
    safe_library_path,
    sweep_orphan_parts,
    write_metadata,
)
from .models import IndexCacheEntry, WadBrowserResult
from .sources import (
    allocate_source_id,
    coerce_source,
    custom_source_id,
    default_index_for_parser,
    infer_source_parser,
    is_idgames_source,
    make_host_name,
    normalize_parser,
    normalize_source_id,
    normalize_source_url,
    parse_custom_source_entry,
    seed_sources,
)
from .text import (
    human_size,
    normalize_metadata_text,
    query_matches,
    query_matches_any,
    result_identity,
    result_search_blob,
    short_age,
    summarize_text,
    tokenize_query,
)
from .workers import _DiscoverSourcesWorker, _DownloadWorker, _SearchWorker

# Backward-compatible legacy (underscore-prefixed) names used by the widget
# body below, kept from the pre-split monolith.
_coerce_parser = normalize_parser
_normalize_source_id = normalize_source_id
_result_identity = result_identity
_normalize_metadata_text = normalize_metadata_text
_tokenize_query = tokenize_query
_query_matches = query_matches
_query_matches_any = query_matches_any
_result_search_blob = result_search_blob
_human_size = human_size
_short_age = short_age
_summarize_text = summarize_text
_IndexCacheEntry = IndexCacheEntry

class _SourcePriorityList(QListWidget):
    orderChanged = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()


class WadFinder(QWidget):
    addRequested = pyqtSignal(list)
    removedRequested = pyqtSignal(list)
    statusChanged = pyqtSignal(str)
    browserModeRequested = pyqtSignal(bool)

    def __init__(
        self,
        parent=None,
        library_dir: Optional[str] = None,
        source_state: Optional[List[Dict[str, Any]]] = None,
        source_state_changed=None,
        library_dir_changed=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Mod Browser")
        self.setToolTip("Search and download community PWADs and add them to launch order")

        self.searchSources: Dict[str, Dict[str, str]] = {}
        self.sourceOrder: List[str] = []
        self._seed_sources(source_state)
        self.source_state_changed = source_state_changed
        self.library_dir_changed = library_dir_changed

        # cache_root/library_dir come from user config and may point at a
        # removed drive, a path occupied by a file, or an unwritable location.
        # Never let mkdir failures kill the whole app at startup.
        cache_root = Path(os.getenv("BFG_CACHE_DIR", Path.home() / ".cache" / "bfg.py"))
        try:
            (cache_root / "source_index").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"WadFinder: cache dir {cache_root} unusable ({exc}); falling back to temp dir")
            cache_root = Path(tempfile.gettempdir()) / "bfg.py"
            try:
                (cache_root / "source_index").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.cache_root = cache_root

        self.library_dir = Path(library_dir).expanduser() if library_dir else cache_root / "mods"
        try:
            self.library_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"WadFinder: library dir {self.library_dir} unusable ({exc}); falling back to {cache_root / 'mods'}")
            self.library_dir = cache_root / "mods"
            try:
                self.library_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self._health_file = self.cache_root / "source_health.json"
        self._sweep_orphan_parts()

        self._thread_pool = QThreadPool.globalInstance()
        self._source_health: Dict[str, Dict[str, Any]] = {}
        self._search_token = 0
        self._discover_token = 0
        self._download_token = 0
        self._search_sessions: Dict[int, Dict[str, Any]] = {}
        self._discover_session: Optional[int] = None
        self._download_sessions: Dict[int, Dict[str, Any]] = {}
        self._active_search_workers: set = set()
        self._index_cache: Dict[str, _IndexCacheEntry] = {}
        self._rendered_results: List[WadBrowserResult] = []
        self._failed_downloads: List[WadBrowserResult] = []
        self._failed_download_auto_add: bool = False
        self._library_rows: List[Dict[str, Any]] = []

        self._local_filter_timer = QTimer(self)
        self._local_filter_timer.setSingleShot(True)
        self._local_filter_timer.setInterval(250)
        self._local_filter_timer.timeout.connect(self._refresh_local_library)

        # Deferred: parsing up to 11 x 5000-entry JSON index caches on the UI
        # thread delays first paint; run it once the event loop starts.
        QTimer.singleShot(0, self._load_index_cache)
        self._load_source_health_cache()
        self.initUi()
        self._emit_source_state()

    _coerce_source = staticmethod(coerce_source)

    def _seed_sources(self, source_state: Optional[List[Dict[str, Any]]]):
        ordered = seed_sources(source_state)
        self.searchSources = {}
        self.sourceOrder = []
        for entry in ordered:
            source_id = entry["id"]
            self.searchSources[source_id] = entry
            self.sourceOrder.append(source_id)

    def initUi(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        headerRow = QHBoxLayout()
        title = QLabel("BFG ONLINE TERMINAL // PWAD EXCHANGE")
        title.setObjectName("terminalTitle")
        title.setFrameStyle(QFrame.Box | QFrame.Raised)
        title.setAlignment(Qt.AlignCenter)
        headerRow.addWidget(title, 1)
        self.expandBrowserButton = QPushButton("EXPAND")
        self.expandBrowserButton.setCheckable(True)
        self.expandBrowserButton.setObjectName("primaryButton")
        self.expandBrowserButton.setToolTip("Give the mod browser the full application window")
        self.expandBrowserButton.toggled.connect(self._on_browser_mode_toggled)
        headerRow.addWidget(self.expandBrowserButton)
        root.addLayout(headerRow)

        self.browserSubtitle = QLabel("SEARCH  >  INSPECT  >  DOWNLOAD + QUEUE  >  REORDER  >  UNLEASH")
        self.browserSubtitle.setObjectName("mutedHint")
        self.browserSubtitle.setAlignment(Qt.AlignCenter)
        root.addWidget(self.browserSubtitle)

        # Search controls.
        searchRow = QHBoxLayout()
        self.sourceCombo = QComboBox()
        self.sourceCombo.setMinimumWidth(180)
        self.sourceCombo.currentIndexChanged.connect(self._on_search_source_changed)
        self._refresh_source_combo()

        self.searchInput = QLineEdit()
        self.searchInput.setPlaceholderText("Search by filename, theme, description, author, or mod name")
        self.searchInput.returnPressed.connect(self._on_search)

        self.searchButton = QPushButton("SEARCH NETWORK")
        self.searchButton.setObjectName("primaryButton")
        self.searchButton.clicked.connect(self._on_search)
        self.clearSearchButton = QPushButton("RESET")
        self.clearSearchButton.clicked.connect(self._on_clear_search)
        searchRow.addWidget(self.sourceCombo, 1)
        searchRow.addWidget(self.searchInput, 3)
        searchRow.addWidget(self.searchButton)
        searchRow.addWidget(self.clearSearchButton)
        root.addLayout(searchRow)

        self.browserTabs = QTabWidget()
        self.sourceOrderList = _SourcePriorityList()
        self.sourceOrderList.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sourceOrderList.itemSelectionChanged.connect(self._on_selection_changed)
        self.sourceOrderList.orderChanged.connect(self._sync_source_order_from_ui)
        self.sourceOrderList.setDragDropMode(QAbstractItemView.InternalMove)
        self.sourceOrderList.setDragEnabled(True)
        self.sourceOrderList.setAcceptDrops(True)
        self.sourceOrderList.setDropIndicatorShown(True)
        self.sourceOrderList.setDefaultDropAction(Qt.MoveAction)
        self.sourceOrderList.setMinimumHeight(150)
        self._rebuild_source_list()

        sourceButtons = QGridLayout()
        sourceButtons.setHorizontalSpacing(8)
        sourceButtons.setVerticalSpacing(8)
        self.sourceUpButton = QPushButton("Move Up")
        self.sourceUpButton.clicked.connect(lambda: self._reorder_source(-1))
        self.sourceDownButton = QPushButton("Move Down")
        self.sourceDownButton.clicked.connect(lambda: self._reorder_source(1))
        self.sourceTopButton = QPushButton("Move to Top")
        self.sourceTopButton.clicked.connect(self._move_source_to_top)
        self.sourceBottomButton = QPushButton("Move to Bottom")
        self.sourceBottomButton.clicked.connect(self._move_source_to_bottom)
        self.sourceRemoveButton = QPushButton("Remove")
        self.sourceRemoveButton.clicked.connect(self._remove_selected_custom_sources)
        self.sourceToggleButton = QPushButton("Enable/Disable")
        self.sourceToggleButton.clicked.connect(self._toggle_selected_source)
        self.sourceEnableAllButton = QPushButton("Enable All")
        self.sourceEnableAllButton.clicked.connect(lambda: self._set_all_sources_enabled(True))
        self.sourceDisableAllButton = QPushButton("Disable All")
        self.sourceDisableAllButton.clicked.connect(lambda: self._set_all_sources_enabled(False))
        self.sourcePurgeButton = QPushButton("Purge Unavailable")
        self.sourcePurgeButton.setToolTip("Remove user/discovered sources that are unavailable")
        self.sourcePurgeButton.clicked.connect(self._purge_unavailable_sources)

        sourceButtons.addWidget(self.sourceUpButton, 0, 0)
        sourceButtons.addWidget(self.sourceDownButton, 0, 1)
        sourceButtons.addWidget(self.sourceToggleButton, 0, 2)
        sourceButtons.addWidget(self.sourceTopButton, 1, 0)
        sourceButtons.addWidget(self.sourceBottomButton, 1, 1)
        sourceButtons.addWidget(self.sourceRemoveButton, 1, 2)
        sourceButtons.addWidget(self.sourceEnableAllButton, 2, 0)
        sourceButtons.addWidget(self.sourceDisableAllButton, 2, 1)
        sourceButtons.addWidget(self.sourcePurgeButton, 2, 2)

        discoverRow = QGridLayout()
        discoverRow.setHorizontalSpacing(8)
        discoverRow.setVerticalSpacing(8)
        self.discoverSeedInput = QLineEdit()
        self.discoverSeedInput.setPlaceholderText(
            "Optional discovery seed URLs (comma/space separated)"
        )
        self.discoverSeedInput.setToolTip("Leave blank to use built-in discovery seeds.")
        self.discoverSourcesButton = QPushButton("Discover Doomworld Mirrors")
        self.discoverSourcesButton.setToolTip("Fetch current Doomworld/idgames mirrors and add any missing")
        self.discoverSourcesButton.clicked.connect(self._discover_mirrors)
        self.discoverClearDiscoveredButton = QPushButton("Clear Discovered")
        self.discoverClearDiscoveredButton.clicked.connect(self._clear_discovered_sources)
        discoverRow.addWidget(self.discoverSeedInput, 0, 0, 1, 2)
        discoverRow.addWidget(self.discoverSourcesButton, 1, 0)
        discoverRow.addWidget(self.discoverClearDiscoveredButton, 1, 1)

        sourceAddRow = QGridLayout()
        sourceAddRow.setHorizontalSpacing(8)
        sourceAddRow.setVerticalSpacing(8)
        self.customSourceInput = QLineEdit()
        self.customSourceInput.setPlaceholderText(
            "Add source(s): URL | Name | index | parser. One per line."
        )
        self.customSourceInput.setMinimumHeight(26)
        self.customSourceButton = QPushButton("Add Source")
        self.customSourceButton.clicked.connect(self._add_custom_source)
        self.customSourceListReset = QPushButton("Clear Custom")
        self.customSourceListReset.clicked.connect(self._clear_custom_source)
        self.customSourcePresetButton = QPushButton("Add Built-in Sources")
        self.customSourcePresetButton.clicked.connect(self._add_builtin_sources)

        sourceAddRow.addWidget(self.customSourceInput, 0, 0, 1, 4)
        sourceAddRow.addWidget(self.customSourceButton, 1, 0)
        sourceAddRow.addWidget(self.customSourceListReset, 1, 1)
        sourceAddRow.addWidget(self.customSourcePresetButton, 1, 2, 1, 2)

        localFilterRow = QHBoxLayout()
        localFilterLabel = QLabel("Library filter:")
        self.localFilterInput = QLineEdit()
        self.localFilterInput.setPlaceholderText("Filter library by name, source, or remote path")
        self.localFilterInput.textChanged.connect(self._local_filter_timer.start)
        localFilterRow.addWidget(localFilterLabel)
        localFilterRow.addWidget(self.localFilterInput, 1)

        # Search + local library.
        self.resultsTree = QTreeWidget()
        self.resultsTree.setObjectName("downloadResults")
        self.resultsTree.setHeaderLabels(["Source", "Mod / package", "Description", "Size", "Archive path"])
        self.resultsTree.setSelectionMode(self.resultsTree.ExtendedSelection)
        self.resultsTree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.resultsTree.setAlternatingRowColors(True)
        self.resultsTree.setUniformRowHeights(True)
        self.resultsTree.setSortingEnabled(True)
        self.resultsTree.itemSelectionChanged.connect(self._on_selection_changed)
        self.resultsTree.itemDoubleClicked.connect(self._on_result_item_double_clicked)
        self.resultsTree.setMinimumHeight(140)
        self.resultsTree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.resultsTree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.resultsTree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.resultsTree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.resultsTree.header().setSectionResizeMode(4, QHeaderView.Interactive)
        self.resultsTree.setColumnWidth(0, 120)
        self.resultsTree.setColumnWidth(4, 220)

        self.localTree = QTreeWidget()
        self.localTree.setObjectName("modLibrary")
        self.localTree.setAlternatingRowColors(True)
        self.localTree.setHeaderLabels(["Library file", "Size", "Source", "Installed", "Remote path"])
        self.localTree.setSelectionMode(self.localTree.ExtendedSelection)
        self.localTree.setSortingEnabled(True)
        self.localTree.itemSelectionChanged.connect(self._on_selection_changed)
        self.localTree.itemDoubleClicked.connect(self._add_selected)
        self.localTree.setMinimumHeight(100)

        self.openButton = QPushButton("Open Page")
        self.openButton.setToolTip("Open the selected result page in your browser")
        self.openButton.clicked.connect(self._open_selected_remote)

        self.resultDetailsButton = QPushButton("View Details")
        self.resultDetailsButton.setToolTip("Show the full description for the selected result")
        self.resultDetailsButton.clicked.connect(self._show_selected_result_details)

        self.downloadButton = QPushButton("DOWNLOAD ONLY")
        self.downloadButton.setToolTip("Save the selected mods to the library without changing the launch loadout")
        self.downloadButton.clicked.connect(self._download_selected)

        self.selectAllResultsButton = QPushButton("Select All")
        self.selectAllResultsButton.setToolTip("Select every visible search result")
        self.selectAllResultsButton.clicked.connect(self._select_all_results)

        self.downloadAllButton = QPushButton("DOWNLOAD ALL")
        self.downloadAllButton.setToolTip("Download every visible search result")
        self.downloadAllButton.clicked.connect(self._download_all_results)
        self.retryFailedButton = QPushButton("Retry Failed")
        self.retryFailedButton.clicked.connect(self._retry_failed_downloads)
        self.retryFailedButton.setToolTip("Retry only the failed downloads from the last batch.")

        self.addAllButton = QPushButton("DOWNLOAD + QUEUE ALL")
        self.addAllButton.setToolTip("Add every visible search result to the launch list")
        self.addAllButton.clicked.connect(self._add_all_results)

        self.clearResultsSelectionButton = QPushButton("Clear Selection")
        self.clearResultsSelectionButton.setToolTip("Clear the current result selection")
        self.clearResultsSelectionButton.clicked.connect(self._clear_results_selection)

        self.addButton = QPushButton("DOWNLOAD + QUEUE")
        self.addButton.setObjectName("primaryButton")
        self.addButton.setToolTip("Download remote selections if needed, then add everything to the launch loadout")
        self.addButton.clicked.connect(self._add_selected)

        self.deleteButton = QPushButton("DELETE FILE")
        self.deleteButton.setObjectName("dangerButton")
        self.deleteButton.setToolTip("Delete the selected local files from the library")
        self.deleteButton.clicked.connect(self._delete_selected_local)
        self.selectAllLocalButton = QPushButton("Select All")
        self.selectAllLocalButton.setToolTip("Select every local library entry")
        self.selectAllLocalButton.clicked.connect(self._select_all_local)
        self.clearLocalSelectionButton = QPushButton("Clear Selection")
        self.clearLocalSelectionButton.setToolTip("Clear the current local-library selection")
        self.clearLocalSelectionButton.clicked.connect(self._clear_local_selection)
        self.selectAllLocalButton.hide()
        self.clearLocalSelectionButton.hide()
        self.openLocalButton = QPushButton("QUEUE SELECTED")
        self.openLocalButton.setObjectName("primaryButton")
        self.openLocalButton.setToolTip("Add selected library files to the launch loadout")
        self.openLocalButton.clicked.connect(self._add_selected)
        self.inspectLocalButton = QPushButton("OPEN FILE")
        self.inspectLocalButton.clicked.connect(self._open_selected_local_file)
        self.openLocalFolderButton = QPushButton("Open Folder")
        self.openLocalFolderButton.clicked.connect(self._open_selected_local_folder)

        self.openLibraryButton = QPushButton("Open Library")
        self.openLibraryButton.setToolTip("Open the current library folder")
        self.openLibraryButton.clicked.connect(self._open_library_dir)
        self.libraryDirButton = QPushButton("Set Library")
        self.libraryDirButton.setToolTip("Choose a different library folder")
        self.libraryDirButton.clicked.connect(self._change_library_dir)

        resultsActions = QGridLayout()
        resultsActions.setHorizontalSpacing(8)
        resultsActions.setVerticalSpacing(8)
        resultsActions.addWidget(self.addButton, 0, 0)
        resultsActions.addWidget(self.downloadButton, 0, 1)
        resultsActions.addWidget(self.resultDetailsButton, 0, 2)
        resultsActions.addWidget(self.openButton, 0, 3)
        resultsActions.addWidget(self.selectAllResultsButton, 1, 0)
        resultsActions.addWidget(self.clearResultsSelectionButton, 1, 1)
        resultsActions.addWidget(self.addAllButton, 1, 2)
        resultsActions.addWidget(self.downloadAllButton, 1, 3)
        resultsActions.addWidget(self.retryFailedButton, 2, 0, 1, 4)
        self.retryFailedButton.hide()

        libraryActions = QGridLayout()
        libraryActions.setHorizontalSpacing(8)
        libraryActions.setVerticalSpacing(8)
        libraryActions.addWidget(self.openLocalButton, 0, 0)
        libraryActions.addWidget(self.openLocalFolderButton, 0, 1)
        libraryActions.addWidget(self.deleteButton, 0, 2)
        libraryActions.addWidget(self.inspectLocalButton, 1, 0)
        libraryActions.addWidget(self.openLibraryButton, 1, 1)
        libraryActions.addWidget(self.libraryDirButton, 1, 2)

        sourcesTab = QWidget()
        sourcesLayout = QVBoxLayout(sourcesTab)
        sourcesLayout.setContentsMargins(6, 6, 6, 6)
        sourcesLayout.setSpacing(6)

        sourceIntro = QLabel(
            "Sources are searched from top to bottom. Disabled or unreachable mirrors are skipped."
        )
        sourceIntro.setObjectName("mutedHint")
        sourceIntro.setWordWrap(True)
        sourcesLayout.addWidget(sourceIntro)

        self.sourceScroll = QScrollArea()
        self.sourceScroll.setWidgetResizable(True)
        self.sourceScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sourceScroll.setFrameShape(QFrame.NoFrame)
        self.sourceScrollContent = QWidget()
        sourceScrollLayout = QVBoxLayout(self.sourceScrollContent)
        sourceScrollLayout.setContentsMargins(6, 6, 6, 6)
        sourceScrollLayout.setSpacing(10)

        self.priorityGroup = QGroupBox("SEARCH PRIORITY && HEALTH")
        priorityLayout = QVBoxLayout(self.priorityGroup)
        priorityLayout.addWidget(self.sourceOrderList)
        priorityLayout.addLayout(sourceButtons)
        sourceScrollLayout.addWidget(self.priorityGroup)

        self.discoveryGroup = QGroupBox("DISCOVER MIRRORS")
        discoveryLayout = QVBoxLayout(self.discoveryGroup)
        discoveryHelp = QLabel("Find current Doomworld/idgames mirrors. The seed field is optional.")
        discoveryHelp.setObjectName("mutedHint")
        discoveryHelp.setWordWrap(True)
        discoveryLayout.addWidget(discoveryHelp)
        discoveryLayout.addLayout(discoverRow)
        sourceScrollLayout.addWidget(self.discoveryGroup)

        self.customGroup = QGroupBox("CUSTOM SOURCES")
        customLayout = QVBoxLayout(self.customGroup)
        customHelp = QLabel("Advanced: add one source URL, or a URL | Name | index | parser record.")
        customHelp.setObjectName("mutedHint")
        customHelp.setWordWrap(True)
        customLayout.addWidget(customHelp)
        customLayout.addLayout(sourceAddRow)
        sourceScrollLayout.addWidget(self.customGroup)
        sourceScrollLayout.addStretch(1)

        self.sourceScroll.setWidget(self.sourceScrollContent)
        sourcesLayout.addWidget(self.sourceScroll, 1)

        resultsTab = QWidget()
        resultsLayout = QVBoxLayout(resultsTab)
        resultsLayout.setContentsMargins(8, 8, 8, 8)
        resultsLayout.setSpacing(8)
        self.resultSelectionLabel = QLabel("NO MOD SELECTED — click a row to inspect and install")
        self.resultSelectionLabel.setObjectName("selectionBanner")
        self.resultSelectionLabel.setWordWrap(True)
        resultsLayout.addWidget(self.resultSelectionLabel)
        self.resultPreview = QPlainTextEdit()
        self.resultPreview.setReadOnly(True)
        self.resultPreview.setObjectName("browserPreview")
        self.resultPreview.setMinimumHeight(72)
        self.resultPreview.setPlaceholderText("Select a search result to inspect its metadata.")
        resultSplitter = QSplitter(Qt.Vertical)
        resultSplitter.setChildrenCollapsible(False)
        resultSplitter.addWidget(self.resultsTree)
        resultSplitter.addWidget(self.resultPreview)
        resultSplitter.setStretchFactor(0, 3)
        resultSplitter.setStretchFactor(1, 1)
        resultSplitter.setSizes([360, 150])
        resultsLayout.addWidget(resultSplitter, 1)
        resultsLayout.addLayout(resultsActions)

        libraryTab = QWidget()
        libraryLayout = QVBoxLayout(libraryTab)
        libraryLayout.setContentsMargins(8, 8, 8, 8)
        libraryLayout.setSpacing(8)
        libraryLayout.addLayout(localFilterRow)
        self.libraryPreview = QPlainTextEdit()
        self.libraryPreview.setReadOnly(True)
        self.libraryPreview.setObjectName("browserPreview")
        self.libraryPreview.setMinimumHeight(72)
        self.libraryPreview.setPlaceholderText("Select a library file to inspect its origin and metadata.")
        librarySplitter = QSplitter(Qt.Vertical)
        librarySplitter.setChildrenCollapsible(False)
        librarySplitter.addWidget(self.localTree)
        librarySplitter.addWidget(self.libraryPreview)
        librarySplitter.setStretchFactor(0, 3)
        librarySplitter.setStretchFactor(1, 1)
        librarySplitter.setSizes([360, 150])
        libraryLayout.addWidget(librarySplitter, 1)
        libraryLayout.addLayout(libraryActions)

        self.libraryDirLabel = QLabel(f"Library folder: {self.library_dir}")
        self.libraryDirLabel.setWordWrap(True)
        libraryLayout.addWidget(self.libraryDirLabel)

        self.browserTabs.addTab(resultsTab, "RESULTS [0]")
        self.browserTabs.addTab(libraryTab, "LIBRARY [0]")
        self.browserTabs.addTab(sourcesTab, "SOURCES")
        self.browserTabs.currentChanged.connect(self._on_browser_tab_changed)
        root.addWidget(self.browserTabs, 1)

        searchStatusRow = QHBoxLayout()
        self.statusLabel = QLabel("Ready")
        self.statusLabel.setWordWrap(True)
        self.searchProgressBar = QProgressBar()
        self.searchProgressBar.setTextVisible(True)
        self.searchProgressBar.setRange(0, 1)
        self.searchProgressBar.setValue(0)
        self.searchProgressBar.hide()

        searchStatusRow.addWidget(self.statusLabel, 1)
        searchStatusRow.addWidget(self.searchProgressBar)
        root.addLayout(searchStatusRow)

        self.searchFeedbackLabel = QLabel("")
        self.searchFeedbackLabel.setWordWrap(True)
        self.searchFeedbackLabel.hide()
        root.addWidget(self.searchFeedbackLabel)

        self._refresh_local_library()
        self._refresh_retry_state()
        self._set_controls_enabled(True)
        self._on_selection_changed()

    def setCompactMode(self, compact: bool):
        """Prioritize core search/metadata actions in short windows."""
        compact = bool(compact)
        self.browserSubtitle.setVisible(not compact)
        self.resultsTree.setMinimumHeight(80 if compact else 140)
        self.localTree.setMinimumHeight(80 if compact else 100)
        self.resultPreview.setMinimumHeight(64 if compact else 72)
        self.libraryPreview.setMinimumHeight(64 if compact else 72)
        self.addButton.setText("GET + QUEUE" if compact else "DOWNLOAD + QUEUE")
        self.downloadButton.setText("GET ONLY" if compact else "DOWNLOAD ONLY")
        self.resultDetailsButton.setText("DETAILS" if compact else "View Details")
        self.openButton.setText("WEB PAGE" if compact else "Open Page")
        self.resultsTree.setColumnHidden(2, compact)
        self.resultsTree.setColumnHidden(4, compact)

        for control in (
            self.selectAllResultsButton,
            self.clearResultsSelectionButton,
            self.addAllButton,
            self.downloadAllButton,
            self.inspectLocalButton,
            self.openLibraryButton,
            self.libraryDirButton,
        ):
            control.setVisible(not compact)
        if compact:
            self.retryFailedButton.hide()
        elif self._failed_downloads:
            self.retryFailedButton.show()

    def _on_browser_mode_toggled(self, expanded: bool):
        self.expandBrowserButton.setText("BACK TO LAUNCH" if expanded else "EXPAND")
        self.expandBrowserButton.setToolTip(
            "Return to the launch setup" if expanded else "Give the mod browser the full application window"
        )
        self.browserModeRequested.emit(bool(expanded))

    def _on_browser_tab_changed(self, index: int):
        # Source administration needs horizontal and vertical room; entering it
        # automatically uses the browser workspace instead of a cramped pane.
        if index == 2 and not self.expandBrowserButton.isChecked():
            self.expandBrowserButton.setChecked(True)

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
                            description=item.get("description", ""),
                            metadata_text=item.get("metadata_text", ""),
                            source_id=item["source_id"],
                            source_name=item["source_name"],
                            size_bytes=int(item["size_bytes"]),
                            remote_path=item["remote_path"],
                            download_url=item["download_url"],
                            browser_url=item["browser_url"],
                        )
                    )
                fetched_at = float(payload.get("fetched_at", 0.0))
                if time.time() - fetched_at <= INDEX_TTL_SECONDS:
                    self._index_cache[source_id] = _IndexCacheEntry(
                        source_id=source_id,
                        source_name=source["name"],
                        fetched_at=fetched_at,
                        entries=parsed,
                    )
                    source["status"] = SOURCE_STATUS_CACHED
                    source["status_message"] = f"Cached index: {_short_age(time.time() - fetched_at)}"
                    source["status_checked_at"] = fetched_at
                else:
                    try:
                        cache_file.unlink()
                    except OSError:
                        pass
            except Exception:
                continue

    def _load_source_health_cache(self):
        if not self._health_file.exists():
            return
        try:
            payload = json.loads(self._health_file.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return
        for source_id, value in payload.items():
            if not isinstance(value, dict):
                continue
            try:
                self._source_health[source_id] = {
                    "status": str(value.get("status", SOURCE_STATUS_UNKNOWN)).strip().lower(),
                    "status_message": str(value.get("status_message", "")).strip(),
                    "status_checked_at": float(value.get("status_checked_at", 0.0) or 0.0),
                }
            except Exception:
                continue

        for source_id, health in self._source_health.items():
            source = self.searchSources.get(source_id)
            if not source:
                continue
            source["status"] = health.get("status", SOURCE_STATUS_UNKNOWN)
            source["status_message"] = str(health.get("status_message", ""))
            source["status_checked_at"] = float(health.get("status_checked_at", 0.0) or 0.0)

    def _store_index_cache(self, source_id: str, source: Dict[str, str], entries: List[WadBrowserResult]):
        cache_file = self.cache_root / "source_index" / f"{source_id}.json"
        trimmed = entries[:MAX_CACHED_INDEX_ENTRIES]
        payload = {
            "source_id": source_id,
            "source_name": source["name"],
            "fetched_at": time.time(),
            "entries": [
                {
                    "title": item.title,
                    "description": item.description,
                    "metadata_text": item.metadata_text,
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "size_bytes": item.size_bytes,
                    "remote_path": item.remote_path,
                    "download_url": item.download_url,
                    "browser_url": item.browser_url,
                }
                for item in trimmed
            ],
        }
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _store_source_health_cache(self):
        try:
            self._health_file.write_text(
                json.dumps(self._source_health, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _set_source_status(
        self,
        source_id: str,
        status: str,
        message: str = "",
        *,
        persist: bool = False,
    ):
        source = self.searchSources.get(source_id)
        if not source:
            return

        if status not in {
            SOURCE_STATUS_UNKNOWN,
            SOURCE_STATUS_CHECKING,
            SOURCE_STATUS_CACHED,
            SOURCE_STATUS_OK,
            SOURCE_STATUS_UNREACHABLE,
            SOURCE_STATUS_ERROR,
            SOURCE_STATUS_DISABLED,
        }:
            status = SOURCE_STATUS_UNKNOWN

        source["status"] = status
        source["status_message"] = str(message or "").strip()
        source["status_checked_at"] = time.time()

        if status in {SOURCE_STATUS_DISABLED}:
            source["enabled"] = False

        self._source_health[source_id] = {
            "status": source["status"],
            "status_message": source["status_message"],
            "status_checked_at": source["status_checked_at"],
        }
        if persist:
            self._store_source_health_cache()

        self._rebuild_source_list()
        self._emit_source_state()

    def _is_cache_valid(self, source_id: str) -> bool:
        entry = self._index_cache.get(source_id)
        if not entry:
            return False
        return (time.time() - entry.fetched_at) <= INDEX_TTL_SECONDS

    @staticmethod
    def _source_tooltip(source: Dict[str, str]) -> str:
        base = source.get("browser", source.get("base", ""))
        try:
            checked_at = float(source.get("status_checked_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            checked_at = 0.0
        if checked_at:
            age = _short_age(time.time() - checked_at)
            status_message = source.get("status_message", "").strip()
            if status_message:
                return f"{base}\nStatus: {source.get('status', 'unknown')} ({status_message})\nLast checked: {age}"
            return f"{base}\nStatus: {source.get('status', 'unknown')}\nLast checked: {age}"
        return base

    def _source_label(self, source: Dict[str, str], rank: int = 0) -> str:
        status = source.get("status", SOURCE_STATUS_UNKNOWN)
        status_text = {
            SOURCE_STATUS_OK: "ok",
            SOURCE_STATUS_CACHED: "cached",
            SOURCE_STATUS_CHECKING: "checking",
            SOURCE_STATUS_UNREACHABLE: "offline",
            SOURCE_STATUS_ERROR: "error",
            SOURCE_STATUS_DISABLED: "disabled",
            SOURCE_STATUS_UNKNOWN: "unknown",
        }.get(status, "unknown")
        prefix = f"{rank}. " if rank > 0 else ""
        return f"{prefix}{source['name']} ({source['base']}) [{status_text}]"

    def _refresh_source_list(self):
        self._rebuild_source_list()
        self._refresh_source_combo()

    def _emit_source_state(self):
        if not callable(self.source_state_changed):
            return
        self.source_state_changed(
            [
                source.copy()
                for source_id in self.sourceOrder
                if (source := self.searchSources.get(source_id))
            ]
        )

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
            rank = list_index = self.sourceOrder.index(source_id)
            item = QListWidgetItem(self._source_label(source, rank + 1))
            item.setData(Qt.UserRole, source_id)
            item.setToolTip(self._source_tooltip(source))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked
                if checked_map.get(source_id, source.get("enabled", True))
                else Qt.Unchecked
            )
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

    def _sync_source_order_from_ui(self):
        selected = self.sourceOrderList.currentItem().data(Qt.UserRole) if self.sourceOrderList.currentItem() else None
        ordered = []
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            if source_id in self.searchSources:
                ordered.append(source_id)

        if ordered and ordered != self.sourceOrder:
            self.sourceOrder = ordered
            self._emit_source_state()
            self._refresh_source_combo()

            if selected in self.sourceOrder:
                self.sourceOrderList.blockSignals(True)
                self.sourceOrderList.setCurrentRow(self.sourceOrder.index(selected))
                self.sourceOrderList.blockSignals(False)

    def _on_search_source_changed(self, *_):
        self._on_search()

    def _set_controls_enabled(self, enabled: bool):
        self.searchButton.setEnabled(enabled)
        self.clearSearchButton.setEnabled(enabled)
        self.selectAllResultsButton.setEnabled(enabled)
        self.clearResultsSelectionButton.setEnabled(enabled)
        self.customSourceButton.setEnabled(enabled)
        self.customSourceInput.setEnabled(enabled)
        self.customSourceListReset.setEnabled(enabled)
        self.customSourcePresetButton.setEnabled(enabled)
        self.discoverSourcesButton.setEnabled(enabled)
        self.sourceUpButton.setEnabled(enabled)
        self.sourceDownButton.setEnabled(enabled)
        self.sourceTopButton.setEnabled(enabled)
        self.sourceBottomButton.setEnabled(enabled)
        self.sourceToggleButton.setEnabled(enabled)
        self.sourceEnableAllButton.setEnabled(enabled)
        self.sourceDisableAllButton.setEnabled(enabled)
        self.sourcePurgeButton.setEnabled(enabled)
        self.sourceRemoveButton.setEnabled(enabled)
        self.discoverSeedInput.setEnabled(enabled)
        self.sourceOrderList.setEnabled(enabled)
        self.searchInput.setEnabled(enabled)
        self.openButton.setEnabled(enabled)
        self.resultDetailsButton.setEnabled(enabled and bool(self._selected_search_results()))
        self.downloadButton.setEnabled(enabled)
        self.downloadAllButton.setEnabled(enabled and bool(self._rendered_results))
        self.addAllButton.setEnabled(enabled and bool(self._rendered_results))
        self.addButton.setEnabled(enabled)
        self.deleteButton.setEnabled(enabled)
        self.selectAllLocalButton.setEnabled(enabled and bool(self.localTree.topLevelItemCount()))
        self.clearLocalSelectionButton.setEnabled(enabled and bool(self.localTree.selectedItems()))
        self.discoverSourcesButton.setEnabled(enabled)
        self.discoverClearDiscoveredButton.setEnabled(enabled)
        self.customSourceButton.setEnabled(enabled)
        self.customSourceInput.setEnabled(enabled)
        self.customSourceListReset.setEnabled(enabled)
        self.customSourcePresetButton.setEnabled(enabled)
        self.openLocalButton.setEnabled(enabled and bool(self._selected_local_files()))
        self.inspectLocalButton.setEnabled(enabled and bool(self._selected_local_files()))
        self.openLocalFolderButton.setEnabled(enabled and bool(self._selected_local_files()))
        self.openLibraryButton.setEnabled(enabled)
        self.libraryDirButton.setEnabled(enabled)
        self.resultsTree.setEnabled(enabled)
        self.localTree.setEnabled(enabled)
        self.sourceCombo.setEnabled(enabled)
        self._refresh_retry_state()

    def _refresh_retry_state(self):
        self.retryFailedButton.setEnabled(
            not self._is_busy()
            and bool(self._failed_downloads)
        )

    def _update_failed_downloads(self, session: Dict[str, Any]):
        failed_results: List[WadBrowserResult] = list(session.get("failed_results", []))
        if not failed_results:
            self._failed_downloads = []
            self.retryFailedButton.setToolTip("No failed downloads to retry")
            self.retryFailedButton.hide()
            return

        deduped: List[WadBrowserResult] = []
        seen = set()
        for result in failed_results:
            key = _result_identity(result)
            if not key:
                key = result.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)

        self._failed_downloads = deduped
        self.retryFailedButton.show()
        self._failed_download_auto_add = bool(session.get("auto_add", False))
        self.retryFailedButton.setToolTip(
            f"Retry {len(self._failed_downloads)} failed download(s)"
        )

    def _is_busy(self) -> bool:
        return bool(self._search_sessions or self._download_sessions or self._discover_session is not None)

    def _refresh_controls_for_state(self):
        if self._download_sessions or self._discover_session is not None:
            self._set_controls_enabled(False)
            return
        if self._search_sessions:
            self._set_controls_enabled(False)
            self.clearSearchButton.setEnabled(True)
            self.clearSearchButton.setText("CANCEL SEARCH")
            self.resultsTree.setEnabled(self.resultsTree.topLevelItemCount() > 0)
            self._on_selection_changed()
            return
        self.clearSearchButton.setText("RESET")
        self._set_controls_enabled(True)
        self._on_selection_changed()

    def _set_status(self, text: str):
        self.statusLabel.setText(text)
        self.statusChanged.emit(text)

    def _search_feedback_text(self, token: int) -> str:
        session = self._search_sessions.get(token)
        if not session:
            return ""

        query = str(session.get("query", "")).strip()
        total = len(session.get("sources", ()))
        if total <= 0:
            total = 1
        remaining = len(session.get("remaining", ()))
        done = total - remaining

        stats = session.get("source_stats", {})
        scanned = sum(int(v.get("raw", 0)) for v in stats.values())
        matched = sum(int(v.get("matched", 0)) for v in stats.values())
        errors = sum(1 for v in stats.values() if v.get("status") == "error")
        elapsed = time.time() - float(session.get("started_at", time.time()))

        phase = str(session.get("phase", "searching"))
        phase_label = {
            "initial": "initial search",
            "fallback_idgames": "fallback: other idgames mirrors",
            "fallback_all_enabled": "fallback: all enabled sources",
            "fallback_fullsort": "fallback: direct fullsort",
            "relaxed": "fallback: relaxed matching",
        }.get(phase, "searching")
        if not query:
            return (
                f"No query (browse) · {done}/{total} sources in {elapsed:0.1f}s · "
                f"scanned {scanned} index entries"
            )

        if _tokenize_query(query):
            return (
                f'Query "{query}" · {phase_label} · '
                f"{done}/{total} sources in {elapsed:0.1f}s · "
                f"scanned {scanned} entries, matched {matched}"
                + (f", {errors} source errors" if errors else "")
            )

        return (
            f'Terms are mostly stop-words for "{query}" · {done}/{total} sources in {elapsed:0.1f}s · '
            f"{scanned} entries scanned"
        )

    def _update_search_feedback(self, token: int, *, in_progress: bool = True):
        session = self._search_sessions.get(token)
        if not session:
            self.searchFeedbackLabel.hide()
            self.searchProgressBar.hide()
            return

        total = len(session.get("sources", ()))
        remaining = len(session.get("remaining", ()))
        done = total - remaining
        if total > 0:
            self.searchProgressBar.setMaximum(max(total, 1))
            self.searchProgressBar.setValue(done)
            self.searchProgressBar.show()
        else:
            self.searchProgressBar.hide()

        if in_progress:
            self.searchFeedbackLabel.show()
            self.searchProgressBar.show()
        else:
            self.searchProgressBar.hide()
        self.searchFeedbackLabel.setText(self._search_feedback_text(token))

    def _clear_search_feedback(self):
        self.searchFeedbackLabel.clear()
        self.searchFeedbackLabel.hide()
        self.searchProgressBar.hide()

    def _change_library_dir(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select PWAD library folder",
            str(self.library_dir),
        )
        if not selected:
            return
        self._set_library_dir(selected)

    def _sweep_orphan_parts(self):
        """Remove partial downloads orphaned by killed/crashed sessions."""
        sweep_orphan_parts(self.library_dir)

    def _set_library_dir(self, directory: str):
        new_dir = Path(directory).expanduser()
        if not new_dir.exists():
            try:
                new_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._set_status(f"Cannot use library folder {new_dir}: {exc}")
                return
        self.library_dir = new_dir
        self.libraryDirLabel.setText(f"Library folder: {self.library_dir}")
        if callable(self.library_dir_changed):
            self.library_dir_changed(str(self.library_dir))
        self._refresh_local_library()
        self._set_status(f"Library directory set to {self.library_dir}")

    def _selected_source_ids(self) -> List[str]:
        selected_source = self.sourceCombo.currentData()
        if selected_source is None:
            return []

        if selected_source != "all":
            source = self.searchSources.get(selected_source)
            if source and source.get("enabled", True):
                return [selected_source]
            return [
                source_id
                for source_id, source_data in self.searchSources.items()
                if source_data.get("enabled", True)
            ]

        ordered = []
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            source = self.searchSources.get(source_id)
            if not source:
                continue
            if (item.checkState() == Qt.Checked and source.get("enabled", True)) and source_id in self.searchSources:
                ordered.append(source_id)

        if not ordered:
            ordered = [
                source_id
                for source_id in self.sourceOrder
                if (source := self.searchSources.get(source_id))
                and source.get("enabled", True)
            ]

        return ordered

    _is_idgames_source = staticmethod(is_idgames_source)

    def _iter_source_rows(self, ids: Iterable[str]):
        for source_id in self.sourceOrder:
            if source_id in ids:
                yield source_id

    def _add_custom_source(self):
        text = self.customSourceInput.text().strip()
        if not text:
            self._set_status("Enter a mirror URL first.")
            return

        entries: List[dict] = []
        for raw in [line.strip() for line in text.splitlines() if line.strip()]:
            parsed = self._parse_custom_source_entry(raw)
            if parsed is None:
                continue
            entries.append(parsed)

        if not entries:
            self._set_status("No valid source entries found.")
            return

        added = 0
        skipped = 0
        for parsed in entries:
            if self._is_duplicate_source_base(parsed["url"]):
                skipped += 1
                continue

            source_id = self._allocate_source_id(
                self._custom_source_id(parsed["url"], parsed["name"], parsed.get("base"))
            )

            source_name = parsed["name"] or self._make_host_name(parsed["url"])
            source_url = parsed["url"]
            source_index = parsed["index"]
            source_parser = parsed["parser"]
            self.searchSources[source_id] = {
                "id": source_id,
                "name": source_name,
                "base": source_url.rstrip("/"),
                "index": source_index,
                "browser": source_url.rstrip("/"),
                "parser": source_parser,
                "enabled": True,
                "status": SOURCE_STATUS_UNKNOWN,
                "status_message": "Custom source pending reachability check",
                "status_checked_at": 0.0,
            }
            self.sourceOrder.append(source_id)
            added += 1

        if added == 0 and skipped:
            self._set_status("No new sources added. They were duplicates.")
        elif added:
            self._set_status(
                f"Added {added} source(s)"
                + (f" (skipped {skipped} duplicate)" if skipped else "")
            )
        else:
            self._set_status("No new sources added.")

        self._rebuild_source_list()
        self._refresh_source_combo()
        self.customSourceInput.clear()
        self._emit_source_state()

    def _add_builtin_sources(self):
        added = 0
        skipped = 0
        for source in DEFAULT_SOURCES:
            source_id = _normalize_source_id(source.get("id", ""))
            base = str(source.get("base", "")).rstrip("/")
            if not source_id or not base:
                continue
            if source_id in self.searchSources or self._is_duplicate_source_base(base):
                skipped += 1
                continue
            self.searchSources[source_id] = self._coerce_source(source)
            self.sourceOrder.append(source_id)
            added += 1

        if added == 0:
            if skipped:
                self._set_status("No missing built-in sources to add.")
            else:
                self._set_status("Built-in source data missing.")
            return

        self._store_source_health_cache()
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self._set_status(f"Added {added} built-in source(s).")

    def _custom_source_id(self, url: str, name: Optional[str], base: Optional[str] = None) -> str:
        return custom_source_id(url, name, base)

    def _allocate_source_id(self, source_id: str) -> str:
        return allocate_source_id(source_id, self.searchSources)

    _make_host_name = staticmethod(make_host_name)

    _parse_custom_source_entry = staticmethod(parse_custom_source_entry)

    _normalize_source_url = staticmethod(normalize_source_url)

    _infer_source_parser = staticmethod(infer_source_parser)

    _default_index_for_parser = staticmethod(default_index_for_parser)

    def _collect_discovery_seeds(self) -> List[str]:
        if not self.discoverSeedInput.text().strip():
            return []
        seed_lines: List[str] = []
        for raw in re.split(r"[\n,;\s]+", self.discoverSeedInput.text()):
            line = raw.strip()
            if not line:
                continue
            if not re.match(r"^https?://", line, flags=re.I) and not line.startswith("//"):
                line = f"https://{line}"
            seed_lines.append(line)
        return seed_lines

    def _discover_mirrors(self):
        if self._discover_session is not None:
            self._set_status("Mirror discovery already in progress...")
            return

        self._discover_token += 1
        token = self._discover_token
        self._discover_session = token
        self._set_status("Discovering Doomworld idgames mirrors...")
        self._set_controls_enabled(False)

        worker = _DiscoverSourcesWorker(token, seed_urls=self._collect_discovery_seeds())
        worker.signals.finished.connect(self._on_discover_finished)
        worker.signals.failed.connect(self._on_discover_failed)
        self._thread_pool.start(worker)
        if self.discoverSeedInput.text().strip():
            self.discoverSeedInput.clear()

    def _discover_source_id(self, base: str, name: str) -> str:
        normalized = _normalize_source_id(name)
        if not normalized:
            normalized = _normalize_source_id(base)
        if not normalized:
            normalized = "discovered"
        return self._allocate_source_id(f"discovered-{normalized}")

    def _is_duplicate_source_base(self, base: str) -> bool:
        normalized = base.rstrip("/")
        for source in self.searchSources.values():
            if source.get("base", "").rstrip("/") == normalized:
                return True
        return False

    def _on_discover_finished(self, token: int, discovered: list):
        if token != self._discover_session:
            return
        self._discover_session = None
        if not isinstance(discovered, list):
            discovered = []

        added = 0
        updated = 0
        disabled = 0

        for payload in discovered:
            if not isinstance(payload, dict):
                continue

            base = str(payload.get("base", "")).strip()
            if not base:
                continue

            if self._is_duplicate_source_base(base):
                existing = next(
                    (
                        sid
            for sid, item in self.searchSources.items()
                if item.get("base", "").rstrip("/") == base.rstrip("/") and sid.startswith("discovered-")
            ),
            None,
        )
                if existing is not None and existing.startswith("discovered-"):
                    self.searchSources[existing]["enabled"] = bool(payload.get("enabled", True))
                    self.searchSources[existing]["index"] = str(payload.get("index", "fullsort.gz")).strip() or "fullsort.gz"
                    self._set_source_status(
                        existing,
                        SOURCE_STATUS_OK if payload.get("enabled", True) else SOURCE_STATUS_DISABLED,
                        "Mirrors probe updated",
                        persist=True,
                    )
                    updated += 1
                continue

            source_id = self._discover_source_id(base, str(payload.get("name", base)))
            if source_id in self.searchSources:
                continue

            self.searchSources[source_id] = {
                "id": source_id,
                "name": str(payload.get("name", base)),
                "base": base.rstrip("/"),
                "index": str(payload.get("index", "fullsort.gz")).strip() or "fullsort.gz",
                "browser": str(payload.get("browser", base)).rstrip("/"),
                "parser": _coerce_parser(payload.get("parser", "fullsort")),
                "enabled": bool(payload.get("enabled", True)),
                "status": SOURCE_STATUS_OK if bool(payload.get("enabled", True)) else SOURCE_STATUS_DISABLED,
                "status_message": "Discovered and reachable"
                if bool(payload.get("enabled", True))
                else "Discovered but unreachable",
                "status_checked_at": time.time(),
            }
            self.sourceOrder.append(source_id)
            added += 1
            if not bool(payload.get("enabled", True)):
                disabled += 1
                self._source_health[source_id] = {
                    "status": SOURCE_STATUS_DISABLED,
                    "status_message": "Discovered but unreachable",
                    "status_checked_at": time.time(),
                }
            else:
                self._source_health[source_id] = {
                    "status": SOURCE_STATUS_OK,
                    "status_message": "Discovered and reachable",
                    "status_checked_at": time.time(),
                }

        self._store_source_health_cache()

        if added:
            status = f"Discovered {added} new source(s)."
            if disabled:
                status += f" {disabled} mirror(s) unreachable at discovery."
            self._set_status(status)
        elif updated:
            self._set_status("Mirror source reachability updated.")
        else:
            self._set_status("No new Doomworld mirrors discovered.")

        self._rebuild_source_list()
        self._refresh_source_combo()
        self._refresh_controls_for_state()
        self._emit_source_state()

    def _on_discover_failed(self, token: int, error: str):
        if token != self._discover_session:
            return
        self._discover_session = None
        self._refresh_controls_for_state()
        self._set_status(f"Mirror discovery failed: {error}")

    def _set_all_sources_enabled(self, enabled: bool):
        changed = False
        for source_id, source in self.searchSources.items():
            if source.get("enabled") != bool(enabled):
                source["enabled"] = bool(enabled)
                source["status"] = SOURCE_STATUS_OK if enabled else SOURCE_STATUS_DISABLED
                source["status_message"] = "User enabled" if enabled else "User disabled"
                source["status_checked_at"] = time.time()
                self._source_health[source_id] = {
                    "status": source["status"],
                    "status_message": source["status_message"],
                    "status_checked_at": source["status_checked_at"],
                }
                changed = True

        if changed:
            self._store_source_health_cache()
            self._rebuild_source_list()
            self._refresh_source_combo()
            self._emit_source_state()
            self._set_status("Enabled all sources" if enabled else "Disabled all sources")
        else:
            self._set_status("No source state changes.")

    def _clear_custom_source(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if source_id.startswith("custom:"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No user-added source to remove.")
        else:
            self._set_status(f"Removed {removed} user-added source(s).")
            self._store_source_health_cache()
        self._emit_source_state()
        self._refresh_source_list()

    def _purge_unavailable_sources(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if not source_id.startswith(("custom:", "discovered-")):
                continue

            source = self.searchSources.get(source_id)
            if not source:
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1
                continue

            status = source.get("status", SOURCE_STATUS_UNKNOWN)
            if status in {SOURCE_STATUS_UNREACHABLE, SOURCE_STATUS_ERROR}:
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No unavailable user sources to purge.")
        else:
            self._store_source_health_cache()
            self._emit_source_state()
            self._rebuild_source_list()
            self._refresh_source_combo()
            self._set_status(f"Purged {removed} unavailable source(s).")

    def _clear_discovered_sources(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if source_id.startswith("discovered-"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No discovered sources to remove.")
        else:
            self._set_status(f"Removed {removed} discovered source(s).")
            self._store_source_health_cache()
        self._emit_source_state()
        self._refresh_source_list()

    def _remove_selected_custom_sources(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a user-added source in the list.")
            return

        removed = 0
        for item in selected:
            source_id = item.data(Qt.UserRole)
            if source_id and (
                source_id.startswith("custom:") or source_id.startswith("discovered-")
            ):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1
        if removed == 0:
            self._set_status("Only user-added sources can be removed this way.")
        else:
            self._set_status(f"Removed {removed} source(s).")
            self._store_source_health_cache()
        self._emit_source_state()

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
        source = self.searchSources[source_id]
        source["enabled"] = item.checkState() == Qt.Checked
        self._set_source_status(
            source_id,
            SOURCE_STATUS_DISABLED if not source["enabled"] else SOURCE_STATUS_UNKNOWN,
            "User-disabled" if not source["enabled"] else "",
        )
        self._emit_source_state()
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
        self._emit_source_state()

        self.sourceOrderList.setCurrentRow(target)
        self._set_status(f"Moved source: {self.searchSources[source_id]['name']}")

    def _move_source_to_top(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        try:
            idx = self.sourceOrder.index(source_id)
        except ValueError:
            return

        if idx <= 0:
            self._set_status("Source already at top.")
            return

        self.sourceOrder.pop(idx)
        self.sourceOrder.insert(0, source_id)
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self.sourceOrderList.setCurrentRow(0)
        self._set_status(f"Moved source to top: {self.searchSources[source_id]['name']}")

    def _move_source_to_bottom(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        try:
            idx = self.sourceOrder.index(source_id)
        except ValueError:
            return

        if idx >= len(self.sourceOrder) - 1:
            self._set_status("Source already at bottom.")
            return

        self.sourceOrder.pop(idx)
        self.sourceOrder.append(source_id)
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self.sourceOrderList.setCurrentRow(len(self.sourceOrder) - 1)
        self._set_status(f"Moved source to bottom: {self.searchSources[source_id]['name']}")

    def _on_clear_search(self):
        if self._search_sessions:
            # Workers are not force-terminated, but advancing the token makes
            # their late results harmless and immediately restores the UI.
            self._search_token += 1
            self._search_sessions.clear()
            self.searchProgressBar.hide()
            self._set_status("Search cancelled.")
        self.searchInput.clear()
        self.resultsTree.clear()
        self._rendered_results = []
        self.browserTabs.setTabText(0, "RESULTS [0]")
        self._clear_search_feedback()
        self._refresh_controls_for_state()
        if not self.statusLabel.text().startswith("Search cancelled"):
            self._set_status("Search cleared.")

    def _on_search(self):
        if not self.expandBrowserButton.isChecked():
            self.expandBrowserButton.setChecked(True)
        query = self.searchInput.text().strip().lower()
        source_ids = self._selected_source_ids()

        self.resultsTree.clear()
        if not source_ids:
            self._rendered_results = []
            self._clear_search_feedback()
            self._set_status("No source selected.")
            return

        self._rendered_results = []

        self._search_token += 1
        token = self._search_token
        selected_sources = list(self._iter_source_rows(source_ids))
        self._search_sessions[token] = {
            "query": query,
            "started_at": time.time(),
            "sources": list(selected_sources),
            "remaining": set(selected_sources),
            "requested_sources": list(selected_sources),
            "results": {},
            "raw_results": {},
            "errors": {},
            "ready": False,
            "retry_count": 0,
            "phase": "initial",
            "source_stats": {},
        }

        self._set_status("Searching...")
        self._update_search_feedback(token, in_progress=True)
        self._set_controls_enabled(False)
        self.clearSearchButton.setEnabled(True)
        self.clearSearchButton.setText("CANCEL SEARCH")

        started = 0
        for source_id in self._search_sessions[token]["sources"]:
            source = self.searchSources.get(source_id)
            if not source:
                self._search_sessions[token]["remaining"].discard(source_id)
                continue

            cached = self._index_cache.get(source_id)
            if (not query) and self._is_cache_valid(source_id) and cached is not None:
                source["status_message"] = f"Cached index: {_short_age(time.time() - cached.fetched_at)}"
                source["status"] = SOURCE_STATUS_CACHED
                source["status_checked_at"] = cached.fetched_at
                self._set_source_status(
                    source_id,
                    SOURCE_STATUS_CACHED,
                    source["status_message"],
                    persist=True,
                )
                self._on_search_ready(token, source_id, cached.entries)
                continue

            self._set_source_status(source_id, SOURCE_STATUS_CHECKING, "Refreshing index")
            started += 1
            worker = _SearchWorker(token, source_id, source, query=query)
            worker.setAutoDelete(False)
            self._active_search_workers.add(worker)

            def _on_search_worker_finished(_token: int, _source_id: str, payload: List[WadBrowserResult], _worker=worker):
                self._active_search_workers.discard(_worker)
                self._on_search_ready(_token, _source_id, payload)

            def _on_search_worker_failed(_token: int, _source_id: str, error: str, _worker=worker):
                self._active_search_workers.discard(_worker)
                self._on_search_failed(_token, _source_id, error)

            worker.signals.finished.connect(_on_search_worker_finished)
            worker.signals.failed.connect(_on_search_worker_failed)
            self._thread_pool.start(worker)

        if not started:
            self._finalize_search_session(token)

    def _collect_query_retry_sources(
        self,
        session: Dict[str, Any],
        *,
        include_disabled: bool = False,
    ) -> List[str]:
        excluded = set(session.get("sources", ()))
        return [
            source_id
            for source_id in self.sourceOrder
            if (
                source_id not in excluded
                and (include_disabled or self.searchSources.get(source_id, {}).get("enabled", True))
                and self._is_idgames_source(self.searchSources.get(source_id, {}))
            )
        ]

    def _on_search_ready(self, token: int, source_id: str, payload: List[WadBrowserResult]):
        if token != self._search_token:
            return
        session = self._search_sessions.get(token)
        if not session:
            return

        session["remaining"].discard(source_id)

        query = session["query"]
        session.setdefault("raw_results", {})
        session["raw_results"][source_id] = list(payload)
        filtered = self._filter_search_results(payload, query)
        session.setdefault("source_stats", {})
        session["source_stats"][source_id] = {
            "status": "ok",
            "raw": len(payload),
            "matched": len(filtered),
        }
        source = self.searchSources.get(source_id)
        if source:
            normalized_payload = list(payload[:MAX_CACHED_INDEX_ENTRIES])
            self._index_cache[source_id] = _IndexCacheEntry(
                source_id=source_id,
                source_name=source["name"],
                fetched_at=time.time(),
                entries=normalized_payload,
            )
            self._store_index_cache(source_id, source, normalized_payload)
        self._set_source_status(
            source_id,
            SOURCE_STATUS_OK,
            f"{len(filtered)} result(s)",
            persist=True,
        )

        session["results"][source_id] = filtered
        self._set_search_progress(token)
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
        session.setdefault("source_stats", {})
        session["source_stats"][source_id] = {
            "status": "error",
            "raw": 0,
            "matched": 0,
            "error": error,
        }
        self._set_source_status(
            source_id,
            SOURCE_STATUS_UNREACHABLE,
            error,
            persist=True,
        )
        self._set_search_progress(token)
        self._render_search_results(token)
        if not session["remaining"]:
            self._finalize_search_session(token)

    def _set_search_progress(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return
        total = len(session.get("sources", []))
        if total <= 0:
            return
        self._update_search_feedback(token, in_progress=True)
        self._set_status(self._search_feedback_text(token))

    def _filter_search_results(self, entries: List[WadBrowserResult], query: str) -> List[WadBrowserResult]:
        if not query:
            # Show newest/popular-ish items first by file size while avoiding overload.
            filtered = sorted(entries, key=lambda item: item.size_bytes, reverse=True)
            return filtered[:DEFAULT_SOURCE_LIMIT]

        q = query.strip().lower()
        filtered: List[WadBrowserResult] = []
        fallback_filtered: List[WadBrowserResult] = []
        loose_matches: List[WadBrowserResult] = []
        query_tokens = _tokenize_query(q)
        for item in entries:
            haystack = _result_search_blob(item)
            if _query_matches(haystack, q):
                filtered.append(item)
                continue
            if _query_matches_any(haystack, q):
                fallback_filtered.append(item)
                continue

            if query_tokens:
                filename = Path(item.remote_path).name.lower()
                title = item.title.lower()
                compact_filename = re.sub(r"[^a-z0-9]+", "", filename)
                compact_title = re.sub(r"[^a-z0-9]+", "", title)
                if (
                    any(token in filename or token in compact_filename for token in query_tokens)
                    or any(token in title or token in compact_title for token in query_tokens)
                ):
                    loose_matches.append(item)

        combined = []
        dedupe = set()
        for item in filtered:
            key = _result_identity(item)
            if key in dedupe:
                continue
            dedupe.add(key)
            combined.append((item, 1))

        for item in fallback_filtered:
            key = _result_identity(item)
            if key in dedupe:
                continue
            dedupe.add(key)
            combined.append((item, 0))

        for item in loose_matches:
            key = _result_identity(item)
            if key in dedupe:
                continue
            dedupe.add(key)
            combined.append((item, -1))

        return [item for item, _priority in sorted(
            combined,
            key=lambda payload: (
                payload[1],
                self._search_score(payload[0], q),
                payload[0].size_bytes,
            ),
            reverse=True,
        )][:DEFAULT_SOURCE_LIMIT]

    def _filter_search_results_relaxed(self, entries: List[WadBrowserResult], query: str) -> List[WadBrowserResult]:
        if not query:
            return sorted(entries, key=lambda item: item.size_bytes, reverse=True)[:DEFAULT_SOURCE_LIMIT]

        q = query.strip().lower()
        query_tokens = _tokenize_query(q)
        if not query_tokens:
            return sorted(entries, key=lambda item: item.size_bytes, reverse=True)[:DEFAULT_SOURCE_LIMIT]

        loose: List[WadBrowserResult] = []
        dedupe = set()
        for item in entries:
            title = item.title.lower()
            path = item.remote_path.lower()
            filename = Path(item.remote_path).name.lower()
            description = item.description.lower()
            metadata = item.metadata_text.lower()
            compact_path = re.sub(r"[^a-z0-9]+", "", path)
            compact_title = re.sub(r"[^a-z0-9]+", "", title)
            compact_filename = re.sub(r"[^a-z0-9]+", "", filename)
            compact_description = re.sub(r"[^a-z0-9]+", "", description)

            haystack = f"{title}|{filename}|{path}|{description}|{metadata}".lower()
            haystack_compact = f"{compact_title}|{compact_filename}|{compact_path}|{compact_description}"

            if any(
                token in haystack or token in haystack_compact
                for token in query_tokens
            ):
                key = _result_identity(item)
                if key in dedupe:
                    continue
                dedupe.add(key)
                loose.append(item)

        loose.sort(
            key=lambda item: (
                self._search_score(item, q),
                item.size_bytes,
                item.title.lower(),
            ),
            reverse=True,
        )
        return loose[:DEFAULT_SOURCE_LIMIT]

    @staticmethod
    def _search_score(item: WadBrowserResult, query: str) -> int:
        if not query:
            return 0
        normalized = query.strip().lower()
        tokens = _tokenize_query(normalized)
        if not tokens:
            return 0
        title = item.title.lower()
        path = item.remote_path.lower()
        description = item.description.lower()
        metadata_text = item.metadata_text.lower()
        score = 0
        if normalized in title:
            score += 120
        if normalized in path:
            score += 90
        if normalized in description:
            score += 60
        if normalized in metadata_text:
            score += 45
        for token in tokens:
            if token in title:
                score += 18
            if token in path:
                score += 12
            if token in description:
                score += 9
            if token in metadata_text:
                score += 6
        return score

    def _render_search_results(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        selected_identities = {
            _result_identity(payload)
            for payload in self._selected_search_results()
            if _result_identity(payload)
        }
        self.resultsTree.blockSignals(True)
        self.resultsTree.clear()
        self._rendered_results = []
        source_order = session["sources"]
        source_rank = {source_id: rank for rank, source_id in enumerate(source_order)}
        all_results: List[WadBrowserResult] = []
        for source_id in source_order:
            all_results.extend(session["results"].get(source_id, []))

        # Keep order consistent and avoid duplicate remote entries for stability.
        merged: List[WadBrowserResult] = []
        seen = set()
        for item in all_results:
            key = _result_identity(item)
            if not key:
                key = item.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        query = str(session.get("query", "")).strip().lower()
        if query:
            merged.sort(
                key=lambda item: (
                    source_rank.get(item.source_id, len(source_rank) + 1),
                    -self._search_score(item, query),
                    -item.size_bytes,
                    item.title.lower(),
                )
            )
        else:
            merged.sort(
                key=lambda item: (
                    source_rank.get(item.source_id, len(source_rank) + 1),
                    -item.size_bytes,
                    item.title.lower(),
                )
            )

        for item in merged:
            details = _normalize_metadata_text(item.description, item.metadata_text)
            summary = _summarize_text(details)
            row = QTreeWidgetItem([
                item.source_name,
                item.title,
                summary,
                _human_size(item.size_bytes),
                item.remote_path,
            ])
            row.setData(0, Qt.UserRole, item)
            row.setData(3, Qt.UserRole, item.size_bytes)
            if details:
                row.setToolTip(1, details)
                row.setToolTip(2, details)
                row.setToolTip(4, details)
            self.resultsTree.addTopLevelItem(row)
            if _result_identity(item) in selected_identities:
                row.setSelected(True)
        self._rendered_results = merged
        if merged and not self.resultsTree.selectedItems():
            first = self.resultsTree.topLevelItem(0)
            first.setSelected(True)
            self.resultsTree.setCurrentItem(first)
        self.resultsTree.blockSignals(False)
        self.resultsTree.setEnabled(bool(merged) and not self._download_sessions)
        self.browserTabs.setTabText(0, f"RESULTS [{len(merged)}]")
        self._on_selection_changed()

    def _finalize_search_session(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        query = str(session.get("query", "")).strip()
        query_tokens = _tokenize_query(query)
        retry_count = int(session.get("retry_count", 0))
        elapsed = time.time() - float(session.get("started_at", time.time()))
        source_count = max(len(session.get("sources", ())), 1)
        source_stats = session.get("source_stats", {})
        scanned_entries = sum(int(item.get("raw", 0)) for item in source_stats.values())
        prefiltered_hits = sum(int(item.get("matched", 0)) for item in source_stats.values())
        sources_done = len(source_stats)
        error_count = len(session.get("errors", {}))

        count = sum(len(items) for items in session["results"].values())
        if count == 0:
            if query and retry_count < 1:
                fallback_sources = self._collect_query_retry_sources(
                    session,
                    include_disabled=False,
                )
                if not fallback_sources:
                    fallback_sources = self._collect_query_retry_sources(
                        session,
                        include_disabled=True,
                    )
                    if fallback_sources:
                        self._set_status("Primary source had no matches; expanding search to all Doomworld mirrors.")

                if fallback_sources:
                    session["retry_count"] = retry_count + 1
                    session["sources"].extend(fallback_sources)
                    session["remaining"] = set(fallback_sources)
                    session["phase"] = "fallback_idgames"

                    for source_id in fallback_sources:
                        source = self.searchSources.get(source_id)
                        if not source:
                            session["remaining"].discard(source_id)
                            continue
                        self._set_source_status(source_id, SOURCE_STATUS_CHECKING, "Searching fallback source")
                        worker = _SearchWorker(token, source_id, source, query=query)
                        worker.setAutoDelete(False)
                        self._active_search_workers.add(worker)

                        def _on_search_worker_finished(_token: int, _source_id: str, payload: List[WadBrowserResult], _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_ready(_token, _source_id, payload)

                        def _on_search_worker_failed(_token: int, _source_id: str, error: str, _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_failed(_token, _source_id, error)

                        worker.signals.finished.connect(_on_search_worker_finished)
                        worker.signals.failed.connect(_on_search_worker_failed)
                        self._thread_pool.start(worker)

                    self._set_status("No matches found, retrying with all enabled idgames mirrors...")
                    return

            # If only one branch was available (or all enabled mirrors were already
            # searched) retry once more against every remaining enabled source.
            if query and retry_count < 2:
                fallback_sources = [
                    source_id
                    for source_id in self.sourceOrder
                    if (
                        source_id not in session.get("sources", ())
                        and self.searchSources.get(source_id, {}).get("enabled", True)
                    )
                ]
                if not fallback_sources:
                    fallback_sources = [
                        source_id
                        for source_id in self.sourceOrder
                        if (
                            source_id not in session.get("sources", ())
                            and self._is_idgames_source(self.searchSources.get(source_id, {}))
                        )
                    ]

                if fallback_sources:
                    session["retry_count"] = retry_count + 1
                    session["sources"].extend(fallback_sources)
                    session["remaining"] = set(fallback_sources)
                    session["phase"] = "fallback_all_enabled"

                    for source_id in fallback_sources:
                        source = self.searchSources.get(source_id)
                        if not source:
                            session["remaining"].discard(source_id)
                            continue
                        self._set_source_status(
                            source_id,
                            SOURCE_STATUS_CHECKING,
                            "Retrying search on all enabled sources",
                        )
                        worker = _SearchWorker(token, source_id, source, query=query)
                        worker.setAutoDelete(False)
                        self._active_search_workers.add(worker)

                        def _on_search_worker_finished_retry(_token: int, _source_id: str, payload: List[WadBrowserResult], _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_ready(_token, _source_id, payload)

                        def _on_search_worker_failed_retry(_token: int, _source_id: str, error: str, _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_failed(_token, _source_id, error)

                        worker.signals.finished.connect(_on_search_worker_finished_retry)
                        worker.signals.failed.connect(_on_search_worker_failed_retry)
                        self._thread_pool.start(worker)

                    self._set_status("No matches, retrying with all enabled sources...")
                    return

            if query and retry_count < 3:
                fallback_sources = [
                    source_id
                    for source_id in self.sourceOrder
                    if source_id not in session.get("sources", ())
                    and self.searchSources.get(source_id, {}).get("enabled", True)
                ]

                if not fallback_sources:
                    fallback_sources = [
                        source_id
                        for source_id in self.sourceOrder
                        if self.searchSources.get(source_id, {}).get("enabled", True)
                        and source_id not in session.get("sources", ())
                    ]
                if not fallback_sources and len(session.get("requested_sources", [])) == 1:
                    fallback_sources = [
                        source_id
                        for source_id in self.sourceOrder
                        if source_id not in session.get("sources", ())
                        and self.searchSources.get(source_id, {}).get("status", SOURCE_STATUS_UNKNOWN)
                        != SOURCE_STATUS_DISABLED
                    ]

                if not fallback_sources:
                    status = "No matches. Try a broader query or another source."
                    session["retry_count"] = retry_count
                    # No additional retry candidates are available.
                else:
                    session["retry_count"] = retry_count + 1
                    for source_id in fallback_sources:
                        if source_id not in session["sources"]:
                            session["sources"].append(source_id)
                    session["remaining"] = set(fallback_sources)

                    for source_id in fallback_sources:
                        source = self.searchSources.get(source_id)
                        if not source:
                            session["remaining"].discard(source_id)
                            continue

                        force_fullsort = dict(source)
                        force_fullsort["parser"] = "fullsort"
                        force_fullsort.setdefault("index", "fullsort.gz")

                        self._set_source_status(
                            source_id,
                            SOURCE_STATUS_CHECKING,
                            "Retrying query with direct fullsort",
                        )
                        worker = _SearchWorker(token, source_id, force_fullsort, query=query)
                        worker.setAutoDelete(False)
                        self._active_search_workers.add(worker)

                        def _on_search_worker_finished_fullsort(_token: int, _source_id: str, payload: List[WadBrowserResult], _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_ready(_token, _source_id, payload)

                        def _on_search_worker_failed_fullsort(_token: int, _source_id: str, error: str, _worker=worker):
                            self._active_search_workers.discard(_worker)
                            self._on_search_failed(_token, _source_id, error)

                        worker.signals.finished.connect(_on_search_worker_finished_fullsort)
                        worker.signals.failed.connect(_on_search_worker_failed_fullsort)
                        self._thread_pool.start(worker)

                    self._set_status("No matches. Re-running enabled sources with direct fullsort search...")
                    session["phase"] = "fallback_fullsort"
                    return

            if query and not session.get("relaxed_applied"):
                raw_payloads = [
                    item
                    for payload in session.get("raw_results", {}).values()
                    for item in payload
                ]
                relaxed = self._filter_search_results_relaxed(raw_payloads, query)
                if relaxed:
                    session["relaxed_applied"] = True
                    session["phase"] = "relaxed"
                    session["sources"] = ["_fallback_relaxed"]
                    session["results"] = {"_fallback_relaxed": relaxed}
                    self._set_search_progress(token)
                    self._render_search_results(token)
                    self._search_sessions.pop(token, None)
                    self._refresh_controls_for_state()
                    self.searchFeedbackLabel.setText(
                        (
                            f"Relaxed matching engaged · completed in {elapsed:0.1f}s. "
                            f"Showing broader filename/path matches for '{query}'."
                        )
                    )
                    self.searchFeedbackLabel.show()
                    self.searchProgressBar.hide()
                    self._set_status("No exact matches. Showing relaxed filename/path matches.")
                    return

            if not query:
                status = (
                    f"No entries found for the selected source set. "
                    f"Try enabling more sources or rebuilding indexes."
                )
            elif not query_tokens:
                status = f'No usable query terms in "{query}". Try "blood", "skeleton", or "river".'
            else:
                status = f'No matches for "{query}".'

            details = [
                f"Complete in {elapsed:0.1f}s",
                f"Sources done: {sources_done}/{source_count}",
                f"Entries scanned: {scanned_entries}",
                f"Matched: {prefiltered_hits}",
            ]
            if error_count:
                details.append(f"{error_count} source issue(s)")
            status = f"{status} ({', '.join(details)})"
            if error_count:
                status += " Check source health and network."

            self.searchFeedbackLabel.setText(status)
            self.searchFeedbackLabel.show()
            self.searchProgressBar.hide()
        else:
            status = (
                f"Found {count} result(s) in {elapsed:0.1f}s across "
                f"{sources_done}/{source_count} source(s)."
            )
            if session["errors"]:
                status += " " + "; ".join(sorted(session["errors"].values()))
            self.searchFeedbackLabel.setText(
                (
                    f"Found {count} result(s). "
                    f"Completed in {elapsed:0.1f}s, scanned {scanned_entries} entries, "
                    f"matched {prefiltered_hits} entries."
                )
            )
            self.searchFeedbackLabel.show()
            self.searchProgressBar.hide()

        self._search_sessions.pop(token, None)
        self._refresh_controls_for_state()
        self._set_status(status)

    def _on_selection_changed(self):
        selected_remote = self._selected_search_results()
        search_count = len(selected_remote)
        local_count = len(self.localTree.selectedItems())
        open_enabled = bool(search_count)
        download_session_active = bool(self._download_sessions)
        download_enabled = bool(search_count) and not download_session_active
        add_enabled = bool(search_count or local_count) and not download_session_active
        delete_enabled = bool(local_count)

        self.openButton.setEnabled(open_enabled)
        self.resultDetailsButton.setEnabled(open_enabled)
        self.downloadButton.setEnabled(download_enabled)
        self.downloadAllButton.setEnabled(bool(self._rendered_results) and not download_session_active)
        self.selectAllResultsButton.setEnabled(self.resultsTree.topLevelItemCount() > 0)
        self.clearResultsSelectionButton.setEnabled(len(self.resultsTree.selectedItems()) > 0)
        self.selectAllLocalButton.setEnabled(self.localTree.topLevelItemCount() > 0)
        self.clearLocalSelectionButton.setEnabled(len(self.localTree.selectedItems()) > 0)
        self.addButton.setEnabled(add_enabled)
        self.addAllButton.setEnabled(bool(self._rendered_results) and not download_session_active)
        self.deleteButton.setEnabled(delete_enabled)
        self.openLocalButton.setEnabled(delete_enabled)
        self.inspectLocalButton.setEnabled(delete_enabled)
        self.openLocalFolderButton.setEnabled(delete_enabled)

        if search_count == 1:
            result = selected_remote[0]
            self.resultSelectionLabel.setText(
                f"SELECTED: {result.title}  //  choose DOWNLOAD + QUEUE or double-click the row"
            )
            self.resultSelectionLabel.setProperty("active", True)
        elif search_count > 1:
            self.resultSelectionLabel.setText(
                f"SELECTED: {search_count} MODS  //  DOWNLOAD + QUEUE installs them in displayed order"
            )
            self.resultSelectionLabel.setProperty("active", True)
        else:
            self.resultSelectionLabel.setText("NO MOD SELECTED — click a row to inspect and install")
            self.resultSelectionLabel.setProperty("active", False)
        self.resultSelectionLabel.style().unpolish(self.resultSelectionLabel)
        self.resultSelectionLabel.style().polish(self.resultSelectionLabel)

        self._update_selection_previews()

        # Keep search button available if query is empty or results are visible.
        if self.searchInput.text().strip() and not self._search_sessions and not self._download_sessions:
            self.searchButton.setEnabled(True)

    def _update_selection_previews(self):
        if not hasattr(self, "resultPreview") or not hasattr(self, "libraryPreview"):
            return
        remote = self._selected_search_results()
        if not remote:
            self.resultPreview.setPlainText(
                "NO REMOTE MOD SELECTED\n\n"
                "Select a result to review its description, source, archive path, and size.\n"
                "Use DOWNLOAD + QUEUE to install it and add it to the current loadout."
            )
        elif len(remote) > 1:
            known_size = sum(item.size_bytes for item in remote if item.size_bytes > 0)
            self.resultPreview.setPlainText(
                f"BATCH SELECTED: {len(remote)} MODS\n"
                f"KNOWN DOWNLOAD SIZE: {_human_size(known_size)}\n\n"
                + "\n".join(f"[{item.source_name}] {item.title}" for item in remote[:18])
                + (f"\n... +{len(remote) - 18} more" if len(remote) > 18 else "")
            )
        else:
            self.resultPreview.setPlainText(self._result_details_text(remote[0]))

        local = self._selected_local_files()
        row_by_path = {str(row.get("path", "")): row for row in self._library_rows}
        if not local:
            self.libraryPreview.setPlainText(
                "NO LIBRARY MOD SELECTED\n\n"
                "Downloaded files live here independently of the current launch loadout.\n"
                "QUEUE SELECTED adds a file to the loadout without copying it again."
            )
        elif len(local) > 1:
            total_size = sum(int(row_by_path.get(path, {}).get("size", 0)) for path in local)
            self.libraryPreview.setPlainText(
                f"LIBRARY BATCH: {len(local)} MODS\n"
                f"DISK SPACE: {_human_size(total_size)}\n\n"
                + "\n".join(Path(path).name for path in local[:18])
                + (f"\n... +{len(local) - 18} more" if len(local) > 18 else "")
            )
        else:
            row = row_by_path.get(local[0], {})
            details = _normalize_metadata_text(row.get("description", ""), row.get("metadata_text", ""))
            self.libraryPreview.setPlainText(
                f"TITLE       : {row.get('title') or Path(local[0]).name}\n"
                f"FILE        : {Path(local[0]).name}\n"
                f"SIZE        : {row.get('size_text', '—')}\n"
                f"SOURCE      : {row.get('source', 'local')}\n"
                f"INSTALLED   : {row.get('installed_text', 'unknown')}\n"
                f"REMOTE PATH : {row.get('source_path', '-')}\n"
                f"LOCAL PATH  : {local[0]}\n\n"
                f"{details or 'No preserved description is available for this local file.'}"
            )

        # setPlainText can leave a newly resized editor scrolled to its final
        # line; metadata summaries should always open at their title.
        self.resultPreview.verticalScrollBar().setValue(0)
        self.libraryPreview.verticalScrollBar().setValue(0)
        self.resultPreview.moveCursor(QTextCursor.Start)
        self.libraryPreview.moveCursor(QTextCursor.Start)

    def _selected_search_results(self) -> List[WadBrowserResult]:
        ordered = []
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            if item.isSelected():
                payload = item.data(0, Qt.UserRole)
                if isinstance(payload, WadBrowserResult):
                    ordered.append(payload)
        return ordered

    def _result_details_text(self, result: WadBrowserResult) -> str:
        details = _normalize_metadata_text(result.description, result.metadata_text)
        if not details:
            details = "No description available."
        return (
            f"TITLE       : {result.title}\n"
            f"SOURCE      : {result.source_name}\n"
            f"SIZE        : {_human_size(result.size_bytes)}\n"
            f"ARCHIVE PATH: {result.remote_path}\n"
            f"DOWNLOAD URL: {result.download_url}\n\n"
            f"DESCRIPTION\n{'-' * 54}\n"
            f"{details}"
        )

    def _show_result_details(self, result: WadBrowserResult):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Result Details: {result.title}")
        dialog.resize(760, 420)

        layout = QVBoxLayout(dialog)
        summary = QLabel(
            f"<b>{escape(result.title)}</b><br>"
            f"Source: {escape(result.source_name)}<br>"
            f"Size: {escape(_human_size(result.size_bytes))}<br>"
            f"Path: {escape(result.remote_path)}"
        )
        summary.setTextFormat(Qt.RichText)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        details = QPlainTextEdit(dialog)
        details.setReadOnly(True)
        details.setPlainText(self._result_details_text(result))
        layout.addWidget(details, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def _show_selected_result_details(self):
        selected = self._selected_search_results()
        if not selected:
            self._set_status("No search item selected to inspect.")
            return
        self._show_result_details(selected[0])

    def _on_result_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, WadBrowserResult):
            return
        if column == 2:
            self._show_result_details(payload)
            return
        self._add_selected()

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
        selected_results = self._selected_search_results()
        if not selected_files and not selected_results:
            self._set_status("No selected item to add.")
            return

        added = []
        seen = set()
        for file_path in selected_files:
            if file_path not in seen:
                added.append(file_path)
                seen.add(file_path)

        missing = []
        for result in selected_results:
            candidate = self._safe_library_path(result)
            if str(candidate) in seen:
                continue
            if candidate.exists():
                added.append(str(candidate))
                seen.add(str(candidate))
            else:
                missing.append(result)

        if added:
            self.addRequested.emit(added)

        if missing:
            self._start_download_session(missing, auto_add=True)
        elif added:
            self._set_status("All selected mods are already downloaded and added.")
        else:
            self._set_status("No downloadable or local selection available.")

    def _add_all_results(self):
        if not self._rendered_results:
            self._set_status("No search results to add.")
            return
        self._start_download_session(self._rendered_results, auto_add=True)

    def _download_all_results(self):
        if not self._rendered_results:
            self._set_status("No search results to download.")
            return
        self._start_download_session(self._rendered_results, auto_add=False)

    def _select_all_results(self):
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            item.setSelected(True)

    def _clear_results_selection(self):
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            item.setSelected(False)

    def _select_all_local(self):
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            item.setSelected(True)

    def _clear_local_selection(self):
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            item.setSelected(False)

    def _download_selected(self):
        selected_results = self._selected_search_results()
        if not selected_results:
            self._set_status("No remote selection to download.")
            return
        self._start_download_session(selected_results, auto_add=False)

    def _build_library_identity_map(self) -> Dict[tuple, Path]:
        return build_library_identity_map(self.library_dir)

    def _safe_library_path(
        self,
        result: WadBrowserResult,
        reserved: Optional[set] = None,
        identity_map: Optional[Dict[tuple, Path]] = None,
    ) -> Path:
        return safe_library_path(result, self.library_dir, reserved, identity_map)

    def _metadata_path(self, destination: str) -> Path:
        return metadata_path(destination)

    def _write_metadata(self, destination: str, result: WadBrowserResult):
        write_metadata(destination, result)

    def _read_metadata(self, destination: str) -> Dict[str, Any]:
        return read_metadata(destination)

    def _collect_library_rows(self) -> List[Dict[str, Any]]:
        rows = collect_library_rows(self.library_dir)
        self._library_rows = rows
        return rows

    def _library_row_matches(self, row: Dict[str, Any], query: str) -> bool:
        return library_row_matches(row, query)

    def _render_library_rows(self, rows: List[Dict[str, Any]], keep_selected: Optional[Iterable[str]] = None):
        selected = set(str(path) for path in (keep_selected or []))
        self.localTree.clear()
        for row in rows:
            item = QTreeWidgetItem([
                row.get("name", ""),
                row.get("size_text", "—"),
                row.get("source", "local"),
                row.get("installed_text", "local"),
                row.get("source_path", "-"),
            ])
            file_path = row.get("path", "")
            item.setData(0, Qt.UserRole, file_path)
            item.setToolTip(0, str(file_path))
            details = _normalize_metadata_text(row.get("description", ""), row.get("metadata_text", ""))
            if details:
                item.setToolTip(2, details)
                item.setToolTip(4, details)
            if file_path in selected:
                item.setSelected(True)
            self.localTree.addTopLevelItem(item)
        self.browserTabs.setTabText(1, f"LIBRARY [{len(rows)}]")

    def _start_download_session(self, results: List[WadBrowserResult], auto_add: bool):
        if not results:
            return

        deduped: List[WadBrowserResult] = []
        seen = set()
        for result in results:
            key = _result_identity(result)
            if not key:
                key = result.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
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
            "failed_results": [],
            "by_url": by_url,
            "ordered_urls": ordered_urls,
            "path_by_url": {},
            "progress_by_url": {},
            "total_by_url": {},
        }
        self._failed_downloads = []
        self._refresh_retry_state()
        self._set_controls_enabled(False)
        self.searchProgressBar.setRange(0, 100)
        self.searchProgressBar.setValue(0)
        self.searchProgressBar.setFormat(f"DOWNLOADING 0/{len(deduped)}  %p%")
        self.searchProgressBar.show()
        self._set_status(f"Preparing {len(deduped)} download(s)...")

        reserved: set = set()
        identity_map = self._build_library_identity_map()
        for result in deduped:
            target = self._safe_library_path(result, reserved, identity_map)
            reserved.add(str(target).lower())
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
        session = self._download_sessions.get(token)
        if session is None:
            return
        session["progress_by_url"][url] = received
        session["total_by_url"][url] = total
        known_total = sum(value for value in session["total_by_url"].values() if value > 0)
        known_received = sum(
            min(session["progress_by_url"].get(key, 0), size)
            for key, size in session["total_by_url"].items()
            if size > 0
        )
        percent = int((known_received / known_total) * 100) if known_total else 0
        completed = len(session.get("paths", []))
        overall = len(session.get("ordered_urls", []))
        self.searchProgressBar.setValue(percent)
        self.searchProgressBar.setFormat(f"DOWNLOADING {completed}/{overall}  %p%")
        if total:
            self._set_status(
                f"Receiving {Path(urlparse(url).path).name or 'mod'} — "
                f"{_human_size(received)} / {_human_size(total)} ({received / total:.0%})"
            )

    def _finish_download_session(self, session_id: int):
        payload = self._download_sessions.pop(session_id, None)
        if payload is None:
            return

        self._refresh_controls_for_state()
        self._refresh_local_library()
        self.searchProgressBar.hide()

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
        self._update_failed_downloads(payload)
        self._refresh_retry_state()

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
                description="",
                metadata_text="",
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
        result = session.get("by_url", {}).get(source_url)
        if result is not None:
            session["failed_results"].append(result)
        if session["pending"] <= 0:
            self._finish_download_session(token)

    def _retry_failed_downloads(self):
        if self._is_busy():
            self._set_status("Cannot retry while a session is running.")
            return

        if not self._failed_downloads:
            self._set_status("No failed downloads to retry.")
            return

        pending = list(self._failed_downloads)
        self._start_download_session(pending, auto_add=self._failed_download_auto_add)

    def _open_selected_remote(self):
        selected = self._selected_search_results()
        if not selected:
            self._set_status("No search item selected to open.")
            return
        for entry in selected:
            if entry and entry.browser_url:
                # browser_url can come from on-disk caches/sidecars; only ever
                # open http(s) links, never file:/javascript: etc.
                if urlparse(entry.browser_url).scheme in {"http", "https"}:
                    webbrowser.open(entry.browser_url)
                else:
                    self._set_status(f"Refusing to open non-web URL: {entry.browser_url}")

    def _open_selected_local_file(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        self._open_paths(selected)

    def _delete_selected_local(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        response = QMessageBox.question(
            self,
            "Delete Mod Files",
            f"Delete {len(selected)} selected file(s)?\n\nThis action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            self._set_status("Delete cancelled.")
            return

        deleted = 0
        deleted_paths = []
        for file_path in selected:
            p = Path(file_path)
            try:
                if p.is_file():
                    p.unlink()
                    meta = self._metadata_path(str(p))
                    if meta.exists():
                        meta.unlink()
                    deleted += 1
                    deleted_paths.append(file_path)
            except OSError as exc:
                self._set_status(f"Could not delete {file_path}: {exc}")
        self._refresh_local_library()
        if deleted:
            self._set_status(f"Deleted {deleted} file(s).")
            self.removedRequested.emit(deleted_paths)

    def _refresh_local_library(self):
        selected = self._selected_local_files()
        rows = self._collect_library_rows()
        query = getattr(self, "localFilterInput", None)
        query_text = str(query.text()).strip() if query is not None else ""

        filtered = [row for row in rows if self._library_row_matches(row, query_text)]
        self._render_library_rows(filtered, keep_selected=selected)

        if query_text:
            self._set_status(
                f"Library: {len(filtered)} of {len(rows)} matched in {self.library_dir}"
            )
        else:
            self._set_status(f"Library: {len(rows)} managed files in {self.library_dir}")

    def refresh_local_library(self):
        """Public entry point to rescan the local library (alias of the
        private ``_refresh_local_library``, which stays for compatibility)."""
        self._refresh_local_library()

    def _open_library_dir(self):
        try:
            self.library_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_status(f"Cannot open library folder {self.library_dir}: {exc}")
            return
        webbrowser.open(self.library_dir.as_uri())

    def _open_selected_local_folder(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        opened = set()
        for file_path in selected:
            parent = Path(file_path).expanduser().parent.resolve()
            if parent in opened:
                continue
            webbrowser.open(parent.as_uri())
            opened.add(parent)

    def _open_paths(self, paths: List[str]):
        for file_path in paths:
            path = Path(file_path).expanduser()
            if not path.exists():
                self._set_status(f"File missing: {path}")
                continue
            webbrowser.open(path.resolve().as_uri())
