import numpy as np
from collections import OrderedDict
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QWidget, QHBoxLayout, QSizePolicy
)
from PyQt5.QtGui import QMovie, QPixmap, QColor, QImage, QPainter, QPainterPath
from PyQt5.QtCore import Qt, QSize, QTimer, QRectF

from src.const import asset_path
from src.performance import perf_settings, animation_runtime


class BloodTextureCache:
    """Cache for pre-generated blood texture frames with bounded memory footprint."""

    _instance = None
    _cache = OrderedDict()
    _cache_limit = 120
    _cache_bytes = 4_000_000

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _max_bytes(cls) -> int:
        return max(256_000, int(perf_settings.get("blood_texture_cache_size", 120)) * 60)

    @classmethod
    def _flush_if_needed(cls):
        total_bytes = 0
        for entry in cls._cache.values():
            total_bytes += entry["bytes"]

        while total_bytes > cls._max_bytes() and len(cls._cache) > 0:
            _, entry = cls._cache.popitem(last=False)
            total_bytes -= entry["bytes"]
            if isinstance(entry.get("pixmap"), QPixmap):
                entry["pixmap"] = QPixmap()

    def get_texture(self, width, height, frame):
        key = (width, height, frame)
        if key in self._cache:
            entry = self._cache.pop(key)
            self._cache[key] = entry
            return entry["pixmap"]

        pixmap = self._generate_blood_frame(width, height, frame)
        entry = {
            "pixmap": pixmap,
            "bytes": width * height * 4,
        }
        self._cache[key] = entry
        if len(self._cache) > self._cache_limit:
            _, removed = self._cache.popitem(last=False)
            if isinstance(removed.get("pixmap"), QPixmap):
                removed["pixmap"] = QPixmap()
        self._flush_if_needed()
        return pixmap

    def _generate_blood_frame(self, width, height, t):
        base = np.zeros((height, width, 3), dtype=np.uint8)
        y_indices = np.arange(height).reshape(-1, 1)
        x_indices = np.arange(width).reshape(1, -1)

        ymod = (y_indices + t // 3) % max(1, height)
        r_base = 160 + 40 * np.sin(ymod * 0.18 + t * 0.04)
        g_base = 0 + 18 * np.cos(ymod * 0.13 + t * 0.03)
        b_base = 0 + 10 * np.sin(x_indices * 0.27 + t * 0.01)

        blood_wave = 10 * np.sin(x_indices + t * 0.09 + ymod * 0.08)
        drip = 18 * np.cos(x_indices * 0.08 + t * 0.06)

        red = np.clip(r_base + blood_wave + drip, 90, 255)
        green = np.clip(g_base + 6 * np.sin(x_indices * 0.15), 0, 64)
        blue = np.clip(b_base + 8 * np.cos(x_indices * 0.12), 0, 40)

        base[:, :, 0] = red
        base[:, :, 1] = green
        base[:, :, 2] = blue

        image = QImage(base.data, width, height, 3 * width, QImage.Format_RGB888)
        return QPixmap.fromImage(image)


def generate_doom2_blood_texture(width, height, t=0):
    cache = BloodTextureCache()
    frame = (t // 4) % 60
    return cache.get_texture(width, height, frame)


class ScrollingDoom2Texture(QWidget):
    """Animated blood texture background with deterministic frame skipping."""

    def __init__(self, w, h, border=4, radius=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._scroll = 0
        self._border = border
        self._radius = radius
        self._texture_width = max(128, w)
        self._texture_height = max(64, h)
        self._pixmap = generate_doom2_blood_texture(self._texture_width, self._texture_height)
        self._skip_ticks = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll_texture)
        self._timer.start(animation_runtime.get_interval())

        self._clip_path = QPainterPath()
        self._clip_path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)

    def showEvent(self, event):
        if not self._timer.isActive():
            self._timer.start(animation_runtime.get_interval())
        super().showEvent(event)

    def hideEvent(self, event):
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def _scroll_texture(self):
        if not self.isVisible():
            return

        self._scroll = (self._scroll + 1) % self._texture_width
        should_draw = animation_runtime.should_render(20, self._skip_ticks)
        if should_draw:
            self._skip_ticks = 0
            if self._scroll % 3 == 0:
                self._pixmap = generate_doom2_blood_texture(self._texture_width, self._texture_height, self._scroll)
            self.update()
        else:
            self._skip_ticks += 1

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, perf_settings.get('enable_antialiasing', False))

        painter.setClipPath(self._clip_path)

        visible_rect = event.rect()
        start_x = (visible_rect.x() // self._texture_width) * self._texture_width
        start_y = (visible_rect.y() // self._texture_height) * self._texture_height
        end_x = visible_rect.right() + self._texture_width
        end_y = visible_rect.bottom() + self._texture_height

        for x in range(start_x, end_x, self._texture_width):
            for y in range(start_y, end_y, self._texture_height):
                painter.drawPixmap(x, y, self._pixmap)

        painter.setPen(QColor('#ff2222'))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            self.rect().adjusted(self._border//2, self._border//2, -self._border//2, -self._border//2),
            self._radius, self._radius
        )


class LostSoulWindow(QDialog):
    PADDING = 32
    GIF_SIZE = 64

    def __init__(self, parent=None):
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint
        super().__init__(parent, flags)
        self.setObjectName("lostSoulWindow")
        self.setWindowTitle("Summoning from Hell...")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        w = self.GIF_SIZE + 2 * self.PADDING
        h = self.GIF_SIZE + 2 * self.PADDING
        bloody_container = ScrollingDoom2Texture(w, h, border=4, radius=20, parent=self)
        container_layout = QHBoxLayout(bloody_container)
        container_layout.setContentsMargins(self.PADDING, self.PADDING, self.PADDING, self.PADDING)
        container_layout.setSpacing(0)

        self.label = QLabel(bloody_container)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFixedSize(self.GIF_SIZE, self.GIF_SIZE)
        self.label.setStyleSheet("background: transparent;")
        movie = QMovie(asset_path("assets/lost_soul.gif"))
        movie.setScaledSize(QSize(self.GIF_SIZE, self.GIF_SIZE))
        self.label.setMovie(movie)
        movie.start()
        container_layout.addWidget(self.label, alignment=Qt.AlignCenter)

        layout.addWidget(bloody_container, alignment=Qt.AlignCenter)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(24)
        self.progress.setStyleSheet("""
            QProgressBar {
                background: #1a050a;
                color: #fff0c0;
                border: 2px solid #900d09;
                border-radius: 8px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ff3500, stop:0.5 #ff9900, stop:1 #e4aa1a
                );
                border-radius: 8px;
            }
        """)
        layout.addWidget(self.progress)

    def setRange(self, minimum: int, maximum: int):
        self.progress.setRange(minimum, maximum)

    def setValue(self, value: int):
        self.progress.setValue(value)
