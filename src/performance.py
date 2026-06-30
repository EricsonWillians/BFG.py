"""Performance optimization settings and utilities."""

from __future__ import annotations

from typing import Any, Dict
import os


class PerformanceSettings:
    """Centralized performance configuration."""

    PROFILE_PRESETS = {
        "high": {
            "animation_fps": 20,
            "skull_scaling_quality": "smooth",
            "enable_antialiasing": True,
            "background_animation_enabled": True,
        },
        "low": {
            "animation_fps": 15,
            "skull_scaling_quality": "fast",
            "enable_antialiasing": False,
            "background_animation_enabled": False,
        },
    }

    DEFAULT_SETTINGS = {
        "animation_fps": 20,
        "blood_texture_cache_size": 60,
        "skull_scaling_quality": "smooth",
        "enable_antialiasing": True,
        "background_animation_enabled": True,
        "sprite_frame_caching": True,
        "reduce_paint_events": True,
        "optimize_visibility_checks": True,
        "render_profile": "high",
        "cache_profile_max_frames": 120,
        "log_buffer_max_lines": 1000,
        "persistent_tile_cache": True,
    }

    def __init__(self):
        self.settings = self.DEFAULT_SETTINGS.copy()
        self._load_from_env()

    def _load_from_env(self):
        """Load performance settings from environment variables."""
        env_mappings = [
            ("BFG_ANIMATION_FPS", ("animation_fps", int)),
            ("BFG_CACHE_SIZE", ("blood_texture_cache_size", int)),
            ("BFG_SCALING_QUALITY", ("skull_scaling_quality", str)),
            ("BFG_ANTIALIASING", ("enable_antialiasing", lambda x: x.lower() == "true")),
            ("BFG_BACKGROUND_ANIM", ("background_animation_enabled", lambda x: x.lower() == "true")),
            ("BFG_PERSIST_TILE_CACHE", ("persistent_tile_cache", lambda x: x.lower() == "true")),
            ("BFG_RENDER_PROFILE", ("render_profile", str)),
        ]

        for env_var, (setting_key, converter) in env_mappings:
            value = os.getenv(env_var)
            if value is None:
                continue
            try:
                self.settings[setting_key] = converter(value)
            except (ValueError, TypeError):
                pass

        # Ensure profile is valid after env parsing
        profile = str(self.settings.get("render_profile", "high")).lower()
        if profile not in self.PROFILE_PRESETS:
            self.settings["render_profile"] = "high"

        self.apply_profile(self.settings["render_profile"])

    def apply_profile(self, profile: str) -> None:
        profile = str(profile).lower()
        if profile not in self.PROFILE_PRESETS:
            return
        preset = self.PROFILE_PRESETS[profile]
        for key, value in preset.items():
            self.settings[key] = value
        self.settings["render_profile"] = profile

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set(self, key: str, value: Any):
        if key == "render_profile":
            self.apply_profile(value)
            return
        self.settings[key] = value

    def get_timer_interval(self) -> int:
        fps = self.get("animation_fps", 20)
        try:
            fps = int(fps)
        except (TypeError, ValueError):
            fps = 20
        fps = max(8, min(240, fps))
        return max(8, 1000 // fps)


perf_settings = PerformanceSettings()
