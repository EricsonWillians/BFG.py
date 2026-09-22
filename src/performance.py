"""Performance optimization settings and animation scheduling services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import os


@dataclass
class PerformanceSettingsData:
    animation_fps: int = 20
    blood_texture_cache_size: int = 120
    skull_scaling_quality: str = "smooth"
    enable_antialiasing: bool = True
    background_animation_enabled: bool = True
    sprite_frame_caching: bool = True
    reduce_paint_events: bool = True
    optimize_visibility_checks: bool = True
    render_profile: str = "high"
    cache_profile_max_frames: int = 120
    log_buffer_max_lines: int = 1000
    persistent_tile_cache: bool = True
    tile_cache_max_bytes: int = 4_000_000
    tile_cache_ttl_seconds: int = 120
    mod_cache_max_bytes: int = 4_000_000
    mod_cache_ttl_seconds: int = 24 * 60 * 60
    mod_cache_max_entries: int = 500


class AnimationRuntime:
    """Deterministic animation pacing with low-risk drop-to-safe policy."""

    def __init__(self, settings: "PerformanceSettings"):
        self._settings = settings
        self._current_profile = settings.get("render_profile", "high")
        self._drop_level = 0
        self._frame_gate = 0

    def set_profile(self, profile: str) -> None:
        profile = str(profile).lower()
        if profile not in {"high", "low"}:
            profile = "high"
        self._current_profile = profile

    def get_interval(self) -> int:
        fps = int(self._settings.get("animation_fps", 20))
        fps = max(8, min(240, fps))
        return max(8, 1000 // fps)

    def get_timer_interval(self) -> int:
        return self.get_interval()

    def should_render(self, fps_target: int, dropped: int) -> bool:
        # Adaptive bucket policy: increase skip factor under pressure,
        # return to full speed when paint keeps up.
        fps_target = max(8, min(240, int(fps_target or 20)))

        if dropped > 0:
            self._drop_level = min(4, self._drop_level + 1)
        else:
            self._drop_level = max(0, self._drop_level - 1)

        skip = max(1, 2 ** self._drop_level)
        self._frame_gate = (self._frame_gate + 1) % skip

        # Evenly downsample updates while rendering stress is present.
        return self._frame_gate == 0


class PerformanceSettings:
    PROFILE_PRESETS = {
        "high": {
            "animation_fps": 20,
            "skull_scaling_quality": "smooth",
            "enable_antialiasing": True,
            "background_animation_enabled": True,
        },
        "low": {
            "animation_fps": 12,
            "skull_scaling_quality": "fast",
            "enable_antialiasing": False,
            "background_animation_enabled": False,
        },
    }

    def __init__(self):
        self._data = PerformanceSettingsData().__dict__.copy()
        # Keys explicitly set via BFG_* env vars; these win over profile
        # presets and config-file values.
        self._env_keys = set()
        self.animation_runtime = AnimationRuntime(self)
        # Apply the profile preset FIRST so env overrides (loaded next) win.
        self.apply_profile(self._data.get("render_profile", "high"))
        self._load_from_env()
        env_profile = os.getenv("BFG_RENDER_PROFILE")
        if env_profile:
            # Selects the preset; individual env overrides still win.
            self.apply_profile(env_profile)

    def _load_from_env(self):
        env_mappings = [
            ("BFG_ANIMATION_FPS", "animation_fps", int),
            ("BFG_CACHE_SIZE", "blood_texture_cache_size", int),
            ("BFG_SCALING_QUALITY", "skull_scaling_quality", str),
            ("BFG_ANTIALIASING", "enable_antialiasing", lambda x: str(x).lower() == "true"),
            (
                "BFG_BACKGROUND_ANIM",
                "background_animation_enabled",
                lambda x: str(x).lower() == "true",
            ),
            ("BFG_PERSIST_TILE_CACHE", "persistent_tile_cache", lambda x: str(x).lower() == "true"),
            ("BFG_RENDER_PROFILE", "render_profile", str),
            ("BFG_TILE_CACHE_BYTES", "tile_cache_max_bytes", int),
            ("BFG_TILE_CACHE_TTL", "tile_cache_ttl_seconds", int),
            ("BFG_MOD_CACHE_BYTES", "mod_cache_max_bytes", int),
            ("BFG_MOD_CACHE_TTL", "mod_cache_ttl_seconds", int),
            ("BFG_MOD_CACHE_ENTRIES", "mod_cache_max_entries", int),
            ("BFG_LOG_BUFFER", "log_buffer_max_lines", int),
        ]

        for env_key, setting, converter in env_mappings:
            raw = os.getenv(env_key)
            if raw is None:
                continue
            try:
                self._data[setting] = converter(raw)
                self._env_keys.add(setting)
            except (TypeError, ValueError):
                pass

        profile = str(self._data.get("render_profile", "high")).lower()
        if profile not in self.PROFILE_PRESETS:
            profile = "high"
        self._data["render_profile"] = profile

    def apply_profile(self, profile: str) -> None:
        profile = str(profile).lower()
        if profile not in self.PROFILE_PRESETS:
            profile = "high"
        for key, value in self.PROFILE_PRESETS[profile].items():
            if key in self._env_keys:
                continue  # explicit env override wins over the preset
            self._data[key] = value
        self._data["render_profile"] = profile
        self.animation_runtime.set_profile(profile)

    def apply_config(self, performance_config: Any) -> None:
        """Apply the ``performance`` block of LauncherConfig.

        Maps config keys to their runtime setting names. Explicit env
        overrides (BFG_*) still win over config-file values.
        """
        if performance_config is None:
            return
        mapping = [
            ("log_buffer_max_lines", "log_buffer_max_lines"),
            ("background_animation_enabled", "background_animation_enabled"),
            ("tile_cache_bytes", "tile_cache_max_bytes"),
            ("tile_cache_ttl", "tile_cache_ttl_seconds"),
            ("mod_cache_bytes", "mod_cache_max_bytes"),
            ("mod_cache_ttl", "mod_cache_ttl_seconds"),
            ("mod_cache_entries", "mod_cache_max_entries"),
        ]
        for config_key, setting in mapping:
            if setting in self._env_keys:
                continue
            value = getattr(performance_config, config_key, None)
            if value is not None:
                self._data[setting] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        if key == "render_profile":
            self.apply_profile(value)
            return
        self._data[key] = value

    def get_timer_interval(self) -> int:
        return self.animation_runtime.get_timer_interval()


perf_settings = PerformanceSettings()
animation_runtime = perf_settings.animation_runtime
