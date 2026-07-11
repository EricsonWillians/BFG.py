from PyQt5.QtWidgets import QAction


class OpenWadFinder(QAction):

    def __init__(self, widget, wadFinder):
        super().__init__('&Mod Browser', widget, checkable=True)
        self.setShortcut('Ctrl+B')
        self.setStatusTip('Show or hide the mod browser')
        self.wadFinder = wadFinder
        self.triggered.connect(self.setVisible)

    def setVisible(self):
        isVisible = self.wadFinder.isVisible()
        self.wadFinder.setVisible(not isVisible)
