from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        if self.errors or self.warnings:
            parts = []
            if self.errors:
                parts.append("Errors: " + "; ".join(self.errors))
            if self.warnings:
                parts.append("Warnings: " + "; ".join(self.warnings))
            return " | ".join(parts)
        return ""


@dataclass
class LauncherConfig:
    source_port_path: str = "gzdoom"
    iwad_path: str = ""
    pwad_paths: List[str] = field(default_factory=list)
    extra_options: str = ""
    source_port_dir: str = field(default_factory=lambda: str(Path.home()))
    iwad_dir: str = field(default_factory=lambda: str(Path.home()))
    pwad_dir: str = field(default_factory=lambda: str(Path.home()))
    animated_background: bool = False
    performance_mode: bool = False
    render_profile: str = "high"

    _legacy_key_map = {
        "lastSourcePort": "source_port_path",
        "lastIWad": "iwad_path",
        "lastPWads": "pwad_paths",
        "lastOptions": "extra_options",
        "sourcePortDir": "source_port_dir",
        "iwadDir": "iwad_dir",
        "pwadDir": "pwad_dir",
        "animatedBackground": "animated_background",
        "performanceMode": "performance_mode",
    }

    @classmethod
    def load(cls, path: Union[str, Path]) -> "LauncherConfig":
        """Backward-compatible convenience constructor."""
        return ConfigStore(path).load()

    def save(self, path: Union[str, Path]) -> None:
        """Persist the normalized configuration to disk."""
        ConfigStore(path).save(self)

    @classmethod
    def from_raw(cls, data: Optional[Dict[str, Any]]) -> "LauncherConfig":
        if not isinstance(data, dict):
            return cls()

        return cls(
            source_port_path=_coerce_str(
                data.get("source_port_path", data.get("lastSourcePort", "gzdoom")),
                default="gzdoom",
            ),
            iwad_path=_coerce_str(data.get("iwad_path", data.get("lastIWad", ""))),
            pwad_paths=_coerce_str_list(
                data.get("pwad_paths", data.get("lastPWads", [])),
            ),
            extra_options=_coerce_str(data.get("extra_options", data.get("lastOptions", ""))),
            source_port_dir=_coerce_str(
                data.get("source_port_dir", data.get("sourcePortDir", str(Path.home()))),
                default=str(Path.home()),
            ),
            iwad_dir=_coerce_str(data.get("iwad_dir", data.get("iwadDir", str(Path.home()))), default=str(Path.home())),
            pwad_dir=_coerce_str(data.get("pwad_dir", data.get("pwadDir", str(Path.home()))), default=str(Path.home())),
            animated_background=bool(
                data.get("animated_background", data.get("animatedBackground", False))
            ),
            performance_mode=bool(
                data.get("performance_mode", data.get("performanceMode", False))
            ),
            render_profile=_coerce_str(
                data.get("render_profile", "high"),
                default="high",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_port_path": self.source_port_path,
            "iwad_path": self.iwad_path,
            "pwad_paths": self.pwad_paths,
            "extra_options": self.extra_options,
            "source_port_dir": self.source_port_dir,
            "iwad_dir": self.iwad_dir,
            "pwad_dir": self.pwad_dir,
            "animated_background": self.animated_background,
            "performance_mode": self.performance_mode,
            "render_profile": self.render_profile,
        }

    def get(self, key: str, default: Any = None) -> Any:
        field_name = self._legacy_key_map.get(key, key)
        if hasattr(self, field_name):
            return getattr(self, field_name)
        return default

    def set(self, key: str, value: Any):
        field_name = self._legacy_key_map.get(key, key)
        if not hasattr(self, field_name):
            return
        if field_name == "pwad_paths":
            value = _coerce_str_list(value)
        elif field_name in {"animated_background", "performance_mode"}:
            value = bool(value)
        elif field_name in {"source_port_path", "iwad_path", "extra_options", "source_port_dir", "iwad_dir", "pwad_dir", "render_profile"}:
            value = _coerce_str(value, default=getattr(self, field_name))
        setattr(self, field_name, value)

    def normalized(self) -> "LauncherConfig":
        cfg = LauncherConfig.from_raw(self.to_dict())
        cfg.source_port_path = _normalize_command_path(cfg.source_port_path)
        cfg.iwad_path = str(_normalize_path(cfg.iwad_path)) if cfg.iwad_path else ""
        cfg.pwad_paths = _normalize_wad_list(cfg.pwad_paths)
        cfg.source_port_dir = (
            str(_normalize_path(cfg.source_port_dir, must_exist=False))
            if cfg.source_port_dir else str(Path.home())
        )
        cfg.iwad_dir = (
            str(_normalize_path(cfg.iwad_dir, must_exist=False))
            if cfg.iwad_dir else str(Path.home())
        )
        cfg.pwad_dir = (
            str(_normalize_path(cfg.pwad_dir, must_exist=False))
            if cfg.pwad_dir else str(Path.home())
        )
        if cfg.render_profile not in {"high", "low"}:
            cfg.render_profile = "high"
        return cfg

    def validate(self) -> ValidationResult:
        result = ValidationResult()

        source_port = _coerce_str(self.source_port_path, default="").strip()
        if not source_port:
            result.is_valid = False
            result.errors.append("No source port configured.")
        elif not _has_executable(source_port):
            result.is_valid = False
            result.errors.append(f"Source port not found or not executable: {source_port}")

        if self.iwad_path:
            iwad = _normalize_path(self.iwad_path)
            if not iwad.exists():
                result.is_valid = False
                result.errors.append(f"IWAD path does not exist: {self.iwad_path}")
            elif not iwad.is_file():
                result.is_valid = False
                result.errors.append(f"IWAD is not a file: {self.iwad_path}")

        for path in self.pwad_paths:
            wad = _normalize_path(path)
            if not wad.exists():
                result.is_valid = False
                result.errors.append(f"Mod file does not exist: {path}")
            elif not wad.is_file():
                result.is_valid = False
                result.errors.append(f"Mod is not a file: {path}")

        return result


class ConfigStore:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def load(self) -> LauncherConfig:
        if not self.path.exists():
            return LauncherConfig()

        try:
            with self.path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError, ValueError):
            return LauncherConfig()
        return LauncherConfig.from_raw(data).normalized()

    def save(self, config: LauncherConfig) -> None:
        payload = config.normalized().to_dict()
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text if text else default


def _coerce_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _normalize_path(path: str, *, must_exist: bool = True) -> Path:
    text = _coerce_str(path, default="")
    if not text:
        return Path()

    p = Path(text).expanduser()
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        p = p.expanduser().absolute()

    if must_exist and not p.exists():
        return p

    return p


def _normalize_command_path(path: str) -> str:
    path = _coerce_str(path, default="").strip()
    if not path:
        return "gzdoom"
    candidate = Path(path).expanduser()
    if candidate.exists():
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError):
            pass
        return str(candidate)
    return path.strip()


def _normalize_wad_list(paths: List[str]) -> List[str]:
    seen = set()
    out = []
    for entry in paths:
        normalized = str(_normalize_path(entry, must_exist=False))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        p = Path(normalized)
        if p.is_file():
            out.append(str(p))
    return out


def _has_executable(command: str) -> bool:
    if not command:
        return False
    candidate = Path(command).expanduser()
    if candidate.exists():
        if not candidate.is_absolute():
            candidate = candidate.resolve()

    if candidate.exists():
        if os.name == "nt":
            return candidate.is_file()
        return candidate.is_file() and os.access(str(candidate), os.X_OK)

    if os.name == "nt":
        which = shutil.which(command)
        if not which:
            return False
        return Path(which).is_file()

    return shutil.which(command) is not None
