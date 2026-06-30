from __future__ import annotations

import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
from dataclasses import dataclass, field
from typing import List, Optional, Union
from PyQt5.QtCore import QObject, QCoreApplication, QEventLoop, pyqtSignal

from src.config import LauncherConfig
from src.launch_controller import LaunchOrchestrator, build_launch_args, resolve_source_port
from src.performance import perf_settings


VERSION = "3.0.0"
DEFAULT_LOG_LINES = 1200
DEFAULT_LOG_BYTES = 256_000


def _cache_dir() -> Path:
    candidates = [
        os.getenv("BFG_CACHE_DIR"),
        os.getenv("XDG_CACHE_HOME"),
        os.getenv("APPDATA") if os.name == "nt" else None,
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate).expanduser() / "bfg.py"
            try:
                path.mkdir(parents=True, exist_ok=True)
                return path
            except OSError:
                continue
    fallback = Path.home() / ".cache" / "bfg.py"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _runtime_log_path() -> Path:
    return _cache_dir() / "runtime.log"


def _configure_runtime_logger() -> logging.Logger:
    logger = logging.getLogger("bfg.runtime")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        filename=str(_runtime_log_path()),
        maxBytes=DEFAULT_LOG_BYTES,
        backupCount=3,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    return logger


@dataclass
class RuntimeOptions:
    config_path: str = "config.json"
    performance_test: bool = False
    no_animations: bool = False
    source_port: Optional[str] = None
    iwad: Optional[str] = None
    pwad_dir: Optional[str] = None
    pwads: List[str] = field(default_factory=list)
    extra_options: str = ""
    exit_after_launch: bool = False
    check_config: bool = False
    test_config: bool = False
    no_gui: bool = False
    version: bool = False


def parse_runtime_options(argv: Optional[List[str]] = None) -> RuntimeOptions:
    parser = argparse.ArgumentParser(prog="BFG.py", add_help=True)
    parser.add_argument("--config", default="config.json", dest="config_path")
    parser.add_argument("--performance-test", action="store_true")
    parser.add_argument("--no-animations", action="store_true")
    parser.add_argument("--source-port")
    parser.add_argument("--iwad")
    parser.add_argument("--pwad-dir")
    parser.add_argument("--pwad", action="append", default=[])
    parser.add_argument("--extra-options")
    parser.add_argument("--exit-after-launch", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--test-config", action="store_true")
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--version", action="store_true")

    args = parser.parse_args(argv)
    return RuntimeOptions(**vars(args))


class ApplicationRuntime(QObject):
    status = pyqtSignal(str)
    log = pyqtSignal(str)
    config_saved = pyqtSignal(str)
    launch_state = pyqtSignal(str)
    launch_output = pyqtSignal(str)
    launch_error = pyqtSignal(str)
    launch_finished = pyqtSignal(object, object)

    def __init__(self, options: Optional[RuntimeOptions] = None, parent=None):
        super().__init__(parent)
        self.options = options or RuntimeOptions()
        self.logger = _configure_runtime_logger()
        self.config = LauncherConfig.load(self.options.config_path).normalized()
        self.config = self._apply_cli_overrides(self.config)
        self.config = self.config.normalized()

        self.launch_orchestrator = LaunchOrchestrator(self)
        self.launch_orchestrator.state_changed.connect(self.launch_state.emit)
        self.launch_orchestrator.output_line.connect(self.launch_output.emit)
        self.launch_orchestrator.error.connect(self.launch_error.emit)
        self.launch_orchestrator.finished.connect(self.launch_finished.emit)

        self._startup_summary_logged = False
        self._log_startup_summary()

        if self.options.no_animations:
            self.config.animated_background = False
            self.config.performance.background_animation_enabled = False

        if self.options.test_config:
            self.options.check_config = True

    def _apply_cli_overrides(self, config: LauncherConfig) -> LauncherConfig:
        config = config.normalized()
        if self.options.source_port:
            config.source_port_path = self.options.source_port
            config.source_port_dir = str(Path(self.options.source_port).expanduser().parent)
        if self.options.iwad:
            config.iwad_path = self.options.iwad
            config.iwad_dir = str(Path(self.options.iwad).expanduser().parent)
        if self.options.pwad_dir:
            config.pwad_dir = self.options.pwad_dir
        if self.options.extra_options:
            config.extra_options = self.options.extra_options
        if self.options.pwads:
            config.pwad_paths = list(dict.fromkeys(config.pwad_paths + self.options.pwads))
        if self.options.no_animations:
            config.animated_background = False
            config.performance_mode = False
            config.render_profile = "low"
        return config

    def _log_startup_summary(self) -> None:
        if self._startup_summary_logged:
            return

        resolved_source, failure = resolve_source_port(self.config.source_port_path)
        if not resolved_source:
            resolved_source = "<unresolved>"

        self.logger.info("Startup summary: version=%s platform=%s source=%s profile=%s", 
                         VERSION,
                         platform.platform(),
                         resolved_source,
                         self.config.render_profile)
        if failure:
            self.logger.warning("Startup validation warning: %s", failure)
        self._startup_summary_logged = True

    def validate_config(self):
        return self.config.validate(strict=True)

    def save_config(self, *, atomic: bool = True, backup_count: int = 2) -> None:
        self.config = self.config.normalized()
        path = Path(self.options.config_path)
        self.config.save(path, atomic=atomic, backup_count=backup_count)
        self.config_saved.emit(str(path))

    def launch_preview(self, config: Optional[LauncherConfig] = None) -> str:
        payload = config.normalized() if config else self.config.normalized()
        source, _ = resolve_source_port(payload.source_port_path)
        args = build_launch_args(payload)
        return f"{source or payload.source_port_path} {' '.join(args)}".strip()

    def run_performance_test(self):
        from performance_test import run_performance_test

        return run_performance_test()

    def run_no_gui(self) -> int:
        validation = self.validate_config()
        self._emit_validation(validation)
        if not validation.is_valid:
            return 2

        if not (self.options.exit_after_launch or self.options.test_config):
            self.logger.info("No-GUI launch requires --exit-after-launch for deterministic teardown.")
            print("No-GUI mode requires --exit-after-launch to run launch flow deterministically.")
            return 2

        if self.options.check_config:
            return 0

        app = QCoreApplication.instance() or QCoreApplication([])
        result = {"code": 0}
        done = {"finished": False}

        def _on_finished(code, reason):
            done["finished"] = True
            if code is None:
                result["code"] = 1
            else:
                result["code"] = int(code)
            if reason:
                self.logger.info("Headless launch finished: %s", reason)

        self.launch_orchestrator.finished.connect(_on_finished)

        handle = self.launch_orchestrator.start_launch(self.config)
        if not handle.started:
            self.launch_orchestrator.stop_launch()
            return 1

        while not done["finished"]:
            app.processEvents()
            if self.launch_orchestrator.is_running():
                continue
            break

        return int(result.get("code", 0) or 0)

    def _emit_validation(self, validation) -> None:
        if validation.errors:
            for msg in validation.errors:
                print(msg)
                self.log.emit(msg)
        if validation.warnings:
            for msg in validation.warnings:
                print(msg)
                self.log.emit(msg)

        print(validation.message or "Configuration valid")
        self.log.emit(validation.message or "Configuration valid")

    def print_version(self) -> str:
        return VERSION
