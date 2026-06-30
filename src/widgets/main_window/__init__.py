from __future__ import annotations

import shlex
import time
import sys
from pathlib import Path, PurePath
from typing import Optional

from PyQt5.Qt import Qt
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDesktopWidget,
    QErrorMessage,
    QGroupBox,
    QHBoxLayout,
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src import const
from src.launch_controller import LaunchOrchestrator, build_launch_args
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
        self.setMinimumSize(640, 480)
        self.resize(const.SCREEN_WIDTH, const.SCREEN_HEIGHT)
        self.center()
        self.setWindowTitle(const.MAIN_WINDOW_TITLE)

        self.centralWidget = QWidget()
        self.setupResponsiveLayout()
        self.setCentralWidget(self.centralWidget)
        self.errorDialog = QErrorMessage()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self.createMenu()
        self.addWidgets()

        theme_file = Path('assets/nc_theme.qss')
        if theme_file.exists():
            with open(theme_file, 'r') as fh:
                self.setStyleSheet(fh.read())

        self.show()

    def setupResponsiveLayout(self):
        self.mainLayout = QHBoxLayout(self.centralWidget)
        self.mainLayout.setSpacing(12)
        self.mainLayout.setContentsMargins(12, 12, 12, 12)

        self.leftPanel = QWidget()
        self.leftPanel.setMinimumWidth(320)
        self.leftPanel.setMaximumWidth(600)
        self.leftPanel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.leftLayout = QVBoxLayout(self.leftPanel)
        self.leftLayout.setSpacing(8)
        self.leftLayout.setContentsMargins(0, 0, 0, 0)

        self.rightPanel = QWidget()
        self.rightPanel.setMinimumWidth(200)
        self.rightPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rightLayout = QVBoxLayout(self.rightPanel)
        self.rightLayout.setSpacing(12)
        self.rightLayout.setContentsMargins(0, 0, 0, 0)

        self.mainLayout.addWidget(self.leftPanel, 2)
        self.mainLayout.addWidget(self.rightPanel, 1)

    def addWidgets(self):
        self.config = self.config.normalized()
        self.config.render_profile = self.config.render_profile

        self.sourcePortGroup = QGroupBox("Source Port")
        sourcePortLayout = QVBoxLayout(self.sourcePortGroup)

        self.sourcePortPathInput = PathInput()
        self.sourcePortPathInput.setToolTip('Path to gzdoom or zandronum')
        self.sourcePortPathInput.setText(self.config.source_port_path)
        self.sourcePortPathInput.installEventFilter(self)

        self.sourcePortBrowseButton = QPushButton('Browse...')
        self.sourcePortBrowseButton.setToolTip('Select a source port executable')
        self.sourcePortBrowseButton.clicked.connect(self.browseSourcePort)

        sourcePortInputLayout = QHBoxLayout()
        sourcePortInputLayout.addWidget(self.sourcePortPathInput, 1)
        sourcePortInputLayout.addWidget(self.sourcePortBrowseButton, 0)
        sourcePortLayout.addLayout(sourcePortInputLayout)

        self.iwadGroup = QGroupBox("IWAD (Main Game)")
        iwadLayout = QVBoxLayout(self.iwadGroup)

        self.iwadInput = IWadInput()
        self.iwadInput.setToolTip('Main IWAD file')
        self.iwadInput.setText(self.config.iwad_path)
        self.iwadInput.installEventFilter(self)

        self.iwadBrowseButton = QPushButton('Browse...')
        self.iwadBrowseButton.setToolTip('Select an IWAD file')
        self.iwadBrowseButton.clicked.connect(self.openIWadAction._open)

        iwadInputLayout = QHBoxLayout()
        iwadInputLayout.addWidget(self.iwadInput, 1)
        iwadInputLayout.addWidget(self.iwadBrowseButton, 0)
        iwadLayout.addLayout(iwadInputLayout)

        self.modPanel = ModPanel(self)
        self.modPanel.setMods(self.config.pwad_paths)
        self.modPanel.addRequested.connect(self.openPWadAction._open)
        self.modPanel.modsChanged.connect(self.saveConfig)
        self.modPanel.selectedPathsChanged.connect(self.updatePWadInfo)

        self.wadFinder = WadFinder(self, library_dir=self.config.pwad_dir or None)
        self.wadFinder.addRequested.connect(self._on_browser_add)
        self.wadFinder.statusChanged.connect(self.statusBar().showMessage)

        self.optionsGroup = QGroupBox("Extra Options")
        optionsLayout = QVBoxLayout(self.optionsGroup)

        self.extraOptionsInput = QLineEdit()
        self.extraOptionsInput.setToolTip('Additional command line arguments')
        self.extraOptionsInput.setText(self.config.extra_options)
        self.extraOptionsInput.installEventFilter(self)
        optionsLayout.addWidget(self.extraOptionsInput)

        self.launchButton = LaunchButton(text="*** UNLEASH HELL ***")
        self.launchButton.setMinimumHeight(40)
        self.launchButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.launchButton.clicked.connect(self._onLaunchRequested)

        self.lostSoulWidget = DoomSoulWidget(
            skull_gif_path="assets/lost_soul.gif",
            animated_background=self.config.animated_background,
        )
        self.lostSoulWidget.setMinimumSize(180, 180)
        self.lostSoulWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.logWindow = LogWindow(self)
        self.loadingWindow = LostSoulWindow(self)

        self.updatePWadInfo()

        self.installResponsiveLayout()
        self.set_render_profile(self.config.render_profile)
        self.applyWarningsOrErrors()

    def createMenu(self):
        self.openSourcePortAction = OpenSourcePortAction(
            self,
            self.setSourcePort,
            self.config,
            self.saveSourcePortPath,
            browse_handler=self.browseSourcePort,
        )
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
        fileMenu.addAction(self.openIWadAction)
        fileMenu.addAction(self.openPWadAction)
        self.openWadFinderAction = OpenWadFinder(self, self.wadFinder)
        self.openWadFinderAction.setChecked(True)
        fileMenu.addAction(self.openWadFinderAction)
        fileMenu.addAction(self.exitAction)

        configMenu = menuBar.addMenu('&Config')
        configMenu.addAction(self.animatedBgAction)
        configMenu.addAction(self.performanceModeAction)

        helpMenu = menuBar.addMenu('&Help')
        helpMenu.addAction(self.openWadRepository)

    def installResponsiveLayout(self):
        self.leftLayout.addWidget(self.sourcePortGroup)
        self.leftLayout.addWidget(self.iwadGroup)
        self.leftLayout.addWidget(self.modPanel, 1)
        self.leftLayout.addWidget(self.optionsGroup)
        self.leftLayout.addWidget(self.launchButton)

        self.rightLayout.addWidget(self.lostSoulWidget, 1)
        self.rightLayout.addWidget(self.wadFinder, 1)

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
        return super(MainWindow, self).eventFilter(source, event)

    def setSourcePort(self, sourcePort: str):
        self.sourcePortPathInput.setText(sourcePort)
        self.config.source_port_path = sourcePort
        self.runtime.config.source_port_path = sourcePort

    def setIWad(self, wad: str):
        self.iwadInput.setText(wad)
        self.config.iwad_path = wad
        self.runtime.config.iwad_path = wad

    def addPWads(self, wads: list):
        dialog = self.loadingWindow
        dialog.setRange(0, len(wads))
        dialog.setValue(0)
        dialog.show()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        duplicate_count = 0
        seen = set()
        try:
            for i, wad in enumerate(wads):
                dialog.setValue(i)
                if wad in seen or wad in self.modPanel.allPaths():
                    msg = f"The wad {wad} has already been added to the wad list."
                    self.errorDialog.showMessage(msg)
                    duplicate_count += 1
                QApplication.processEvents()
                seen.add(wad)
            dialog.setValue(len(wads))
        finally:
            QApplication.restoreOverrideCursor()
            dialog.hide()
        self.modPanel.addMods(wads)
        if duplicate_count and duplicate_count == len(wads):
            self.statusBar().showMessage("No new mods were added.", 2500)
        self.saveConfig()

    def removeSelectedPWads(self):
        self.modPanel.removeSelected()
        self.saveConfig()

    def _on_browser_add(self, paths: list):
        self.modPanel.addMods(paths)
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

    def saveSourcePortPath(self, filename: str):
        self.config.source_port_dir = str(PurePath(filename).parent)
        self.config.source_port_path = filename
        self.sourcePortPathInput.setText(filename)
        self.saveConfig()

    def browseSourcePort(self):
        start_dir = self.config.source_port_dir or str(Path.home())
        file_filter = (
            "Executable files (*.exe);;All files (*.*)"
            if sys.platform.startswith("win")
            else "All files (*)"
        )
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select a source port executable",
            start_dir,
            file_filter,
            options=options,
        )
        if filename:
            self.saveSourcePortPath(filename)

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
        self.runtime.config = self.config

    def _set_launch_busy(self, busy: bool):
        controls = [
            self.sourcePortPathInput,
            self.sourcePortBrowseButton,
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
        self.errorDialog.showMessage(message)
        self.statusBar().showMessage(message, 5000)
        self.loadingWindow.hide()
        self.launchButton.set_loading(False)
        self._set_launch_busy(False)

    def _on_launch_finished(self, exit_code, reason: Optional[str]):
        self.launchButton.set_loading(False)
        self._set_launch_busy(False)
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

        if width < 700:
            self.mainLayout.setSpacing(8)
            self.mainLayout.setContentsMargins(8, 8, 8, 8)
            self.leftLayout.setSpacing(6)
            self.rightLayout.setSpacing(8)
        elif width > 1200:
            self.mainLayout.setSpacing(20)
            self.mainLayout.setContentsMargins(20, 16, 20, 16)
            self.leftLayout.setSpacing(12)
            self.rightLayout.setSpacing(16)
        else:
            self.mainLayout.setSpacing(12)
            self.mainLayout.setContentsMargins(12, 12, 12, 12)
            self.leftLayout.setSpacing(8)
            self.rightLayout.setSpacing(12)

        if hasattr(self, 'lostSoulWidget'):
            if width < 700 or height < 500:
                self.lostSoulWidget.setMinimumSize(150, 150)
            else:
                self.lostSoulWidget.setMinimumSize(180, 180)

    def closeEvent(self, event):
        try:
            self.saveConfig()
        except Exception as exc:
            self.errorDialog.showMessage(f"Failed to save config: {exc}")

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
