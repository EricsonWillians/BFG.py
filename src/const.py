from pathlib import Path

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 820
MAIN_WINDOW_TITLE = '💀 BFG.py 💀'

# Project root (directory containing main.py / assets/), derived from this
# file's location so the app works regardless of the process CWD.
BASE_DIR = Path(__file__).resolve().parent.parent


def asset_path(relative: str) -> str:
    """Resolve an asset path relative to the project root."""
    return str(BASE_DIR / relative)


DEFAULT_CONFIG_PATH = str(BASE_DIR / "config.json")
