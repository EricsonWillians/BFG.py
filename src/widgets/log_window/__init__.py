from collections import deque

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit
from PyQt5.QtCore import Qt, QTimer


class LogWindow(QDialog):
    """Simple window to display source-port output."""

    # Source ports can emit hundreds of lines per second at startup; appending
    # each line individually forces a full document relayout + repaint per line
    # and stalls the whole UI. Queue lines and flush in batches instead.
    _FLUSH_INTERVAL_MS = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Source Port Log')
        self.resize(600, 400)
        layout = QVBoxLayout()
        self.textEdit = QPlainTextEdit()
        self.textEdit.setMaximumBlockCount(1200)
        self.textEdit.setReadOnly(True)
        layout.addWidget(self.textEdit)
        self.setLayout(layout)

        self._pending = deque()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self._FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)

    def clear(self):
        self._pending.clear()
        self.textEdit.clear()

    def append(self, text: str):
        """Queue a line of text; displayed on the next batched flush."""
        self._pending.append(text.rstrip())
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def flush(self):
        """Display any queued lines immediately (e.g. on launch finished)."""
        if self._flush_timer.isActive():
            self._flush_timer.stop()
        self._flush_pending()

    def _flush_pending(self):
        if not self._pending:
            return
        chunk = "\n".join(self._pending)
        self._pending.clear()
        self.textEdit.setUpdatesEnabled(False)
        try:
            self.textEdit.appendPlainText(chunk)
            # Ensure the latest text is visible; buffer stays bounded via
            # maximumBlockCount.
            self.textEdit.moveCursor(self.textEdit.textCursor().End)
        finally:
            self.textEdit.setUpdatesEnabled(True)
