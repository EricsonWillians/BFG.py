import sys
from PyQt5.QtWidgets import QAction, QFileDialog

class OpenSourcePortAction(QAction):
    def __init__(self, widget, setSourcePort, config, saveSourcePortPath, browse_handler=None):
        super().__init__("&Open Source Port", widget)
        self.widget = widget
        self.setShortcut("Ctrl+O")
        self.setStatusTip("Select a source port")
        self.triggered.connect(self._open)
        self.setSourcePort = setSourcePort
        self.config = config
        self.saveSourcePortPath = saveSourcePortPath
        self.browse_handler = browse_handler

    def _open(self):
        if self.browse_handler is not None:
            return self.browse_handler()

        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        file_filter = (
            "Executable files (*.exe);;All files (*.*)"
            if sys.platform.startswith("win")
            else "All files (*)"
        )
        filename, _ = QFileDialog.getOpenFileName(
            self.widget,
            "Select a source port",
            self.config.get("sourcePortDir"),
            file_filter,
            options=options,
        )
        if filename:
            self.saveSourcePortPath(filename)
            self.setSourcePort(filename)
