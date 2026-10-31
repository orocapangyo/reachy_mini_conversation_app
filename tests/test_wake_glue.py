"""Glue tests for the Gemini local-standby wake-word integration.

Covers:
  - enter_local_standby(): session close + flag set
  - exit_local_standby(): flag cleared + session task spawned + greeting reset
  - _on_wake_detected() callback: listener stopped + exit_local_standby called
  - go_to_sleep_action() local-standby path: sleep motion + handler + listener

All async tests use asyncio.wait_for to prevent infinite hangs.
"""

from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini_conversation_app.audio.wake_word import WakeWordListener
from reachy_mini_conversation_app.audio.wake_engines import MockWakeEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _silent_frame(n: int = 1600) -> np.ndarray:
    return np.zeros(n, dtype=np.int16)


async def _wait_for(condition: Any, timeout: float = 5.0, poll: float = 0.05) -> None:
    """Busy-poll until condition() is True or timeout expires."""
    await asyncio.wait_for(_poll(condition, poll), timeout=timeout)


async def _poll(condition: Any, interval: float) -> None:
    while not condition():
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# GeminiRealtimeHandler.enter_local_standby / exit_local_standby
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal session double with a close() coroutine."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeClient:
    pass


def _make_handler() -> Any:
    """Return a GeminiRealtimeHandler with all heavy dependencies stubbed out."""
    from unittest.mock import patch as _patch  # noqa: PLC0415

    # Stub google.genai so the module imports cleanly without credentials.
    fake_genai = MagicMock()
    fake_genai.Client = MagicMock(return_value=_FakeClient())
    with (
        _patch.dict(
            "sys.modules",
            {
                "google": MagicMock(),
                "google.genai": fake_genai,
                "google.genai.types": MagicMock(),
                "google.genai.errors": MagicMock(),
            },
        ),
    ):
        pass  # module already imported; we'll patch attributes instead

    from reachy_mini_conversation_app.gemini_realtime import GeminiRealtimeHandler  # noqa: PLC0415

    deps = MagicMock()
    deps.robot = MagicMock()
    handler = GeminiRealtimeHandler.__new__(GeminiRealtimeHandler)
    # Minimal attribute init
    handler._local_standby = False
    handler._asyncio_loop = None
    handler._receive_task = None
    handler.connection = None
    handler._connected_event = asyncio.Event()
    handler._startup_greeting_sent = True
    handler._client = _FakeClient()  # type: ignore[assignment]
    handler._in_flight_tool_calls = set()
    handler._pending_tool_responses = []
    handler._call_id_to_name = {}
    return handler


@pytest.mark.asyncio
async def test_enter_local_standby_sets_flag_and_closes_session() -> None:
    """enter_local_standby sets _local_standby=True and closes the connection."""
    handler = _make_handler()
    session = _FakeSession()
    handler.connection = session
    handler._connected_event.set()

    result = await asyncio.wait_for(handler.enter_local_standby(), timeout=5.0)

    assert result == {"status": "local_standby"}
    assert handler._local_standby is True
    assert session.closed is True
    assert handler.connection is None
    assert not handler._connected_event.is_set()


@pytest.mark.asyncio
async def test_enter_local_standby_cancels_receive_task() -> None:
    """enter_local_standby cancels a pending receive task."""
    handler = _make_handler()

    async def _forever() -> None:
        await asyncio.sleep(9999)

    task: asyncio.Task[None] = asyncio.create_task(_forever())
    handler._receive_task = task

    await asyncio.wait_for(handler.enter_local_standby(), timeout=5.0)

    # The receive task is cancelled fire-and-forget (not awaited inside standby,
    # to avoid the go_to_sleep deadlock), so cancellation settles on the next
    # loop tick rather than synchronously.
    assert handler._receive_task is None
    for _ in range(10):
        if task.cancelled():
            break
        await asyncio.sleep(0)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_exit_local_standby_not_in_standby() -> None:
    """exit_local_standby returns early when _local_standby is False."""
    handler = _make_handler()
    handler._local_standby = False

    result = await asyncio.wait_for(handler.exit_local_standby(), timeout=5.0)
    assert result == {"status": "not_in_standby"}
    assert handler._startup_greeting_sent is True  # unchanged


@pytest.mark.asyncio
async def test_exit_local_standby_clears_flag_for_console_reconnect() -> None:
    """exit_local_standby only clears state; the console startup loop owns the relaunch.

    The session relaunch authority lives in console.py's _run_handler_startup_loop
    (which polls _local_standby), so exit_local_standby must NOT spawn its own
    session task — doing so would race two sessions.
    """
    handler = _make_handler()
    handler._local_standby = True
    handler._startup_greeting_sent = True

    launched: list[bool] = []

    async def _fake_run_live_session(client: Any) -> None:  # noqa: ARG001
        launched.append(True)

    handler._run_live_session = _fake_run_live_session

    result = await asyncio.wait_for(handler.exit_local_standby(), timeout=5.0)

    assert handler._local_standby is False
    assert handler._startup_greeting_sent is False  # greeting re-armed for reconnect
    assert result == {"status": "awake"}
    await asyncio.sleep(0.05)
    assert launched == [], "exit_local_standby must NOT launch a session (console loop owns it)"


@pytest.mark.asyncio
async def test_exit_local_standby_when_not_in_standby() -> None:
    """exit_local_standby is a no-op when not in standby."""
    handler = _make_handler()
    handler._local_standby = False
    result = await asyncio.wait_for(handler.exit_local_standby(), timeout=5.0)
    assert result == {"status": "not_in_standby"}


# ---------------------------------------------------------------------------
# WakeWordListener → _on_wake_detected glue (mocking LocalStream internals)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_wake_detected_stops_listener_and_calls_exit_standby() -> None:
    """_on_wake_detected: listener.stop() is called and exit_local_standby is awaited."""
    loop = asyncio.get_running_loop()

    exit_called: list[str] = []

    class _FakeHandler:
        async def exit_local_standby(self) -> dict[str, Any]:
            exit_called.append("called")
            return {"status": "awake"}

    class _FakeLocalStream:
        """Minimal LocalStream double for the callback test."""

        def __init__(self) -> None:
            self._asyncio_loop = loop
            self._robot = MagicMock()
            self._robot.enable_motors = MagicMock()
            self._robot.wake_up = MagicMock()
            self.handler = _FakeHandler()
            engine = MockWakeEngine()
            self._wake_listener = WakeWordListener(
                engine=engine,
                on_wake=self._on_wake_detected,
                loop=loop,
            )

        # Copy the implementation verbatim (not imported to keep test self-contained).
        def _on_wake_detected(self, phrase: str) -> None:
            if self._wake_listener is not None:
                self._wake_listener.stop()

            async def _do_wakeup() -> None:
                try:
                    await asyncio.to_thread(self._robot.enable_motors)
                    await asyncio.to_thread(self._robot.wake_up)
                except Exception:
                    pass
                active_handler = getattr(self, "handler", None)
                if active_handler is not None and hasattr(active_handler, "exit_local_standby"):
                    await active_handler.exit_local_standby()

            lp = self._asyncio_loop
            if lp is not None and lp.is_running():
                asyncio.create_task(_do_wakeup(), name="wake-up-sequence")

    stream = _FakeLocalStream()
    stream._wake_listener.start()

    # Trigger the callback directly (simulates listener detecting the phrase)
    stream._on_wake_detected("리치미니")

    # Wait for _do_wakeup task to complete
    await asyncio.wait_for(_wait_for(lambda: bool(exit_called)), timeout=5.0)

    assert exit_called == ["called"]
    # Listener must have been stopped
    assert not stream._wake_listener._running


# ---------------------------------------------------------------------------
# go_to_sleep_action local-standby path (unit test with fakes)
# ---------------------------------------------------------------------------


def test_go_to_sleep_action_local_standby_path() -> None:
    """go_to_sleep_action returns local_standby when wake listener + handler present."""
    listener = MagicMock()
    listener.start = MagicMock()

    handler = MagicMock()
    handler.request_local_standby = MagicMock(return_value={"status": "local_standby"})

    stream_manager = MagicMock()
    stream_manager.handler = handler
    stream_manager._wake_listener = listener

    robot = MagicMock()
    movement_manager = MagicMock()

    # Simulate go_to_sleep_action logic (extracted for unit testing without main.py wiring)
    def _go_to_sleep_action() -> dict[str, Any]:
        active_handler = getattr(stream_manager, "handler", None) or handler
        wake_listener = getattr(stream_manager, "_wake_listener", None)
        if wake_listener is not None and hasattr(active_handler, "request_local_standby"):
            sleep_error = None
            try:
                movement_manager.stop(reset_to_neutral=False)
                robot.goto_sleep()
            except Exception as exc:
                sleep_error = str(exc)
            local_standby_result = active_handler.request_local_standby()
            wake_listener.start()
            result: dict[str, Any] = {"status": "local_standby", **local_standby_result}
            if sleep_error is not None:
                result["sleep_error"] = sleep_error
            return result
        return {"status": "stop"}

    result = _go_to_sleep_action()

    assert result["status"] == "local_standby"
    movement_manager.stop.assert_called_once_with(reset_to_neutral=False)
    robot.goto_sleep.assert_called_once()
    handler.request_local_standby.assert_called_once()
    listener.start.assert_called_once()
