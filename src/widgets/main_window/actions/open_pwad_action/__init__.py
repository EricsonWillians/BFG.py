from PyQt5.QtWidgets import QAction, QFileDialog


class OpenPWadAction(QAction):

    def __init__(self, widget, addPWads, config, saveWadPath):
        super().__init__('&Open PWADs', widget)
        self.widget = widget
        self.setShortcut('Ctrl+P')
        self.setStatusTip('Select PWAD files')
        self.triggered.connect(self._open)
        self.addPWads = addPWads
        self.config = config
        self.saveWadPath = saveWadPath

    def _open(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filenames, _ = QFileDialog.getOpenFileNames(
            self.widget,
            "Add mods to the loadout",
            self.config.get("pwadDir"),
            "Doom mods (*.wad *.pk3 *.ipk3 *.pk7 *.pke *.zip);;WAD files (*.wad);;Packages (*.pk3 *.ipk3 *.pk7 *.pke *.zip);;All files (*)",
            options=options,
        )
        if filenames:
            self.saveWadPath(filenames, isIWad=False)
            self.addPWads(filenames)
