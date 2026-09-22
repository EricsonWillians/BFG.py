from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import shlex
from pathlib import Path
import shutil
import time
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QProcess, QTimer, QElapsedTimer, pyqtSignal

from src.config import LauncherConfig
from src.performance import perf_settings
from src.source_port_discovery import can_auto_detect, discover_source_ports, find_best_match

# ANSI escape sequences: CSI (\x1b[...), OSC (\x1b]...\x07/\x1b\\), and
# remaining two-character escapes.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


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


def _resolve_macos_app_binary(candidate: Path) -> Optional[Path]:
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

    executables = [bin_path for bin_path in binaries if bin_path.is_file() and os.access(str(bin_path), os.X_OK)]
    if executables:
        return executables[0]

    for candidate in binaries:
        if candidate.is_file():
            return candidate
    return None


def resolve_source_port(
    command: str,
    *,
    discover: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    command = str(command).strip()
    if not command:
        return None, "No source port configured."

    candidate = Path(command).expanduser()
    if candidate.exists():
        if candidate.is_file():
            if os.name == "nt":
                return str(candidate), None
            if os.access(str(candidate), os.X_OK):
                return str(candidate), None
            return None, f"Source port is not executable: {command}"

        if os.name != "nt" and candidate.suffix.lower() == ".app":
            bundle_binary = _resolve_macos_app_binary(candidate)
            if bundle_binary and os.access(str(bundle_binary), os.X_OK):
                return str(bundle_binary), None
            if bundle_binary:
                return None, f"Source port app bundle binary is not executable: {command}"
            return None, f"Source port is not a file: {command}"

        return None, f"Source port is not a file: {command}"

    if os.name == "nt":
        if not command.lower().endswith(".exe"):
            command = f"{command}.exe"

    which = shutil.which(command)
    if which:
        return which, None

    if discover:
        best_match = find_best_match(command)
        if best_match:
            return best_match[1], None

    return None, f"Source port not found on PATH: {command}"


def _split_options(raw: str) -> List[str]:
    if not raw:
        return []
    # QProcess takes an argv list, so no shell quoting is needed on any
    # platform. posix=True strips quote characters; posix=False kept them in
    # the tokens and corrupted quoted paths containing spaces on Windows.
    return shlex.split(raw, posix=True)


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
            reason = validation.errors[0] if validation.errors else "Validation failed."
            self._last_failure_reason = reason
            self._set_state(self.STATE_FAILED)
            return LaunchHandle(request_id=request_id, started=False, reason=reason)

        if validation.warnings:
            for warning in validation.warnings:
                self._append_output(warning)

        resolved_source, failure = resolve_source_port(normalized.source_port_path)
        if failure is not None:
            self._record_error(failure)
            self._set_state(self.STATE_FAILED)
            return LaunchHandle(request_id=request_id, started=False, reason=failure)

        try:
            args = build_launch_args(normalized)
        except Exception as exc:  # pragma: no cover - defensive
            reason = f"Invalid extra options: {exc}"
            self._record_error(reason)
            self._set_state(self.STATE_FAILED)
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

        # Do NOT waitForStarted() here: it blocks the GUI thread for up to 3s.
        # Start failures are delivered asynchronously via errorOccurred and
        # handled in _on_error, which records the failure and emits finished.
        return LaunchHandle(request_id=request_id, started=True)

    def request_stop(self) -> None:
        if not self.is_running():
            return

        if self._state == self.STATE_CANCELING:
            return

        self._set_state(self.STATE_CANCELING)

        self._startup_timeout.stop()
        if self._process.state() == QProcess.Running:
            # Non-blocking: terminate, then escalate to kill via timer if the
            # process is still alive. The async finished signal transitions state.
            self._process.terminate()
            request_id = self._active_request_id
            QTimer.singleShot(1200, lambda rid=request_id: self._kill_if_running(rid))
        elif self._process.state() == QProcess.NotRunning:
            self._set_state(self.STATE_IDLE, reason="Stopped")

    def _kill_if_running(self, request_id: int) -> None:
        # Guard against stale timers: if a new launch started after this stop
        # was requested, the request id changed and we must not kill it.
        if request_id != self._active_request_id:
            return
        if self._state not in {self.STATE_CANCELING, self.STATE_FAILED}:
            return
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()

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
        self._raise_attempts = 0
        self._raise_game_window_when_mapped()

    def _raise_game_window_when_mapped(self) -> None:
        """Best-effort: bring the game window to the front once it maps.

        When the user is focused on another (e.g. fullscreen) app, the window
        manager's focus-stealing prevention maps the game window buried
        underneath it — the game runs (menu music plays) but stays invisible.
        A launcher requesting activation right after launch is a legitimate
        user action, so WMs honor _NET_ACTIVE_WINDOW here.
        """
        if self._state != self.STATE_RUNNING:
            return
        pid = int(self._process.processId())
        if pid <= 0:
            return
        if _x11_activate_window_for_pid(pid):
            return
        self._raise_attempts += 1
        # The SDL window can take several seconds to map; keep polling.
        if self._raise_attempts <= 40:
            QTimer.singleShot(500, self._raise_game_window_when_mapped)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        if self._state == self.STATE_FAILED:
            # Startup-timeout path: the state was forced to FAILED before the
            # terminated process reported its exit. The finished signal must
            # still be emitted so listeners (status, logging, config save)
            # behave like on every other exit path.
            if self._last_failure_reason is None:
                return
        elif self._state not in {self.STATE_LAUNCHING, self.STATE_RUNNING, self.STATE_CANCELING}:
            return
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

        if self._state == self.STATE_FAILED:
            reason = reason or f"Process exited with code {exit_code}"
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
        if self._state not in {self.STATE_LAUNCHING, self.STATE_RUNNING, self.STATE_CANCELING}:
            return
        if self._state == self.STATE_CANCELING:
            # terminate()/kill() during a user-requested stop reports as a
            # crash; surface it as a normal stop instead.
            reason = self._last_failure_reason or "Stopped by user"
            self._set_state(self.STATE_FINISHED)
            self.finished.emit(self._last_exit_code, reason)
            self._startup_timeout.stop()
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
        # Strip ANSI escape sequences (source ports print truecolor terminal
        # art on shutdown); they render as garbage and bloat layout cost.
        line = _ANSI_ESCAPE_RE.sub("", line).rstrip()
        if not line:
            return
        self._buffer.append(f"[{datetime.now(timezone.utc).isoformat()}] {line}")
        self.output_line.emit(line)
        self.output.emit(line)

    def _record_error(self, message: str) -> None:
        self._last_failure_reason = message
        self.error.emit(message)


# Compatibility alias expected by legacy call sites.
LaunchController = LaunchOrchestrator


def _x11_activate_window_for_pid(pid: int) -> bool:
    """Raise + focus the top-level X11 window owned by ``pid``.

    Returns True when the window was found and the activation request was
    sent. No-op (False) off X11 or when the window has not mapped yet.
    Uses ctypes/libX11 directly to avoid new dependencies.
    """
    if os.name == "nt" or not os.environ.get("DISPLAY"):
        return False
    import ctypes
    from ctypes import (
        POINTER, byref, c_char_p, c_int, c_long, c_ulong, c_void_p,
    )

    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    except OSError:
        return False

    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return False
    try:
        # Declare full prototypes: without them ctypes assumes 32-bit ints and
        # corrupts the 64-bit long arguments of XGetWindowProperty.
        x11.XDefaultRootWindow.restype = c_ulong
        x11.XDefaultRootWindow.argtypes = [c_void_p]
        x11.XInternAtom.restype = c_ulong
        x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x11.XGetWindowProperty.restype = c_int
        x11.XGetWindowProperty.argtypes = [
            c_void_p, c_ulong, c_ulong, c_long, c_long, c_int, c_ulong,
            POINTER(c_ulong), POINTER(c_int), POINTER(c_ulong),
            POINTER(c_ulong), POINTER(POINTER(c_ulong)),
        ]
        x11.XFree.argtypes = [c_void_p]
        x11.XFlush.argtypes = [c_void_p]
        x11.XCloseDisplay.argtypes = [c_void_p]
        root = x11.XDefaultRootWindow(dpy)

        net_client_list = x11.XInternAtom(dpy, b"_NET_CLIENT_LIST", 0)
        net_wm_pid = x11.XInternAtom(dpy, b"_NET_WM_PID", 0)
        net_active_window = x11.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
        XA_WINDOW, XA_CARDINAL = 33, 6

        actual_type = c_ulong()
        actual_format = c_int()
        nitems = c_ulong()
        bytes_after = c_ulong()

        prop = POINTER(c_ulong)()
        status = x11.XGetWindowProperty(
            dpy, root, net_client_list, 0, 1024, 0, XA_WINDOW,
            byref(actual_type), byref(actual_format), byref(nitems),
            byref(bytes_after), byref(prop),
        )
        if status != 0 or not prop:
            return False
        try:
            windows = [int(prop[i]) for i in range(nitems.value)]
        finally:
            x11.XFree(prop)

        target = None
        for wid in windows:
            wprop = POINTER(c_ulong)()
            status = x11.XGetWindowProperty(
                dpy, c_ulong(wid), net_wm_pid, 0, 1, 0, XA_CARDINAL,
                byref(actual_type), byref(actual_format), byref(nitems),
                byref(bytes_after), byref(wprop),
            )
            if status == 0 and wprop and nitems.value == 1 and int(wprop[0]) == pid:
                target = wid
            if wprop:
                x11.XFree(wprop)
            if target is not None:
                break
        if target is None:
            return False

        class _ClientData(ctypes.Union):
            _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10), ("l", c_long * 5)]

        class _ClientMessage(ctypes.Structure):
            _fields_ = [
                ("type", c_int), ("serial", c_ulong), ("send_event", c_int),
                ("display", c_void_p), ("window", c_ulong),
                ("message_type", c_ulong), ("format", c_int), ("data", _ClientData),
            ]

        class _XEvent(ctypes.Union):
            _fields_ = [("type", c_int), ("xclient", _ClientMessage), ("pad", c_long * 24)]

        event = _XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.window = target
        event.xclient.message_type = net_active_window
        event.xclient.format = 32
        event.xclient.data.l[0] = 2  # source: direct user action (launcher)
        event.xclient.data.l[1] = 0  # timestamp: CurrentTime
        event.xclient.data.l[2] = 0  # no requestor window
        mask = (1 << 20) | (1 << 21)  # SubstructureRedirect | SubstructureNotify
        x11.XSendEvent(dpy, root, 0, mask, byref(event))
        x11.XFlush(dpy)
        return True
    except Exception:  # pragma: no cover - X11 interop is best-effort
        return False
    finally:
        x11.XCloseDisplay(dpy)