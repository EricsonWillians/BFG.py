# 💀 BFG.py 💀

![screenshot](./assets/screenshot.png)

> **THE ULTIMATE GZDOOM LAUNCHER**  
> _BORN OF HACKS. FORGED IN PYTHON. DESIGNED FOR THE TERMINAL-OBSESSED._  
>  
> _Written by Ericson Willians — Survivor of countless segmentation faults and WAD corruption events._

---

## 🔥 "ABANDON ALL HOPE, YE WHO RUN THIS LAUNCHER" 🔥

Welcome, mortal.  
You stand at the gateway to **BFG.py**—a launcher so retro, so blue, so absolutely *cursed* that even your config files might burst into flames.

No more batch files. No more hand-editing command lines.  
Just a *searing*, 90s BBS-inspired interface for GZDoom. Complete with a floating lost soul, ANSI colors, and real modder suffering.

---

## 📋 TABLE OF CONTENTS

- [🛠️ Installation Guide](#️-installation-guide)
- [🚀 Quick Start](#-quick-start)
- [🎮 How to Use](#-how-to-use)
- [🪓 Features](#-features-straight-outta-id-software)
- [⚡ Performance](#-performance-optimizations)
- [🎨 UI/UX](#-responsive-uiux-design)
- [🔧 Troubleshooting](#-troubleshooting)
- [📚 Advanced Usage](#-advanced-usage)

---

## 🛠️ INSTALLATION GUIDE

### **Prerequisites: What You Need to Summon This Demon**

Before you can unleash hell, make sure you have:

1. **Python 3.7 or higher** (3.9+ recommended)
2. **GZDoom** (or another compatible source port)
3. **DOOM WAD files** (DOOM.WAD, DOOM2.WAD, etc.)
4. **Git** (to clone the repository)

### **Step 1: Check Your Python Installation**

```bash
# Check if Python is installed and what version
python --version
# or try
python3 --version

# If you don't have Python, install it:
# Windows: Download from https://python.org
# macOS: brew install python3
# Ubuntu/Debian: sudo apt install python3 python3-pip
# Arch Linux: sudo pacman -S python python-pip
# Fedora: sudo dnf install python3 python3-pip
```

### **Step 2: Install Required System Packages**

**On Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3-pip python3-venv git
sudo apt install python3-pyqt5 python3-pyqt5.qtmultimedia  # Optional: system PyQt5
```

**On Arch Linux:**
```bash
sudo pacman -S python python-pip git
sudo pacman -S python-pyqt5  # Optional: system PyQt5
```

**On macOS:**
```bash
# Install Homebrew if you don't have it: https://brew.sh
brew install python3 git
```

**On Windows:**
```powershell
# Install Python from https://python.org (make sure to check "Add to PATH")
# Install Git from https://git-scm.com
# Then use Command Prompt or PowerShell for the following steps
```

### **Step 3: Clone and Install BFG.py**

```bash
# Clone the repository
git clone https://github.com/yourname/bfgpy.git
cd bfgpy

# Install managed dependencies with uv
uv sync

# Test the installation
uv run bfg
```

### **Step 4: Install GZDoom (if you don't have it)**

**Ubuntu/Debian:**
```bash
sudo apt install gzdoom
```

**Arch Linux:**
```bash
sudo pacman -S gzdoom
```

**macOS:**
```bash
brew install gzdoom
```

**Windows:**
- Download from: https://zdoom.org/downloads
- Extract to a folder (e.g., `C:\Games\GZDoom\`)
- Remember the path to `gzdoom.exe`

---

## 🚀 QUICK START

### **For the Impatient Demon Slayer**

```bash
# 1. Clone and enter the hellish directory
git clone https://github.com/yourname/bfgpy.git
cd bfgpy

# 2. Install the cursed dependencies
uv sync

# 3. UNLEASH HELL
uv run bfg
```

### **First Launch Setup**

1. **Set Source Port Path**: Point to your GZDoom executable
   - Linux: Usually `/usr/bin/gzdoom` or `/usr/games/gzdoom`
   - macOS: `/usr/local/bin/gzdoom` or `/Applications/GZDoom.app/Contents/MacOS/gzdoom`
   - Windows: `C:\Games\GZDoom\gzdoom.exe` (or wherever you installed it)

2. **Select IWAD**: Choose your main DOOM WAD file
   - `DOOM.WAD` (Original DOOM)
   - `DOOM2.WAD` (DOOM II: Hell on Earth)
   - `PLUTONIA.WAD` (Final DOOM: The Plutonia Experiment)
   - `TNT.WAD` (Final DOOM: TNT Evilution)

3. **Click "*** UNLEASH HELL ***"** and start ripping and tearing!

---

## 🎮 HOW TO USE

### **Main Interface Walkthrough**

The launcher is divided into two main panels:

#### **Left Panel: The Control Center of Carnage**

1. **Source Port Section**
   - Enter the path to your GZDoom executable
   - Use the file browser or type the path manually
   - The launcher remembers your last setting

2. **IWAD Section (Main Game)**
   - Select your base DOOM WAD file
   - Click "Browse..." to find your WAD files
   - Required for launching any DOOM game

3. **PWADs Section (Mods)**
   - Add modification files (.wad, .pk3, .zip)
   - **Add...**: Browse and select mod files
   - **Remove**: Delete selected mods from the list
   - **↑/↓**: Reorder mods (load order matters!)
   - **Drag & Drop**: Reorder by dragging items

4. **Extra Options**
   - Add custom command-line arguments
   - Examples: `-skill 4`, `-warp 1 1`, `-nomonsters`

5. **Launch Button**
   - The big red "*** UNLEASH HELL ***" button
   - Starts GZDoom with your selected configuration
   - Shows progress while loading

#### **Right Panel: Information & Eye Candy**

1. **Animated Lost Soul**
   - Classic DOOM demon animation
   - Can be disabled in Config menu for performance
   - Resizes with the window

2. **Mod Info Panel**
   - Shows details about selected mods
   - File sizes, modification dates
   - WAD/PK3 contents preview
   - Select mods in the PWAD list to see info

### **Menu Options**

#### **File Menu**
- **Source Port**: Browse for GZDoom executable
- **IWAD**: Browse for main DOOM WAD files
- **PWAD**: Browse for mod files
- **Exit**: Close the launcher

#### **Config Menu**
- **Animated Background**: Toggle the moving background
- **Performance Mode**: Enable optimizations for slower systems

#### **Help Menu**
- **WAD Repository**: Links to mod download sites

### **Keyboard Shortcuts**

- **Enter** (in Source Port field): Launch DOOM immediately
- **Delete** (in PWAD list): Remove selected mods
- **Ctrl+A** (in PWAD list): Select all mods
- **F5**: Refresh mod info panel

---

## 🪓 FEATURES STRAIGHT OUTTA ID SOFTWARE

| **HELLISH FEATURE**         | **WHAT IT DOES**                                         |
|----------------------------|----------------------------------------------------------|
| IWAD Selector              | Choose your fate: DOOM, DOOM II, or some eldritch WAD    |
| PWAD Stack                 | Load 'em up—slaughter your mods in any order             |
| Launch Option Memory       | Your last rituals are remembered for next time           |
| Live Log Window            | See GZDoom's innermost secrets scroll by in real time    |
| Mod Info Panel             | View mod sizes, file dates, and what you're actually loading |
| Animated Lost Soul         | A GIF demon taunts you, just like a 1994 CD-ROM shareware |
| ANSI/CRT Visuals           | Blue backgrounds, yellow text, the full 90s DOS treatment|
| Progress & Busy Cursor     | Loading big mods? Wait in terror, just like the old days |
| **Responsive Layout**      | **Scales beautifully from 640x480 to 4K displays**      |
| **Organized Sections**     | **Clean group boxes for better mod management**          |
| **Drag & Drop**            | **Reorder mods by dragging them around**                 |
| **Auto-Save Config**       | **Your settings are automatically saved**                |

---

## 📸 SCREENSHOT

![BFG.py in all its hellish glory](./assets/screenshot.png)

---

## ⚡ PERFORMANCE OPTIMIZATIONS

BFG.py has been optimized for smooth skull rendering and overall performance:

### 🔧 **Automatic Optimizations**
- **Frame Caching**: Sprite frames are pre-cached to avoid repeated operations
- **Vectorized Texture Generation**: Blood textures use NumPy vectorization
- **Smart Paint Events**: Only visible areas are redrawn
- **Reduced Timer Frequency**: Animations run at optimized 20 FPS by default
- **Memory Management**: Temporary files and pixmaps are properly cleaned up

### 🎛️ **Performance Settings**
Enable **Performance Mode** from the Config menu for maximum speed, or use environment variables:

```bash
# Reduce animation FPS for slower machines
BFG_ANIMATION_FPS=15 uv run bfg

# Enable smooth scaling (higher quality, slower)
BFG_SCALING_QUALITY=smooth uv run bfg

# Enable antialiasing (prettier, slower)
BFG_ANTIALIASING=true uv run bfg

# Disable background animation entirely
BFG_BACKGROUND_ANIM=false uv run bfg
```

### 📊 **Performance Testing**
Run the performance test to monitor FPS, memory usage, and CPU:

```bash
uv run bfg-perf
```

### 🚀 **Quick Performance Optimization**
```bash
# Run the automatic optimizer
uv run bfg-optimize

# This will:
# - Disable animated background for max performance
# - Enable performance mode
# - Show environment variables for fine-tuning
# - Check your system for potential issues
```

---

## 🎨 RESPONSIVE UI/UX DESIGN

The interface adapts intelligently to different screen sizes and resolutions:

### 📱 **Adaptive Layout**
- **Small Windows** (< 700px): Compact spacing, smaller skull widget
- **Normal Windows** (700-1200px): Balanced proportions, optimal spacing  
- **Large Windows** (> 1200px): Generous spacing, maximum visual impact

### 🎯 **Smart Resizing**
- Left panel: Controls stay organized in clean sections
- Right panel: Skull and mod info scale proportionally
- Group boxes: Automatically adjust padding and margins
- Buttons: Maintain proper touch targets at all sizes

### 🖥️ **Multi-Monitor Support**
- Minimum size: 640×480 (retro CRT compatible)
- Scales up to 4K displays without losing the retro aesthetic
- Maintains aspect ratios and visual hierarchy

### 🎮 **Test the Layout**
```bash
uv run bfg-layout-check
```

---

## 🔧 TROUBLESHOOTING

### **Common Issues and Solutions**

#### **"Python not found" or "Command not found"**
```bash
# Try these alternatives:
python3 -m bfg       # Linux/macOS
uv run bfg           # Any uv-managed environment
py -m bfg            # Windows

# If still not working, reinstall Python:
# Make sure to check "Add Python to PATH" during installation
```

#### **"ModuleNotFoundError: No module named 'PyQt5'"**
```bash
# Install PyQt5 specifically:
uv add PyQt5

# Or try system package (Linux):
sudo apt install python3-pyqt5  # Ubuntu/Debian
sudo pacman -S python-pyqt5     # Arch Linux

# If using a venv outside uv, make sure it is activated:
source doom_env/bin/activate     # Linux/macOS
doom_env\Scripts\activate        # Windows
```

#### **"Permission denied" when running GZDoom**
```bash
# Make sure GZDoom is executable (Linux/macOS):
chmod +x /path/to/gzdoom

# Check if GZDoom is in your PATH:
which gzdoom
gzdoom --version
```

#### **"No such file or directory" for WAD files**
- Make sure your IWAD path is correct
- WAD files are case-sensitive on Linux/macOS
- Try absolute paths instead of relative paths
- Check file permissions

#### **Launcher crashes or freezes**
```bash
# Run a deterministic configuration validation:
uv run bfg --check-config

# Check system requirements:
python -c "import sys; print(sys.version)"
python -c "import PyQt5; print('PyQt5 OK')"

# Try performance mode:
BFG_ANIMATION_FPS=5 uv run bfg
```

#### **Animated skull not showing**
- Check if `assets/lost_soul.gif` exists
- Try disabling animated background in Config menu
- Update PyQt5: `uv add --dev PyQt5`

### **Getting Help**

If you're still having issues:

1. **Check the logs**: Look for error messages in the terminal
2. **Try minimal setup**: Use just IWAD, no PWADs or extra options
3. **Test GZDoom directly**: Make sure `gzdoom -iwad /path/to/doom.wad` works
4. **Update dependencies**: `uv add --upgrade package_name`
5. **Create an issue**: Include your OS, Python version, and error messages

---

## 📚 ADVANCED USAGE

### **Command Line Arguments**

```bash
# Validate config and exit (non-zero when invalid)
uv run bfg --config my_config.json --check-config

# Run with explicit launch intent (headless CLI flow)
uv run bfg --source-port /usr/games/gzdoom --iwad /games/doom/DOOM2.WAD \
  --pwad ~/.doom/mods/some_mod.wad --extra-options "-skill 4"

# Run performance test window
uv run bfg --performance-test

# Run without animations
uv run bfg --no-animations

# Headless mode (no Qt windows)
uv run bfg --no-gui --exit-after-launch
```

### **Environment Variables**

```bash
# Performance tuning
export BFG_ANIMATION_FPS=30
export BFG_CACHE_SIZE=100
export BFG_SCALING_QUALITY=smooth
export BFG_ANTIALIASING=true
export BFG_BACKGROUND_ANIM=false

# Debug options
export BFG_DEBUG=true
export BFG_LOG_LEVEL=DEBUG

# Custom paths
export BFG_CONFIG_DIR=~/.config/bfgpy
export BFG_CACHE_DIR=/tmp/bfg-cache
```

### **Configuration File**

The launcher creates a `config.json` file with your settings:

```json
{
  "source_port_path": "/usr/bin/gzdoom",
  "iwad_path": "/games/doom/DOOM2.WAD",
  "pwad_paths": [
    "/games/doom/mods/brutal_doom.pk3",
    "/games/doom/mods/beautiful_doom.pk3"
  ],
  "extra_options": "-skill 4 -fast",
  "animated_background": true,
  "performance_mode": false,
  "source_port_dir": "/usr/bin",
  "iwad_dir": "/games/doom",
  "pwad_dir": "/games/doom/mods"
}
```

### **Custom Themes**

You can modify `assets/nc_theme.qss` to customize the appearance:

```css
/* Change the main colors */
QWidget {
    background-color: #000080;  /* Background */
    color: #FFFF00;             /* Text */
}

/* Customize the launch button */
QPushButton#launchButton {
    background-color: #FF0000;  /* Red background */
    color: #FFFFFF;             /* White text */
    font-size: 16pt;            /* Bigger text */
}
```

### **Batch Operations**

```bash
# Install multiple mod packs
python -c "
from src.widgets.main_window import MainWindow
import sys
from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)
window = MainWindow()
# Add your batch operations here
"

# Export mod list
python -c "
import json
with open('config.json', 'r') as f:
    config = json.load(f)
    for mod in config.get('pwad_paths', []):
        print(mod)
"
```

### **Integration with Other Tools**

```bash
# Use with mod managers
uv run bfg --pwad-dir ~/.local/share/doom/mods

# Integration with Steam
uv run bfg --source-port ~/.steam/steam/steamapps/common/Doom/gzdoom

# Automated testing
uv run bfg --check-config
uv run bfg --no-gui --exit-after-launch
```

---

## 🎯 TIPS & TRICKS

### **Mod Management**
- **Load Order Matters**: Put gameplay mods before cosmetic mods
- **Check Compatibility**: Some mods conflict with each other
- **Use Mod Info**: Select mods to see their contents and size
- **Backup Saves**: Keep backups of your save games before trying new mods

### **Performance Tips**
- **Disable Animations**: Turn off animated background for older systems
- **Use Performance Mode**: Enable in Config menu for automatic optimizations
- **Close Other Apps**: Free up RAM and CPU for better performance
- **SSD Storage**: Keep WAD files on fast storage for quicker loading

### **Keyboard Shortcuts**
- **Enter**: Quick launch from Source Port field
- **Ctrl+O**: Open IWAD browser
- **Ctrl+Shift+O**: Open PWAD browser
- **Delete**: Remove selected PWADs
- **F5**: Refresh mod info

### **Advanced GZDoom Options**
```bash
# Common useful options for Extra Options field:
-skill 4              # Nightmare difficulty
-fast                 # Fast monsters
-respawn              # Respawning monsters
-nomonsters           # No monsters (for exploration)
-warp 1 1             # Start at specific level
-record demo1         # Record a demo
-playdemo demo1       # Play a demo
-loadgame save1       # Load specific save
```

---

## 🏆 CREDITS & ACKNOWLEDGMENTS

**BFG.py** was forged in the fires of hell by:

- **Ericson Willians** - Original creator and demon summoner
- **The DOOM Community** - For keeping the flame alive since 1993
- **id Software** - For creating the greatest game ever made
- **GZDoom Team** - For the amazing source port
- **Python Community** - For the tools to make this possible

### **Special Thanks**
- **John Carmack** - For open-sourcing DOOM
- **John Romero** - For the level design inspiration
- **Trent Reznor** - For the original DOOM soundtrack vibes
- **All Beta Testers** - Who survived the early crashes

---

## 📜 LICENSE

This project is licensed under the **MIT License** - see the LICENSE file for details.

**DOOM** is a trademark of id Software LLC. This launcher is not affiliated with or endorsed by id Software.

---

## 🔗 USEFUL LINKS

- **GZDoom**: https://zdoom.org/
- **DOOM Mods**: https://www.doomworld.com/idgames/
- **ModDB DOOM**: https://www.moddb.com/games/doom
- **Brutal DOOM**: https://www.moddb.com/mods/brutal-doom
- **Beautiful DOOM**: https://www.moddb.com/mods/beautiful-doom
- **DOOM Wiki**: https://doomwiki.org/

---

*"In the first age, in the first battle, when the shadows first lengthened, one stood. Burned by the embers of Armageddon, his soul blistered by the fires of Hell and tainted beyond ascension, he chose the path of perpetual torment. In his ravenous hatred he found no peace; and with boiling blood he scoured the Umbral Plains seeking vengeance against the dark lords who had wronged him. He wore the crown of the Night Sentinels, and those that tasted the bite of his sword named him... the Doom Slayer."*

**RIP AND TEAR, UNTIL IT IS DONE.**
