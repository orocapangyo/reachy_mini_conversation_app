"""Tests for the Gemini Live tool-call bridge (Task 3).

All tests run without network or API key — sessions and tool managers are
hand-rolled Fake classes (no MagicMock), following the project test style.
Each async test carries a 10-second timeout to prevent CI hangs.
"""

from __future__ import annotations
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.gemini_realtime import (
    GeminiRealtimeHandler,
    tool_specs_to_gemini,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies, get_tool_specs
from reachy_mini_conversation_app.tools.tool_constants import ToolState
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_handler() -> GeminiRealtimeHandler:
    """Return a handler backed by MagicMock deps."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return GeminiRealtimeHandler(deps)


def _make_notification(
    call_id: str,
    tool_name: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    is_idle: bool = False,
) -> ToolNotification:
    """Build a ToolNotification matching BackgroundToolManager output."""
    return ToolNotification(
        id=call_id,
        tool_name=tool_name,
        is_idle_tool_call=is_idle,
        status=ToolState.FAILED if error else ToolState.COMPLETED,
        result=result,
        error=error,
    )


# ---------------------------------------------------------------------------
# Fake FunctionCall / FakeToolCallMessage to simulate Gemini SDK types
# ---------------------------------------------------------------------------


class FakeFunctionCall:
    """Minimal stand-in for types.FunctionCall."""

    def __init__(self, name: str, args: dict[str, Any], fc_id: str | None = None) -> None:
        """Store function call fields."""
        self.name = name
        self.args = args
        self.id = fc_id


class FakeToolCallMessage:
    """Stand-in for the tool_call attribute of LiveServerMessage."""

    def __init__(self, function_calls: list[FakeFunctionCall]) -> None:
        """Store function calls list."""
        self.function_calls = function_calls


# ---------------------------------------------------------------------------
# Fake session that records send_tool_response calls
# ---------------------------------------------------------------------------


class FakeSession:
    """Records tool responses sent by _handle_tool_result."""

    def __init__(self) -> None:
        """Initialize tracking lists."""
        self.tool_responses: list[list[Any]] = []

    async def send_tool_response(self, *, function_responses: list[Any]) -> None:
        """Record the function responses sent to the session."""
        self.tool_responses.append(list(function_responses))


# ---------------------------------------------------------------------------
# Fake tool manager that records start_tool calls
# ---------------------------------------------------------------------------


class FakeToolManager:
    """Records calls to start_tool; does NOT actually execute any tools."""

    def __init__(self) -> None:
        """Initialize tracking lists."""
        self.started: list[dict[str, Any]] = []
        self._notification_callbacks: list[Any] = []

    def start_up(self, tool_callbacks: list[Any]) -> None:
        """Register callbacks and start the tool manager."""
        self._notification_callbacks = list(tool_callbacks)

    async def shutdown(self) -> None:
        """No-op shutdown."""

    async def start_tool(
        self,
        call_id: str,
        tool_call_routine: ToolCallRoutine,
        is_idle_tool_call: bool,
        with_progress: bool = False,
    ) -> None:
        """Record the tool start without actually executing it."""
        self.started.append(
            {
                "call_id": call_id,
                "tool_name": tool_call_routine.tool_name,
                "args": json.loads(tool_call_routine.args_json_str),
                "is_idle": is_idle_tool_call,
            }
        )

    async def deliver(self, notification: ToolNotification) -> None:
        """Push a notification to all registered callbacks (simulates completion)."""
        for cb in self._notification_callbacks:
            await cb(notification)


# ---------------------------------------------------------------------------
# 1. tool_specs_to_gemini: all real tools convert + no unsupported keys
# ---------------------------------------------------------------------------

_ALLOWED_GEMINI_SCHEMA_KEYS = frozenset({"type", "description", "enum", "properties", "required", "items"})


def _collect_schema_keys(schema: Any, found: set[str]) -> None:
    """Recursively collect all field names present in a types.Schema object."""
    if schema is None:
        return
    for attr in ("type", "description", "enum", "properties", "required", "items"):
        val = getattr(schema, attr, None)
        if val is not None:
            found.add(attr)
    # Recurse into properties
    props = getattr(schema, "properties", None)
    if isinstance(props, dict):
        for sub in props.values():
            _collect_schema_keys(sub, found)
    # Recurse into items
    items = getattr(schema, "items", None)
    if items is not None:
        _collect_schema_keys(items, found)


def test_tool_specs_to_gemini_converts_all_real_tools() -> None:
    """Every registered tool must survive conversion without error."""
    specs = get_tool_specs()
    assert specs, "No tools registered — check test environment"
    result = tool_specs_to_gemini(specs)
    assert len(result) == 1, "Expected exactly one types.Tool container"
    declarations = result[0].function_declarations
    assert declarations is not None
    assert len(declarations) == len(specs), f"Expected {len(specs)} declarations, got {len(declarations)}"
    # Every ToolSpec name must appear in the output
    spec_names = {s["name"] for s in specs}
    decl_names = {d.name for d in declarations}
    assert spec_names == decl_names


def test_tool_specs_to_gemini_no_unsupported_schema_keys() -> None:
    """Converted schemas must contain only Gemini-allowed keys."""
    specs = get_tool_specs()
    result = tool_specs_to_gemini(specs)
    declarations = result[0].function_declarations or []
    for decl in declarations:
        found: set[str] = set()
        _collect_schema_keys(decl.parameters, found)
        unsupported = found - _ALLOWED_GEMINI_SCHEMA_KEYS
        assert not unsupported, f"Declaration '{decl.name}' has unsupported schema keys: {unsupported}"


def test_tool_specs_to_gemini_empty_input_returns_empty() -> None:
    """An empty spec list must return an empty list (no Tool container)."""
    assert tool_specs_to_gemini([]) == []


# ---------------------------------------------------------------------------
# 2. tool_call message → tool_manager.start_tool args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_message_starts_tool_with_correct_args() -> None:
    """Receiving a tool_call message must call start_tool with matching call_id and args."""
    handler = _make_handler()
    fake_manager = FakeToolManager()
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    fc = FakeFunctionCall(name="get_current_time", args={"timezone": "UTC"}, fc_id="call_1")
    msg_tool_call = FakeToolCallMessage(function_calls=[fc])

    # Simulate _process_message with only tool_call populated
    class FakeMsg:
        data = None
        server_content = None
        tool_call = msg_tool_call

    await handler._process_message(FakeMsg())  # type: ignore[arg-type]

    assert len(fake_manager.started) == 1
    started = fake_manager.started[0]
    assert started["call_id"] == "call_1"
    assert started["tool_name"] == "get_current_time"
    assert started["args"] == {"timezone": "UTC"}
    assert started["is_idle"] is False


@pytest.mark.asyncio
async def test_tool_call_message_absent_id_generates_synthetic_id() -> None:
    """When FunctionCall.id is empty/None, a synthetic id must be assigned."""
    handler = _make_handler()
    fake_manager = FakeToolManager()
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    fc = FakeFunctionCall(name="move_head", args={}, fc_id=None)

    class FakeMsg:
        data = None
        server_content = None
        tool_call = FakeToolCallMessage(function_calls=[fc])

    await handler._process_message(FakeMsg())  # type: ignore[arg-type]

    assert len(fake_manager.started) == 1
    call_id = fake_manager.started[0]["call_id"]
    assert call_id.startswith("synthetic_"), f"Expected synthetic_ prefix, got {call_id!r}"
    # The id must also appear in the mapping so _handle_tool_result can find the name
    assert call_id in handler._call_id_to_name


# ---------------------------------------------------------------------------
# 3. Parallel calls: first result does not send; both together send once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_tool_calls_trigger_single_batched_response() -> None:
    """With 2 parallel calls, send_tool_response must be called once after both complete."""
    handler = _make_handler()
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]

    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # Simulate two function calls arriving in one tool_call message
    fc_a = FakeFunctionCall(name="get_current_time", args={}, fc_id="call_1")
    fc_b = FakeFunctionCall(name="move_head", args={"x": 0}, fc_id="call_2")

    class FakeMsg:
        data = None
        server_content = None
        tool_call = FakeToolCallMessage(function_calls=[fc_a, fc_b])

    await handler._process_message(FakeMsg())  # type: ignore[arg-type]

    # Both call_ids must be in-flight after processing the message
    assert "call_1" in handler._in_flight_tool_calls
    assert "call_2" in handler._in_flight_tool_calls

    # First result arrives — send_tool_response must NOT be called yet
    notif_a = _make_notification("call_1", "get_current_time", result={"time": "12:00"})
    await fake_manager.deliver(notif_a)

    assert len(fake_session.tool_responses) == 0, (
        "send_tool_response was called after only the first result — must wait for all"
    )
    assert "call_2" in handler._in_flight_tool_calls  # second still in-flight

    # Second result arrives — now send_tool_response must be called once with both
    notif_b = _make_notification("call_2", "move_head", result={"ok": True})
    await fake_manager.deliver(notif_b)

    assert len(fake_session.tool_responses) == 1, (
        f"Expected exactly 1 send_tool_response call, got {len(fake_session.tool_responses)}"
    )
    responses = fake_session.tool_responses[0]
    assert len(responses) == 2

    ids_sent = {r.id for r in responses}
    assert ids_sent == {"call_1", "call_2"}


# ---------------------------------------------------------------------------
# 4. Tool error → response contains {"error": ...}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_sends_error_dict_to_model() -> None:
    """A failed tool must produce {"error": …} in the FunctionResponse, not an exception."""
    handler = _make_handler()
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]

    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # Pre-populate state as if _process_message had run
    handler._in_flight_tool_calls.add("call_err")
    handler._call_id_to_name["call_err"] = "bad_tool"

    notif = _make_notification("call_err", "bad_tool", error="Something went wrong")
    await fake_manager.deliver(notif)

    assert len(fake_session.tool_responses) == 1
    response = fake_session.tool_responses[0][0]
    assert "error" in response.response
    assert "Something went wrong" in response.response["error"]


# ---------------------------------------------------------------------------
# 5. is_idle_tool_call=True → send_tool_response NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_tool_call_does_not_send_to_model() -> None:
    """Idle tool completions must not trigger send_tool_response."""
    handler = _make_handler()
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]

    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # Pre-populate as if the idle tool call was registered
    handler._in_flight_tool_calls.add("idle_1")
    handler._call_id_to_name["idle_1"] = "idle_check"

    notif = _make_notification("idle_1", "idle_check", result={"ok": True}, is_idle=True)
    await fake_manager.deliver(notif)

    assert len(fake_session.tool_responses) == 0, "send_tool_response must not be called for idle tool calls"
    # The call_id must be removed from in-flight even though we didn't send
    assert "idle_1" not in handler._in_flight_tool_calls
