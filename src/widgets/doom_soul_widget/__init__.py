import os
import sys
import tempfile
import time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np
from PyQt5.Qt import Qt
from PyQt5.QtCore import QTimer, QSize
from PyQt5.QtGui import QImage, QPainter, QMovie, QPixmap
from PyQt5.QtWidgets import QApplication, QSizePolicy, QWidget
from PIL import Image

from src.performance import animation_runtime, perf_settings

_TILE_CACHE = OrderedDict()


def _cache_limit_bytes() -> int:
    return max(512_000, int(perf_settings.get("tile_cache_max_bytes", 4_000_000)))


def _cache_ttl_seconds() -> int:
    return max(10, int(perf_settings.get("tile_cache_ttl_seconds", 120)))


def generate_hell_tile_array(width, height, seed=None):
    if seed is not None:
        np.random.seed(seed)

    y_coords, x_coords = np.mgrid[0:height, 0:width]
    r = np.clip(26 + 32 * np.sin(0.11 * y_coords + 0.19 * x_coords) + 38, 0, 255)
    g = np.clip(7 + 9 * np.cos(0.19 * y_coords + 0.13 * x_coords), 0, 255)
    b = np.clip(2 + 6 * np.sin(0.09 * x_coords - 0.11 * y_coords), 0, 255)

    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :, 0] = r
    arr[:, :, 1] = g
    arr[:, :, 2] = b
    arr[:, :, 3] = 255

    cracks = np.random.rand(3, 4) * np.array([[width, height, 2 * np.pi, width / 3]])
    for cx, cy, a, l in cracks:
        t_vals = np.arange(0, int(l), 2)
        px = ((cx + t_vals * np.cos(a + np.sin(t_vals * 0.19))) % width).astype(int)
        py = ((cy + t_vals * np.sin(a + np.cos(t_vals * 0.12))) % height).astype(int)
        for i in range(len(px)):
            y_slice = slice(max(0, py[i] - 1), min(height, py[i] + 2))
            x_slice = slice(max(0, px[i] - 1), min(width, px[i] + 2))
            arr[y_slice, x_slice, :3] = 0

    ember_count = max(1, (width * height) // 1280)
    for _ in range(int(ember_count)):
        ex, ey = np.random.randint(0, width), np.random.randint(0, height)
        radius = np.random.randint(2, 4)
        y_slice = slice(max(0, ey - radius), min(height, ey + radius + 1))
        x_slice = slice(max(0, ex - radius), min(width, ex + radius + 1))

        yy, xx = np.mgrid[y_slice, x_slice]
        dy = yy - ey
        dx = xx - ex
        dist = np.sqrt(dx * dx + dy * dy)
        mask = dist <= radius
        glow = (220 - dist * 38).astype(int)

        arr[y_slice, x_slice, 0] = np.where(mask, np.clip(arr[y_slice, x_slice, 0] + glow, 0, 255), arr[y_slice, x_slice, 0])
        arr[y_slice, x_slice, 1] = np.where(mask, np.clip(arr[y_slice, x_slice, 1] + glow // 3, 0, 255), arr[y_slice, x_slice, 1])

    cy, cx = height / 2, width / 2
    d = np.sqrt((x_coords - cx) ** 2 + (y_coords - cy) ** 2)
    fade = 0.85 + 0.15 * np.cos(np.pi * d / (0.7 * max(width, height)))
    arr[:, :, :3] = (arr[:, :, :3].astype(np.float32) * fade[:, :, np.newaxis]).astype(np.uint8)

    return arr


def save_hell_tile_png(arr, path):
    img = Image.fromarray(arr, "RGBA")
    img.save(path)


def _make_tile_key(width: int, height: int, seed: Optional[int]) -> Tuple[int, int, int]:
    return (width, height, int(seed or 0))


def _cleanup_cache(force: bool = False):
    max_bytes = _cache_limit_bytes()
    ttl = _cache_ttl_seconds()
    now = time.time()
    total = 0
    for entry in _TILE_CACHE.values():
        total += entry["size"]

    # Remove expired files first.
    for key in list(_TILE_CACHE.keys()):
        entry = _TILE_CACHE[key]
        if force or now - entry["last_used"] > ttl:
            _TILE_CACHE.pop(key, None)
            try:
                if os.path.isfile(entry["path"]):
                    os.remove(entry["path"])
            except OSError:
                pass

    # Then trim LRU by bytes.
    for key in list(_TILE_CACHE.keys()):
        if total <= max_bytes:
            break
        key_to_evict, evict_entry = _TILE_CACHE.popitem(last=False)
        total -= evict_entry["size"]
        try:
            if os.path.isfile(evict_entry["path"]):
                os.remove(evict_entry["path"])
        except OSError:
            pass

        if key_to_evict == key and key not in _TILE_CACHE:
            break


def ensure_tile_file(tile_w: int, tile_h: int, seed=None, keep_existing: bool = False):
    if tile_w <= 0 or tile_h <= 0:
        tile_w = max(1, tile_w)
        tile_h = max(1, tile_h)

    seed = int(seed) if seed is not None else int(time.time())
    key = _make_tile_key(tile_w, tile_h, seed)

    now = time.time()
    if key in _TILE_CACHE:
        entry = _TILE_CACHE.pop(key)
        entry["refcount"] += 1
        entry["last_used"] = now
        _TILE_CACHE[key] = entry
        return entry["path"]

    tempdir = tempfile.gettempdir()
    path = os.path.join(tempdir, f"bfg_tile_{tile_w}x{tile_h}_{seed}_{os.getpid()}.png")
    if keep_existing and os.path.isfile(path):
        arr_path = path
    else:
        arr = generate_hell_tile_array(tile_w, tile_h, seed)
        save_hell_tile_png(arr, path)
        arr_path = path

    try:
        size = os.path.getsize(arr_path)
    except OSError:
        size = 0

    _TILE_CACHE[key] = {
        "path": arr_path,
        "refcount": 1,
        "last_used": now,
        "size": size,
    }
    _TILE_CACHE.move_to_end(key)
    _cleanup_cache()
    return arr_path


def release_tile_file(tile_w: int, tile_h: int, seed=None, path: Optional[str] = None):
    seed = int(seed) if seed is not None else int(time.time())
    key = _make_tile_key(tile_w, tile_h, seed)
    entry = _TILE_CACHE.get(key)
    if not entry:
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return
    entry["refcount"] = max(0, entry["refcount"] - 1)
    entry["last_used"] = time.time()
    _TILE_CACHE[key] = entry
    if entry["refcount"] <= 0:
        _cleanup_cache(force=True)


def get_temp_tile_path():
    return ensure_tile_file(96, 64, seed=int(datetime.now().strftime('%Y%m%d')),
                           keep_existing=perf_settings.get("persistent_tile_cache", True))


class DoomSoulWidget(QWidget):
    def __init__(self, skull_gif_path: str, parent=None, tile_w=96, tile_h=64, animated_background=False):
        super().__init__(parent)
        self.setMinimumSize(180, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._tile_w, self._tile_h = tile_w, tile_h
        self._tile_seed = int(datetime.now().strftime('%Y%m%d'))
        self._animated_background = animated_background
        self._tile_path = None
        self._tile_key = None
        self._dropped_ticks = 0
        self._target_fps = max(8, int(perf_settings.get("animation_fps", 20)))

        if self._animated_background:
            self._tile_key = (self._tile_w, self._tile_h, self._tile_seed)
            self._tile_path = ensure_tile_file(
                self._tile_w,
                self._tile_h,
                seed=self._tile_seed,
                keep_existing=perf_settings.get("persistent_tile_cache", True),
            )
            self._tile_pixmap = QPixmap(self._tile_path)
        else:
            self._tile_pixmap = None

        self._scroll = 0
        self._cached_skull = None
        self._cached_skull_size = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if self._animated_background:
            self._timer.start(animation_runtime.get_interval())

        self.skull_gif = QMovie(skull_gif_path)
        self.skull_gif.jumpToFrame(0)
        self.skull_frame = None
        self.skull_gif.frameChanged.connect(self.updateSkullFrame)
        self.skull_gif.start()

    def _timer_interval(self) -> int:
        fps = max(8, int(perf_settings.get("animation_fps", 20)))
        if not self._animated_background:
            return max(8, 1000 // fps)
        return animation_runtime.get_interval()

    def updateSkullFrame(self, idx):
        frame = self.skull_gif.currentPixmap().toImage().convertToFormat(QImage.Format_ARGB32)
        self.skull_frame = frame
        self._cached_skull = None
        self.update()

    def _tick(self):
        if self._animated_background and self._tile_pixmap and self.isVisible():
            self._scroll = (self._scroll + 1) % max(1, self._tile_w)
            should_draw = animation_runtime.should_render(self._target_fps, self._dropped_ticks)
            if should_draw:
                self._dropped_ticks = 0
                if self._scroll % 2 == 0:
                    self.update()
            else:
                self._dropped_ticks += 1

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, perf_settings.get('enable_antialiasing', False))

        if self._animated_background and self._tile_pixmap:
            visible_rect = event.rect()
            start_x = ((visible_rect.x() - self._scroll) // self._tile_w) * self._tile_w + self._tile_scroll_adjust()
            start_y = (visible_rect.y() // self._tile_h) * self._tile_h
            end_x = visible_rect.right() + self._tile_w
            end_y = visible_rect.bottom() + self._tile_h

            for y in range(start_y, end_y, self._tile_h):
                for x in range(start_x - self._tile_w, end_x, self._tile_w):
                    painter.drawPixmap(x, y, self._tile_pixmap)
        else:
            painter.fillRect(event.rect(), Qt.black)

        if self.skull_frame:
            size = int(min(w, h) * 0.82)
            if self._cached_skull is None or self._cached_skull_size != size:
                transform_mode = Qt.SmoothTransformation if perf_settings.get('skull_scaling_quality') == 'smooth' else Qt.FastTransformation
                self._cached_skull = self.skull_frame.scaled(
                    QSize(size, size), Qt.KeepAspectRatio, transform_mode
                )
                self._cached_skull_size = size

            sx = (w - size) // 2
            sy = (h - size) // 2
            painter.drawImage(sx, sy, self._cached_skull)

    def setAnimatedBackground(self, enabled):
        if self._animated_background == bool(enabled):
            return
        self._animated_background = bool(enabled)
        self._timer.stop()

        if self._animated_background:
            if not self._tile_pixmap:
                self._tile_key = (self._tile_w, self._tile_h, self._tile_seed)
                self._tile_path = ensure_tile_file(
                    self._tile_w,
                    self._tile_h,
                    seed=self._tile_seed,
                    keep_existing=perf_settings.get("persistent_tile_cache", True),
                )
                self._tile_pixmap = QPixmap(self._tile_path)
            self._timer.start(self._timer_interval())
        else:
            if self._tile_pixmap and self._tile_path:
                release_tile_file(*self._tile_key, path=self._tile_path)
                self._tile_pixmap = None
                self._tile_key = None
                self._tile_path = None

        self.update()

    def _tile_scroll_adjust(self):
        return self._scroll

    def setPlaybackPaused(self, paused: bool):
        """Suspend decorative animation while the widget is hidden for space."""
        paused = bool(paused)
        self.skull_gif.setPaused(paused)
        if paused:
            self._timer.stop()
        elif self._animated_background:
            self._timer.start(self._timer_interval())

    def closeEvent(self, event):
        if self._tile_key and self._tile_path:
            release_tile_file(*self._tile_key, path=self._tile_path)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    skull_gif = os.path.join(os.path.dirname(__file__), "assets", "lost_soul.gif")
    w = DoomSoulWidget(skull_gif)
    w.setWindowTitle("DOOM Soul Widget")
    w.resize(320, 320)
    w.show()
    sys.exit(app.exec_())
