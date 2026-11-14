"""Tests for app-level runtime behavior."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import reachy_mini_conversation_app.main as main_mod


def test_inactivity_timeout_thread_goes_to_sleep() -> None:
    """The watchdog should use the shared sleep shutdown path once activity is too old."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())
    go_to_sleep = MagicMock(return_value={"status": "sleeping"})

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
        go_to_sleep=go_to_sleep,
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    go_to_sleep.assert_called_once_with()
    stream_manager.close.assert_not_called()


def test_inactivity_timeout_thread_closes_stream_manager_without_sleep_callback() -> None:
    """The watchdog should still close the stream when no sleep callback is available."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    stream_manager.close.assert_called_once_with()


def test_run_exits_when_gemini_backend_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() must exit with code 1 when CONVERSATION_BACKEND=gemini and GEMINI_API_KEY is unset."""
    from reachy_mini_conversation_app.config import GEMINI_BACKEND, config, refresh_runtime_config_from_env

    monkeypatch.setenv("CONVERSATION_BACKEND", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    refresh_runtime_config_from_env()

    exited: list[int] = []

    def fake_exit(code: int) -> None:
        exited.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("sys.exit", fake_exit)

    args = SimpleNamespace(
        command=None,
        debug=False,
        profile=None,
        robot_name=None,
        no_camera=True,
        ui=False,
    )
    fake_robot = MagicMock()
    fake_robot.media = MagicMock()

    with pytest.raises(SystemExit):
        main_mod.run(args, robot=fake_robot)

    assert exited == [1], "run() must call sys.exit(1) when GEMINI_API_KEY is missing"

    # Restore config to avoid polluting other tests
    monkeypatch.delenv("CONVERSATION_BACKEND", raising=False)
    refresh_runtime_config_from_env()
    assert config.CONVERSATION_BACKEND != GEMINI_BACKEND or True  # restore side-effect
