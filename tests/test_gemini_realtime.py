"""Tests for the Gemini Live realtime backend (config + handler)."""

import json
import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.conversation_handler as conv_mod
from reachy_mini_conversation_app.config import (
    HF_BACKEND,
    GEMINI_BACKEND,
    OPENAI_BACKEND,
    config,
    _normalize_conversation_backend,
    refresh_runtime_config_from_env,
)
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.gemini_realtime import (
    GEMINI_AVAILABLE_VOICES,
    GeminiRealtimeHandler,
    _default_gemini_voice,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, HF_BACKEND),
        ("", HF_BACKEND),
        ("gemini", GEMINI_BACKEND),
        ("Gemini", GEMINI_BACKEND),
        ("google", GEMINI_BACKEND),
        ("openai", OPENAI_BACKEND),
        ("hf", HF_BACKEND),
        ("unknown", HF_BACKEND),
    ],
)
def test_normalize_conversation_backend_gemini(raw: str | None, expected: str) -> None:
    """CONVERSATION_BACKEND accepts gemini and google case-insensitively."""
    assert _normalize_conversation_backend(raw) == expected


def test_conversation_backend_reads_gemini_from_environment(monkeypatch: Any) -> None:
    """CONVERSATION_BACKEND=gemini in environment reaches config singleton."""
    original = config.CONVERSATION_BACKEND
    monkeypatch.setenv("CONVERSATION_BACKEND", "gemini")
    refresh_runtime_config_from_env()
    try:
        assert config.CONVERSATION_BACKEND == GEMINI_BACKEND
    finally:
        monkeypatch.delenv("CONVERSATION_BACKEND", raising=False)
        refresh_runtime_config_from_env()
    assert config.CONVERSATION_BACKEND == original


def test_config_exposes_gemini_settings() -> None:
    """Config singleton exposes GEMINI_API_KEY, GEMINI_LIVE_MODEL, GEMINI_VOICE with defaults."""
    assert hasattr(config, "GEMINI_API_KEY")
    assert config.GEMINI_LIVE_MODEL == "gemini-3.1-flash-live-preview"
    assert config.GEMINI_VOICE == "Kore"


def test_default_gemini_voice_validates_config(monkeypatch: Any) -> None:
    """GEMINI_VOICE is normalized against catalog; invalid values fall back to Kore."""
    monkeypatch.setattr(config, "GEMINI_VOICE", "puck")
    assert _default_gemini_voice() == "Puck"

    monkeypatch.setattr(config, "GEMINI_VOICE", "invalid_voice")
    assert _default_gemini_voice() == "Kore"


def _make_handler(**kwargs: Any) -> GeminiRealtimeHandler:
    return GeminiRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_gemini_handler_requires_api_key(monkeypatch: Any) -> None:
    """A missing GEMINI_API_KEY fails immediately with RuntimeError."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handler = _make_handler()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await handler._run_session()


@pytest.mark.asyncio
async def test_get_available_voices_returns_gemini_catalog() -> None:
    """Voice list returns the Gemini Live voice catalog."""
    handler = _make_handler()
    assert await handler.get_available_voices() == GEMINI_AVAILABLE_VOICES


def test_voice_normalization_against_gemini_catalog() -> None:
    """Case-insensitive Gemini voices resolve; unknown voices fall back to default."""
    handler = _make_handler(startup_voice="FENRIR")
    assert handler.get_current_voice() == "Fenrir"

    handler_unknown = _make_handler(startup_voice="invalid")
    assert handler_unknown.get_current_voice() == "Kore"


@pytest.mark.asyncio
async def test_receive_monoizes_and_resamples_to_16k() -> None:
    """Stereo frames at 48 kHz arrive at WebSocket as 16 kHz mono PCM16."""
    sent_messages: list[str] = []

    class FakeWS:
        async def send(self, data: str) -> None:
            sent_messages.append(data)

    handler = _make_handler()
    handler.ws = FakeWS()

    # 4800 samples of 48 kHz mono = 0.1s -> should resample to 1600 samples at 16 kHz
    audio_48k = np.arange(4800, dtype=np.int16)
    await handler.receive((48000, audio_48k))

    assert len(sent_messages) == 1
    payload = json.loads(sent_messages[0])
    assert "realtimeInput" in payload
    audio_data = payload["realtimeInput"]["audio"]["data"]
    decoded = np.frombuffer(base64.b64decode(audio_data), dtype=np.int16)
    assert decoded.shape[0] == 1600


@pytest.mark.asyncio
async def test_emit_resamples_24k_audio_to_device_rate(monkeypatch: Any) -> None:
    """24 kHz model audio must be resampled to playback device rate."""
    handler = _make_handler()
    handler.deps.reachy_mini.media.get_output_audio_samplerate.return_value = 16000
    pcm = (np.sin(np.arange(2400) * 0.05) * 10000).astype(np.int16).reshape(1, -1)
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=(24000, pcm)))

    result = await handler.emit()
    assert isinstance(result, tuple)
    rate, audio = result
    assert rate == 16000
    assert audio.dtype == np.int16
    assert audio.shape == (1, 1600)


@pytest.mark.asyncio
async def test_handle_server_message_tool_call() -> None:
    """Incoming toolCall runs the tool through BackgroundToolManager."""
    handler = _make_handler()
    handler.tool_manager = MagicMock()
    handler.tool_manager.start_tool = AsyncMock()

    msg = json.dumps(
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "fc_123",
                        "name": "dance",
                        "args": {"dance_name": "happy"},
                    }
                ]
            }
        }
    )
    await handler._handle_server_message(msg)

    assert "fc_123" in handler._in_flight_tool_calls
    handler.tool_manager.start_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_server_message_interrupted_drains_audio() -> None:
    """Interrupted serverContent removes pending audio while keeping text."""
    handler = _make_handler()
    audio_frame = (24000, np.zeros(2400, dtype=np.int16))
    text_item = AdditionalOutputs({"role": "assistant", "content": "hello"})

    await handler.output_queue.put(audio_frame)
    await handler.output_queue.put(text_item)
    await handler.output_queue.put(audio_frame)

    msg = json.dumps({"serverContent": {"interrupted": True}})
    await handler._handle_server_message(msg)

    # Queue should now only contain the text item, no audio frames
    items = []
    while not handler.output_queue.empty():
        items.append(handler.output_queue.get_nowait())

    assert len(items) == 1
    assert items[0] == text_item


@pytest.mark.asyncio
async def test_handle_tool_result_sends_tool_response() -> None:
    """_handle_tool_result sends toolResponse JSON to the WebSocket."""
    sent: list[str] = []

    class FakeWS:
        async def send(self, data: str) -> None:
            sent.append(data)

    handler = _make_handler()
    handler.ws = FakeWS()
    handler._in_flight_tool_calls.add("call_1")

    completed = MagicMock()
    completed.id = "call_1"
    completed.tool_name = "move_head"
    completed.error = None
    completed.result = {"moved": True}

    await handler._handle_tool_result(completed)

    assert len(sent) == 1
    resp = json.loads(sent[0])
    assert "toolResponse" in resp
    fr = resp["toolResponse"]["functionResponses"][0]
    assert fr["id"] == "call_1"
    assert fr["name"] == "move_head"
    assert fr["response"]["output"] == {"moved": True}
    assert "call_1" not in handler._in_flight_tool_calls


@pytest.mark.asyncio
async def test_change_voice_reconnects_session(monkeypatch: Any) -> None:
    """change_voice restarts the live session when connected."""
    handler = _make_handler()
    handler.ws = MagicMock()
    restart = AsyncMock()
    monkeypatch.setattr(handler, "_restart_session", restart)

    result = await handler.change_voice("puck")
    assert result == "Voice changed to Puck; reconnecting session."
    assert handler.get_current_voice() == "Puck"
    restart.assert_awaited_once()


@pytest.mark.asyncio
async def test_say_sends_client_content() -> None:
    """Say sends user text via clientContent."""
    sent: list[str] = []

    class FakeWS:
        async def send(self, data: str) -> None:
            sent.append(data)

    handler = _make_handler()
    handler.ws = FakeWS()

    await handler.say("반가워 리치")
    assert len(sent) == 1
    data = json.loads(sent[0])
    assert data["clientContent"]["turns"][0]["parts"][0]["text"] == "반가워 리치"
    assert data["clientContent"]["turnComplete"] is True
