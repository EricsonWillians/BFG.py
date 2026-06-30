#!/usr/bin/env python3
"""Performance test script for BFG.py."""

import sys
import time
import psutil
import os
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import QTimer
from src.widgets.doom_soul_widget import DoomSoulWidget
from src.widgets.lost_soul_window import LostSoulWindow
from src.performance import perf_settings

class PerformanceTestWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BFG.py - Performance Test")
        self.setGeometry(100, 100, 800, 600)
        
        layout = QVBoxLayout()
        
        # Performance stats label
        self.stats_label = QLabel("Performance Stats:\nInitializing...")
        layout.addWidget(self.stats_label)
        
        # Add skull widget for testing
        self.skull_widget = DoomSoulWidget(
            skull_gif_path="assets/lost_soul.gif",
            animated_background=True
        )
        layout.addWidget(self.skull_widget)
        
        self.setLayout(layout)
        
        # Performance monitoring
        self.process = psutil.Process(os.getpid())
        self.frame_count = 0
        self.start_time = time.time()
        
        # Update stats timer
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.update_stats)
        self.stats_timer.start(1000)  # Update every second
        
        # Frame counting timer
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self.count_frame)
        self.frame_timer.start(16)  # ~60 FPS for counting
    
    def count_frame(self):
        self.frame_count += 1
    
    def update_stats(self):
        try:
            # Calculate runtime
            runtime = time.time() - self.start_time
            
            # Calculate FPS
            fps = self.frame_count / runtime if runtime > 0 else 0
            
            # Get memory usage
            memory_info = self.process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            # Get CPU usage
            cpu_percent = self.process.cpu_percent()
            
            # Update stats display
            stats_text = f"""Performance Stats:
Runtime: {runtime:.1f}s
Frames: {self.frame_count}
FPS: {fps:.1f}
Memory: {memory_mb:.1f} MB
CPU: {cpu_percent:.1f}%
Animation FPS Setting: {perf_settings.get('animation_fps')}
Antialiasing: {perf_settings.get('enable_antialiasing')}
Scaling Quality: {perf_settings.get('skull_scaling_quality')}"""
            
            self.stats_label.setText(stats_text)

        except Exception as e:
            self.stats_label.setText(f"Error monitoring performance: {e}")

def run_performance_test():
    """Run the performance test."""
    app = QApplication(sys.argv)
    
    print("BFG.py - Performance Test")
    print("=" * 40)
    print("Testing skull rendering performance...")
    print(f"Performance settings:")
    print(f"  Animation FPS: {perf_settings.get('animation_fps')}")
    print(f"  Antialiasing: {perf_settings.get('enable_antialiasing')}")
    print(f"  Scaling Quality: {perf_settings.get('skull_scaling_quality')}")
    print(f"  Timer Interval: {perf_settings.get_timer_interval()}ms")
    print()
    
    # Test different performance modes
    test_window = PerformanceTestWindow()
    test_window.show()
    
    print("Performance test window opened.")
    print("Watch the stats in the window and close it when done.")
    print("You can set environment variables to test different settings:")
    print("  BFG_ANIMATION_FPS=15 uv run bfg-perf")
    print("  BFG_ANTIALIASING=true uv run bfg-perf")
    print("  BFG_SCALING_QUALITY=smooth uv run bfg-perf")
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    run_performance_test()
