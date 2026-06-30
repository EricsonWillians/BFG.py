from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
import shlex
from pathlib import Path
import shutil
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal

from src.config import LauncherConfig


@dataclass
class LaunchValidation:
    is_valid: bool
    errors: List[str]
    warnings: List[str]


def build_launch_args(config: LauncherConfig) -> List[str]:
    args: List[str] = []
    if config.iwad_path:
        args.extend(["-iwad", config.iwad_path])

    if config.pwad_paths:
        args.extend(["-file", *config.pwad_paths])

    if config.extra_options:
        if os.name == "nt":
            split_options = shlex.split(config.extra_options, posix=False)
        else:
            split_options = shlex.split(config.extra_options)
        args.extend(split_options)
    return args


def validate_launch_target(config: LauncherConfig) -> LaunchValidation:
    errors: List[str] = []
    warnings: List[str] = []
    config_validation = config.validate()
    errors.extend(config_validation.errors)
    warnings.extend(config_validation.warnings)
    return LaunchValidation(
        is_valid=config_validation.is_valid and not errors,
        errors=errors,
        warnings=warnings,
    )


def resolve_source_port(command: str) -> Tuple[Optional[str], Optional[str]]:
    command = str(command).strip()
    if not command:
        return None, "No source port configured."

    candidate = Path(command).expanduser()
    if candidate.exists():
        if os.name == "nt":
            if candidate.is_file():
                return str(candidate.resolve() if not candidate.is_absolute() else candidate), None
            return None, f"Source port is not a file: {command}"
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        if os.access(str(candidate), os.X_OK):
            return str(candidate), None
        return None, f"Source port is not executable: {command}"

    # allow PATH lookup
    which = shutil.which(command)
    if os.name == "nt" and not which and not command.lower().endswith(".exe"):
        which = shutil.which(f"{command}.exe")
    if which:
        return which, None

    return None, f"Source port not found on PATH: {command}"


class LaunchController(QObject):
    STATE_STARTING = "starting"
    STATE_RUNNING = "running"
    STATE_STOPPED = "stopped"
    STATE_FAILED = "failed"
    STATE_FINISHED = "finished"

    state_changed = pyqtSignal(str)
    output = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._buffer: deque[str] = deque(maxlen=1000)
        self._state = self.STATE_STOPPED
        self._startup_timeout = QTimer(self)
        self._startup_timeout.setSingleShot(True)
        self._startup_timeout.setInterval(7000)
        self._startup_timeout.timeout.connect(self._on_startup_timeout)
        self._active_config = None  # type: Optional[LauncherConfig]

        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

    def is_running(self) -> bool:
        return self._state in {self.STATE_STARTING, self.STATE_RUNNING}

    def start_launch(self, config: LauncherConfig) -> bool:
        if self.is_running():
            self.error.emit("A launch is already in progress.")
            return False

        normalized_config = config.normalized()
        validation = validate_launch_target(normalized_config)
        if not validation.is_valid:
            for msg in validation.errors:
                self.error.emit(msg)
            self.state_changed.emit(self.STATE_FAILED)
            return False

        source_port, failure = resolve_source_port(normalized_config.source_port_path)
        if failure is not None:
            self.error.emit(failure)
            self.state_changed.emit(self.STATE_FAILED)
            return False

        try:
            args = build_launch_args(normalized_config)
        except ValueError as exc:
            self.error.emit(f"Invalid extra option string: {exc}")
            self.state_changed.emit(self.STATE_FAILED)
            return False

        self._active_config = normalized_config
        self._buffer.clear()
        self._state = self.STATE_STARTING
        self.state_changed.emit(self.STATE_STARTING)

        self._process.setProgram(source_port)
        self._process.setArguments(args)
        self._process.start()
        self._startup_timeout.start()
        return True

    def stop_launch(self) -> None:
        if not self.is_running():
            return

        self._startup_timeout.stop()
        if self._process.state() == QProcess.Running:
            self._process.terminate()
            if not self._process.waitForFinished(1200):
                self._process.kill()
                self._process.waitForFinished(1200)
        self._state = self.STATE_STOPPED
        self.state_changed.emit(self.STATE_STOPPED)

    def _on_started(self):
        if self._state == self.STATE_STOPPED:
            return
        self._startup_timeout.stop()
        self._state = self.STATE_RUNNING
        self.state_changed.emit(self.STATE_RUNNING)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._startup_timeout.stop()
        if self._state in {self.STATE_STOPPED, self.STATE_FAILED}:
            return
        self._state = self.STATE_FINISHED
        self.state_changed.emit(self.STATE_FINISHED)
        self.finished.emit(int(exit_code))

    def _on_error(self, _error) -> None:
        if self._state == self.STATE_STOPPED:
            return
        msg = self._process.errorString()
        if msg:
            self.error.emit(msg)
        self._state = self.STATE_FAILED
        self._startup_timeout.stop()
        self.state_changed.emit(self.STATE_FAILED)

    def _on_startup_timeout(self):
        if self._state in {self.STATE_STARTING, self.STATE_RUNNING}:
            self.error.emit("Launch timed out while starting the source port.")
            self.stop_launch()

    def _read_stdout(self):
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "ignore")
        if data:
            for line in data.splitlines():
                self._append_output(line)

    def _read_stderr(self):
        data = bytes(self._process.readAllStandardError()).decode("utf-8", "ignore")
        if data:
            for line in data.splitlines():
                self._append_output(line)

    def _append_output(self, line: str) -> None:
        self._buffer.append(line)
        self.output.emit(line)
