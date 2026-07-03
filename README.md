# BFG.py

![BFG.py screenshot](./assets/screenshot.png)

**BFG.py** is a cross-platform **Doom launcher** with a fast PyQt UI, robust config validation, improved process control, and a built-in idgames mod browser/search workflow.

- Platform: Windows / Linux / macOS
- Python: 3.9+
- Primary dependency: PyQt5
- Package name: `bfg-py` (version `3.0.0`)

---

## What this project does

- Configure and launch your Doom source port (typically GZDoom)
- Manage IWAD and PWAD ordering
- Store launch settings in a structured config (`schema_versioned` JSON)
- Run headless checks and launch flows from CI/scripting
- Discover and browse community maps from idgames and other sources
- Improve stability with validation, bounded logs, and deterministic launch states

---

## Folder structure (quick map)

- `main.py` — fallback entry script
- `bfg/cli.py` — CLI entry (`bfg` / `python -m bfg`)
- `src/` — application runtime, configuration, launch, parser, and widgets
  - `src/runtime.py` — runtime options + startup lifecycle
  - `src/config.py` — config schema, migration, validation
  - `src/launch_controller.py` — safe launch orchestration
  - `src/widgets/` — main UI components
- `assets/` — UI assets
- `config.json` — generated user config (created on first launch)
- `README.md` — this file

---

## Install (beginner-friendly)

You only need two tools: **Python** and **uv**.

### 1) Install Python

- Windows: install from [python.org](https://python.org)
- macOS: `brew install python`
- Linux: package manager (`python3`, `python3-pip`) or `brew` equivalent

### 2) Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or install from official docs if you prefer the installer for your OS.

### 3) Download the project

```bash
git clone https://github.com/yourname/bfgpy.git
cd bfgpy
```

### 4) Create environment and install dependencies

```bash
uv sync
```

If this command fails, verify you are inside the repository root (where `pyproject.toml` exists).

### 5) Run the app

```bash
uv run bfg
```

If `uv run bfg` doesn’t work in your setup, these are equivalent:

```bash
uv run python -m bfg
python -m bfg
```

---

## First-run setup (for casual users)

1. Click/choose **Source Port** and point it to `gzdoom`, `gzdoom.exe`, or your port executable.
2. Pick an **IWAD** (`doom.wad`, `doom2.wad`, etc.).
3. Add PWADs/PK3s to the list.
4. Press **Launch**.

If a field is empty or invalid, BFG.py now validates before launch and shows a clear error.

---

## Recommended run commands

Use one of these commands to avoid confusion:

- GUI launcher (normal):
  ```bash
  uv run bfg
  ```

- Print version:
  ```bash
  uv run bfg --version
  ```

- Validate config only:
  ```bash
  uv run bfg --check-config
  ```

- Validate and exit without starting GUI or game:
  ```bash
  uv run bfg --test-config
  ```

- Run a headless launch flow (CI / scripts):
  ```bash
  uv run bfg --no-gui --exit-after-launch
  ```

- Disable animation quickly for weaker PCs:
  ```bash
  uv run bfg --no-animations
  ```

- Performance smoke test:
  ```bash
  uv run bfg --performance-test
  ```

- Open Doom map browser quickly for PWAD discovery:
  ```bash
  uv run bfg --pwad-dir ~/.doom/mods
  ```

---

## Full CLI reference

`bfg` supports:

- `--config PATH`
- `--performance-test`
- `--no-animations`
- `--source-port PATH`
- `--iwad PATH`
- `--pwad-dir PATH`
- `--pwad PATH` (repeatable)
- `--extra-options "TEXT"`
- `--exit-after-launch`
- `--check-config`
- `--test-config`
- `--no-gui`
- `--version`

Examples:

```bash
# Explicit launch intent from shell
uv run bfg \
  --source-port /usr/games/gzdoom \
  --iwad /games/doom/doom2.wad \
  --pwad /games/doom/mods/ancient_doom.pk3 \
  --pwad /games/doom/mods/overhaul.pk3 \
  --extra-options "-skill 4 -nosound"

# Validation and diagnostics (CI-friendly)
uv run bfg --test-config --check-config
uv run bfg --no-gui --check-config
```

---

## Configuration file explained

The launcher now uses a versioned structure and keeps old flat keys for compatibility where possible.

### Current schema (abridged)

```json
{
  "schema_version": 3,
  "paths": {
    "source_port_path": "gzdoom",
    "iwad_path": "",
    "pwad_paths": [],
    "source_port_dir": "<folder>",
    "iwad_dir": "<folder>",
    "pwad_dir": "<folder>"
  },
  "ui": {
    "animated_background": false,
    "performance_mode": false,
    "render_profile": "high"
  },
  "performance": {
    "log_buffer_max_lines": 1000,
    "background_animation_enabled": true,
    "tile_cache_bytes": 4000000,
    "tile_cache_ttl": 120,
    "mod_cache_bytes": 4000000,
    "mod_cache_ttl": 86400,
    "mod_cache_entries": 500
  },
  "extra_options": "",
  "browser_sources": [
    { "id": "youfailit", "name": "...", "base": "https://youfailit.net/pub/idgames", "index": "fullsort.gz", "browser": "https://youfailit.net/pub/idgames", "parser": "fullsort", "enabled": true }
  ]
}
```

When you run with `--check-config`, BFG.py prints validation errors/warnings and exits with a non-zero code if invalid.

---

## Troubleshooting (most common)

### `ModuleNotFoundError: No module named 'PyQt5'`

```bash
uv add PyQt5
# or recreate environment with full deps:
uv sync
```

### “No such file” for source port / IWAD / PWAD

- Use absolute paths in the UI or CLI.
- Ensure executable bit is set for the source port on Linux/macOS:
  ```bash
  chmod +x /path/to/gzdoom
  ```
- Confirm the IWAD file exists and is a file, not a folder.

### App closes instantly with no UI

- Re-run with config check:
  ```bash
  uv run bfg --check-config
  ```
- Run `uv run bfg` from repo root where `pyproject.toml` exists.

### Bad search results or no matches

- Try a smaller query and fewer stop words.
- Enable more search mirrors in the Sources tab.
- Use direct filename fragments: `river`, `bloodr`, `tnt`.

### Want to disable heavy visuals

- Use `--no-animations` or uncheck animation/performance settings in the UI.

---

## Development helpers

- Layout check:
  ```bash
  uv run bfg-layout-check
  ```
- Contrast check:
  ```bash
  uv run bfg-contrast-check
  ```
- Performance test:
  ```bash
  uv run bfg-perf
  ```

---

## Notes for non-technical users

- If you only want to play now:
  1. Install dependencies, 2. Run `uv run bfg`, 3. Set Source Port + IWAD, 4. Click launch.
- You can always revert mistakes from the config UI; paths can be selected with built-in browse dialogs.
- Nothing is installed system-wide by `uv sync` beyond this project environment.

---

## License

MIT © 2026, see [LICENSE](LICENSE).

DOOM and DOOM-related marks are trademarks of their respective owners.
