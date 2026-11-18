"""Tests for the dashboard tool-call trace hook (thinking panel)."""

from __future__ import annotations
import json
import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock
from collections.abc import Iterator

import pytest

from jisu.dashboard.constants import TRACE_MARKER
from jisu.dashboard.log_buffer import parse_trace
from jisu.dashboard.trace.hook import HOOK_TARGET, shorten, install_trace_hook
from reachy_mini_conversation_app.tools.tool_constants import ToolState
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    BackgroundToolManager,
)


TRACE_LOGGER = "jisu.dashboard.trace.hook"


def _routine(
    tool_name: str = "move_head",
    args_json: str = '{"direction": "left"}',
    result: dict[str, Any] | None = None,
    delay: float = 0.0,
) -> ToolCallRoutine:
    """Mock routine following the dispatcher contract (errors come back as dicts)."""
    routine = MagicMock(spec=ToolCallRoutine)
    routine.tool_name = tool_name
    routine.args_json_str = args_json

    async def _call(manager: BackgroundToolManager) -> dict[str, Any]:
        try:
            if delay:
                await asyncio.sleep(delay)
            return result if result is not None else {"status": "ok"}
        except asyncio.CancelledError:
            return {"error": "Tool cancelled"}

    routine.side_effect = _call
    return routine


@pytest.fixture
def hook() -> Iterator[None]:
    """Install the trace hook for one test and restore the original afterwards."""
    original = getattr(BackgroundToolManager, HOOK_TARGET)
    installed = install_trace_hook()
    try:
        yield
    finally:
        installed.uninstall()
    assert getattr(BackgroundToolManager, HOOK_TARGET) is original


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    events = []
    for record in caplog.records:
        if record.name != TRACE_LOGGER:
            continue
        event = parse_trace(record.getMessage())
        if event is not None:
            events.append(event)
    return events


async def _run(manager: BackgroundToolManager, routine: ToolCallRoutine, *, call_id: str = "call_1") -> Any:
    tool = await manager.start_tool(call_id=call_id, tool_call_routine=routine, is_idle_tool_call=False)
    assert tool._task is not None
    await tool._task
    return tool


@pytest.mark.asyncio
async def test_success_emits_start_and_end(hook: None, caplog: pytest.LogCaptureFixture) -> None:
    """A completed tool reports parsed args, then status/result/duration."""
    caplog.set_level(logging.INFO, logger=TRACE_LOGGER)
    tool = await _run(BackgroundToolManager(), _routine(result={"status": "looking left"}))

    assert tool.status == ToolState.COMPLETED
    assert tool.result == {"status": "looking left"}
    start, end = _events(caplog)
    assert start == {
        "event": "tool_start",
        "id": "call_1",
        "tool": "move_head",
        "idle": False,
        "args": {"direction": "left"},
    }
    assert end["event"] == "tool_end"
    assert end["status"] == "completed"
    assert end["result"] == {"status": "looking left"}
    assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
    assert "error" not in end


@pytest.mark.asyncio
async def test_error_result_is_reported(hook: None, caplog: pytest.LogCaptureFixture) -> None:
    """``{"error": ...}`` results are reported as failed with the error text."""
    caplog.set_level(logging.INFO, logger=TRACE_LOGGER)
    await _run(BackgroundToolManager(), _routine(result={"error": "camera unavailable"}))

    end = _events(caplog)[-1]
    assert end["status"] == "failed"
    assert end["error"] == "camera unavailable"
    assert "result" not in end


@pytest.mark.asyncio
async def test_cancelled_tool_is_reported(hook: None, caplog: pytest.LogCaptureFixture) -> None:
    """Cancelling a running tool still produces an end event."""
    caplog.set_level(logging.INFO, logger=TRACE_LOGGER)
    manager = BackgroundToolManager()
    tool = await manager.start_tool(call_id="c", tool_call_routine=_routine(delay=5), is_idle_tool_call=True)
    await asyncio.sleep(0)
    assert await manager.cancel_tool(tool.tool_id)
    assert tool._task is not None
    await asyncio.gather(tool._task, return_exceptions=True)

    start, end = _events(caplog)
    assert start["idle"] is True
    assert end["status"] == "cancelled"


@pytest.mark.asyncio
async def test_bad_args_and_long_results_are_shortened(hook: None, caplog: pytest.LogCaptureFixture) -> None:
    """Non-JSON args are kept as text; huge strings (base64 frames) are cut."""
    caplog.set_level(logging.INFO, logger=TRACE_LOGGER)
    await _run(BackgroundToolManager(), _routine(args_json="not json", result={"b64_im": "A" * 10_000}))

    start, end = _events(caplog)
    assert start["args"] == "not json"
    assert end["result"]["b64_im"].endswith("chars)")
    assert len(end["result"]["b64_im"]) < 2000


@pytest.mark.asyncio
async def test_trace_failure_does_not_change_tool(
    hook: None, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If emitting a trace blows up, the tool still runs and returns its result."""
    import jisu.dashboard.trace.hook as trace_hook

    def _boom(_build: Any) -> dict[str, Any]:
        raise RuntimeError("trace broken")

    monkeypatch.setattr(trace_hook, "_start_event", lambda *_a: _boom(None))
    monkeypatch.setattr(trace_hook, "_end_event", lambda *_a: _boom(None))
    tool = await _run(BackgroundToolManager(), _routine(result={"ok": True}))

    assert tool.status == ToolState.COMPLETED
    assert tool.result == {"ok": True}
    assert _events(caplog) == []


def test_install_is_idempotent() -> None:
    """Installing twice wraps once; uninstall restores the true original."""
    original = getattr(BackgroundToolManager, HOOK_TARGET)
    first = install_trace_hook()
    second = install_trace_hook()
    try:
        assert first.original is original
        assert second.original is original
    finally:
        second.uninstall()
    assert getattr(BackgroundToolManager, HOOK_TARGET) is original


def test_shorten_limits_containers() -> None:
    """Long lists/dicts are summarized; bytes and objects become safe strings."""
    assert shorten(list(range(5)), max_items=3) == [0, 1, 2, "…(+2 more items)"]
    assert shorten({"a": 1, "b": 2}, max_items=1) == {"a": 1, "…": "+1 more keys"}
    assert shorten(b"\x00\x01") == "<2 bytes>"
    assert shorten(object()).startswith("<object object")
    json.dumps(shorten({"nested": [{"x": "y" * 5000}]}))


def test_trace_marker_line_roundtrip() -> None:
    """The emitted line format is what the log buffer parses."""
    line = TRACE_MARKER + json.dumps({"event": "tool_start", "id": "1"}, ensure_ascii=False)
    assert parse_trace(line) == {"event": "tool_start", "id": "1"}
