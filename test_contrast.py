#!/usr/bin/env python3
"""Test the improved contrast and evil 90s DOOM styling."""

import sys
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout
from src.widgets.pwad_info import PWadInfo
from src.widgets.launch_button import LaunchButton
from src.widgets.path_input import PathInput
from src.widgets.iwad_input import IWadInput
from src.widgets.pwad_list import PWadList
from PyQt5.QtWidgets import QLineEdit
from pathlib import Path

class ContrastTestWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("💀 BFG.py - Contrast Test 💀")
        self.setGeometry(100, 100, 800, 600)
        
        # Load theme
        theme_file = Path('assets/nc_theme.qss')
        if theme_file.exists():
            with open(theme_file, 'r') as fh:
                self.setStyleSheet(fh.read())
        
        layout = QVBoxLayout()
        
        # Create test widgets
        self.pwad_info = PWadInfo("📁 Mod Info - Contrast Test")
        
        # Add some test content to show contrast
        test_content = """Path: /doom/mods/brutal_doom.pk3
Size: 45,234,567 bytes
Modified: 2023-12-25 13:37:42

ZIP entries: 1,247
 maps/map01.wad (2,345,678 bytes)
 sprites/demons/imp.png (45,123 bytes)
 sounds/weapons/shotgun.wav (234,567 bytes)
 graphics/hud/statusbar.png (12,345 bytes)
 scripts/brutal.acs (8,901 bytes)
 textures/walls/brick01.png (67,890 bytes)
 music/d_runnin.ogg (3,456,789 bytes)
 ...

Type: PWAD
Lumps: 2,847
 000: MAP01 (0 bytes)
 001: THINGS (1,234 bytes)
 002: LINEDEFS (5,678 bytes)
 003: SIDEDEFS (9,012 bytes)
 004: VERTEXES (3,456 bytes)
 005: SEGS (7,890 bytes)
 006: SSECTORS (1,234 bytes)
 007: NODES (5,678 bytes)
 008: SECTORS (9,012 bytes)
 009: REJECT (3,456 bytes)
 010: BLOCKMAP (7,890 bytes)
 ..."""
        
        self.pwad_info.text.setPlainText(test_content)
        layout.addWidget(self.pwad_info)
        
        # Create test launch button
        button_layout = QHBoxLayout()
        
        # Dummy inputs for the launch button
        port_input = PathInput()
        iwad_input = IWadInput()
        pwad_list = PWadList()
        options_input = QLineEdit()
        
        self.launch_button = LaunchButton(
            port_input, iwad_input, pwad_list, options_input, None, None
        )
        
        button_layout.addStretch()
        button_layout.addWidget(self.launch_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)

def test_contrast():
    """Test the improved contrast and styling."""
    app = QApplication(sys.argv)
    
    print("💀 BFG.py - Contrast Test 💀")
    print("=" * 40)
    print("Testing improved contrast and evil 90s styling...")
    print()
    print("✓ Mod Info Panel:")
    print("  - Darker red background (#800000)")
    print("  - Brighter yellow text (#FFFF80)")
    print("  - Bold font weight for better readability")
    print("  - CRT-style text shadows")
    print("  - Better line spacing")
    print()
    print("✓ Launch Button:")
    print("  - Evil 90s DOOM text: '*** UNLEASH HELL ***'")
    print("  - 3D gradient effects")
    print("  - Menacing text shadows")
    print("  - Authentic retro styling")
    print()
    print("Close the window when done testing contrast.")
    
    test_window = ContrastTestWindow()
    test_window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    test_contrast()