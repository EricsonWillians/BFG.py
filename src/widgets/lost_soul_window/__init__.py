import numpy as np
from collections import OrderedDict
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QWidget, QHBoxLayout, QSizePolicy
)
from PyQt5.QtGui import QMovie, QPixmap, QColor, QImage, QPainter, QPainterPath
from PyQt5.QtCore import Qt, QSize, QTimer, QRectF

# Import performance settings with fallback
try:
    from src.performance import perf_settings
except ImportError:
    class FallbackPerfSettings:
        def get(self, key, default=None):
            defaults = {'animation_fps': 20, 'enable_antialiasing': False}
            return defaults.get(key, default)
        def get_timer_interval(self):
            return 50
    perf_settings = FallbackPerfSettings()

class BloodTextureCache:
    """Cache for pre-generated blood texture frames to avoid real-time generation."""
    _instance = None
    _cache = OrderedDict()
    _cache_limit = 120
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_texture(self, width, height, frame):
        key = (width, height, frame)
        if key not in self._cache:
            self._cache[key] = self._generate_blood_frame(width, height, frame)
        else:
            value = self._cache.pop(key)
            self._cache[key] = value
        if len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return self._cache[key]
    
    def _generate_blood_frame(self, width, height, t):
        """Generate a single blood texture frame - optimized version."""
        # Pre-calculate common values
        base = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Vectorized operations for better performance
        y_indices = np.arange(height).reshape(-1, 1)
        x_indices = np.arange(width).reshape(1, -1)
        
        # Base color calculations (vectorized)
        ymod = (y_indices + t // 3) % height
        r_base = 160 + 40 * np.sin(ymod * 0.18 + t * 0.04)
        g_base = 0 + 18 * np.cos(ymod * 0.13 + t * 0.03)
        b_base = 0 + 10 * np.sin(ymod * 0.27 + t * 0.01)
        
        # Wave effects (vectorized)
        blood_wave = 10 * np.sin((x_indices + t) * 0.09 + ymod * 0.08)
        drip = 18 * np.cos(x_indices * 0.08 + t * 0.06)
        
        # Combine and clip
        red = np.clip(r_base + blood_wave + drip, 90, 255)
        green = np.clip(g_base + 6 * np.sin(x_indices * 0.15), 0, 64)
        blue = np.clip(b_base + 8 * np.cos(x_indices * 0.12), 0, 40)
        
        base[:, :, 0] = red
        base[:, :, 1] = green
        base[:, :, 2] = blue
        
        image = QImage(base.data, width, height, 3 * width, QImage.Format_RGB888)
        return QPixmap.fromImage(image)

def generate_doom2_blood_texture(width, height, t=0):
    """Optimized blood texture generation using cache."""
    cache = BloodTextureCache()
    # Reduce frame granularity to improve cache hits
    frame = (t // 4) % 60  # 60 cached frames should be enough
    return cache.get_texture(width, height, frame)

class ScrollingDoom2Texture(QWidget):
    """Optimized widget with cached blood texture background."""
    def __init__(self, w, h, border=4, radius=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._scroll = 0
        self._border = border
        self._radius = radius
        self._texture_width = max(128, w)
        self._texture_height = max(64, h)
        self._pixmap = generate_doom2_blood_texture(self._texture_width, self._texture_height)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll_texture)
        self._timer.start(perf_settings.get_timer_interval())  # Use performance-optimized interval
        
        # Pre-create the clipping path for better performance
        self._clip_path = QPainterPath()
        self._clip_path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)

    def _scroll_texture(self):
        self._scroll = (self._scroll + 1) % self._texture_width  # Slower scroll
        # Only update pixmap every few frames to reduce CPU usage
        if self._scroll % 3 == 0:
            self._pixmap = generate_doom2_blood_texture(self._texture_width, self._texture_height, self._scroll)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, perf_settings.get('enable_antialiasing', False))
        
        # Use pre-created clipping path
        painter.setClipPath(self._clip_path)

        # Optimized tiling - only draw visible tiles
        visible_rect = event.rect()
        start_x = (visible_rect.x() // self._texture_width) * self._texture_width
        start_y = (visible_rect.y() // self._texture_height) * self._texture_height
        end_x = visible_rect.right() + self._texture_width
        end_y = visible_rect.bottom() + self._texture_height
        
        for x in range(start_x, end_x, self._texture_width):
            for y in range(start_y, end_y, self._texture_height):
                painter.drawPixmap(x, y, self._pixmap)

        # Draw the border (simplified)
        painter.setPen(QColor('#ff2222'))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            self.rect().adjusted(self._border//2, self._border//2, -self._border//2, -self._border//2),
            self._radius, self._radius
        )

class LostSoulWindow(QDialog):
    PADDING = 32   # Visibly hellish padding
    GIF_SIZE = 64

    def __init__(self, parent=None):
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint
        super().__init__(parent, flags)
        self.setObjectName("lostSoulWindow")
        self.setWindowTitle("Summoning from Hell...")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        # === BLOODY SCROLLING CONTAINER ===
        w = self.GIF_SIZE + 2 * self.PADDING
        h = self.GIF_SIZE + 2 * self.PADDING
        bloody_container = ScrollingDoom2Texture(w, h, border=4, radius=20, parent=self)
        # Layout for GIF on top of animated blood
        container_layout = QHBoxLayout(bloody_container)
        container_layout.setContentsMargins(self.PADDING, self.PADDING, self.PADDING, self.PADDING)
        container_layout.setSpacing(0)

        # LOST SOUL GIF (centered, floating above scrolling blood)
        self.label = QLabel(bloody_container)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFixedSize(self.GIF_SIZE, self.GIF_SIZE)
        self.label.setStyleSheet("background: transparent;")
        movie = QMovie("assets/lost_soul.gif")
        movie.setScaledSize(QSize(self.GIF_SIZE, self.GIF_SIZE))
        self.label.setMovie(movie)
        movie.start()
        container_layout.addWidget(self.label, alignment=Qt.AlignCenter)

        layout.addWidget(bloody_container, alignment=Qt.AlignCenter)

        # Progress bar (unchanged)
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
