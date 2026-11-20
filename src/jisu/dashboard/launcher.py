"""Start/stop the MuJoCo simulation daemon and the conversation app as subprocesses.

Python port of ``run_sim_mujoco.sh`` (works on macOS, Linux and Windows). Both
processes' stdout/stderr are streamed into a :class:`LogBuffer`.
"""

from __future__ import annotations
import os
import sys
import time
import signal
import socket
import platform
import sysconfig
import threading
import subprocess
from typing import Any
from pathlib import Path
from dataclasses import field, dataclass
from collections.abc import Mapping, Sequence

import httpx

from jisu.tools import TOOLS_DIR
from jisu.persona.seed import SeedFormatError, apply_seed
from jisu.dashboard.constants import (
    APP_UI_PORT,
    DAEMON_PORT,
    DEFAULT_PROFILE,
    DAEMON_WAIT_MAX_S,
    DAEMON_HEALTH_PATH,
    MEMORY_BACKEND_ENV,
    DAEMON_POLL_INTERVAL_S,
    DEFAULT_MEMORY_BACKEND,
    EXTERNAL_TOOLS_DIR_ENV,
    PROCESS_KILL_TIMEOUT_S,
    PROCESS_STOP_TIMEOUT_S,
    DAEMON_HEALTH_TIMEOUT_S,
    AUTOLOAD_EXTERNAL_TOOLS_ENV,
)
from jisu.dashboard.log_buffer import LogBuffer
from jisu.dashboard.memory.gbrain_backend import GBrainMemoryBackend, ensure_brain_initialized


APP_ENTRY_MODULE = "jisu.dashboard.app_entry"
DASHBOARD_SOURCE = "dashboard"
DAEMON_SOURCE = "daemon"
APP_SOURCE = "app"
GBRAIN_BACKEND_NAME = "gbrain"


@dataclass
class LaunchSettings:
    """What to launch and how."""

    profile: str = DEFAULT_PROFILE
    headless: bool = False
    daemon_port: int = DAEMON_PORT
    app_port: int = APP_UI_PORT
    memory_backend: str = DEFAULT_MEMORY_BACKEND
    conversation_backend: str | None = None
    debug: bool = False
    manage_daemon: bool = True
    app_extra_args: list[str] = field(default_factory=list)


# -- pure command builders (unit-tested) ------------------------------------


def venv_bin_dir() -> Path:
    """Directory holding this interpreter's console scripts (``bin`` / ``Scripts``)."""
    return Path(sys.executable).parent


def find_executable(bin_dir: Path, name: str, system: str | None = None) -> Path:
    """Return ``bin_dir/name`` with the Windows ``.exe`` suffix when needed."""
    system = system or platform.system()
    if system == "Windows":
        exe = bin_dir / f"{name}.exe"
        if exe.exists() or not (bin_dir / name).exists():
            return exe
    return bin_dir / name


def build_daemon_command(
    bin_dir: Path,
    *,
    headless: bool,
    system: str | None = None,
    libdir: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return ``(argv, extra_env)`` for the simulation daemon.

    On macOS the MuJoCo viewer only runs under ``mjpython``, which needs the
    interpreter's LIBDIR on the dyld fallback path.
    """
    system = system or platform.system()
    daemon = str(find_executable(bin_dir, "reachy-mini-daemon", system))
    if headless:
        return [daemon, "--sim", "--headless"], {}
    if system == "Darwin":
        resolved_libdir = libdir if libdir is not None else str(sysconfig.get_config_var("LIBDIR") or "")
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        dyld = f"{resolved_libdir}:{existing}" if existing else resolved_libdir
        mjpython = str(find_executable(bin_dir, "mjpython", system))
        return [mjpython, daemon, "--sim"], {"DYLD_FALLBACK_LIBRARY_PATH": dyld}
    return [daemon, "--sim"], {}


def build_app_command(python: str, settings: LaunchSettings) -> list[str]:
    """Return argv for the conversation app started through the memory-hook entry point."""
    argv = [python, "-u", "-m", APP_ENTRY_MODULE, "--ui", "--profile", settings.profile]
    if settings.debug:
        argv.append("--debug")
    argv.extend(settings.app_extra_args)
    return argv


def build_app_env(base_env: Mapping[str, str], settings: LaunchSettings) -> dict[str, str]:
    """Return the environment for the conversation app subprocess."""
    env = dict(base_env)
    env.update(
        {
            "REACHY_MINI_HOST": "localhost",
            "REACHY_MINI_PORT": str(settings.daemon_port),
            "REACHY_MINI_CUSTOM_PROFILE": settings.profile,
            MEMORY_BACKEND_ENV: settings.memory_backend,
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            # jisu-owned LLM tools (update_persona); upstream tools/ stays untouched.
            EXTERNAL_TOOLS_DIR_ENV: str(TOOLS_DIR),
            AUTOLOAD_EXTERNAL_TOOLS_ENV: "1",
        }
    )
    if settings.conversation_backend:
        env["CONVERSATION_BACKEND"] = settings.conversation_backend
    return env


def daemon_health_url(port: int) -> str:
    """Return the daemon readiness URL."""
    return f"http://127.0.0.1:{port}{DAEMON_HEALTH_PATH}"


def daemon_ready(port: int) -> bool:
    """Return whether the daemon answers its health endpoint."""
    try:
        response = httpx.get(daemon_health_url(port), timeout=DAEMON_HEALTH_TIMEOUT_S)
    except httpx.HTTPError:
        return False
    return response.status_code < 400


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether something accepts TCP connections on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


# -- process management -----------------------------------------------------


class ManagedProcess:
    """One subprocess whose output lines go to a :class:`LogBuffer`."""

    def __init__(self, name: str, logs: LogBuffer) -> None:
        """Create an idle process slot labelled ``name`` in the logs."""
        self.name = name
        self._logs = logs
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self.started_at: float | None = None

    @property
    def pid(self) -> int | None:
        """PID of the running process, if any."""
        return self._proc.pid if self._proc is not None else None

    def is_running(self) -> bool:
        """Return whether the process is alive."""
        return self._proc is not None and self._proc.poll() is None

    def returncode(self) -> int | None:
        """Exit code once finished, else None."""
        return self._proc.poll() if self._proc is not None else None

    def start(self, argv: Sequence[str], env: Mapping[str, str], cwd: Path | None = None) -> None:
        """Spawn the process (no-op if already running)."""
        if self.is_running():
            return
        # Own process group/session: a Ctrl+C in the dashboard terminal must not hit the
        # children directly; the dashboard stops them itself (one clean interrupt each).
        is_windows = platform.system() == "Windows"
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if is_windows else 0
        self._logs.append(DASHBOARD_SOURCE, f"INFO: starting {self.name}: {' '.join(argv)}")
        self._proc = subprocess.Popen(
            list(argv),
            env=dict(env),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            start_new_session=not is_windows,
        )
        self.started_at = time.time()
        self._reader = threading.Thread(target=self._pump, name=f"{self.name}-log-reader", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._logs.append(self.name, line)
        code = proc.wait()
        level = "INFO" if code in (0, -signal.SIGINT, 130) else "WARNING"
        self._logs.append(DASHBOARD_SOURCE, f"{level}: {self.name} exited with code {code}")

    def stop(self, *, graceful: bool = True) -> None:
        """Stop the process: interrupt (Ctrl+C semantics) first, then kill."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._logs.append(DASHBOARD_SOURCE, f"INFO: stopping {self.name} (pid {proc.pid})")
        try:
            if graceful:
                _send_interrupt(proc)
            else:
                proc.terminate()
            proc.wait(timeout=PROCESS_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._logs.append(DASHBOARD_SOURCE, f"WARNING: {self.name} did not stop in time; killing")
            proc.kill()
            try:
                proc.wait(timeout=PROCESS_KILL_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        if self._reader is not None:
            self._reader.join(timeout=PROCESS_KILL_TIMEOUT_S)


def _send_interrupt(proc: subprocess.Popen[str]) -> None:
    """Deliver Ctrl+C (POSIX SIGINT / Windows CTRL_BREAK) so the app runs its sleep-on-exit path."""
    if platform.system() == "Windows":
        ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break is not None:
            proc.send_signal(ctrl_break)
            return
        proc.terminate()
        return
    proc.send_signal(signal.SIGINT)


class SimulationLauncher:
    """Owns the daemon + conversation app processes for the dashboard."""

    def __init__(self, settings: LaunchSettings, logs: LogBuffer, *, cwd: Path | None = None) -> None:
        """Prepare (but do not start) the processes."""
        self.settings = settings
        self.logs = logs
        self._cwd = cwd
        self._bin_dir = venv_bin_dir()
        self._lock = threading.RLock()
        self.daemon = ManagedProcess(DAEMON_SOURCE, logs)
        self.app = ManagedProcess(APP_SOURCE, logs)
        self.daemon_reused = False

    def _log(self, message: str) -> None:
        self.logs.append(DASHBOARD_SOURCE, message)

    def _prepare_memory_backend(self) -> None:
        """Create the GBrain brain on first use, so the default backend works out of the box."""
        if self.settings.memory_backend != GBRAIN_BACKEND_NAME:
            return
        brain = GBrainMemoryBackend(require_ready=False)
        if brain.is_initialized():
            return
        self._log(f"INFO: initializing GBrain brain at {brain.home} (first run, may take a while)")
        message = ensure_brain_initialized(brain=brain)
        if message:
            self._log(message)
        if brain.is_initialized():
            self._seed_persona(brain)

    def _seed_persona(self, brain: GBrainMemoryBackend) -> None:
        """Put the profile's initial persona into a freshly created brain."""
        try:
            result = apply_seed(brain, self.settings.profile)
        except SeedFormatError as exc:
            self._log(f"WARNING: persona seed not applied: {exc}")
            return
        if result.saved or result.existing or result.skipped:
            self._log(f"INFO: persona seed {result.path.name}: {result.summary()}")

    def ensure_daemon(self) -> None:
        """Reuse a running daemon or start one and wait until it is healthy."""
        with self._lock:
            port = self.settings.daemon_port
            if daemon_ready(port):
                if not self.daemon.is_running():
                    self.daemon_reused = True
                    self._log(f"INFO: reusing simulation daemon already running on port {port}")
                return
            if not self.settings.manage_daemon:
                raise RuntimeError(f"no daemon on port {port} and daemon management is disabled")

            argv, extra_env = build_daemon_command(self._bin_dir, headless=self.settings.headless)
            env = {**os.environ, **extra_env}
            self.daemon.start(argv, env, cwd=self._cwd)
            self.daemon_reused = False
            deadline = time.monotonic() + DAEMON_WAIT_MAX_S
            while time.monotonic() < deadline:
                if not self.daemon.is_running():
                    raise RuntimeError(f"simulation daemon exited early (code {self.daemon.returncode()})")
                if daemon_ready(port):
                    self._log(f"INFO: simulation daemon ready on port {port}")
                    return
                time.sleep(DAEMON_POLL_INTERVAL_S)
            raise RuntimeError(f"simulation daemon not ready after {DAEMON_WAIT_MAX_S:.0f}s")

    def start_app(self, memory_backend: str | None = None) -> None:
        """Start the conversation app (and the daemon if needed)."""
        with self._lock:
            if memory_backend:
                self.settings.memory_backend = memory_backend.strip().lower()
            if self.app.is_running():
                return
            if port_in_use(self.settings.app_port):
                raise RuntimeError(
                    f"port {self.settings.app_port} is already in use: stop the other conversation app first"
                )
            self.ensure_daemon()
            self._prepare_memory_backend()
            argv = build_app_command(sys.executable, self.settings)
            env = build_app_env(os.environ, self.settings)
            self.app.start(argv, env, cwd=self._cwd)

    def stop_app(self) -> None:
        """Stop the conversation app (robot goes to sleep like on Ctrl+C)."""
        with self._lock:
            self.app.stop()

    def restart_app(self, memory_backend: str | None = None) -> None:
        """Restart the conversation app, optionally switching the memory backend."""
        with self._lock:
            self.stop_app()
            self.start_app(memory_backend)

    def shutdown(self) -> None:
        """Stop the app and the daemon we started (a reused daemon keeps running)."""
        with self._lock:
            self.app.stop()
            if self.daemon.is_running():
                self.daemon.stop(graceful=False)

    def status(self) -> dict[str, Any]:
        """Return a JSON-serializable status summary."""
        return {
            "app": {
                "running": self.app.is_running(),
                "pid": self.app.pid,
                "returncode": self.app.returncode(),
                "started_at": self.app.started_at,
                "port": self.settings.app_port,
            },
            "daemon": {
                "managed": self.daemon.is_running(),
                "reused": self.daemon_reused,
                "pid": self.daemon.pid,
                "port": self.settings.daemon_port,
            },
            "memory_backend": self.settings.memory_backend,
            "profile": self.settings.profile,
        }
