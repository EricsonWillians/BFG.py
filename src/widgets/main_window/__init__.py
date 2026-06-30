import sys
from pathlib import Path, PurePath

from PyQt5.Qt import Qt
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDesktopWidget,
    QErrorMessage,
    QGroupBox,
    QHBoxLayout,
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
from src.config import ConfigStore
from src.launch_controller import LaunchController

try:
    from src.performance import perf_settings
except ImportError:
    # Fallback if performance module has issues
    class FallbackPerfSettings:
        def get(self, key, default=None):
            return default

        def set(self, key, value):
            pass
    perf_settings = FallbackPerfSettings()

from .actions.open_source_port_action import OpenSourcePortAction
from .actions.open_iwad_action import OpenIWadAction
from .actions.open_pwad_action import OpenPWadAction
from .actions.open_wad_repository import OpenWadRepository
from .actions.exit_action import ExitAction

from src.widgets.iwad_input import IWadInput
from src.widgets.pwad_list import PWadList
from src.widgets.path_input import PathInput
from src.widgets.launch_button import LaunchButton
from src.widgets.log_window import LogWindow
from src.widgets.pwad_info import PWadInfo
from src.widgets.lost_soul_window import LostSoulWindow
from src.widgets.doom_soul_widget import DoomSoulWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.configStore = ConfigStore("config.json")
        self.config = self.configStore.load().normalized()
        self.launchController = LaunchController(self)
        self.launchController.state_changed.connect(self._on_launch_state_changed)
        self.launchController.output.connect(self._on_launch_output)
        self.launchController.finished.connect(self._on_launch_finished)
        self.launchController.error.connect(self._on_launch_error)

        self.initUi()

    def initUi(self):
        # Set minimum and preferred sizes for better scaling
        self.setMinimumSize(640, 480)
        self.resize(const.SCREEN_WIDTH, const.SCREEN_HEIGHT)
        self.center()
        self.setWindowTitle(const.MAIN_WINDOW_TITLE)

        # Create central widget with responsive layout
        self.centralWidget = QWidget()
        self.setupResponsiveLayout()
        self.setCentralWidget(self.centralWidget)
        self.errorDialog = QErrorMessage()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self.createMenu()
        self.addWidgets()

        # Load Norton Commander inspired theme
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
        self.config.render_profile = "low" if self.config.performance_mode else self.config.render_profile

        # Source Port section
        self.sourcePortGroup = QGroupBox("Source Port")
        sourcePortLayout = QVBoxLayout(self.sourcePortGroup)

        self.sourcePortPathInput = PathInput()
        self.sourcePortPathInput.setToolTip('Path to gzdoom or zandronum')
        self.sourcePortPathInput.setText(self.config.source_port_path)
        self.sourcePortPathInput.installEventFilter(self)
        sourcePortLayout.addWidget(self.sourcePortPathInput)

        # IWAD section
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

        # PWAD section
        self.pwadGroup = QGroupBox("PWADs (Mods)")
        pwadLayout = QVBoxLayout(self.pwadGroup)

        self.pwadList = PWadList()
        self.pwadList.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pwadList.setMinimumHeight(120)
        for wad in self.config.pwad_paths:
            self.pwadList.addWad(wad)
        self.pwadList.orderChanged.connect(self.saveConfig)
        pwadLayout.addWidget(self.pwadList)

        # PWAD buttons
        self.pwadButtons = QWidget()
        pwadBtnsLayout = QHBoxLayout(self.pwadButtons)
        pwadBtnsLayout.setContentsMargins(0, 0, 0, 0)
        pwadBtnsLayout.setSpacing(4)

        self.pwadAddButton = QPushButton('Add...')
        self.pwadAddButton.setToolTip('Add PWAD or PK3 files')
        self.pwadAddButton.clicked.connect(self.openPWadAction._open)

        self.pwadRemoveButton = QPushButton('Remove')
        self.pwadRemoveButton.setToolTip('Remove selected mods')
        self.pwadRemoveButton.clicked.connect(self.removeSelectedPWads)

        self.pwadUpButton = QPushButton('↑')
        self.pwadUpButton.setToolTip('Move selected mods up')
        self.pwadUpButton.setMaximumWidth(30)
        self.pwadUpButton.clicked.connect(self.pwadList.moveUp)

        self.pwadDownButton = QPushButton('↓')
        self.pwadDownButton.setToolTip('Move selected mods down')
        self.pwadDownButton.setMaximumWidth(30)
        self.pwadDownButton.clicked.connect(self.pwadList.moveDown)

        pwadBtnsLayout.addWidget(self.pwadAddButton)
        pwadBtnsLayout.addWidget(self.pwadRemoveButton)
        pwadBtnsLayout.addStretch()
        pwadBtnsLayout.addWidget(self.pwadUpButton)
        pwadBtnsLayout.addWidget(self.pwadDownButton)
        pwadLayout.addWidget(self.pwadButtons)

        # Extra Options section
        self.optionsGroup = QGroupBox("Extra Options")
        optionsLayout = QVBoxLayout(self.optionsGroup)

        self.extraOptionsInput = QLineEdit()
        self.extraOptionsInput.setToolTip('Additional command line arguments')
        self.extraOptionsInput.setText(self.config.extra_options)
        self.extraOptionsInput.installEventFilter(self)
        optionsLayout.addWidget(self.extraOptionsInput)

        self.launchButton = LaunchButton(
            text="*** UNLEASH HELL ***",
        )
        self.launchButton.setMinimumHeight(40)
        self.launchButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.launchButton.clicked.connect(self._onLaunchRequested)

        # Right panel widgets
        self.lostSoulWidget = DoomSoulWidget(
            skull_gif_path="assets/lost_soul.gif",
            animated_background=self.config.animated_background,
        )
        self.lostSoulWidget.setMinimumSize(180, 180)
        self.lostSoulWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.pwadInfo = PWadInfo()
        self.pwadInfo.setMinimumHeight(120)
        self.pwadInfo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.logWindow = LogWindow(self)
        self.loadingWindow = LostSoulWindow(self)

        self.pwadList.itemSelectionChanged.connect(self.updatePWadInfo)
        self.updatePWadInfo()

        self.installResponsiveLayout()
        self.set_render_profile(self.config.render_profile)
        self.applyWarningsOrErrors()

    def createMenu(self):
        self.openSourcePortAction = OpenSourcePortAction(
            self, self.setSourcePort, self.config, self.saveSourcePortPath
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
        fileMenu.addAction(self.exitAction)

        configMenu = menuBar.addMenu('&Config')
        configMenu.addAction(self.animatedBgAction)
        configMenu.addAction(self.performanceModeAction)

        helpMenu = menuBar.addMenu('&Help')
        helpMenu.addAction(self.openWadRepository)

    def installResponsiveLayout(self):
        self.leftLayout.addWidget(self.sourcePortGroup)
        self.leftLayout.addWidget(self.iwadGroup)
        self.leftLayout.addWidget(self.pwadGroup, 1)
        self.leftLayout.addWidget(self.optionsGroup)
        self.leftLayout.addWidget(self.launchButton)

        self.rightLayout.addWidget(self.lostSoulWidget, 1)
        self.rightLayout.addWidget(self.pwadInfo, 1)
        self.rightLayout.addStretch(0)

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

    def setIWad(self, wad: str):
        self.iwadInput.setText(wad)
        self.config.iwad_path = wad

    def addPWads(self, wads: list):
        dialog = self.loadingWindow
        dialog.setRange(0, len(wads))
        dialog.setValue(0)
        dialog.show()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for i, wad in enumerate(wads):
                dialog.setValue(i)
                if not self.pwadList.addWad(wad):
                    msg = (
                        f"The wad {wad} has already "
                        "been added to the wad list."
                    )
                    self.errorDialog.showMessage(msg)
                QApplication.processEvents()
            dialog.setValue(len(wads))
        finally:
            QApplication.restoreOverrideCursor()
            dialog.hide()
        self.saveConfig()

    def removeSelectedPWads(self):
        for item in self.pwadList.selectedItems():
            index = self.pwadList.indexOfTopLevelItem(item)
            self.pwadList.takeTopLevelItem(index)
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
        else:
            if filename:
                self.config.pwad_dir = str(PurePath(filename[0]).parent)
        self.saveConfig()

    def saveSourcePortPath(self, filename: str):
        self.config.source_port_dir = str(PurePath(filename).parent)
        self.config.source_port_path = filename
        self.saveConfig()

    def getConfig(self):
        return self.config.to_dict()

    def saveConfig(self):
        self.config.source_port_path = self.sourcePortPathInput.text().strip() or "gzdoom"
        self.config.iwad_path = self.iwadInput.text().strip()
        self.config.extra_options = self.extraOptionsInput.text().strip()
        self.config.pwad_paths = [
            item.data(0, Qt.UserRole) for item in self.pwadList.getItems()
        ]
        self.config.animated_background = self.animatedBgAction.isChecked()
        self.config.performance_mode = self.performanceModeAction.isChecked()
        self.config.render_profile = "low" if self.config.performance_mode else "high"

        try:
            self.configStore.save(self.config.normalized())
            self.statusBar().showMessage("Configuration saved", 2500)
        except Exception as exc:
            self.statusBar().showMessage(f"Failed to save config: {exc}", 4000)
            self.errorDialog.showMessage(f"Failed to save configuration: {exc}")

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

    def updatePWadInfo(self):
        paths = [
            item.data(0, Qt.UserRole)
            for item in self.pwadList.selectedItems()
        ]
        self.pwadInfo.showInfo(paths)

    def toggleAnimatedBackground(self):
        enabled = self.animatedBgAction.isChecked()
        self.config.animated_background = enabled
        self.lostSoulWidget.setAnimatedBackground(enabled)
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

    def _synchronize_from_ui(self):
        self.config.source_port_path = self.sourcePortPathInput.text().strip() or "gzdoom"
        self.config.iwad_path = self.iwadInput.text().strip()
        self.config.extra_options = self.extraOptionsInput.text().strip()
        self.config.pwad_paths = [
            item.data(0, Qt.UserRole) for item in self.pwadList.getItems()
        ]
        self.config.animated_background = self.animatedBgAction.isChecked()
        self.config.performance_mode = self.performanceModeAction.isChecked()
        self.config.render_profile = "low" if self.config.performance_mode else "high"
        self.set_render_profile(self.config.render_profile)

    def _onLaunchRequested(self):
        self._synchronize_from_ui()
        self.logWindow.clear()
        self.logWindow.show()
        launched = self.launchController.start_launch(self.config.normalized())
        if not launched:
            self.statusBar().showMessage("Launch blocked: fix config errors and try again", 3000)
            return

    def _on_launch_state_changed(self, state: str):
        if state == LaunchController.STATE_STARTING:
            self.statusBar().showMessage("Launching...")
            self.launchButton.set_loading(True)
            self.loadingWindow.setRange(0, 0)
            self.loadingWindow.setValue(0)
            self.loadingWindow.show()
            self.logWindow.clear()
            self.logWindow.show()
        elif state == LaunchController.STATE_RUNNING:
            self.statusBar().showMessage("BFG.py is running")
            self.loadingWindow.hide()
            self.loadingWindow.setValue(0)
        elif state in (LaunchController.STATE_STOPPED, LaunchController.STATE_FAILED):
            self.launchButton.set_loading(False)
            self.loadingWindow.hide()
            if state == LaunchController.STATE_STOPPED:
                self.statusBar().showMessage("Launch stopped", 3000)
        elif state == LaunchController.STATE_FINISHED:
            self.loadingWindow.hide()

    def _on_launch_output(self, line: str):
        self.logWindow.append(line)

    def _on_launch_error(self, message: str):
        self.errorDialog.showMessage(message)
        self.statusBar().showMessage(message, 5000)
        self.loadingWindow.hide()
        self.launchButton.set_loading(False)

    def _on_launch_finished(self, exit_code: int):
        self.launchButton.set_loading(False)
        self.loadingWindow.hide()
        self.logWindow.append(f'Process finished with code {exit_code}')
        self.saveConfig()
        self.statusBar().showMessage(f"Launcher process exited with code {exit_code}", 5000)
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Launch finished",
                f"Source port exited with non-zero code {exit_code}."
            )

    def applyWarningsOrErrors(self):
        validation = self.config.validate()
        if validation.errors:
            self.errorDialog.showMessage("\n".join(validation.errors))
        if validation.warnings:
            self.statusBar().showMessage("Warnings: " + "; ".join(validation.warnings), 6000)
