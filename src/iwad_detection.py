from __future__ import annotations

import os
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class DetectedIWad:
    path: str
    filename: str
    game: str
    search_root: str


_KNOWN_IWADS: Sequence[Tuple[str, str]] = (
    ("doom2.wad", "DOOM II"),
    ("doom.wad", "DOOM"),
    ("plutonia.wad", "Final DOOM: Plutonia"),
    ("tnt.wad", "Final DOOM: TNT"),
    ("freedoom2.wad", "Freedoom: Phase 2"),
    ("freedoom1.wad", "Freedoom: Phase 1"),
    ("freedm.wad", "FreeDM"),
    ("heretic.wad", "Heretic"),
    ("hexen.wad", "Hexen"),
    ("hexdd.wad", "Hexen: Deathkings"),
    ("strife1.wad", "Strife"),
    ("chex3.wad", "Chex Quest 3"),
    ("hacx.wad", "Hacx"),
)

_KNOWN_IWAD_NAMES = {filename for filename, _ in _KNOWN_IWADS}
_DIRECT_CHILD_DIRS: Sequence[str] = (
    "",
    "base",
    "iwads",
    "wads",
    "wad",
    "data",
    "doom",
    "doom2",
    "games/doom",
    "share/doom",
    "share/games/doom",
)
_STEAM_ROOT_HINTS: Sequence[str] = (
    ".steam/steam/steamapps/common",
    ".local/share/Steam/steamapps/common",
    "Library/Application Support/Steam/steamapps/common",
)
_SYSTEM_IWAD_DIRS: Sequence[str] = (
    "/usr/share/games/doom",
    "/usr/local/share/games/doom",
    "/usr/share/doom",
    "/usr/local/share/doom",
    "/opt/doom",
)


def _normalize_dir(value: str) -> Optional[Path]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if candidate.is_file():
        return candidate.parent.resolve()
    if candidate.is_dir():
        return candidate.resolve()
    return None


def _dedupe_dirs(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _doomsday_env_dirs() -> List[Path]:
    out: List[Path] = []
    for env_key in ("DOOMWADDIR",):
        candidate = _normalize_dir(os.getenv(env_key, ""))
        if candidate is not None:
            out.append(candidate)

    path_value = os.getenv("DOOMWADPATH", "")
    if path_value:
        for entry in path_value.split(os.pathsep):
            candidate = _normalize_dir(entry)
            if candidate is not None:
                out.append(candidate)
    return out


def _steam_library_roots() -> List[Path]:
    roots: List[Path] = []
    home = Path.home()
    for suffix in _STEAM_ROOT_HINTS:
        candidate = home / suffix
        if candidate.is_dir():
            roots.append(candidate.resolve())

    if os.name == "nt":
        for env_key in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            base = os.getenv(env_key, "")
            if not base:
                continue
            candidate = Path(base) / "Steam" / "steamapps" / "common"
            if candidate.is_dir():
                roots.append(candidate.resolve())
    return _dedupe_dirs(roots)


def _common_search_roots(
    source_port_path: str = "",
    iwad_dir: str = "",
    extra_dirs: Optional[Iterable[str]] = None,
) -> Tuple[List[Path], List[Path]]:
    direct_roots: List[Path] = []
    recursive_roots: List[Path] = []

    source_dir = _normalize_dir(source_port_path)
    if source_dir is not None:
        direct_roots.append(source_dir)
        direct_roots.append(source_dir.parent)

    explicit_iwad_dir = _normalize_dir(iwad_dir)
    if explicit_iwad_dir is not None:
        direct_roots.append(explicit_iwad_dir)

    if extra_dirs:
        for entry in extra_dirs:
            candidate = _normalize_dir(str(entry))
            if candidate is not None:
                direct_roots.append(candidate)

    cwd = _normalize_dir(os.getcwd())
    if cwd is not None:
        direct_roots.append(cwd)

    home = Path.home()
    for suffix in ("", "doom", "games/doom", ".config/gzdoom", ".local/share/gzdoom"):
        candidate = _normalize_dir(str(home / suffix))
        if candidate is not None:
            direct_roots.append(candidate)

    direct_roots.extend(_doomsday_env_dirs())

    if os.name != "nt":  # POSIX-only system dirs
        for raw in _SYSTEM_IWAD_DIRS:
            candidate = _normalize_dir(raw)
            if candidate is not None:
                direct_roots.append(candidate)

    recursive_roots.extend(_steam_library_roots())
    return _dedupe_dirs(direct_roots), _dedupe_dirs(recursive_roots)


def _search_direct_root(root: Path, root_rank: int) -> List[Tuple[Tuple[int, int, int], DetectedIWad]]:
    out: List[Tuple[Tuple[int, int, int], DetectedIWad]] = []
    for depth, child in enumerate(_DIRECT_CHILD_DIRS):
        candidate_dir = (root / child) if child else root
        if not candidate_dir.is_dir():
            continue
        try:
            names = {entry.name.lower(): entry for entry in candidate_dir.iterdir() if entry.is_file()}
        except OSError:
            continue

        for filename_rank, (filename, game) in enumerate(_KNOWN_IWADS):
            if filename not in names:
                continue
            entry = names[filename]
            out.append(
                (
                    (root_rank, depth, filename_rank),
                    DetectedIWad(
                        path=str(entry.resolve()),
                        filename=filename,
                        game=game,
                        search_root=str(root),
                    ),
                )
            )
    return out


# Memoized per-process: this walks Steam library roots (top levels of every
# installed game) and runs on the UI thread at startup and on every
# source-port change. Direct-root scans stay uncached so IWADs appearing in
# well-known dirs mid-session are still picked up.
@lru_cache(maxsize=None)
def _search_recursive_root(root: Path, root_rank: int, max_depth: int = 2) -> List[Tuple[Tuple[int, int, int], DetectedIWad]]:
    out: List[Tuple[Tuple[int, int, int], DetectedIWad]] = []
    if not root.is_dir():
        return out

    try:
        root_parts = len(root.parts)
        for current_root, dirnames, filenames in os.walk(root):
            current = Path(current_root)
            depth = len(current.parts) - root_parts
            if depth > max_depth:
                dirnames[:] = []
                continue

            filenames_by_lower = {name.lower(): name for name in filenames}
            for filename_rank, (filename, game) in enumerate(_KNOWN_IWADS):
                actual_name = filenames_by_lower.get(filename)
                if not actual_name:
                    continue
                entry = current / actual_name
                if not entry.is_file():
                    continue
                out.append(
                    (
                        (root_rank, depth + 10, filename_rank),
                        DetectedIWad(
                            path=str(entry.resolve()),
                            filename=filename,
                            game=game,
                            search_root=str(root),
                        ),
                    )
                )
    except OSError:
        return out
    return out


def detect_iwad(
    *,
    source_port_path: str = "",
    iwad_path: str = "",
    iwad_dir: str = "",
    extra_dirs: Optional[Iterable[str]] = None,
) -> Optional[DetectedIWad]:
    existing = Path(str(iwad_path or "").strip()).expanduser()
    if existing.is_file() and existing.name.lower() in _KNOWN_IWAD_NAMES:
        return DetectedIWad(
            path=str(existing.resolve()),
            filename=existing.name.lower(),
            game=next((game for name, game in _KNOWN_IWADS if name == existing.name.lower()), existing.stem),
            search_root=str(existing.parent.resolve()),
        )

    direct_roots, recursive_roots = _common_search_roots(
        source_port_path=source_port_path,
        iwad_dir=iwad_dir,
        extra_dirs=extra_dirs,
    )

    scored: List[Tuple[Tuple[int, int, int], DetectedIWad]] = []
    for idx, root in enumerate(direct_roots):
        scored.extend(_search_direct_root(root, idx))

    base_rank = len(direct_roots)
    for idx, root in enumerate(recursive_roots):
        scored.extend(_search_recursive_root(root, base_rank + idx))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0])
    return scored[0][1]
