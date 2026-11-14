"""Tests for the Gemini Live realtime backend (Task 2a: skeleton + audio I/O).

Network and API key are not required — all Gemini sessions are faked via
hand-written Fake classes (no MagicMock), matching the project test style.
"""

from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.conversation_handler as conv_mod
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.gemini_realtime import (
    GEMINI_OUTPUT_SAMPLE_RATE,
    GeminiRealtimeHandler,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_handler(**kwargs: Any) -> GeminiRealtimeHandler:
    """Return a GeminiRealtimeHandler backed by MagicMock deps."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return GeminiRealtimeHandler(deps, **kwargs)


class FakeMessage:
    """Minimal stand-in for types.LiveServerMessage."""

    def __init__(
        self,
        data: bytes | None = None,
        server_content: Any = None,
        tool_call: Any = None,
    ) -> None:
        """Store message fields."""
        self.data = data
        self.server_content = server_content
        self.tool_call = tool_call


class FakeSession:
    """Async context manager that delivers a fixed message list then ends."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        """Store messages and reset tracking state."""
        self._messages = messages
        self.close_called = False
        self.realtime_inputs: list[Any] = []

    async def __aenter__(self) -> "FakeSession":
        """Return self as the session."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """No-op exit."""

    async def receive(self) -> Any:
        """Yield stored messages once; later calls yield nothing (stream drained).

        Mirrors the real SDK: receive() ends at each turn boundary and the
        handler re-enters it, so a replaying fake would loop forever.
        """
        messages, self._messages = self._messages, []
        for msg in messages:
            yield msg

    async def close(self) -> None:
        """Record that close was called."""
        self.close_called = True

    async def send_realtime_input(self, *, audio: Any) -> None:
        """Record the audio blob sent to the session."""
        self.realtime_inputs.append(audio)

    async def send_client_content(self, **_kwargs: Any) -> None:
        """Accept client content without side effects."""


class FakeLive:
    """Stands in for client.aio.live."""

    def __init__(self, session: FakeSession) -> None:
        """Store the session to return from connect()."""
        self._session = session

    def connect(self, *, model: str, config: Any) -> FakeSession:
        """Return the pre-configured fake session."""
        return self._session


class FakeAio:
    """Stands in for client.aio."""

    def __init__(self, session: FakeSession) -> None:
        """Wire up the live attribute."""
        self.live = FakeLive(session)


class FakeClient:
    """Stands in for genai.Client."""

    def __init__(self, session: FakeSession) -> None:
        """Wire up the aio attribute."""
        self.aio = FakeAio(session)


# ---------------------------------------------------------------------------
# 1. Audio message → output_queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_message_enqueued_as_24k_int16_ndarray(monkeypatch: Any) -> None:
    """Raw audio bytes from Gemini must land on the queue as (24000, int16 ndarray(1,-1))."""
    pcm_bytes = (np.arange(480, dtype=np.int16) * 100).tobytes()  # 480 int16 samples
    session = FakeSession([FakeMessage(data=pcm_bytes)])
    client = FakeClient(session)

    handler = _make_handler()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(handler, "tool_manager", MagicMock(start_up=MagicMock(), shutdown=AsyncMock()))

    await handler._run_live_session(client)

    assert not handler.output_queue.empty()
    rate, audio = handler.output_queue.get_nowait()
    assert rate == GEMINI_OUTPUT_SAMPLE_RATE
    assert audio.dtype == np.int16
    assert audio.ndim == 2
    assert audio.shape == (1, 480)
    assert np.array_equal(audio.reshape(-1), np.arange(480, dtype=np.int16) * 100)


# ---------------------------------------------------------------------------
# 2. receive(): stereo → mono, 16 kHz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_monoizes_stereo_frames_before_upload() -> None:
    """Channels-last stereo float frames must reach send_realtime_input as mono 16 kHz PCM."""
    session = FakeSession([])
    handler = _make_handler()
    handler.connection = session

    left = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    stereo = np.stack([left, np.zeros_like(left)], axis=1)  # (N, 2) channels-last

    await handler.receive((16000, stereo))

    assert len(session.realtime_inputs) == 1
    blob = session.realtime_inputs[0]
    # Verify mime_type encodes the input rate
    assert "16000" in blob.mime_type
    # Round-trip: blob data is mono int16 at 16 kHz → same number of samples as input
    decoded = np.frombuffer(blob.data, dtype=np.int16)
    assert decoded.shape[0] == 1600  # 16 kHz mono
    # The left channel was non-silent; the right was zeros — energy must survive
    assert np.abs(decoded).max() > 0


@pytest.mark.asyncio
async def test_receive_drops_frame_when_no_session() -> None:
    """receive() must drop frames silently when no session is active."""
    handler = _make_handler()
    # connection is None by default
    frame = (16000, np.zeros(1600, dtype=np.int16))
    # Should not raise
    await handler.receive(frame)


# ---------------------------------------------------------------------------
# 3. emit(): resample model audio to device rate (4 cases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_resamples_model_audio_to_output_device_rate(monkeypatch: Any) -> None:
    """24 kHz model audio must be resampled to the playback device rate."""
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
async def test_emit_passes_through_non_audio_output(monkeypatch: Any) -> None:
    """Transcript payloads and None emissions must pass through untouched."""
    handler = _make_handler()
    handler.deps.reachy_mini.media.get_output_audio_samplerate.return_value = 16000
    outputs = AdditionalOutputs({"role": "assistant", "content": "hi"})
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=outputs))

    assert await handler.emit() is outputs

    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=None))
    assert await handler.emit() is None


@pytest.mark.asyncio
async def test_emit_falls_back_to_16k_when_device_rate_unavailable(
    monkeypatch: Any,
) -> None:
    """A media backend that cannot report its rate must not break playback."""
    handler = _make_handler()
    handler.deps.reachy_mini.media.get_output_audio_samplerate.side_effect = RuntimeError("no device")
    pcm = np.zeros(2400, dtype=np.int16)
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=(24000, pcm)))

    rate, audio = await handler.emit()

    assert rate == 16000
    assert audio.shape == (1600,)


@pytest.mark.asyncio
async def test_emit_keeps_audio_when_device_matches_model_rate(monkeypatch: Any) -> None:
    """A 24 kHz-capable output device must receive the model audio unchanged."""
    handler = _make_handler()
    handler.deps.reachy_mini.media.get_output_audio_samplerate.return_value = 24000
    pcm = np.arange(240, dtype=np.int16).reshape(1, -1)
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=(24000, pcm)))

    rate, audio = await handler.emit()

    assert rate == 24000
    assert np.array_equal(audio, pcm)


# ---------------------------------------------------------------------------
# 4. Reconnect: APIError 1 × then success; MAX_ATTEMPTS exceeded
# ---------------------------------------------------------------------------


class _FailFirstSession(FakeSession):
    """Session whose __aenter__ raises APIError on the first call."""

    def __init__(self, parent: "_CountingFakeClient", success_messages: list[FakeMessage]) -> None:
        """Store parent reference for attempt tracking."""
        super().__init__(success_messages)
        self._parent = parent

    async def __aenter__(self) -> "FakeSession":
        """Raise APIError until fail_before threshold is reached."""
        self._parent.connect_attempts += 1
        if self._parent.connect_attempts < self._parent.fail_before:
            from google.genai import errors

            raise errors.APIError(503, "UNAVAILABLE: test")
        return self

    async def __aexit__(self, *args: Any) -> None:
        """No-op exit."""


class _CountingFakeClient:
    """Fake client that tracks connect attempts and fails the first N."""

    def __init__(self, fail_before: int, success_messages: list[FakeMessage]) -> None:
        """Configure failure threshold and success session."""
        self.connect_attempts = 0
        self.fail_before = fail_before
        self._session = _FailFirstSession(self, success_messages)

    @property
    def aio(self) -> Any:
        """Return self so connect() is accessible as client.aio.live.connect()."""
        return self

    @property
    def live(self) -> Any:
        """Return self so connect() is accessible as client.aio.live.connect()."""
        return self

    def connect(self, *, model: str, config: Any) -> "_FailFirstSession":
        """Return the shared session (which may raise on entry)."""
        return self._session


@pytest.mark.asyncio
async def test_api_error_triggers_backoff_and_reconnect(monkeypatch: Any) -> None:
    """APIError on first connect must be retried after back-off; second attempt succeeds."""
    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    pcm_bytes = np.zeros(240, dtype=np.int16).tobytes()
    client = _CountingFakeClient(fail_before=2, success_messages=[FakeMessage(data=pcm_bytes)])

    handler = _make_handler()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
    # Zero out the base delay so the test runs fast without patching asyncio.sleep globally.
    monkeypatch.setattr(gem_mod, "RECONNECT_BASE_DELAY_S", 0.0)
    # Also make jitter deterministic and tiny.
    monkeypatch.setattr(gem_mod.random, "uniform", lambda _a, _b: 0.0)

    # Patch genai.Client to return our fake client
    monkeypatch.setattr(gem_mod.genai, "Client", lambda **_kw: client)

    await handler.start_up()

    # Two connect attempts: first failed (APIError), second succeeded
    assert client.connect_attempts == 2


@pytest.mark.asyncio
async def test_max_reconnect_attempts_exceeded_raises(monkeypatch: Any) -> None:
    """Failing more than RECONNECT_MAX_ATTEMPTS times must propagate the final error."""
    from google.genai import errors

    import reachy_mini_conversation_app.gemini_realtime as gem_mod

    class AlwaysFailSession:
        """Context manager that always raises APIError on entry."""

        async def __aenter__(self) -> "AlwaysFailSession":
            """Raise APIError unconditionally."""
            raise errors.APIError(503, "UNAVAILABLE: always")

        async def __aexit__(self, *args: Any) -> None:
            """No-op exit."""

    class AlwaysFailClient:
        """Fake client whose connect always raises."""

        @property
        def aio(self) -> Any:
            """Return self to enable client.aio.live.connect()."""
            return self

        @property
        def live(self) -> Any:
            """Return self to enable client.aio.live.connect()."""
            return self

        def connect(self, **_kwargs: Any) -> AlwaysFailSession:
            """Return a session that will always fail to enter."""
            return AlwaysFailSession()

    handler = _make_handler()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gem_mod, "RECONNECT_BASE_DELAY_S", 0.0)
    monkeypatch.setattr(gem_mod.random, "uniform", lambda _a, _b: 0.0)

    monkeypatch.setattr(gem_mod.genai, "Client", lambda **_kw: AlwaysFailClient())

    with pytest.raises(errors.APIError):
        await handler.start_up()


# ---------------------------------------------------------------------------
# 5. shutdown(): cancel loop → close session → manager shutdown (order)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_order_loop_cancel_session_close_manager_shutdown() -> None:
    """shutdown() must cancel the receive task, then close the session, then stop the manager."""
    call_log: list[str] = []

    class TrackedSession:
        """Session that records close() calls."""

        async def close(self) -> None:
            """Record session close."""
            call_log.append("session_close")

    class TrackedToolManager:
        """Replacement tool manager that records shutdown() calls."""

        async def shutdown(self) -> None:
            """Record manager shutdown."""
            call_log.append("manager_shutdown")

    handler = _make_handler()
    handler.connection = TrackedSession()
    handler.tool_manager = TrackedToolManager()

    async def long_task() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            call_log.append("loop_cancelled")
            raise

    handler._receive_task = asyncio.create_task(long_task())
    # Yield to the event loop so long_task() has a chance to start before we cancel it.
    await asyncio.sleep(0)

    await handler.shutdown()

    assert call_log == ["loop_cancelled", "session_close", "manager_shutdown"], call_log
