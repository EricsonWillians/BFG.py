from PyQt5.QtWidgets import QAction


class ExitAction(QAction):

    def __init__(self, widget):
        super().__init__('&Exit', widget)
        self.setShortcut('Ctrl+Q')
        self.setStatusTip('Exit application')
        # Close the window instead of qApp.quit: quit() exits the event loop
        # without delivering closeEvent, which would bypass saveConfig() and
        # orphan a running game process.
        self.triggered.connect(widget.close)
