from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import os
import shlex
from pathlib import Path
import shutil
import time
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QProcess, QTimer, QElapsedTimer, pyqtSignal

from src.config import LauncherConfig
from src.performance import perf_settings


@dataclass
class LaunchValidation:
    is_valid: bool
    errors: List[str]
    warnings: List[str]


@dataclass
class LaunchHandle:
    request_id: int
    started: bool
    reason: Optional[str] = None


@dataclass
class LaunchResult:
    exit_code: Optional[int]
    reason: Optional[str] = None


def build_launch_args(config: LauncherConfig) -> List[str]:
    args: List[str] = []
    if config.iwad_path:
        args.extend(["-iwad", config.iwad_path])

    if config.pwad_paths:
        args.extend(["-file", *config.pwad_paths])

    if config.extra_options:
        args.extend(_split_options(config.extra_options))

    return args


def validate_launch_target(config: LauncherConfig) -> LaunchValidation:
    result = config.validate(strict=True)
    return LaunchValidation(result.is_valid, result.errors, result.warnings)


def resolve_source_port(command: str) -> Tuple[Optional[str], Optional[str]]:
    command = str(command).strip()
    if not command:
        return None, "No source port configured."

    candidate = Path(command).expanduser()
    if candidate.exists():
        if os.name == "nt":
            if candidate.is_file():
                return str(candidate), None
            return None, f"Source port is not a file: {command}"

        if os.access(str(candidate), os.X_OK):
            return str(candidate), None
        return None, f"Source port is not executable: {command}"

    if os.name == "nt" and not command.lower().endswith(".exe"):
        command = f"{command}.exe"

    which = shutil.which(command)
    if which:
        return which, None

    return None, f"Source port not found on PATH: {command}"


def _split_options(raw: str) -> List[str]:
    if not raw:
        return []
    if os.name == "nt":
        return shlex.split(raw, posix=False)
    return shlex.split(raw)


class LaunchOrchestrator(QObject):
    STATE_IDLE = "idle"
    STATE_VALIDATING = "validating"
    STATE_LAUNCHING = "launching"
    STATE_RUNNING = "running"
    STATE_FAILED = "failed"
    STATE_CANCELING = "canceling"
    STATE_FINISHED = "finished"

    # Compatibility aliases used by older callers.
    STATE_STARTING = STATE_LAUNCHING
    STATE_STOPPED = STATE_IDLE

    state_changed = pyqtSignal(str)
    output_line = pyqtSignal(str)
    output = pyqtSignal(str)
    finished = pyqtSignal(object, object)
    error = pyqtSignal(str)

    def __init__(self, parent=None, *, startup_timeout_ms: int = 8000):
        super().__init__(parent)
        self._process = QProcess(self)
        self._buffer = deque(maxlen=perf_settings.get("log_buffer_max_lines", 1000))
        self._state = self.STATE_IDLE
        self._startup_timeout_ms = startup_timeout_ms
        self._startup_timeout = QTimer(self)
        self._startup_timeout.setSingleShot(True)
        self._startup_timeout.setInterval(self._startup_timeout_ms)
        self._startup_timeout.timeout.connect(self._on_startup_timeout)

        self._active_config: Optional[LauncherConfig] = None
        self._request_id = 0
        self._active_request_id = 0
        self._last_exit_code: Optional[int] = None
        self._last_failure_reason: Optional[str] = None
        self._last_start_time: Optional[float] = None
        self._last_duration_ms: Optional[int] = None

        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

    @property
    def last_exit_code(self) -> Optional[int]:
        return self._last_exit_code

    @property
    def last_failure_reason(self) -> Optional[str]:
        return self._last_failure_reason

    @property
    def last_launch_elapsed_ms(self) -> Optional[int]:
        return self._last_duration_ms

    def is_running(self) -> bool:
        return self._state in {
            self.STATE_VALIDATING,
            self.STATE_LAUNCHING,
            self.STATE_RUNNING,
            self.STATE_CANCELING,
        }

    def start_launch(self, config: LauncherConfig) -> LaunchHandle:
        self._request_id += 1
        request_id = self._request_id
        self._active_request_id = request_id

        if self.is_running():
            msg = "Launch already in progress."
            self._record_error(msg)
            return LaunchHandle(request_id=request_id, started=False, reason=msg)

        normalized = config.normalized()
        self._set_state(self.STATE_VALIDATING)

        validation = validate_launch_target(normalized)
        if not validation.is_valid:
            for message in validation.errors:
                self._record_error(message)
            reason = validation.message or "Validation failed."
            self._set_state(self.STATE_FAILED, reason=reason)
            return LaunchHandle(request_id=request_id, started=False, reason=reason)

        if validation.warnings:
            for warning in validation.warnings:
                self._append_output(warning)

        resolved_source, failure = resolve_source_port(normalized.source_port_path)
        if failure is not None:
            self._record_error(failure)
            self._set_state(self.STATE_FAILED, reason=failure)
            return LaunchHandle(request_id=request_id, started=False, reason=failure)

        try:
            args = build_launch_args(normalized)
        except Exception as exc:  # pragma: no cover - defensive
            reason = f"Invalid extra options: {exc}"
            self._record_error(reason)
            self._set_state(self.STATE_FAILED, reason=reason)
            return LaunchHandle(request_id=request_id, started=False, reason=reason)

        self._active_config = normalized
        self._last_exit_code = None
        self._last_failure_reason = None
        self._buffer.clear()

        self._append_output(
            "Launch preview: {exe} {args}".format(
                exe=resolved_source,
                args=" ".join(args),
            )
        )

        self._set_state(self.STATE_LAUNCHING)
        self._last_start_time = time.time()
        self._process.setProgram(resolved_source)
        self._process.setArguments(args)
        self._process.start()
        self._startup_timeout.start()

        if self._process.state() == QProcess.NotRunning:
            reason = f"Failed to start process: {resolved_source}"
            self._record_error(reason)
            self._set_state(self.STATE_FAILED, reason=reason)
            return LaunchHandle(request_id=request_id, started=False, reason=reason)

        return LaunchHandle(request_id=request_id, started=True)

    def request_stop(self) -> None:
        if not self.is_running():
            return

        if self._state == self.STATE_CANCELING:
            return

        self._set_state(self.STATE_CANCELING)

        self._startup_timeout.stop()
        if self._process.state() == QProcess.Running:
            self._process.terminate()
            if not self._process.waitForFinished(1200):
                self._process.kill()
                self._process.waitForFinished(1200)
        else:
            self._process.kill()
            self._set_state(self.STATE_IDLE, reason="Stopped")

    def stop_launch(self) -> None:
        self.request_stop()

    def _set_state(self, state: str, *, reason: Optional[str] = None) -> None:
        self._state = state
        self.state_changed.emit(state)
        if reason:
            self._record_error(reason)

    def _on_started(self):
        if self._state != self.STATE_LAUNCHING:
            return
        self._startup_timeout.stop()
        self._set_state(self.STATE_RUNNING)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._startup_timeout.stop()
        self._last_duration_ms = None
        if self._last_start_time:
            self._last_duration_ms = int((time.time() - self._last_start_time) * 1000)

        self._last_exit_code = int(exit_code)
        reason = self._last_failure_reason

        if self._state == self.STATE_CANCELING:
            reason = reason or "Stopped by user"
            self._set_state(self.STATE_FINISHED, reason=reason)
            self.finished.emit(self._last_exit_code, reason)
            return

        if exit_code != 0:
            reason = reason or f"Process exited with code {exit_code}"
            self._set_state(self.STATE_FAILED, reason=reason)
            self.finished.emit(self._last_exit_code, reason)
            return

        self._set_state(self.STATE_FINISHED, reason=None)
        self.finished.emit(self._last_exit_code, reason)

    def _on_error(self, _error) -> None:
        if self._state == self.STATE_IDLE:
            return
        reason = self._process.errorString() or "Unknown launch failure."
        self._last_failure_reason = reason
        self._set_state(self.STATE_FAILED, reason=reason)
        self.finished.emit(None, reason)
        self._startup_timeout.stop()

    def _on_startup_timeout(self):
        if self._state not in {self.STATE_LAUNCHING, self.STATE_RUNNING}:
            return
        self._last_failure_reason = "Launch timeout while waiting for startup."
        self.request_stop()
        self._set_state(self.STATE_FAILED, reason=self._last_failure_reason)

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
        line = line.rstrip("\n")
        if not line:
            return
        self._buffer.append(f"[{datetime.utcnow().isoformat()}Z] {line}")
        self.output_line.emit(line)
        self.output.emit(line)

    def _record_error(self, message: str) -> None:
        self._last_failure_reason = message
        self.error.emit(message)


# Compatibility alias expected by legacy call sites.
LaunchController = LaunchOrchestrator
