from PyQt5.QtWidgets import QAction
import webbrowser


class OpenWadRepository(QAction):

    def __init__(self, widget):
        super().__init__('Open &Doomworld idgames', widget)
        self.setShortcut('Ctrl+Shift+B')
        self.setStatusTip('Open Doomworld idgames in the web browser')
        self.triggered.connect(self.openLink)

    def openLink(self):
        webbrowser.open('https://www.doomworld.com/idgames')
