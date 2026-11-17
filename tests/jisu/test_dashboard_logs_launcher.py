"""Tests for the dashboard log buffer and launcher command builders."""

from __future__ import annotations
import asyncio
from pathlib import Path

import pytest

from jisu.dashboard.launcher import (
    APP_ENTRY_MODULE,
    LaunchSettings,
    build_app_env,
    find_executable,
    build_app_command,
    build_daemon_command,
)
from jisu.dashboard.log_buffer import LogBuffer, parse_log_line


# --------------------------------------------------------------------------
# log parsing / buffer
# --------------------------------------------------------------------------


def test_parse_app_log_format() -> None:
    """Lines in the app's logging format are split into level/logger/message."""
    line = "2026-11-17 10:00:00,123 WARNING reachy_mini_conversation_app.main:108 | Daemon not ready | retry"
    assert parse_log_line(line) == (
        "WARNING",
        "reachy_mini_conversation_app.main:108",
        "Daemon not ready | retry",
    )


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("INFO:     Uvicorn running", "INFO"),
        ("WARN something odd", "WARNING"),
        ("ERROR: boom", "ERROR"),
        ("Traceback (most recent call last):", "ERROR"),
        ("ValueError: bad", "ERROR"),
        ("plain output", "INFO"),
    ],
)
def test_parse_fallback_levels(line: str, level: str) -> None:
    """Other tools' output still gets a sensible level."""
    assert parse_log_line(line)[0] == level


def test_buffer_snapshot_and_capacity() -> None:
    """The buffer keeps the newest lines and filters by sequence number."""
    logs = LogBuffer(max_lines=3)
    for index in range(5):
        logs.append("app", f"line {index}\n")
    entries = logs.snapshot()
    assert [e.raw for e in entries] == ["line 2", "line 3", "line 4"]
    assert [e.raw for e in logs.snapshot(after_seq=entries[1].seq)] == ["line 4"]
    logs.clear()
    assert logs.snapshot() == []
    assert logs.append("app", "next").seq == 6


@pytest.mark.asyncio
async def test_buffer_pushes_to_subscribers_from_threads() -> None:
    """Lines appended from another thread reach the subscriber queue."""
    logs = LogBuffer()
    queue = logs.subscribe()
    await asyncio.to_thread(logs.append, "daemon", "hello from thread")
    entry = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert (entry.source, entry.raw) == ("daemon", "hello from thread")
    logs.unsubscribe(queue)
    logs.append("app", "not delivered")
    await asyncio.sleep(0)
    assert queue.empty()


# --------------------------------------------------------------------------
# launcher command builders
# --------------------------------------------------------------------------


def test_daemon_command_headless() -> None:
    """Headless runs the daemon without the viewer on every OS."""
    argv, env = build_daemon_command(Path("/venv/bin"), headless=True, system="Darwin")
    assert argv == [str(Path("/venv/bin/reachy-mini-daemon")), "--sim", "--headless"]
    assert env == {}


def test_daemon_command_macos_uses_mjpython(monkeypatch: pytest.MonkeyPatch) -> None:
    """MacOS needs mjpython plus the interpreter LIBDIR on the dyld path."""
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/opt/lib")
    argv, env = build_daemon_command(Path("/venv/bin"), headless=False, system="Darwin", libdir="/py/lib")
    assert argv == [str(Path("/venv/bin/mjpython")), str(Path("/venv/bin/reachy-mini-daemon")), "--sim"]
    assert env == {"DYLD_FALLBACK_LIBRARY_PATH": "/py/lib:/opt/lib"}


def test_daemon_command_linux_and_windows(tmp_path: Path) -> None:
    """Linux runs the script directly; Windows uses the .exe console script."""
    argv, env = build_daemon_command(tmp_path, headless=False, system="Linux")
    assert argv == [str(tmp_path / "reachy-mini-daemon"), "--sim"] and env == {}
    argv, _env = build_daemon_command(tmp_path, headless=False, system="Windows")
    assert argv == [str(tmp_path / "reachy-mini-daemon.exe"), "--sim"]


def test_find_executable_prefers_existing_windows_script(tmp_path: Path) -> None:
    """On Windows an extension-less script is used only when no .exe exists."""
    (tmp_path / "tool").write_text("", encoding="utf-8")
    assert find_executable(tmp_path, "tool", "Windows") == tmp_path / "tool"
    (tmp_path / "tool.exe").write_text("", encoding="utf-8")
    assert find_executable(tmp_path, "tool", "Windows") == tmp_path / "tool.exe"
    assert find_executable(tmp_path, "tool", "Linux") == tmp_path / "tool"


def test_app_command_and_env() -> None:
    """The app starts through the memory-hook entry point with the sim connection env."""
    settings = LaunchSettings(
        profile="p1",
        memory_backend="gbrain",
        debug=True,
        daemon_port=8123,
        conversation_backend="gemini",
        app_extra_args=["--no-camera"],
    )
    assert build_app_command("/py", settings) == [
        "/py",
        "-u",
        "-m",
        APP_ENTRY_MODULE,
        "--ui",
        "--profile",
        "p1",
        "--debug",
        "--no-camera",
    ]
    env = build_app_env({"KEEP": "1"}, settings)
    assert env["KEEP"] == "1"
    assert env["REACHY_MINI_PORT"] == "8123"
    assert env["REACHY_MINI_CUSTOM_PROFILE"] == "p1"
    assert env["JISU_MEMORY_BACKEND"] == "gbrain"
    assert env["CONVERSATION_BACKEND"] == "gemini"
    assert env["PYTHONUTF8"] == "1"


def test_app_env_keeps_conversation_backend_from_dotenv() -> None:
    """Without an override the app's own .env decides the conversation backend."""
    env = build_app_env({}, LaunchSettings())
    assert "CONVERSATION_BACKEND" not in env
    assert env["JISU_MEMORY_BACKEND"] == "json"
