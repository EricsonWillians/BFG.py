from PyQt5.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit
from PyQt5.QtCore import Qt


class LogWindow(QDialog):
    """Simple window to display GZDoom output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('GZDoom Log')
        self.resize(600, 400)
        layout = QVBoxLayout()
        self.textEdit = QPlainTextEdit()
        self.textEdit.setMaximumBlockCount(1200)
        self.textEdit.setReadOnly(True)
        layout.addWidget(self.textEdit)
        self.setLayout(layout)

    def clear(self):
        self.textEdit.clear()

    def append(self, text: str):
        """Append a line of text to the log."""
        self.textEdit.appendPlainText(text.rstrip())
        # Ensure the latest text is visible and keep buffer bounded.
        self.textEdit.moveCursor(self.textEdit.textCursor().End)
