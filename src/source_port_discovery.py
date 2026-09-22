from __future__ import annotations

import glob
import os
import shutil
import sys
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class SourcePortCandidate:
    name: str
    executable: str
    path: str
    source_hint: str = ""


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


KNOWN_SOURCE_PORTS = {
    "linux": [
        ("GZDoom", "gzdoom"),
        ("BiasedDoom", "biaseddoom"),
        ("BiasedDoom", "BiasedDoom"),
        ("Zandronum", "zandronum"),
        ("GZDoom SDL", "gzdoom-sdl2"),
        ("PrBoom+", "prboom-plus"),
    ],
    "windows": [
        ("GZDoom", "gzdoom"),
        ("BiasedDoom", "BiasedDoom"),
        ("Zandronum", "zandronum"),
        ("LZDOOM", "lzdoom"),
        ("GZDoom SDL", "gzdoom-sdl"),
    ],
}


# Linux-first search paths.
LINUX_SEARCH_PATHS = [
    "/usr/bin",
    "/usr/local/bin",
    "/usr/games",
    "/opt/gzdoom",
    "/opt/gzdoom/bin",
    str(Path.home() / ".local/bin"),
    str(Path.home() / "bin"),
    str(Path.home() / "Games" / "GZDoom"),
    str(Path.home() / ".steam" / "steam" / "steamapps" / "common" / "GZDoom"),
    str(Path.home() / ".steam" / "steam" / "steamapps" / "common" / "BiasedDoom"),
]


LINUX_APPIMAGE_PATTERNS = [
    str(Path.home() / "Applications" / "*gzdoom*.AppImage"),
    str(Path.home() / "Applications" / "*BiasedDoom*.AppImage"),
    "/opt/*gzdoom*.AppImage",
]


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(str(path), os.X_OK)
    except OSError:
        return False


def _as_resolved(path: Path) -> str:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path)


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if not value:
            continue
        try:
            resolved = Path(value).resolve().as_posix()
        except (OSError, RuntimeError):
            resolved = str(value)
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


# Memoized per-process: install roots do not change during a session, and
# these scans (PATH, well-known dirs, AppImage globs) run on the UI thread
# every time the configured port is edited.
@lru_cache(maxsize=None)
def discover_port_path_for_name(command: str, *, allow_appimages: bool = True) -> Optional[str]:
    candidates: List[str] = []
    command = (command or "").strip()
    if not command:
        return None

    # Direct PATH lookup first.
    path_result = shutil.which(command)
    if path_result:
        candidates.append(path_result)

    # Direct/expanded path.
    explicit = Path(command).expanduser()
    if explicit.exists() and _is_executable(explicit):
        candidates.append(_as_resolved(explicit))

    # Windows extension fallback.
    if os.name == "nt" and not command.lower().endswith(".exe"):
        windows_exe = explicit.with_suffix(".exe")
        if windows_exe.exists() and _is_executable(windows_exe):
            candidates.append(_as_resolved(windows_exe))

    # POSIX install roots (skipped on Windows to avoid wasted stat calls).
    if os.name != "nt":
        for base in LINUX_SEARCH_PATHS:
            base_path = Path(base)
            if not base_path.exists():
                continue
            candidate = base_path / command
            if candidate.exists() and _is_executable(candidate):
                candidates.append(_as_resolved(candidate))

    # AppImage scans (Linux only).
    if allow_appimages and _platform_key() == "linux":
        for pattern in LINUX_APPIMAGE_PATTERNS:
            for path in glob.glob(pattern):
                candidate = Path(path)
                if _is_executable(candidate):
                    candidates.append(_as_resolved(candidate))

    deduped = _dedupe(candidates)
    return deduped[0] if deduped else None


@lru_cache(maxsize=1)
def _discover_source_ports_cached() -> Tuple[SourcePortCandidate, ...]:
    return tuple(_discover_source_ports_uncached())


def discover_source_ports() -> List[SourcePortCandidate]:
    # Memoized per-process (see discover_port_path_for_name).
    return list(_discover_source_ports_cached())


def _discover_source_ports_uncached() -> List[SourcePortCandidate]:
    platform = _platform_key()
    preferred = KNOWN_SOURCE_PORTS.get(platform, KNOWN_SOURCE_PORTS["linux"])
    found: List[SourcePortCandidate] = []

    for name, command in preferred:
        resolved = discover_port_path_for_name(command)
        if not resolved:
            continue
        path_obj = Path(resolved)
        if not _is_executable(path_obj):
            continue
        found.append(
            SourcePortCandidate(
                name=name,
                executable=command,
                path=_as_resolved(path_obj),
                source_hint="system-search",
            )
        )

    return _dedupe_candidates(found)


def known_aliases() -> List[str]:
    aliases = set()
    for _, command in KNOWN_SOURCE_PORTS.get("linux", []) + KNOWN_SOURCE_PORTS.get("windows", []):
        aliases.add(command)
        aliases.add(command.lower())
    return sorted(aliases)


def find_best_match(command: str) -> Optional[Tuple[str, str]]:
    normalized = str(command or "").strip()
    if not normalized:
        # No engine requested and none configured: nothing to match against.
        # (Callers that want "any installed port" use discover_source_ports.)
        return None

    aliases = {alias.lower() for alias in known_aliases()}
    if normalized.lower() not in aliases:
        direct = Path(normalized).expanduser()
        if direct.exists() and _is_executable(direct):
            return normalized, _as_resolved(direct)
        return None

    direct_path = discover_port_path_for_name(normalized)
    if direct_path:
        return normalized, direct_path

    # Never substitute a different engine for a configured alias: silently
    # launching e.g. GZDoom for a Zandronum netplay config breaks the session
    # and must not overwrite the user's setting. Report "not found" instead.
    return None


def clear_discovery_caches() -> None:
    """Drop memoized discovery results (e.g. after installing a new port)."""
    discover_port_path_for_name.cache_clear()
    _discover_source_ports_cached.cache_clear()


def can_auto_detect(command: str) -> bool:
    normalized = (command or "").strip()
    if not normalized:
        return True
    return normalized.lower() in {alias.lower() for alias in known_aliases()}


def _dedupe_candidates(values: List[SourcePortCandidate]) -> List[SourcePortCandidate]:
    seen = set()
    out = []
    for item in values:
        if item.path in seen:
            continue
        seen.add(item.path)
        out.append(item)
    return out
