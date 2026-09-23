from __future__ import annotations

import os
import shlex
import time
from pathlib import Path, PurePath
from typing import Optional

from PyQt5.Qt import Qt
from PyQt5.QtCore import QEvent, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDesktopWidget,
    QErrorMessage,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSplitter,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src import const
from src.const import asset_path
from src.iwad_detection import detect_iwad
from src.launch_controller import LaunchOrchestrator, build_launch_args, resolve_source_port
from src.runtime import ApplicationRuntime
from src.performance import perf_settings

from .actions.open_source_port_action import OpenSourcePortAction
from .actions.open_iwad_action import OpenIWadAction
from .actions.open_pwad_action import OpenPWadAction
from .actions.open_wad_repository import OpenWadRepository
from .actions.open_wad_finder import OpenWadFinder
from .actions.exit_action import ExitAction

from src.widgets.iwad_input import IWadInput
from src.widgets.path_input import PathInput
from src.widgets.launch_button import LaunchButton
from src.widgets.log_window import LogWindow
from src.widgets.lost_soul_window import LostSoulWindow
from src.widgets.doom_soul_widget import DoomSoulWidget
from src.widgets.mod_panel import ModPanel
from src.widgets.pwad_info import _ModInfoCache
from src.widgets.wad_finder import WadFinder


class MainWindow(QMainWindow):
    def __init__(self, runtime: Optional[ApplicationRuntime] = None):
        super().__init__()
        self.runtime = runtime or ApplicationRuntime()
        self.config = self.runtime.config.normalized()

        self.launchController = self.runtime.launch_orchestrator
        self.launchController.state_changed.connect(self._on_launch_state_changed)
        self.launchController.output_line.connect(self._on_launch_output)
        self.launchController.finished.connect(self._on_launch_finished)
        self.launchController.error.connect(self._on_launch_error)

        self._last_launch_status = {
            "start": None,
            "exit_code": None,
            "failure": None,
        }

        self.initUi()

    def initUi(self):
        available = QDesktopWidget().availableGeometry()
        target_width = min(available.width(), max(const.SCREEN_WIDTH, int(available.width() * 0.88)))
        target_height = min(available.height(), max(const.SCREEN_HEIGHT, int(available.height() * 0.88)))
        self.setMinimumSize(960, 680)
        self.resize(target_width, target_height)
        self.center()
        self.setWindowTitle(const.MAIN_WINDOW_TITLE)

        self.centralWidget = QWidget()
        self.setupResponsiveLayout()
        self.setCentralWidget(self.centralWidget)
        self.errorDialog = QErrorMessage()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")
        self._init_wad_finder()

        self.createMenu()
        self.addWidgets()

        theme_file = Path(asset_path('assets/nc_theme.qss'))
        if theme_file.exists():
            with open(theme_file, 'r') as fh:
                self.setStyleSheet(fh.read())

        # NOTE: no self.show() here; the CLI entry point shows the window
        # after construction (avoids a double show).

    def setupResponsiveLayout(self):
        self.mainLayout = QVBoxLayout(self.centralWidget)
        self.mainLayout.setSpacing(10)
        self.mainLayout.setContentsMargins(12, 12, 12, 12)

        self.readinessLabel = QLabel("SYSTEM CHECK PENDING...")
        self.readinessLabel.setObjectName("readinessStrip")
        self.readinessLabel.setAlignment(Qt.AlignCenter)
        self.readinessLabel.setWordWrap(True)

        self.topConfigPanel = QWidget()
        self.topConfigPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.topConfigLayout = QHBoxLayout(self.topConfigPanel)
        self.topConfigLayout.setSpacing(12)
        self.topConfigLayout.setContentsMargins(0, 0, 0, 0)

        self.mainSplitter = QSplitter(Qt.Horizontal)
        self.mainSplitter.setChildrenCollapsible(False)

        self.leftPanel = QWidget()
        self.leftPanel.setMinimumWidth(320)
        self.leftPanel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.leftLayout = QVBoxLayout(self.leftPanel)
        self.leftLayout.setSpacing(8)
        self.leftLayout.setContentsMargins(0, 0, 0, 0)

        self.leftScroll = QScrollArea()
        self.leftScroll.setMinimumWidth(320)
        self.leftScroll.setWidgetResizable(True)
        self.leftScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.leftScroll.setWidget(self.leftPanel)

        self.leftColumn = QWidget()
        self.leftColumn.setMinimumWidth(320)
        self.leftColumnLayout = QVBoxLayout(self.leftColumn)
        self.leftColumnLayout.setSpacing(8)
        self.leftColumnLayout.setContentsMargins(0, 0, 0, 0)
        self.leftColumnLayout.addWidget(self.leftScroll, 1)

        self.rightPanel = QWidget()
        self.rightPanel.setMinimumWidth(320)
        self.rightPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rightLayout = QVBoxLayout(self.rightPanel)
        self.rightLayout.setSpacing(12)
        self.rightLayout.setContentsMargins(0, 0, 0, 0)

        self.mainSplitter.addWidget(self.leftColumn)
        self.mainSplitter.addWidget(self.rightPanel)
        self.mainSplitter.setStretchFactor(0, 2)
        self.mainSplitter.setStretchFactor(1, 3)
        self.mainLayout.addWidget(self.readinessLabel)
        self.mainLayout.addWidget(self.topConfigPanel)
        self.mainLayout.addWidget(self.mainSplitter, 1)

    def _init_wad_finder(self):
        self.wadFinder = WadFinder(
            self,
            library_dir=self.config.pwad_dir or None,
            source_state=[
                source.to_dict()
                if hasattr(source, "to_dict")
                else source
                for source in self.config.browser_sources
            ],
            source_state_changed=self._on_browser_sources_changed,
            library_dir_changed=self._on_browser_library_dir_changed,
        )
        self.wadFinder.addRequested.connect(self._on_browser_add)
        self.wadFinder.removedRequested.connect(self._on_browser_removed)
        self.wadFinder.statusChanged.connect(self.statusBar().showMessage)
        self.wadFinder.browserModeRequested.connect(self._set_browser_expanded)

    def addWidgets(self):
        self.config = self.config.normalized()

        # Readiness re-checks stat the filesystem (resolve_source_port +
        # is_file per mod); debounce so typing doesn't stall the UI thread.
        # Created before any widgets connect to it.
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setSingleShot(True)
        self._readiness_timer.setInterval(250)
        self._readiness_timer.timeout.connect(self._update_readiness)

        self.sourcePortGroup = QGroupBox("Engine / source port")
        sourcePortLayout = QVBoxLayout(self.sourcePortGroup)

        self.sourcePortPathInput = PathInput()
        self.sourcePortPathInput.setToolTip('Path to source port executable')
        self.sourcePortPathInput.setPlaceholderText('Double-click or click Browse to choose')
        self.sourcePortPathInput.setText(self.config.source_port_path)
        self.sourcePortPathInput.setCursorPosition(0)
        self.sourcePortPathInput.installEventFilter(self)
        self.sourcePortPathInput.textChanged.connect(self._readiness_timer.start)

        self.sourcePortBrowseButton = QPushButton('Browse...')
        self.sourcePortBrowseButton.setToolTip('Open a file browser to select the source port executable')
        self.sourcePortBrowseButton.clicked.connect(self._browse_source_port)

        self.sourcePortBrowseFolderButton = QPushButton('Browse Folder...')
        self.sourcePortBrowseFolderButton.setToolTip('Open a folder browser to find a source port executable')
        self.sourcePortBrowseFolderButton.clicked.connect(self._browse_source_port_directory)

        self.sourcePortFolderButton = QPushButton('Open folder')
        self.sourcePortFolderButton.setToolTip('Open the source port directory')
        self.sourcePortFolderButton.clicked.connect(self._reveal_source_port_folder)

        sourcePortLayout.addWidget(self.sourcePortPathInput)

        sourcePortActionsLayout = QHBoxLayout()
        sourcePortActionsLayout.setSpacing(8)
        sourcePortActionsLayout.addWidget(self.sourcePortBrowseButton)
        sourcePortActionsLayout.addWidget(self.sourcePortBrowseFolderButton)
        sourcePortActionsLayout.addWidget(self.sourcePortFolderButton)
        sourcePortActionsLayout.addStretch(1)
        sourcePortLayout.addLayout(sourcePortActionsLayout)

        self.iwadGroup = QGroupBox("Base game (IWAD)")
        iwadLayout = QVBoxLayout(self.iwadGroup)

        self.iwadInput = IWadInput()
        self.iwadInput.setToolTip('Main IWAD file')
        self.iwadInput.setText(self.config.iwad_path)
        self.iwadInput.setCursorPosition(0)
        self.iwadInput.installEventFilter(self)
        self.iwadInput.textChanged.connect(self._readiness_timer.start)

        self.iwadBrowseButton = QPushButton('Browse...')
        self.iwadBrowseButton.setToolTip('Select an IWAD file')
        self.iwadBrowseButton.clicked.connect(self.openIWadAction._open)

        iwadInputLayout = QHBoxLayout()
        iwadInputLayout.addWidget(self.iwadInput, 1)
        iwadInputLayout.addWidget(self.iwadBrowseButton, 0)
        iwadLayout.addLayout(iwadInputLayout)
        # Keep the input row top-aligned with the engine group's input row;
        # without a stretch the single row floats to the vertical center.
        iwadLayout.addStretch(1)

        self.modPanel = ModPanel(self)
        self.modPanel.setMods(self.config.pwad_paths)
        self.modPanel.addRequested.connect(self.openPWadAction._open)
        self.modPanel.browseRequested.connect(self._focus_mod_browser)
        # Debounce config saves: drag-reorders/deletes emit modsChanged rapidly.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.saveConfig)
        self.modPanel.modsChanged.connect(self._save_timer.start)

        self.optionsGroup = QGroupBox("Launch options")
        optionsLayout = QVBoxLayout(self.optionsGroup)

        self.extraOptionsInput = QLineEdit()
        self.extraOptionsInput.setToolTip('Additional command line arguments')
        self.extraOptionsInput.setPlaceholderText('+map e1m1  -skill 4  -nomonsters  ...')
        self.extraOptionsInput.setText(self.config.extra_options)
        self.extraOptionsInput.installEventFilter(self)
        optionsLayout.addWidget(self.extraOptionsInput)

        self.launchButton = LaunchButton(text="*** UNLEASH HELL ***")
        self.launchButton.setMinimumHeight(40)
        self.launchButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.launchButton.clicked.connect(self._onLaunchRequested)

        self.lostSoulWidget = DoomSoulWidget(
            skull_gif_path=asset_path("assets/lost_soul.gif"),
            animated_background=self.config.animated_background,
        )
        # Compact inline skull: sits at the left of the readiness row instead
        # of occupying a full-width strip of its own.
        self.lostSoulWidget.setFixedSize(132, 64)
        self.lostSoulWidget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.readinessRow = QWidget()
        readinessRowLayout = QHBoxLayout(self.readinessRow)
        readinessRowLayout.setContentsMargins(0, 0, 0, 0)
        readinessRowLayout.setSpacing(10)
        readinessRowLayout.addWidget(self.lostSoulWidget, 0)
        readinessRowLayout.addWidget(self.readinessLabel, 1)
        self.mainLayout.insertWidget(0, self.readinessRow)

        self.logWindow = LogWindow(self)
        self.loadingWindow = LostSoulWindow(self)

        self.updatePWadInfo()
        # Deferred: IWAD auto-detection iterdirs many roots and walks Steam
        # libraries; keep it off the pre-paint startup path.
        QTimer.singleShot(0, lambda: self._maybe_auto_detect_iwad(show_status=False))

        self.installResponsiveLayout()
        self.set_render_profile(self.config.render_profile)
        perf_settings.apply_config(self.config.performance)
        _ModInfoCache.configure(self.config)
        self.applyWarningsOrErrors()
        self._update_readiness()

        # The online mod browser starts collapsed: the launch flow owns the
        # window. Ctrl+B (menu) or "Find online" opens it; the splitter
        # reclaims the space automatically.
        self.openWadFinderAction.toggled.connect(self._on_browser_toggled)
        self.openWadFinderAction.setChecked(False)
        self.rightPanel.hide()

        # Global launch shortcut (Enter in any path field also launches).
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._onLaunchRequested)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._onLaunchRequested)

    def createMenu(self):
        self.openSourcePortAction = OpenSourcePortAction(
            self,
            self.setSourcePort,
            self.config,
            self.saveSourcePortPath,
        )
        self.openSourcePortFolderAction = QAction("Open Source Port Folder", self)
        self.openSourcePortFolderAction.setStatusTip("Open the selected source port directory")
        self.openSourcePortFolderAction.triggered.connect(self._reveal_source_port_folder)
        self.openIWadAction = OpenIWadAction(
            self, self.setIWad, self.config, self.saveWadPath
        )
        self.openPWadAction = OpenPWadAction(
            self, self.addPWads, self.config, self.saveWadPath
        )
        self.openWadRepository = OpenWadRepository(self)
        self.exitAction = ExitAction(self)

        self.animatedBgAction = QAction('&Animated Background', self)
        self.animatedBgAction.setCheckable(True)
        self.animatedBgAction.setChecked(self.config.animated_background)
        self.animatedBgAction.triggered.connect(self.toggleAnimatedBackground)

        self.performanceModeAction = QAction('&Performance Mode', self)
        self.performanceModeAction.setCheckable(True)
        self.performanceModeAction.setChecked(self.config.performance_mode)
        self.performanceModeAction.triggered.connect(self.togglePerformanceMode)

        menuBar = self.menuBar()
        fileMenu = menuBar.addMenu('&File')
        fileMenu.addAction(self.openSourcePortAction)
        fileMenu.addAction(self.openSourcePortFolderAction)
        fileMenu.addAction(self.openIWadAction)
        fileMenu.addAction(self.openPWadAction)
        self.openWadFinderAction = OpenWadFinder(self, self.wadFinder)
        fileMenu.addAction(self.openWadFinderAction)
        fileMenu.addAction(self.exitAction)

        configMenu = menuBar.addMenu('&Config')
        configMenu.addAction(self.animatedBgAction)
        configMenu.addAction(self.performanceModeAction)

        helpMenu = menuBar.addMenu('&Help')
        helpMenu.addAction(self.openWadRepository)

    def installResponsiveLayout(self):
        self.topConfigLayout.addWidget(self.sourcePortGroup, 3)
        self.topConfigLayout.addWidget(self.iwadGroup, 2)
        self.leftLayout.addWidget(self.modPanel, 1)
        self.leftLayout.addStretch(0)
        self.leftColumnLayout.addWidget(self.optionsGroup)
        self.leftColumnLayout.addWidget(self.launchButton)

        self.rightLayout.addWidget(self.wadFinder, 1)
        self.mainSplitter.setSizes([360, max(520, self.width() - 360)])

    def eventFilter(self, source, event):
        if (
            event.type() == QEvent.KeyPress
            and source in {
                self.sourcePortPathInput,
                self.iwadInput,
                self.extraOptionsInput,
            }
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            self._onLaunchRequested()
            return True
        if (
            event.type() == QEvent.MouseButtonDblClick
            and source is self.sourcePortPathInput
        ):
            self._browse_source_port()
            return True
        return super(MainWindow, self).eventFilter(source, event)

    def _browse_source_port(self):
        action = getattr(self, "openSourcePortAction", None)
        if action is not None and hasattr(action, "open"):
            action.open()
            return

        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        start_dir = self.config.source_port_dir or str(Path.home())
        candidate = Path(start_dir)
        if not candidate.exists():
            candidate = Path.home()
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select a source port",
            str(candidate),
            "Source port executables (*.exe *.bat *.cmd *.com *.app);;Executable files (*);;All files (*)",
            options=options,
        )
        if not selected:
            return
        resolved, failure = resolve_source_port(Path(selected).expanduser(), discover=False)
        if not resolved:
            if failure:
                self.errorDialog.showMessage(failure)
            else:
                self.errorDialog.showMessage("The selected source port path is invalid.")
            return
        self.saveSourcePortPath(resolved)
        self.setSourcePort(resolved)

    def _browse_source_port_directory(self):
        action = getattr(self, "openSourcePortAction", None)
        if action is not None and hasattr(action, "browse_source_port_directory"):
            selected = action.browse_source_port_directory(self)
            if selected is not None:
                self.saveSourcePortPath(selected)
                self.setSourcePort(selected)
            return

        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        start_dir = self.config.source_port_dir or str(Path.home())
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "Select source port folder",
            str(start_dir),
            options=options,
        )
        if not selected_dir:
            return
        try:
            source_files = sorted(
                (entry for entry in Path(selected_dir).iterdir()),
                key=lambda path: path.name.lower(),
            ) if Path(selected_dir).exists() else []
        except OSError:
            self.statusBar().showMessage("Could not read selected folder.", 2500)
            return
        if not source_files:
            self.statusBar().showMessage("No files found in selected folder.", 2500)
            return
        executable = None
        for file_path in source_files:
            if file_path.is_file() and os.access(str(file_path), os.X_OK):
                executable = file_path
                break
        if executable is None:
            self.statusBar().showMessage("No executable was found in selected folder.", 2500)
            return
        self.saveSourcePortPath(str(executable))
        self.setSourcePort(str(executable))

    def setSourcePort(self, sourcePort: str):
        self.sourcePortPathInput.setText(sourcePort)
        self.sourcePortPathInput.setCursorPosition(0)
        self.config.source_port_path = sourcePort
        self.runtime.config.source_port_path = sourcePort
        self._maybe_auto_detect_iwad(show_status=True)

    def setIWad(self, wad: str):
        self.iwadInput.setText(wad)
        self.iwadInput.setCursorPosition(0)
        self.config.iwad_path = wad
        self.runtime.config.iwad_path = wad
        if wad:
            self.config.iwad_dir = str(Path(wad).expanduser().parent)
            self.runtime.config.iwad_dir = self.config.iwad_dir


    def addPWads(self, wads: list):
        # Filter duplicates up front; the old loop only produced one modal
        # popup per duplicate and called QApplication.processEvents(), which
        # allowed re-entrant launches/saves while the list was half-processed.
        existing = set(self.modPanel.allPaths())
        new_wads = [wad for wad in dict.fromkeys(wads) if wad not in existing]
        skipped = len(wads) - len(new_wads)

        if new_wads:
            self.modPanel.addMods(new_wads)
        if skipped and not new_wads:
            self.statusBar().showMessage("No new mods were added.", 2500)
        elif skipped:
            self.statusBar().showMessage(
                f"Added {len(new_wads)} mod(s); skipped {skipped} duplicate(s).", 4000
            )
        elif new_wads:
            self.statusBar().showMessage(f"Added {len(new_wads)} mod(s).", 2500)
        self.saveConfig()

    def removeSelectedPWads(self):
        self.modPanel.removeSelected()
        self.saveConfig()

    def _on_browser_add(self, paths: list):
        self.modPanel.addMods(paths)
        self.saveConfig()
        self.modPanel.refresh_statuses()
        self._update_readiness()

    def _focus_mod_browser(self):
        self.rightPanel.setVisible(True)
        self.wadFinder.show()
        self.wadFinder.browserTabs.setCurrentIndex(0)
        self.openWadFinderAction.setChecked(True)
        self.wadFinder.searchInput.setFocus()
        self.wadFinder.searchInput.selectAll()
        self.statusBar().showMessage("Search the network, inspect a result, then choose DOWNLOAD + QUEUE.", 5000)

    def _on_browser_toggled(self, checked: bool):
        # The browser lives in rightPanel alongside the skull strip; when it
        # is closed the whole panel collapses so the launch flow gets the
        # full window.
        checked = bool(checked)
        if not checked and self.wadFinder.expandBrowserButton.isChecked():
            # Closing a full-window (expanded) browser: restore the launch
            # layout first, otherwise both panels end up hidden and the
            # window is left empty.
            self.wadFinder.expandBrowserButton.setChecked(False)
        self.rightPanel.setVisible(checked)

    def _set_browser_expanded(self, expanded: bool):
        expanded = bool(expanded)
        self.topConfigPanel.setVisible(not expanded)
        self.readinessLabel.setVisible(not expanded)
        self.leftColumn.setVisible(not expanded)
        if expanded:
            self.wadFinder.setCompactMode(False)
            self.mainSplitter.setSizes([0, max(900, self.width())])
            self.statusBar().showMessage(
                "Browser expanded — select a result, inspect its metadata, then DOWNLOAD + QUEUE.",
                5000,
            )
            return

        compact = self.height() < 760
        self.lostSoulWidget.setPlaybackPaused(compact)
        self.lostSoulWidget.setVisible(not compact)
        self.wadFinder.setCompactMode(compact)
        self.mainSplitter.setSizes([360, max(520, self.width() - 360)])
        self.statusBar().showMessage("Returned to launch setup.", 3000)

    def _on_browser_removed(self, paths: list[str]):
        if not paths:
            return

        managed = set(self.modPanel.allPaths())
        removed = [path for path in paths if path in managed]
        if not removed:
            return
        self.modPanel.removePaths(removed)
        self.saveConfig()

    def center(self):
        qr = self.frameGeometry()
        cp = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

    def saveWadPath(self, filename: str, isIWad: bool):
        if isIWad:
            self.config.iwad_dir = str(PurePath(filename).parent)
            self.config.iwad_path = filename
            self.iwadInput.setText(filename)
        else:
            if filename:
                self.config.pwad_dir = str(PurePath(filename[0]).parent)
                if hasattr(self, "wadFinder"):
                    self.wadFinder.library_dir = Path(self.config.pwad_dir).expanduser()
                    self.wadFinder._refresh_local_library()
        self.saveConfig()

    def _on_browser_sources_changed(self, payload: list[dict]):
        try:
            self.config.set(
                "browser_sources",
                [
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "base": item.get("base"),
                        "index": item.get("index", "fullsort.gz"),
                        "browser": item.get("browser", item.get("base", "")),
                        "parser": item.get("parser", "fullsort"),
                        "enabled": bool(item.get("enabled", True)),
                        "status": item.get("status", "unknown"),
                        "status_message": item.get("status_message", ""),
                        "status_checked_at": item.get("status_checked_at", 0.0),
                    }
                    for item in payload
                    if isinstance(item, dict)
                ],
            )
            self.runtime.config.browser_sources = self.config.browser_sources
            # WadFinder emits its normalized source state during construction,
            # before the remaining form controls exist.
            if hasattr(self, "sourcePortPathInput"):
                self.saveConfig()
        except Exception:
            self.statusBar().showMessage("Failed to sync browser source settings.", 3000)

    def _on_browser_library_dir_changed(self, path: str):
        normalized = str(Path(path).expanduser())
        self.config.pwad_dir = normalized
        self.runtime.config.pwad_dir = normalized
        try:
            self.saveConfig()
        except Exception:
            self.statusBar().showMessage("Failed to save browser library directory.", 2500)

    def saveSourcePortPath(self, filename: str):
        selected = Path(filename).expanduser().resolve()
        self.config.source_port_dir = str(selected.parent)
        self.config.source_port_path = str(selected)
        self.sourcePortPathInput.setText(str(selected))
        self.sourcePortPathInput.setCursorPosition(0)
        self.runtime.config.source_port_path = str(selected)
        self.runtime.config.source_port_dir = str(selected.parent)
        self.saveConfig()

    def _reveal_source_port_folder(self):
        raw_path = self.sourcePortPathInput.text().strip()
        selected = Path(raw_path).expanduser() if raw_path else None

        if selected and selected.is_file():
            selected = selected.parent
        elif selected and selected.is_dir():
            selected = selected
        elif self.config.source_port_dir:
            candidate = Path(self.config.source_port_dir).expanduser()
            selected = candidate if candidate.exists() else Path.home()
        else:
            selected = Path.home()

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected)))

    def getConfig(self):
        return self.config.to_dict()

    def saveConfig(self):
        self._synchronize_from_ui()
        try:
            self.runtime.config = self.config.normalized()
            self.runtime.save_config()
            self.statusBar().showMessage("Configuration saved", 2500)
        except Exception as exc:
            self.statusBar().showMessage(f"Failed to save config: {exc}", 4000)
            self.errorDialog.showMessage(f"Failed to save configuration: {exc}")

    def _synchronize_from_ui(self):
        self.config.source_port_path = self.sourcePortPathInput.text().strip() or "gzdoom"
        self.config.iwad_path = self.iwadInput.text().strip()
        self.config.extra_options = self.extraOptionsInput.text().strip()
        self.config.pwad_paths = self.modPanel.allPaths()
        self.config.animated_background = self.animatedBgAction.isChecked()
        self.config.performance_mode = self.performanceModeAction.isChecked()
        self.config.render_profile = "low" if self.config.performance_mode else "high"
        perf_settings.apply_profile(self.config.render_profile)
        self._maybe_auto_detect_iwad(show_status=False)
        self.runtime.config = self.config
        self._update_readiness()

    def _update_readiness(self):
        if not hasattr(self, "readinessLabel") or not hasattr(self, "sourcePortPathInput"):
            return
        source_text = self.sourcePortPathInput.text().strip()
        source, _ = resolve_source_port(source_text, discover=False) if source_text else (None, None)
        iwad_text = self.iwadInput.text().strip() if hasattr(self, "iwadInput") else ""
        iwad_ready = bool(iwad_text and Path(iwad_text).expanduser().is_file())
        mod_paths = self.modPanel.allPaths() if hasattr(self, "modPanel") else []
        missing_mods = sum(1 for path in mod_paths if not Path(path).expanduser().is_file())

        engine = "OK" if source else "NEEDS SETUP"
        base = "OK" if iwad_ready else "NEEDS IWAD"
        mods = f"{len(mod_paths)} QUEUED"
        if missing_mods:
            mods += f" / {missing_mods} MISSING"
        ready = bool(source and iwad_ready and not missing_mods)
        prompt = "READY TO UNLEASH" if ready else "COMPLETE RED ITEMS TO LAUNCH"
        self.readinessLabel.setText(
            f"ENGINE [{engine}]   //   BASE GAME [{base}]   //   MODS [{mods}]   //   {prompt}"
        )
        self.readinessLabel.setProperty("ready", ready)
        self.readinessLabel.style().unpolish(self.readinessLabel)
        self.readinessLabel.style().polish(self.readinessLabel)

    def _maybe_auto_detect_iwad(self, *, show_status: bool) -> bool:
        current_iwad = self.iwadInput.text().strip() if hasattr(self, "iwadInput") else self.config.iwad_path
        if current_iwad and Path(current_iwad).expanduser().is_file():
            return False

        detected = detect_iwad(
            source_port_path=self.sourcePortPathInput.text().strip() if hasattr(self, "sourcePortPathInput") else self.config.source_port_path,
            iwad_path=current_iwad,
            iwad_dir=self.config.iwad_dir,
            extra_dirs=[self.config.source_port_dir, self.config.pwad_dir],
        )
        if not detected:
            return False

        self.iwadInput.setText(detected.path)
        self.iwadInput.setCursorPosition(0)
        self.config.iwad_path = detected.path
        self.config.iwad_dir = str(Path(detected.path).expanduser().parent)
        self.runtime.config.iwad_path = detected.path
        self.runtime.config.iwad_dir = self.config.iwad_dir
        if show_status:
            self.statusBar().showMessage(f"Auto-detected IWAD: {detected.game}", 3500)
        return True

    def _set_launch_busy(self, busy: bool):
        controls = [
            self.sourcePortPathInput,
            self.sourcePortBrowseButton,
            self.sourcePortBrowseFolderButton,
            self.sourcePortFolderButton,
            self.openSourcePortFolderAction,
            self.iwadInput,
            self.iwadBrowseButton,
            self.modPanel,
            self.wadFinder,
            self.extraOptionsInput,
            self.openSourcePortAction,
            self.openIWadAction,
            self.openPWadAction,
            self.animatedBgAction,
            self.performanceModeAction,
            self.launchButton,
        ]
        for widget in controls:
            widget.setEnabled(not busy)

    def _onLaunchRequested(self):
        self._synchronize_from_ui()
        self.saveConfig()

        cfg = self.config.normalized()
        preview = self._launch_preview(cfg)
        self.statusBar().showMessage(preview, 4000)
        self.logWindow.clear()
        self.logWindow.show()

        launched = self.launchController.start_launch(cfg)
        if not launched.started:
            self._set_launch_busy(False)
            self.statusBar().showMessage("Launch blocked: fix config errors and try again", 3000)
            return

    def _launch_preview(self, cfg) -> str:
        args = build_launch_args(cfg)
        quoted = " ".join(shlex.quote(arg) for arg in args)
        return f"Launch: {cfg.source_port_path} {quoted}".strip()

    def _on_launch_state_changed(self, state: str):
        if state == LaunchOrchestrator.STATE_VALIDATING:
            self.statusBar().showMessage("Validating launch configuration...")
        elif state == LaunchOrchestrator.STATE_LAUNCHING:
            self._set_launch_busy(True)
            self._last_launch_status["start"] = time.time()
            self.statusBar().showMessage("Launching...")
            self.loadingWindow.setRange(0, 0)
            self.loadingWindow.setValue(0)
            self.loadingWindow.show()
            self.logWindow.clear()
            self.logWindow.show()
            self.launchButton.set_loading(True)
        elif state == LaunchOrchestrator.STATE_RUNNING:
            self.statusBar().showMessage("BFG.py running")
            self.loadingWindow.hide()
            self.loadingWindow.setValue(0)
        elif state == LaunchOrchestrator.STATE_CANCELING:
            self.statusBar().showMessage("Launch stop requested")
            self._set_launch_busy(True)
            self.logWindow.append("Stop requested...")
        elif state in (LaunchOrchestrator.STATE_FAILED, LaunchOrchestrator.STATE_FINISHED):
            self.launchButton.set_loading(False)
            self._set_launch_busy(False)
            self.loadingWindow.hide()
            if state == LaunchOrchestrator.STATE_FAILED:
                self.statusBar().showMessage("Launch failed", 3000)
            else:
                self.statusBar().showMessage("Launch finished", 3000)
        elif state == LaunchOrchestrator.STATE_IDLE:
            self._set_launch_busy(False)
            self.launchButton.set_loading(False)

    def _on_launch_output(self, line: str):
        self.logWindow.append(line)

    def _on_launch_error(self, message: str):
        self.logWindow.append(f"ERROR: {message}")
        self.logWindow.flush()
        self.errorDialog.showMessage(message)
        self.statusBar().showMessage(message, 5000)
        self.loadingWindow.hide()
        self.launchButton.set_loading(False)
        self._set_launch_busy(False)

    def _on_launch_finished(self, exit_code, reason: Optional[str]):
        self.launchButton.set_loading(False)
        self._set_launch_busy(False)
        self.logWindow.flush()
        self.loadingWindow.hide()

        self._last_launch_status["exit_code"] = exit_code
        self._last_launch_status["failure"] = reason

        self.logWindow.append(f"Process finished with code {exit_code}")
        if reason:
            self.logWindow.append(f"Reason: {reason}")

        elapsed = 0.0
        if self._last_launch_status["start"]:
            elapsed = time.time() - self._last_launch_status["start"]
        status = f"Launcher process exited with code {exit_code}"
        if reason:
            status = f"Launcher exited ({reason})"
        if elapsed:
            status = f"{status} in {elapsed:.1f}s"

        self.statusBar().showMessage(status, 5000)
        self.saveConfig()

        if exit_code is None:
            return
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Launch finished",
                f"Source port exited with non-zero code {exit_code}.\nReason: {reason or 'unknown'}",
            )

    def applyWarningsOrErrors(self):
        validation = self.config.validate()
        if validation.errors:
            self.errorDialog.showMessage("\n".join(validation.errors))
        if validation.warnings:
            self.statusBar().showMessage("Warnings: " + "; ".join(validation.warnings), 6000)

    def resizeEvent(self, event):
        super().resizeEvent(event)

        width = event.size().width()
        height = event.size().height()

        # Drag-resizing fires dozens of resizeEvents; each setSpacing/
        # setContentsMargins call triggers a full-window relayout. Only
        # re-apply when the layout bucket actually changes.
        bucket = 0 if width < 700 else (2 if width > 1200 else 1)
        compact = height < 760
        resize_key = (bucket, compact, width < 700 or height < 500)
        if resize_key == getattr(self, "_last_resize_key", None):
            return
        self._last_resize_key = resize_key

        if bucket == 0:
            self.mainLayout.setSpacing(6)
            self.mainLayout.setContentsMargins(8, 6, 8, 6)
            self.leftLayout.setSpacing(5)
            self.rightLayout.setSpacing(6)
        elif bucket == 2:
            self.mainLayout.setSpacing(12)
            self.mainLayout.setContentsMargins(14, 12, 14, 12)
            self.leftLayout.setSpacing(8)
            self.rightLayout.setSpacing(10)
        else:
            self.mainLayout.setSpacing(8)
            self.mainLayout.setContentsMargins(10, 8, 10, 8)
            self.leftLayout.setSpacing(6)
            self.rightLayout.setSpacing(8)

        if hasattr(self, 'lostSoulWidget'):
            browser_expanded = (
                hasattr(self, "wadFinder")
                and self.wadFinder.expandBrowserButton.isChecked()
            )
            if browser_expanded:
                self.wadFinder.setCompactMode(False)
                return
            self.lostSoulWidget.setPlaybackPaused(compact)
            self.lostSoulWidget.setVisible(not compact)
            if hasattr(self, "wadFinder"):
                self.wadFinder.setCompactMode(compact)

    def changeEvent(self, event):
        # Re-stat READY/MISSING when the window regains focus so mods
        # downloaded into place (or deleted externally) are reflected.
        if event.type() == QEvent.WindowActivate:
            self.modPanel.refresh_statuses()
        super().changeEvent(event)

    def closeEvent(self, event):
        try:
            self.saveConfig()
        except Exception as exc:
            self.errorDialog.showMessage(f"Failed to save config: {exc}")

        # Persist any mod-metadata cache entries written since the last
        # debounced flush.
        _ModInfoCache.flush_now()

        if self.launchController.is_running():
            self.launchController.stop_launch()
        super().closeEvent(event)

    def updatePWadInfo(self, paths=None):
        if paths is None:
            paths = self.modPanel.selectedPaths()
        self.modPanel.pwadInfo.showInfo(paths)

    def toggleAnimatedBackground(self):
        enabled = self.animatedBgAction.isChecked()
        self.config.animated_background = enabled
        self.lostSoulWidget.setAnimatedBackground(enabled)
        self.runtime.config.animated_background = enabled
        self.saveConfig()

    def togglePerformanceMode(self):
        enabled = self.performanceModeAction.isChecked()
        self.config.performance_mode = enabled
        self.config.render_profile = "low" if enabled else "high"
        self.set_render_profile(self.config.render_profile)
        self.saveConfig()

    def set_render_profile(self, profile: str):
        self.config.render_profile = "low" if profile == "low" else "high"
        perf_settings.apply_profile(self.config.render_profile)
