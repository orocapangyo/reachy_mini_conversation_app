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
from google.genai import types

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


# ---------------------------------------------------------------------------
# 6. Per-message response groups: a long-running tool must not block later turns
# ---------------------------------------------------------------------------


def _tool_call_msg(*calls: FakeFunctionCall) -> Any:
    """Build a fake LiveServerMessage carrying only a tool_call with the given calls."""

    class FakeMsg:
        data = None
        server_content = None
        tool_call = FakeToolCallMessage(function_calls=list(calls))

    return FakeMsg()


@pytest.mark.asyncio
async def test_long_running_tool_does_not_block_later_single_call_group() -> None:
    """A later turn's tool response is sent immediately while an earlier tool is still running.

    Regression: pomodoro_timer/build_insight (15–25 min) held the process-wide in-flight
    set, so every later tool call (e.g. task_status) completed but its response was
    never sent and the model went silent.
    """
    handler = _make_handler()
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]

    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # Turn 1: a long-running tool is started and never completes during this test.
    await handler._process_message(_tool_call_msg(FakeFunctionCall("pomodoro_timer", {"minutes": 25}, "call_long")))
    # Turn 2 (a new server message): a quick status query.
    await handler._process_message(_tool_call_msg(FakeFunctionCall("task_status", {}, "call_status")))

    assert handler._in_flight_tool_calls == {"call_long", "call_status"}

    await fake_manager.deliver(_make_notification("call_status", "task_status", result={"running": True}))

    assert len(fake_session.tool_responses) == 1, "later group's response must be sent without waiting for call_long"
    sent = fake_session.tool_responses[0]
    assert [r.id for r in sent] == ["call_status"]
    assert sent[0].name == "task_status"
    assert sent[0].response == {"running": True}

    # The long-running call is still in flight and still tracked.
    assert handler._in_flight_tool_calls == {"call_long"}
    assert "call_long" in handler._tool_call_groups
    assert "call_status" not in handler._tool_call_groups

    # When the long tool finally finishes, it is sent on its own — not re-sending call_status.
    await fake_manager.deliver(_make_notification("call_long", "pomodoro_timer", result={"done": True}))
    assert len(fake_session.tool_responses) == 2
    assert [r.id for r in fake_session.tool_responses[1]] == ["call_long"]
    assert handler._in_flight_tool_calls == set()
    assert handler._tool_call_groups == {}


@pytest.mark.asyncio
async def test_two_calls_in_one_message_are_sent_together_despite_other_group() -> None:
    """A real parallel batch still waits for both of its calls, independent of other groups."""
    handler = _make_handler()
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]

    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # An unrelated long-running call from an earlier message is in flight the whole time.
    await handler._process_message(_tool_call_msg(FakeFunctionCall("build_insight", {}, "call_long")))
    # One message with two parallel calls.
    await handler._process_message(
        _tool_call_msg(
            FakeFunctionCall("get_current_time", {}, "call_a"),
            FakeFunctionCall("move_head", {"x": 0}, "call_b"),
        )
    )

    # Both ids of the batch share one group object; the long call has its own.
    assert handler._tool_call_groups["call_a"] is handler._tool_call_groups["call_b"]
    assert handler._tool_call_groups["call_long"] is not handler._tool_call_groups["call_a"]
    assert handler._tool_call_groups["call_a"].pending == {"call_a", "call_b"}

    await fake_manager.deliver(_make_notification("call_a", "get_current_time", result={"time": "12:00"}))
    assert fake_session.tool_responses == [], "must wait for the second call of the same message"

    await fake_manager.deliver(_make_notification("call_b", "move_head", result={"ok": True}))
    assert len(fake_session.tool_responses) == 1
    assert {r.id for r in fake_session.tool_responses[0]} == {"call_a", "call_b"}

    # The unrelated long-running call is untouched.
    assert handler._in_flight_tool_calls == {"call_long"}
    assert set(handler._tool_call_groups) == {"call_long"}


@pytest.mark.asyncio
async def test_session_start_clears_tool_call_group_state() -> None:
    """Stale group/in-flight state from a previous session is cleared when a new session starts."""
    handler = _make_handler()
    fake_manager = FakeToolManager()
    handler.tool_manager = fake_manager  # type: ignore[assignment]

    # Leftover state as if a long-running tool was in flight when the old session died.
    await handler._process_message(_tool_call_msg(FakeFunctionCall("pomodoro_timer", {}, "call_stale")))
    assert handler._in_flight_tool_calls == {"call_stale"}
    assert "call_stale" in handler._tool_call_groups
    assert "call_stale" in handler._call_id_to_name

    class _ConnectFailed(RuntimeError):
        pass

    class _FakeLive:
        def connect(self, *, model: str, config: Any) -> Any:  # noqa: ARG002
            raise _ConnectFailed("no network in tests")

    class _FakeAio:
        live = _FakeLive()

    class _FakeClient:
        aio = _FakeAio()

    # _run_live_session clears batch state before connecting; connect then fails fast.
    with pytest.raises(_ConnectFailed):
        await handler._run_live_session(_FakeClient())  # type: ignore[arg-type]

    assert handler._in_flight_tool_calls == set()
    assert handler._tool_call_groups == {}
    assert handler._call_id_to_name == {}


# ---------------------------------------------------------------------------
# 7. Fire-and-forget tools: NON_BLOCKING declaration + SILENT response
# ---------------------------------------------------------------------------


def _declaration(name: str) -> types.FunctionDeclaration:
    """Return the Gemini declaration generated for one registered tool."""
    declarations = tool_specs_to_gemini(get_tool_specs())[0].function_declarations or []
    return next(d for d in declarations if d.name == name)


def test_fire_and_forget_tool_is_declared_non_blocking() -> None:
    """Tools with needs_response=False are NON_BLOCKING; tools whose result is spoken keep the default."""
    assert _declaration("play_emotion").behavior == types.Behavior.NON_BLOCKING
    assert _declaration("get_schedule").behavior is None


async def _deliver_single(handler: GeminiRealtimeHandler, notification: ToolNotification) -> Any:
    """Register one in-flight call, deliver its result and return the FunctionResponse sent."""
    fake_session = FakeSession()
    handler.connection = fake_session  # type: ignore[assignment]
    fake_manager = FakeToolManager()
    fake_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    handler.tool_manager = fake_manager  # type: ignore[assignment]
    handler._in_flight_tool_calls.add(notification.id)
    handler._call_id_to_name[notification.id] = notification.tool_name

    await fake_manager.deliver(notification)

    assert len(fake_session.tool_responses) == 1
    return fake_session.tool_responses[0][0]


@pytest.mark.asyncio
async def test_fire_and_forget_result_is_sent_silent() -> None:
    """Regression: a play_emotion result must not prompt a new generation (the model repeated its reply)."""
    sent = await _deliver_single(
        _make_handler(), _make_notification("call_1", "play_emotion", result={"status": "queued", "emotion": "happy"})
    )
    assert sent.scheduling == types.FunctionResponseScheduling.SILENT


@pytest.mark.asyncio
async def test_fire_and_forget_error_is_not_silent() -> None:
    """An error from a fire-and-forget tool must still be spoken, so it is not SILENT."""
    sent = await _deliver_single(
        _make_handler(), _make_notification("call_1", "play_emotion", result={"error": "Emotion system not available"})
    )
    assert sent.scheduling is None


@pytest.mark.asyncio
async def test_spoken_result_tool_has_no_scheduling() -> None:
    """Tools whose result the model must relay keep the default scheduling (generation is triggered)."""
    sent = await _deliver_single(
        _make_handler(), _make_notification("call_1", "get_schedule", result={"events": ["standup 10:00"]})
    )
    assert sent.scheduling is None
