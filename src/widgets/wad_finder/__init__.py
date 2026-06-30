from PyQt5.QtWidgets import (
    QLabel,
    QGridLayout,
    QWidget,
)


class WadFinder(QWidget):
    def __init__(self):
        super().__init__()
        self.initUi()

    def initUi(self):
        self.setWindowTitle("WAD Finder (disabled)")
        self.setToolTip("WAD discovery is currently unavailable from inside the launcher.")
        self.setEnabled(False)
        self.grid = QGridLayout()
        self.setLayout(self.grid)
        self.grid.addWidget(
            QLabel(
                "WAD finder is temporarily disabled. Use the Download wads menu item to open the online repository."
            ),
            0,
            0,
        )

    def findWads(self, searchWord):
        return None
