from __future__ import annotations

import json
import logging
import os
import shutil
import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.source_port_discovery import can_auto_detect, find_best_match


logger = logging.getLogger(__name__)

DEFAULT_CACHE_ENTRIES = 300
SCHEMA_VERSION = 3


@dataclass
class BrowserSourceConfig:
    source_id: str
    name: str
    base: str
    index: str
    browser: str
    parser: str = "fullsort"
    enabled: bool = True
    status: str = "unknown"
    status_message: str = ""
    status_checked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.source_id,
            "name": self.name,
            "base": self.base,
            "index": self.index,
            "browser": self.browser,
            "parser": self.parser,
            "enabled": self.enabled,
            "status": self.status,
            "status_message": self.status_message,
            "status_checked_at": self.status_checked_at,
        }


DEFAULT_BROWSER_SOURCES: List[Dict[str, Any]] = [
    {
        "id": "youfailit",
        "name": "Doomworld / idgames mirror (youfailit)",
        "base": "https://youfailit.net/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://youfailit.net/pub/idgames",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "cyberd",
        "name": "Doomworld / idgames mirror (idgames.cyberd.org)",
        "base": "https://idgames.cyberd.org",
        "index": "fullsort.gz",
        "browser": "https://idgames.cyberd.org/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "gamers",
        "name": "Gamers.org mirror",
        "base": "https://www.gamers.org/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://www.gamers.org/pub/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "braindrain",
        "name": "BraindrainLAN mirror",
        "base": "https://mirror.braindrainlan.nu/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://mirror.braindrainlan.nu/pub/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "fu-berlin",
        "name": "FU Berlin mirror",
        "base": "https://ftp.fu-berlin.de/pc/games/idgames",
        "index": "fullsort.gz",
        "browser": "https://ftp.fu-berlin.de/pc/games/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "lethe",
        "name": "Lethe Chinstrap mirror",
        "base": "https://lethe.chinstrap.org/idgames",
        "index": "fullsort.gz",
        "browser": "https://lethe.chinstrap.org/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "lug-mtu",
        "name": "LUG/MTU mirror",
        "base": "https://mirrors.lug.mtu.edu/idgames",
        "index": "fullsort.gz",
        "browser": "https://mirrors.lug.mtu.edu/idgames",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "doomgate",
        "name": "Doomgate mirror",
        "base": "https://doomgate.gamers.org/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://doomgate.gamers.org/pub/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "ftpmirror-infania",
        "name": "FTP Mirror Infania",
        "base": "https://ftpmirror.infania.net/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://ftpmirror.infania.net/pub/idgames/",
        "parser": "fullsort",
        "enabled": True,
    },
    {
        "id": "doomworld-official",
        "name": "Doomworld / idgames (official - likely blocked)",
        "base": "https://www.doomworld.com/idgames",
        "index": "",
        "browser": "https://www.doomworld.com/idgames",
        "parser": "html",
        "enabled": True,
    },
    {
        "id": "doomworld-api",
        "name": "Doomworld / idgames API (search)",
        "base": "https://www.doomworld.com/idgames",
        "index": "api.php",
        "browser": "https://www.doomworld.com/idgames",
        "parser": "idgames_api",
        "enabled": True,
    },
]


def _normalize_source_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw).strip().lower()).strip("_")


def _normalize_browser_parser(raw: str) -> str:
    parser = str(raw).strip().lower()
    parser = parser.replace("-", "_").replace(" ", "_")
    if not parser:
        return "fullsort"

    parser_aliases = {
        "text": "text",
        "txt": "text",
        "api": "idgames_api",
        "idgamesapi": "idgames_api",
        "id_api": "idgames_api",
        "idgames": "idgames_api",
        "crawl": "html",
        "webcrawl": "html",
        "web_crawl": "html",
        "crawler": "html",
        "json": "json",
        "jsn": "json",
        "js": "json",
        "rss": "rss",
        "xml": "rss",
        "atom": "rss",
        "html": "html",
        "fullsort": "fullsort",
        "auto": "auto",
    }
    if parser in parser_aliases:
        return parser_aliases[parser]
    return parser if parser in {"fullsort", "html", "auto", "idgames_api", "json", "rss", "text"} else "fullsort"


def _coerce_source_status(raw: Any) -> str:
    status = str(raw).strip().lower()
    if not status:
        return "unknown"
    return status if status in {"unknown", "checking", "cached", "ok", "unreachable", "error", "disabled"} else "unknown"


def _coerce_browser_source_entry(value: Any) -> Optional[BrowserSourceConfig]:
    if not isinstance(value, dict):
        return None

    base = str(value.get("base", "")).strip()
    if base:
        if not (base.startswith("http://") or base.startswith("https://")):
            base = f"https://{base}"
        base = base.rstrip("/")

    name = str(value.get("name", "")).strip() or _normalize_source_id(base) or "custom-source"
    index = str(value.get("index", "fullsort.gz")).strip() or "fullsort.gz"
    browser = str(value.get("browser", base)).strip().rstrip("/") or base
    source_id = _normalize_source_id(str(value.get("id", name)))
    if not source_id:
        source_id = _normalize_source_id(base) or _normalize_source_id(name) or "source"
    return BrowserSourceConfig(
        source_id=source_id,
        name=name,
        base=base,
        index=index,
        browser=browser,
        parser=_normalize_browser_parser(value.get("parser", "fullsort")),
        enabled=bool(value.get("enabled", True)),
        status=_coerce_source_status(value.get("status", "unknown")),
        status_message=str(value.get("status_message", "")).strip(),
        status_checked_at=_coerce_float(value.get("status_checked_at", 0.0), default=0.0),
    )


def _coerce_browser_sources(raw: Any) -> List[BrowserSourceConfig]:
    entries = _coerce_list(raw)
    if not entries:
        return [_coerce_browser_source_entry(src) for src in DEFAULT_BROWSER_SOURCES if _coerce_browser_source_entry(src) is not None]

    out: List[BrowserSourceConfig] = []
    seen = set()
    for payload in entries:
        cfg = _coerce_browser_source_entry(payload)
        if not cfg:
            continue
        if not cfg.base:
            continue
        if cfg.source_id in seen:
            continue
        seen.add(cfg.source_id)
        out.append(cfg)

    if out:
        return out
    return [
        item
        for item in [
            _coerce_browser_source_entry(src)
            for src in DEFAULT_BROWSER_SOURCES
        ]
        if item is not None
    ]


def _source_list_to_dict(sources: List[BrowserSourceConfig]) -> List[Dict[str, Any]]:
    return [item.to_dict() for item in sources]


def _default_browser_sources() -> List[BrowserSourceConfig]:
    out: List[BrowserSourceConfig] = []
    for item in _coerce_browser_sources(DEFAULT_BROWSER_SOURCES):
        if item:
            out.append(item)
    return out


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        parts = []
        if self.errors:
            parts.append("Errors: " + "; ".join(self.errors))
        if self.warnings:
            parts.append("Warnings: " + "; ".join(self.warnings))
        return " | ".join(parts)


@dataclass
class PathsConfig:
    source_port_path: str = "gzdoom"
    iwad_path: str = ""
    pwad_paths: List[str] = field(default_factory=list)
    source_port_dir: str = field(default_factory=lambda: str(Path.home()))
    iwad_dir: str = field(default_factory=lambda: str(Path.home()))
    pwad_dir: str = field(default_factory=lambda: str(Path.home()))


@dataclass
class UIConfig:
    animated_background: bool = False
    performance_mode: bool = False
    render_profile: str = "high"


@dataclass
class PerformanceConfig:
    log_buffer_max_lines: int = 1000
    background_animation_enabled: bool = True
    tile_cache_bytes: int = 4_000_000
    tile_cache_ttl: int = 120
    mod_cache_bytes: int = 4_000_000
    mod_cache_ttl: int = 24 * 60 * 60
    mod_cache_entries: int = 500


@dataclass
class LauncherConfig:
    schema_version: int = SCHEMA_VERSION
    paths: PathsConfig = field(default_factory=PathsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    extra_options: str = ""
    browser_sources: List[BrowserSourceConfig] = field(default_factory=_default_browser_sources)

    # Legacy flat-key compatibility map (legacy name -> new name).
    _legacy_keys = {
        "lastSourcePort": "source_port_path",
        "lastIWad": "iwad_path",
        "lastPWads": "pwad_paths",
        "lastOptions": "extra_options",
        "sourcePortDir": "source_port_dir",
        "iwadDir": "iwad_dir",
        "pwadDir": "pwad_dir",
        "animatedBackground": "animated_background",
        "performanceMode": "performance_mode",
        "browserSources": "browser_sources",
    }
    # Reverse map (new name -> legacy name) for legacy lookups during load.
    _new_to_legacy_keys = {new: legacy for legacy, new in _legacy_keys.items()}

    @property
    def source_port_path(self) -> str:
        return self.paths.source_port_path

    @source_port_path.setter
    def source_port_path(self, value: str) -> None:
        self.paths.source_port_path = _coerce_str(value, default="gzdoom")

    @property
    def iwad_path(self) -> str:
        return self.paths.iwad_path

    @iwad_path.setter
    def iwad_path(self, value: str) -> None:
        self.paths.iwad_path = _coerce_str(value)

    @property
    def pwad_paths(self) -> List[str]:
        return self.paths.pwad_paths

    @pwad_paths.setter
    def pwad_paths(self, value: List[str]) -> None:
        self.paths.pwad_paths = _coerce_str_list(value)

    @property
    def source_port_dir(self) -> str:
        return self.paths.source_port_dir

    @source_port_dir.setter
    def source_port_dir(self, value: str) -> None:
        self.paths.source_port_dir = _coerce_str(value, default=str(Path.home()))

    @property
    def iwad_dir(self) -> str:
        return self.paths.iwad_dir

    @iwad_dir.setter
    def iwad_dir(self, value: str) -> None:
        self.paths.iwad_dir = _coerce_str(value, default=str(Path.home()))

    @property
    def pwad_dir(self) -> str:
        return self.paths.pwad_dir

    @pwad_dir.setter
    def pwad_dir(self, value: str) -> None:
        self.paths.pwad_dir = _coerce_str(value, default=str(Path.home()))

    @property
    def animated_background(self) -> bool:
        return self.ui.animated_background

    @animated_background.setter
    def animated_background(self, value: bool) -> None:
        self.ui.animated_background = bool(value)

    @property
    def performance_mode(self) -> bool:
        return self.ui.performance_mode

    @performance_mode.setter
    def performance_mode(self, value: bool) -> None:
        self.ui.performance_mode = bool(value)

    @property
    def render_profile(self) -> str:
        return self.ui.render_profile

    @render_profile.setter
    def render_profile(self, value: str) -> None:
        self.ui.render_profile = str(value)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "LauncherConfig":
        return ConfigStore(path).load()

    def save(self, path: Union[str, Path], *, atomic: bool = True, backup_count: int = 2) -> None:
        ConfigStore(path).save(self, atomic=atomic, backup_count=backup_count)

    @classmethod
    def from_raw(cls, data: Optional[Dict[str, Any]]) -> "LauncherConfig":
        if not isinstance(data, dict):
            return cls()

        schema_version = _coerce_int(data.get("schema_version", 1) or 1, default=1)
        raw_paths = _coerce_dict(data.get("paths"))
        raw_ui = _coerce_dict(data.get("ui"))
        raw_perf = _coerce_dict(data.get("performance"))

        def legacy_lookup(key: str, fallback: Any = None) -> Any:
            if key in data:
                return data[key]
            legacy = cls._new_to_legacy_keys.get(key)
            if legacy and legacy in data:
                return data[legacy]
            return fallback

        paths = PathsConfig(
            source_port_path=_coerce_str(
                legacy_lookup("source_port_path", data.get("lastSourcePort"))
            ),
            iwad_path=_coerce_str(legacy_lookup("iwad_path", data.get("lastIWad"))),
            pwad_paths=_coerce_str_list(legacy_lookup("pwad_paths", data.get("lastPWads", []))),
            source_port_dir=_coerce_str(
                raw_paths.get("source_port_dir", legacy_lookup("source_port_dir", None)),
                default=str(Path.home()),
            ),
            iwad_dir=_coerce_str(
                raw_paths.get("iwad_dir", legacy_lookup("iwad_dir", None)),
                default=str(Path.home()),
            ),
            pwad_dir=_coerce_str(
                raw_paths.get("pwad_dir", legacy_lookup("pwad_dir", None)),
                default=str(Path.home()),
            ),
        )

        if not paths.source_port_path:
            paths.source_port_path = "gzdoom"

        # Explicit v3 nested blocks take precedence over legacy/top-level keys.
        if "source_port_path" in raw_paths:
            paths.source_port_path = _coerce_str(raw_paths.get("source_port_path", "gzdoom"))
        if "iwad_path" in raw_paths:
            paths.iwad_path = _coerce_str(raw_paths.get("iwad_path"))
        if "pwad_paths" in raw_paths:
            paths.pwad_paths = _coerce_str_list(raw_paths.get("pwad_paths", []))
        if "source_port_dir" in raw_paths:
            paths.source_port_dir = _coerce_str(raw_paths.get("source_port_dir"), default=str(Path.home()))
        if "iwad_dir" in raw_paths:
            paths.iwad_dir = _coerce_str(raw_paths.get("iwad_dir"), default=str(Path.home()))
        if "pwad_dir" in raw_paths:
            paths.pwad_dir = _coerce_str(raw_paths.get("pwad_dir"), default=str(Path.home()))

        ui = UIConfig(
            animated_background=bool(raw_ui.get("animated_background", legacy_lookup("animated_background", False))),
            performance_mode=bool(raw_ui.get("performance_mode", legacy_lookup("performance_mode", False))),
            render_profile=_coerce_str(
                raw_ui.get("render_profile", legacy_lookup("render_profile", "high")),
                default="high",
            ),
        )

        performance = PerformanceConfig(
            log_buffer_max_lines=_coerce_int(
                raw_perf.get("log_buffer_max_lines", 1000), default=1000
            ),
            background_animation_enabled=bool(
                raw_perf.get("background_animation_enabled", legacy_lookup("animated_background", True))
            ),
            tile_cache_bytes=_coerce_int(raw_perf.get("tile_cache_bytes", 4_000_000), default=4_000_000),
            tile_cache_ttl=_coerce_int(raw_perf.get("tile_cache_ttl", 120), default=120),
            mod_cache_bytes=_coerce_int(raw_perf.get("mod_cache_bytes", 4_000_000), default=4_000_000),
            mod_cache_ttl=_coerce_int(raw_perf.get("mod_cache_ttl", 24 * 60 * 60), default=24 * 60 * 60),
            mod_cache_entries=_coerce_int(raw_perf.get("mod_cache_entries", 500), default=500),
        )

        browser_sources = _coerce_browser_sources(
            data.get("browser_sources", data.get("browserSources"))
        )
        return cls(
            schema_version=max(schema_version, SCHEMA_VERSION),
            paths=paths,
            ui=ui,
            performance=performance,
            extra_options=_coerce_str(
                data.get("extra_options", data.get("lastOptions", "")),
                default="",
            ),
            browser_sources=browser_sources,
        )

    def normalized(self) -> "LauncherConfig":
        cfg = LauncherConfig.from_raw(self.to_dict())
        cfg.paths.source_port_path = _normalize_command_path(cfg.paths.source_port_path)
        cfg.paths.iwad_path = str(_normalize_path(cfg.paths.iwad_path)) if cfg.paths.iwad_path else ""
        cfg.paths.source_port_dir = str(_normalize_path(cfg.paths.source_port_dir))
        cfg.paths.iwad_dir = str(_normalize_path(cfg.paths.iwad_dir))
        cfg.paths.pwad_dir = str(_normalize_path(cfg.paths.pwad_dir))
        cfg.paths.pwad_paths = _normalize_wad_list(cfg.paths.pwad_paths)
        cfg.browser_sources = _coerce_browser_sources([entry.to_dict() for entry in cfg.browser_sources])

        if cfg.ui.render_profile not in {"high", "low"}:
            cfg.ui.render_profile = "high"

        if cfg.ui.performance_mode:
            cfg.ui.render_profile = "low"

        cfg.extra_options = cfg.extra_options.strip()
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "paths": {
                "source_port_path": self.paths.source_port_path,
                "iwad_path": self.paths.iwad_path,
                "pwad_paths": self.paths.pwad_paths,
                "source_port_dir": self.paths.source_port_dir,
                "iwad_dir": self.paths.iwad_dir,
                "pwad_dir": self.paths.pwad_dir,
            },
            "ui": {
                "animated_background": self.ui.animated_background,
                "performance_mode": self.ui.performance_mode,
                "render_profile": self.ui.render_profile,
            },
            "performance": {
                "log_buffer_max_lines": self.performance.log_buffer_max_lines,
                "background_animation_enabled": self.performance.background_animation_enabled,
                "tile_cache_bytes": self.performance.tile_cache_bytes,
                "tile_cache_ttl": self.performance.tile_cache_ttl,
                "mod_cache_bytes": self.performance.mod_cache_bytes,
                "mod_cache_ttl": self.performance.mod_cache_ttl,
                "mod_cache_entries": self.performance.mod_cache_entries,
            },
            "extra_options": self.extra_options,
            "lastOptions": self.extra_options,
            "lastSourcePort": self.paths.source_port_path,
            "lastIWad": self.paths.iwad_path,
            "lastPWads": list(self.paths.pwad_paths),
            "browser_sources": _source_list_to_dict(self.browser_sources),
            "sourcePortDir": self.paths.source_port_dir,
            "iwadDir": self.paths.iwad_dir,
            "pwadDir": self.paths.pwad_dir,
            "animatedBackground": self.ui.animated_background,
            "performanceMode": self.ui.performance_mode,
        }

    def validate(self, strict: bool = True) -> ValidationResult:
        result = ValidationResult()

        source_port = _coerce_str(self.paths.source_port_path, default="").strip()
        if not source_port:
            result.is_valid = False
            result.errors.append("No source port configured.")
        elif not _has_executable(source_port):
            best_match = find_best_match(source_port) if can_auto_detect(source_port) else None
            if best_match:
                result.warnings.append(
                    "Source port not found or not executable: "
                    f"{source_port}. Discovered installed port: {best_match[1]}"
                )
            else:
                result.is_valid = False
                result.errors.append(f"Source port not found or not executable: {source_port}")

        if self.paths.iwad_path:
            iwad = _normalize_path(self.paths.iwad_path)
            if not iwad.exists():
                result.is_valid = False
                result.errors.append(f"IWAD does not exist: {self.paths.iwad_path}")
            elif not iwad.is_file():
                result.is_valid = False
                result.errors.append(f"IWAD is not a file: {self.paths.iwad_path}")
        elif strict:
            result.warnings.append("No IWAD selected; relying on source-port default resolution.")

        for path in self.paths.pwad_paths:
            wad = _normalize_path(path)
            if not wad.exists():
                result.is_valid = False
                result.errors.append(f"Mod does not exist: {path}")
            elif not wad.is_file():
                result.is_valid = False
                result.errors.append(f"Mod is not a file: {path}")

        if self.performance.tile_cache_bytes < 256_000:
            result.warnings.append("tile_cache_bytes is very small; frame caching may stutter.")

        return result

    def get(self, key: str, default: Any = None) -> Any:
        for namespace in (self.paths.__dict__, self.ui.__dict__, self.performance.__dict__, {"extra_options": self.extra_options}):
            if key in namespace:
                return namespace[key]
        if key in self._legacy_keys:
            mapped = self._legacy_keys[key]
            return self.get(mapped, default)
        if key in self.to_dict():
            return self.to_dict()[key]
        return default

    _COERCED_PROPERTY_KEYS = frozenset({
        "source_port_path", "iwad_path", "pwad_paths",
        "source_port_dir", "iwad_dir", "pwad_dir",
        "animated_background", "performance_mode", "render_profile",
    })

    def set(self, key: str, value: Any):
        if key in self._legacy_keys:
            self.set(self._legacy_keys[key], value)
            return
        if key in self._COERCED_PROPERTY_KEYS:
            # Route through the LauncherConfig property setters so values are
            # coerced (e.g. strings -> lists) instead of stored raw.
            setattr(self, key, value)
        elif hasattr(self.performance, key):
            setattr(self.performance, key, value)
        elif key == "extra_options":
            self.extra_options = _coerce_str(value, default="")
        elif key == "browser_sources" or key == "browserSources":
            self.browser_sources = _coerce_browser_sources(value)


class ConfigStore:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def load(self) -> LauncherConfig:
        if not self.path.exists():
            return LauncherConfig().normalized()

        try:
            with self.path.open("r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except (OSError, json.JSONDecodeError, ValueError):
            return LauncherConfig().normalized()

        try:
            return LauncherConfig.from_raw(raw).normalized()
        except Exception as exc:  # defensive: never crash on a malformed config
            logger.warning("Ignoring malformed config %s: %s", self.path, exc)
            return LauncherConfig().normalized()

    def save(
        self,
        config: LauncherConfig,
        *,
        atomic: bool = True,
        backup_count: int = 2,
    ) -> None:
        payload = config.normalized().to_dict()
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

        data = json.dumps(payload, indent=2)
        if atomic:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fp:
                fp.write(data)
            if self.path.exists():
                _make_backups(self.path, backup_count)
            os.replace(tmp, self.path)
        else:
            with self.path.open("w", encoding="utf-8") as fp:
                fp.write(data)


def _make_backups(path: Path, keep: int) -> None:
    if keep <= 0:
        return

    # Millisecond resolution plus a uniqueness bump avoids two rapid saves
    # collapsing onto the same backup name and losing a generation.
    timestamp = int(time.time() * 1000)
    backup = path.with_suffix(path.suffix + f".{timestamp}.bak")
    while backup.exists():
        timestamp += 1
        backup = path.with_suffix(path.suffix + f".{timestamp}.bak")
    try:
        if path.exists():
            os.replace(path, backup)
    except OSError:
        return

    backups = sorted(path.parent.glob(f"{path.name}.*.bak"))
    while len(backups) > keep:
        old = backups.pop(0)
        try:
            old.unlink()
        except OSError:
            pass


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text if text else default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        value_int = int(value)
        return value_int
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = _coerce_str(item)
        if not text:
            continue
        text = str(Path(text).expanduser())
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_path(path: str) -> Path:
    text = _coerce_str(path, default="")
    if not text:
        return Path()

    candidate = Path(text).expanduser()
    try:
        candidate = candidate.resolve()
    except (OSError, RuntimeError):
        candidate = candidate.expanduser().absolute()

    return candidate


def _normalize_command_path(path: str) -> str:
    text = _coerce_str(path, default="").strip()
    if not text:
        return "gzdoom"

    candidate = Path(text).expanduser()
    if candidate.exists():
        if candidate.is_file():
            try:
                return str(candidate.resolve())
            except (OSError, RuntimeError):
                return text

        if os.name != "nt" and candidate.suffix.lower() == ".app":
            bundle_binary = _resolve_macos_app_binary(candidate)
            if bundle_binary:
                try:
                    return str(bundle_binary.resolve())
                except (OSError, RuntimeError):
                    return str(bundle_binary)

        try:
            return str(candidate.resolve())
        except (OSError, RuntimeError):
            return text

    if os.name == "nt" and candidate.suffix.lower() != ".exe":
        exe = candidate.with_suffix(".exe")
        if exe.exists():
            return str(exe)

    return text


def _normalize_wad_list(paths: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for entry in paths:
        normalized = str(_normalize_path(entry))
        # _normalize_path("") yields Path() whose str() is "." — never treat
        # the current working directory as a mod.
        if not normalized or normalized == "." or normalized in seen:
            continue
        seen.add(normalized)
        # Keep paths even if the file is currently unavailable (e.g. unmounted
        # drive); existence is enforced at launch time by validate_launch_target.
        out.append(str(Path(normalized)))
    return out


def _has_executable(command: str) -> bool:
    if not command:
        return False
    candidate = Path(command).expanduser()
    if candidate.exists():
        if os.name == "nt":
            return candidate.is_file()
        if candidate.suffix.lower() == ".app":
            bundle_binary = _resolve_macos_app_binary(candidate)
            if bundle_binary:
                return os.access(str(bundle_binary), os.X_OK)
            return False
        return candidate.is_file() and os.access(str(candidate), os.X_OK)

    if os.name == "nt":
        if not command.lower().endswith(".exe"):
            command = f"{command}.exe"

    which = shutil.which(command)
    if not which:
        return False
    return Path(which).is_file()


def _resolve_macos_app_binary(candidate: Path) -> Path | None:
    if os.name == "nt":
        return None

    if not candidate.exists() or not candidate.is_dir() or candidate.suffix.lower() != ".app":
        return None

    binary_dir = candidate / "Contents" / "MacOS"
    if not binary_dir.is_dir():
        return None

    try:
        binaries = sorted(binary_dir.iterdir())
    except OSError:
        return None

    executables = [
        bin_path for bin_path in binaries if bin_path.is_file() and os.access(str(bin_path), os.X_OK)
    ]
    if executables:
        return executables[0]

    for item in binaries:
        if item.is_file():
            return item
    return None
