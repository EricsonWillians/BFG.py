import os
import shutil
from pathlib import Path

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 820
MAIN_WINDOW_TITLE = '💀 BFG.py 💀'

# Project root (directory containing main.py / assets/), derived from this
# file's location so the app works regardless of the process CWD.
BASE_DIR = Path(__file__).resolve().parent.parent

# Legacy repo-local config location. No longer written to by default: when
# installed as a wheel, BASE_DIR lives inside site-packages (read-only or
# wiped on upgrade). Migrated once into the per-user location on first run.
LEGACY_CONFIG_PATH = BASE_DIR / "config.json"


def asset_path(relative: str) -> str:
    """Resolve an asset path relative to the project root."""
    return str(BASE_DIR / relative)


def _default_config_path() -> str:
    """Per-user config location: $BFG_CONFIG_PATH > XDG/APPDATA dir."""
    override = os.getenv("BFG_CONFIG_PATH")
    if override:
        return str(Path(override).expanduser())
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return str(base / "bfg.py" / "config.json")


DEFAULT_CONFIG_PATH = _default_config_path()


def migrate_legacy_config(target: str = DEFAULT_CONFIG_PATH) -> bool:
    """Copy the legacy repo-local config into the per-user location, once.

    Never deletes the legacy file and never overwrites an existing target.
    Returns True when a migration was performed.
    """
    target_path = Path(target).expanduser()
    if target_path.exists() or not LEGACY_CONFIG_PATH.exists():
        return False
    try:
        if target_path.resolve() == LEGACY_CONFIG_PATH.resolve():
            return False
    except OSError:
        pass
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_CONFIG_PATH, target_path)
        return True
    except OSError:
        return False
