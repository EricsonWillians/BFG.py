#!/usr/bin/env python3
"""Test the new responsive layout."""

import sys
from PyQt5.QtWidgets import QApplication
from src.widgets.main_window import MainWindow

def run_layout_check():
    """Test the responsive layout at different window sizes."""
    app = QApplication(sys.argv)
    
    # Create main window
    window = MainWindow()
    
    print("💀 BFG.py - Layout Test 💀")
    print("=" * 40)
    print("Testing responsive layout...")
    print("Try resizing the window to see how it adapts!")
    print("The layout should:")
    print("  ✓ Keep proper proportions")
    print("  ✓ Maintain minimum sizes")
    print("  ✓ Scale widgets appropriately")
    print("  ✓ Look good at different sizes")
    print()
    print("Close the window when done testing.")
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    run_layout_check()