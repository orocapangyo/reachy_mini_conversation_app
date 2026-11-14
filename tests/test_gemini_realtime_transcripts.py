"""Tests for Task 2b: transcription, interruption, greeting, and UI signals.

Network and API key are not required — all Gemini sessions are faked via
hand-written Fake classes (no MagicMock), matching the project test style.
"""

from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.gemini_realtime import GeminiRealtimeHandler
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_handler(**kwargs: Any) -> GeminiRealtimeHandler:
    """Return a GeminiRealtimeHandler backed by MagicMock deps."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return GeminiRealtimeHandler(deps, **kwargs)


class _FakeTranscription:
    """Minimal stand-in for types.Transcription."""

    def __init__(self, text: str, finished: bool | None = None) -> None:
        self.text = text
        self.finished = finished


class _FakeServerContent:
    """Minimal stand-in for types.LiveServerContent."""

    def __init__(
        self,
        input_transcription: _FakeTranscription | None = None,
        output_transcription: _FakeTranscription | None = None,
        interrupted: bool | None = None,
        turn_complete: bool | None = None,
    ) -> None:
        self.input_transcription = input_transcription
        self.output_transcription = output_transcription
        self.interrupted = interrupted
        self.turn_complete = turn_complete


class _FakeMessage:
    """Minimal stand-in for types.LiveServerMessage."""

    def __init__(
        self,
        data: bytes | None = None,
        server_content: _FakeServerContent | None = None,
        tool_call: Any = None,
    ) -> None:
        self.data = data
        self.server_content = server_content
        self.tool_call = tool_call


# ---------------------------------------------------------------------------
# 1. Input transcription → partial emit + user_partial AdditionalOutputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_transcription_emits_partial_to_observer() -> None:
    """input_transcription text must be forwarded to the observer with final=False."""
    handler = _make_handler()

    observed: list[tuple[str, str, bool]] = []
    handler.set_transcript_observer(lambda role, text, final: observed.append((role, text, final)))

    msg = _FakeMessage(server_content=_FakeServerContent(input_transcription=_FakeTranscription("Hello")))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    assert len(observed) == 1
    role, text, final = observed[0]
    assert role == "user"
    assert "Hello" in text
    assert final is False


@pytest.mark.asyncio
async def test_input_transcription_queues_user_partial_additional_outputs() -> None:
    """input_transcription must put a user_partial AdditionalOutputs on the output queue."""
    handler = _make_handler()
    msg = _FakeMessage(server_content=_FakeServerContent(input_transcription=_FakeTranscription("Hi there")))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    assert not handler.output_queue.empty()
    item = handler.output_queue.get_nowait()
    assert isinstance(item, AdditionalOutputs)
    payload = item.args[0]
    assert payload["role"] == "user_partial"
    assert "Hi there" in payload["content"]


@pytest.mark.asyncio
async def test_input_transcription_sets_listening_signal() -> None:
    """Receiving input_transcription must call movement_manager.set_listening(True)."""
    handler = _make_handler()
    msg = _FakeMessage(server_content=_FakeServerContent(input_transcription=_FakeTranscription("test")))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    handler.deps.movement_manager.set_listening.assert_called_with(True)


# ---------------------------------------------------------------------------
# 2. turn_complete → final user transcript + final assistant transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_complete_flushes_input_transcript_as_final() -> None:
    """turn_complete must emit the accumulated input transcript with final=True."""
    handler = _make_handler()

    observed: list[tuple[str, str, bool]] = []
    handler.set_transcript_observer(lambda role, text, final: observed.append((role, text, final)))

    # Accumulate two partial input chunks
    msg1 = _FakeMessage(server_content=_FakeServerContent(input_transcription=_FakeTranscription("Hello ")))
    msg2 = _FakeMessage(server_content=_FakeServerContent(input_transcription=_FakeTranscription("world")))
    msg3 = _FakeMessage(server_content=_FakeServerContent(turn_complete=True))

    for msg in [msg1, msg2, msg3]:
        await asyncio.wait_for(handler._process_message(msg), timeout=5)

    # Last observed call should be the final user transcript
    final_calls = [(r, t, f) for r, t, f in observed if f is True and r == "user"]
    assert len(final_calls) == 1
    role, text, final = final_calls[0]
    assert "Hello " in text and "world" in text
    assert final is True


@pytest.mark.asyncio
async def test_turn_complete_flushes_output_transcript_as_final() -> None:
    """turn_complete must emit the accumulated output transcript with final=True."""
    handler = _make_handler()

    observed: list[tuple[str, str, bool]] = []
    handler.set_transcript_observer(lambda role, text, final: observed.append((role, text, final)))

    msg1 = _FakeMessage(server_content=_FakeServerContent(output_transcription=_FakeTranscription("Sure, ")))
    msg2 = _FakeMessage(server_content=_FakeServerContent(output_transcription=_FakeTranscription("here you go.")))
    msg3 = _FakeMessage(server_content=_FakeServerContent(turn_complete=True))

    for msg in [msg1, msg2, msg3]:
        await asyncio.wait_for(handler._process_message(msg), timeout=5)

    assistant_finals = [(r, t, f) for r, t, f in observed if f is True and r == "assistant"]
    assert len(assistant_finals) == 1
    role, text, final = assistant_finals[0]
    assert "Sure, " in text and "here you go." in text
    assert final is True


@pytest.mark.asyncio
async def test_turn_complete_stops_speaking() -> None:
    """turn_complete must call movement_manager.set_speaking(False)."""
    handler = _make_handler()
    msg = _FakeMessage(server_content=_FakeServerContent(turn_complete=True))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    handler.deps.movement_manager.set_speaking.assert_called_with(False)


@pytest.mark.asyncio
async def test_turn_complete_queues_assistant_additional_outputs() -> None:
    """turn_complete must put an assistant AdditionalOutputs on the output queue."""
    handler = _make_handler()

    handler._output_transcript_buffer = "Response text."
    msg = _FakeMessage(server_content=_FakeServerContent(turn_complete=True))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    items = []
    while not handler.output_queue.empty():
        items.append(handler.output_queue.get_nowait())

    assistant_items = [i for i in items if isinstance(i, AdditionalOutputs) and i.args[0].get("role") == "assistant"]
    assert len(assistant_items) == 1
    assert assistant_items[0].args[0]["content"] == "Response text."


# ---------------------------------------------------------------------------
# 3. interrupted → drain queue + _clear_queue + set_speaking(False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_drains_output_queue() -> None:
    """Interrupted must remove all pending items from output_queue."""
    import numpy as np

    from reachy_mini_conversation_app.gemini_realtime import GEMINI_OUTPUT_SAMPLE_RATE

    handler = _make_handler()
    # Pre-load the queue with some items
    await handler.output_queue.put((GEMINI_OUTPUT_SAMPLE_RATE, np.zeros((1, 240), dtype=np.int16)))
    await handler.output_queue.put(AdditionalOutputs({"role": "assistant", "content": "text"}))

    msg = _FakeMessage(server_content=_FakeServerContent(interrupted=True))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    assert handler.output_queue.empty()


@pytest.mark.asyncio
async def test_interrupted_calls_clear_queue_callable() -> None:
    """Interrupted must invoke _clear_queue() when it is set on the handler."""
    handler = _make_handler()
    flush_calls: list[None] = []
    handler._clear_queue = lambda: flush_calls.append(None)

    msg = _FakeMessage(server_content=_FakeServerContent(interrupted=True))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    assert len(flush_calls) == 1


@pytest.mark.asyncio
async def test_interrupted_stops_speaking() -> None:
    """Interrupted must call movement_manager.set_speaking(False)."""
    handler = _make_handler()
    msg = _FakeMessage(server_content=_FakeServerContent(interrupted=True))
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    handler.deps.movement_manager.set_speaking.assert_called_with(False)


@pytest.mark.asyncio
async def test_interrupted_without_clear_queue_does_not_raise() -> None:
    """Interrupted must work without error when _clear_queue is None (default)."""
    handler = _make_handler()
    assert handler._clear_queue is None  # default state
    msg = _FakeMessage(server_content=_FakeServerContent(interrupted=True))
    # Should not raise
    await asyncio.wait_for(handler._process_message(msg), timeout=5)


# ---------------------------------------------------------------------------
# 4. First audio chunk → set_speaking(True); turn_complete resets flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_audio_chunk_triggers_set_speaking_true() -> None:
    """The first audio data chunk in a turn must call movement_manager.set_speaking(True)."""
    import numpy as np

    handler = _make_handler()
    pcm = np.zeros(240, dtype=np.int16).tobytes()
    msg = _FakeMessage(data=pcm)
    await asyncio.wait_for(handler._process_message(msg), timeout=5)

    handler.deps.movement_manager.set_speaking.assert_called_with(True)


@pytest.mark.asyncio
async def test_subsequent_audio_chunks_do_not_repeat_set_speaking() -> None:
    """set_speaking(True) must fire only once per turn, not on each chunk."""
    import numpy as np

    handler = _make_handler()
    pcm = np.zeros(240, dtype=np.int16).tobytes()

    for _ in range(3):
        await asyncio.wait_for(handler._process_message(_FakeMessage(data=pcm)), timeout=5)

    # set_speaking should have been called exactly once with True
    true_calls = [c for c in handler.deps.movement_manager.set_speaking.call_args_list if c == call(True)]
    assert len(true_calls) == 1


@pytest.mark.asyncio
async def test_turn_complete_resets_is_speaking_flag() -> None:
    """After turn_complete, a new audio chunk must trigger set_speaking(True) again."""
    import numpy as np

    handler = _make_handler()
    pcm = np.zeros(240, dtype=np.int16).tobytes()

    # First turn
    await asyncio.wait_for(handler._process_message(_FakeMessage(data=pcm)), timeout=5)
    await asyncio.wait_for(
        handler._process_message(_FakeMessage(server_content=_FakeServerContent(turn_complete=True))),
        timeout=5,
    )

    # Second turn
    await asyncio.wait_for(handler._process_message(_FakeMessage(data=pcm)), timeout=5)

    true_calls = [c for c in handler.deps.movement_manager.set_speaking.call_args_list if c == call(True)]
    assert len(true_calls) == 2


# ---------------------------------------------------------------------------
# 5. Startup greeting: sent once per handler; second session does not repeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_greeting_sent_when_prompt_is_nonempty(monkeypatch: Any) -> None:
    """_send_startup_greeting_prompt must call send_client_content when the prompt is non-empty."""
    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    monkeypatch.setattr(gem_mod, "get_session_greeting_prompt", lambda: "Good morning!")

    handler = _make_handler()
    fake_session = MagicMock()
    fake_session.send_client_content = AsyncMock()
    handler.connection = fake_session  # type: ignore[assignment]

    await asyncio.wait_for(handler._send_startup_greeting_prompt(), timeout=5)

    fake_session.send_client_content.assert_called_once()
    kwargs = fake_session.send_client_content.call_args.kwargs
    assert kwargs["turns"]["parts"][0]["text"] == "Good morning!"
    assert handler._startup_greeting_sent is True


@pytest.mark.asyncio
async def test_startup_greeting_not_sent_when_prompt_is_empty(monkeypatch: Any) -> None:
    """_send_startup_greeting_prompt must not call send_client_content for an empty prompt."""
    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    monkeypatch.setattr(gem_mod, "get_session_greeting_prompt", lambda: "")

    handler = _make_handler()
    fake_session = MagicMock()
    fake_session.send_client_content = AsyncMock()
    handler.connection = fake_session  # type: ignore[assignment]

    await asyncio.wait_for(handler._send_startup_greeting_prompt(), timeout=5)

    fake_session.send_client_content.assert_not_called()
    assert handler._startup_greeting_sent is True


@pytest.mark.asyncio
async def test_startup_greeting_not_repeated_on_second_call(monkeypatch: Any) -> None:
    """_send_startup_greeting_prompt must fire at most once per handler instance."""
    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    monkeypatch.setattr(gem_mod, "get_session_greeting_prompt", lambda: "Hello!")

    handler = _make_handler()
    fake_session = MagicMock()
    fake_session.send_client_content = AsyncMock()
    handler.connection = fake_session  # type: ignore[assignment]

    await asyncio.wait_for(handler._send_startup_greeting_prompt(), timeout=5)
    await asyncio.wait_for(handler._send_startup_greeting_prompt(), timeout=5)

    assert fake_session.send_client_content.call_count == 1


@pytest.mark.asyncio
async def test_startup_greeting_skipped_when_no_connection(monkeypatch: Any) -> None:
    """_send_startup_greeting_prompt must do nothing if connection is None."""
    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    monkeypatch.setattr(gem_mod, "get_session_greeting_prompt", lambda: "Hi!")

    handler = _make_handler()
    # connection is None by default
    # Should not raise
    await asyncio.wait_for(handler._send_startup_greeting_prompt(), timeout=5)

    assert handler._startup_greeting_sent is False
