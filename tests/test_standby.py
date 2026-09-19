"""Tests for the standby (sleep-wait) state machine and wake phrase matching."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.openai_realtime import (
    OpenAIRealtimeHandler,
    matches_wake_phrase,
    configured_wake_phrases,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


def _make_handler() -> OpenAIRealtimeHandler:
    return OpenAIRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))


class _FakeSession:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self._captured = captured

    async def update(self, **kwargs: Any) -> None:
        self._captured.append(kwargs)


class _FakeItem:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self._captured = captured

    async def create(self, **kwargs: Any) -> None:
        self._captured.append(kwargs)


class _FakeConnection:
    def __init__(self) -> None:
        self.session_updates: list[dict[str, Any]] = []
        self.created_items: list[dict[str, Any]] = []
        self.session = _FakeSession(self.session_updates)
        self.conversation = MagicMock()
        self.conversation.item = _FakeItem(self.created_items)


# --- wake phrase matching -----------------------------------------------------


@pytest.mark.parametrize(
    "transcript",
    ["깨어나 리치미니", "리치 미니 일어나!", "야, 깨어나.", "리치미니야 안녕", "이제 일어나볼까"],
)
def test_wake_phrase_matches_loose_variants(transcript: str) -> None:
    """Spacing and punctuation variants of the wake phrases must match."""
    assert matches_wake_phrase(transcript, ["깨어나", "리치미니", "일어나"]) is True


@pytest.mark.parametrize("transcript", ["안녕하세요", "오늘 날씨 어때", "", "   "])
def test_wake_phrase_rejects_non_wake_text(transcript: str) -> None:
    """Unrelated or empty transcripts must not wake the robot."""
    assert matches_wake_phrase(transcript, ["깨어나", "리치미니", "일어나"]) is False


def test_configured_wake_phrases_env_override(monkeypatch: Any) -> None:
    """REACHY_MINI_WAKE_PHRASES replaces the default list; blank falls back."""
    monkeypatch.setattr(config, "REACHY_MINI_WAKE_PHRASES", "굿모닝, 헬로")
    assert configured_wake_phrases() == ["굿모닝", "헬로"]

    monkeypatch.setattr(config, "REACHY_MINI_WAKE_PHRASES", "")
    assert configured_wake_phrases() == ["깨어나", "리치미니", "일어나"]


# --- standby state machine ----------------------------------------------------


@pytest.mark.asyncio
async def test_enter_standby_sleeps_and_mutes_responses() -> None:
    """Standby entry runs the sleep motion and switches VAD to transcription-only."""
    handler = _make_handler()
    handler.connection = _FakeConnection()

    result = await handler.enter_standby()

    assert result["status"] == "standby"
    assert handler.in_standby is True
    handler.deps.movement_manager.stop.assert_called_once_with(reset_to_neutral=False)
    handler.deps.reachy_mini.goto_sleep.assert_called_once()
    turn_detection = handler.connection.session_updates[-1]["session"]["audio"]["input"]["turn_detection"]
    assert turn_detection["create_response"] is False
    assert turn_detection["interrupt_response"] is False
    assert handler._idle_behavior_ready() is False


@pytest.mark.asyncio
async def test_wake_from_standby_restores_session_and_greets() -> None:
    """Waking restarts motion, runs the wake-up move, restores VAD, and greets."""
    handler = _make_handler()
    handler.connection = _FakeConnection()
    handler._standby = True
    monkeypatch_safe_response = AsyncMock()
    handler._safe_response_create = monkeypatch_safe_response  # type: ignore[method-assign]

    await handler.wake_from_standby()

    assert handler.in_standby is False
    handler.deps.movement_manager.start.assert_called_once()
    handler.deps.reachy_mini.enable_motors.assert_called_once()
    handler.deps.reachy_mini.wake_up.assert_called_once()
    turn_detection = handler.connection.session_updates[-1]["session"]["audio"]["input"]["turn_detection"]
    # The server merges session updates, so create_response must be explicitly
    # re-enabled — omitting it would leave the standby False in place.
    assert turn_detection["create_response"] is True
    assert handler.connection.created_items, "wake greeting item must be queued"
    monkeypatch_safe_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_transcript_with_wake_phrase_triggers_wake(monkeypatch: Any) -> None:
    """A final user transcript containing a wake phrase schedules wake_from_standby."""
    handler = _make_handler()
    handler._standby = True
    handler._standby_loop = asyncio.get_running_loop()
    wake = AsyncMock()
    monkeypatch.setattr(handler, "wake_from_standby", wake)

    handler._emit_transcript("user", "깨어나 리치미니", True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_completion_during_standby_wakes_without_greeting(monkeypatch: Any) -> None:
    """A timer finishing while asleep wakes the robot (no wake greeting) before announcing."""
    import reachy_mini_conversation_app.huggingface_realtime as hf_mod
    from reachy_mini_conversation_app.tools.tool_constants import ToolState

    handler = _make_handler()
    handler._standby = True
    parent_handle = AsyncMock()
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_handle_tool_result", parent_handle)
    wake = AsyncMock()
    monkeypatch.setattr(handler, "wake_from_standby", wake)

    notification = MagicMock()
    notification.tool_name = "pomodoro_timer"
    notification.status = ToolState.COMPLETED

    await handler._handle_tool_result(notification)

    wake.assert_awaited_once_with(greet=False)
    parent_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_go_to_sleep_completion_does_not_wake(monkeypatch: Any) -> None:
    """The go_to_sleep tool completing right after standby entry must not wake."""
    import reachy_mini_conversation_app.huggingface_realtime as hf_mod
    from reachy_mini_conversation_app.tools.tool_constants import ToolState

    handler = _make_handler()
    handler._standby = True
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_handle_tool_result", AsyncMock())
    wake = AsyncMock()
    monkeypatch.setattr(handler, "wake_from_standby", wake)

    notification = MagicMock()
    notification.tool_name = "go_to_sleep"
    notification.status = ToolState.COMPLETED

    await handler._handle_tool_result(notification)

    wake.assert_not_awaited()


@pytest.mark.asyncio
async def test_wake_without_greeting_skips_greeting_item() -> None:
    """wake_from_standby(greet=False) restores the session but stays silent."""
    handler = _make_handler()
    handler.connection = _FakeConnection()
    handler._standby = True
    handler._safe_response_create = AsyncMock()  # type: ignore[method-assign]

    await handler.wake_from_standby(greet=False)

    assert handler.in_standby is False
    assert handler.connection.created_items == []


@pytest.mark.asyncio
async def test_assistant_transcript_never_triggers_wake(monkeypatch: Any) -> None:
    """Assistant text mentioning wake words must not wake the robot."""
    handler = _make_handler()
    handler._standby = True
    handler._standby_loop = asyncio.get_running_loop()
    wake = AsyncMock()
    monkeypatch.setattr(handler, "wake_from_standby", wake)

    handler._emit_transcript("assistant", "깨어나 리치미니", True)
    await asyncio.sleep(0)

    wake.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_transcript_without_wake_phrase_triggers_sleeping_notice(monkeypatch: Any) -> None:
    """A final user transcript without a wake phrase schedules _send_standby_sleeping_notice."""
    handler = _make_handler()
    handler._standby = True
    handler._standby_loop = asyncio.get_running_loop()
    wake = AsyncMock()
    notice = AsyncMock()
    monkeypatch.setattr(handler, "wake_from_standby", wake)
    monkeypatch.setattr(handler, "_send_standby_sleeping_notice", notice)

    handler._emit_transcript("user", "지금 몇 시야?", True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    wake.assert_not_awaited()
    notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_standby_sleeping_notice_queues_prompt() -> None:
    """_send_standby_sleeping_notice queues the sleep notice and enforces rate limiting."""
    handler = _make_handler()
    handler.connection = _FakeConnection()
    handler._standby = True
    safe_response = AsyncMock()
    handler._safe_response_create = safe_response  # type: ignore[method-assign]

    await handler._send_standby_sleeping_notice()

    assert handler.connection.created_items, "sleeping notice item must be queued"
    safe_response.assert_awaited_once()

    # Calling again immediately should be rate-limited (cooldown)
    safe_response.reset_mock()
    await handler._send_standby_sleeping_notice()
    safe_response.assert_not_awaited()
