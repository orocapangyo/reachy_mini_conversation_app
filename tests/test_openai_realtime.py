"""Tests for the OpenAI realtime backend (config + handler)."""

import base64
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.conversation_handler as conv_mod
import reachy_mini_conversation_app.huggingface_realtime as hf_mod
from reachy_mini_conversation_app.config import (
    HF_BACKEND,
    GEMINI_BACKEND,
    OPENAI_BACKEND,
    DEFAULT_PROFILES_DIRECTORY,
    config,
    _normalize_conversation_backend,
    refresh_runtime_config_from_env,
)
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


if TYPE_CHECKING:
    from reachy_mini_conversation_app.openai_realtime import OpenAIRealtimeHandler


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, HF_BACKEND),
        ("", HF_BACKEND),
        ("hf", HF_BACKEND),
        ("huggingface", HF_BACKEND),
        ("HF", HF_BACKEND),
        ("openai", OPENAI_BACKEND),
        ("OpenAI", OPENAI_BACKEND),
        ("gemini", GEMINI_BACKEND),
        ("GEMINI", GEMINI_BACKEND),
        (" gemini ", GEMINI_BACKEND),
        ("bogus", HF_BACKEND),
    ],
)
def test_normalize_conversation_backend(raw: str | None, expected: str) -> None:
    """CONVERSATION_BACKEND accepts hf/huggingface/openai/gemini case-insensitively, defaults to HF."""
    assert _normalize_conversation_backend(raw) == expected


def test_conversation_backend_reads_the_environment(monkeypatch: Any) -> None:
    """CONVERSATION_BACKEND=openai in the environment must reach the config singleton."""
    original_backend = config.CONVERSATION_BACKEND
    monkeypatch.setenv("CONVERSATION_BACKEND", "openai")
    refresh_runtime_config_from_env()
    try:
        assert config.CONVERSATION_BACKEND == OPENAI_BACKEND
    finally:
        monkeypatch.delenv("CONVERSATION_BACKEND", raising=False)
        refresh_runtime_config_from_env()
    assert config.CONVERSATION_BACKEND == original_backend


def test_config_exposes_openai_settings() -> None:
    """The config singleton must expose the OpenAI backend settings with safe defaults."""
    assert hasattr(config, "OPENAI_API_KEY")
    assert config.OPENAI_REALTIME_MODEL  # non-empty default
    assert config.OPENAI_VOICE  # non-empty default


def test_resample_pcm_16k_to_24k_length_and_dtype() -> None:
    """16 kHz mono int16 frames must resample to 1.5x length, keeping dtype."""
    from reachy_mini_conversation_app.openai_realtime import resample_pcm

    source = np.arange(1600, dtype=np.int16)
    result = resample_pcm(source, 16000, 24000)

    assert result.dtype == np.int16
    assert result.shape[0] == 2400
    # Monotone ramp stays monotone after linear interpolation.
    assert result[0] == source[0]
    assert int(result[-1]) >= int(source[-2])


def test_resample_pcm_24k_to_16k_length_and_dtype() -> None:
    """24 kHz model audio must downsample to 2/3 length, keeping int16 dtype."""
    from reachy_mini_conversation_app.openai_realtime import resample_pcm

    source = (np.sin(np.arange(2400) * 0.05) * 10000).astype(np.int16)
    result = resample_pcm(source, 24000, 16000)

    assert result.dtype == np.int16
    assert result.shape[0] == 1600


def test_resample_pcm_same_rate_is_passthrough() -> None:
    """Same-rate input must be returned unchanged (no copy semantics required)."""
    from reachy_mini_conversation_app.openai_realtime import resample_pcm

    source = np.arange(160, dtype=np.int16)
    result = resample_pcm(source, 24000, 24000)

    assert np.array_equal(result, source)


@pytest.mark.parametrize("length", [1, 2])
def test_resample_pcm_downsamples_frames_shorter_than_filter_kernel(length: int) -> None:
    """Tail chunks shorter than the anti-alias kernel must not crash downsampling.

    Regression: np.convolve(mode="same") pads such inputs to the kernel length,
    which desynced fp/xp in np.interp and killed the playback loop mid-turn.
    """
    from reachy_mini_conversation_app.openai_realtime import resample_pcm

    source = np.ones(length, dtype=np.int16) * 1000
    result = resample_pcm(source, 24000, 16000)

    assert result.dtype == np.int16
    assert result.shape[0] == max(1, round(length * 16000 / 24000))


def test_default_openai_voice_validates_config(monkeypatch: Any) -> None:
    """OPENAI_VOICE is normalized against the catalog; unsupported values fall back to marin."""
    from reachy_mini_conversation_app import openai_realtime as oai_mod

    monkeypatch.setattr(config, "OPENAI_VOICE", "CEDAR")
    assert oai_mod._default_openai_voice() == "cedar"

    monkeypatch.setattr(config, "OPENAI_VOICE", "Aiden")  # HF voice, not OpenAI
    assert oai_mod._default_openai_voice() == "marin"


def _fake_openai_client(captured_kwargs: dict[str, Any]) -> type:
    """Return a fake AsyncOpenAI class that records its constructor kwargs."""

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

    return FakeClient


def _make_handler(**kwargs: Any) -> "OpenAIRealtimeHandler":
    from reachy_mini_conversation_app.openai_realtime import OpenAIRealtimeHandler

    return OpenAIRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_build_realtime_client_targets_openai_endpoint(monkeypatch: Any) -> None:
    """The client must hit api.openai.com with the API key and pass the model via connect query."""
    client_kwargs: dict[str, Any] = {}
    monkeypatch.setattr(hf_mod, "AsyncOpenAI", _fake_openai_client(client_kwargs))
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "OPENAI_REALTIME_MODEL", "gpt-realtime-mini")

    handler = _make_handler()
    client = await handler._build_realtime_client()

    assert client is not None
    assert client_kwargs["api_key"] == "sk-test"
    assert client_kwargs["base_url"] == "https://api.openai.com/v1"
    assert client_kwargs["websocket_base_url"] == "wss://api.openai.com/v1"
    assert handler._realtime_connect_query == {"model": "gpt-realtime-mini"}


@pytest.mark.asyncio
async def test_build_realtime_client_requires_api_key(monkeypatch: Any) -> None:
    """A missing OPENAI_API_KEY must fail loudly, never silently connect."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)

    handler = _make_handler()

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await handler._build_realtime_client()


def test_session_config_uses_24k_pcm_and_korean_language(monkeypatch: Any) -> None:
    """Session config must request 24 kHz PCM both ways and forward the transcription language."""
    from reachy_mini_conversation_app import openai_realtime as oai_mod

    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(oai_mod, "get_session_voice", lambda default: default)
    monkeypatch.setattr(config, "REALTIME_TRANSCRIPTION_LANGUAGE", "ko")

    handler = _make_handler()
    session = handler._get_session_config([])

    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["input"]["transcription"]["language"] == "ko"
    assert session["audio"]["output"]["voice"] == "marin"


def test_voice_normalization_against_openai_catalog() -> None:
    """Case-insensitive OpenAI voices resolve; HF voices fall back to marin."""
    handler = _make_handler(startup_voice="CEDAR")
    assert handler.get_current_voice() == "cedar"

    handler_hf_voice = _make_handler(startup_voice="Aiden")
    assert handler_hf_voice.get_current_voice() == "marin"


@pytest.mark.asyncio
async def test_get_available_voices_returns_openai_catalog() -> None:
    """The UI voice list must be the OpenAI catalog, not the HF one."""
    from reachy_mini_conversation_app.openai_realtime import OPENAI_AVAILABLE_VOICES

    handler = _make_handler()
    assert await handler.get_available_voices() == OPENAI_AVAILABLE_VOICES


@pytest.mark.asyncio
async def test_change_voice_restarts_live_session(monkeypatch: Any) -> None:
    """OpenAI locks the voice after audio output, so a live change must reconnect."""
    handler = _make_handler()
    handler.connection = MagicMock()
    restart = AsyncMock()
    monkeypatch.setattr(handler, "_restart_session", restart)

    result = await handler.change_voice("cedar")

    assert result == "Voice changed to cedar; reconnecting session."
    assert handler.get_current_voice() == "cedar"
    restart.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_voice_without_connection_defers(monkeypatch: Any) -> None:
    """With no live session the voice is stored for the next connection."""
    handler = _make_handler()
    handler.connection = None

    result = await handler.change_voice("Cedar")

    assert result == "Voice changed. Will take effect on next connection."
    assert handler.get_current_voice() == "cedar"


@pytest.mark.asyncio
async def test_localstream_voice_methods_follow_the_active_handler() -> None:
    """The UI voice list/current voice must come from the OpenAI handler, not the HF config."""
    from reachy_mini_conversation_app.console import LocalStream
    from reachy_mini_conversation_app.openai_realtime import OPENAI_AVAILABLE_VOICES

    handler = _make_handler(startup_voice="cedar")
    stream = LocalStream(handler, MagicMock())

    assert await stream.get_available_voices() == OPENAI_AVAILABLE_VOICES
    assert stream.get_current_voice() == "cedar"


@pytest.mark.asyncio
async def test_localstream_voice_methods_fall_back_when_handler_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler that cannot answer must not break the voice UI."""
    from reachy_mini_conversation_app.config import HF_AVAILABLE_VOICES, config
    from reachy_mini_conversation_app.console import LocalStream

    monkeypatch.setattr(config, "CONVERSATION_BACKEND", "huggingface")
    handler = MagicMock()
    handler.get_available_voices = AsyncMock(side_effect=RuntimeError("backend down"))
    handler.get_current_voice = MagicMock(side_effect=RuntimeError("backend down"))
    stream = LocalStream(handler, MagicMock(), startup_voice="Aiden")

    assert await stream.get_available_voices() == HF_AVAILABLE_VOICES
    assert stream.get_current_voice() == "Aiden"


@pytest.mark.asyncio
async def test_receive_resamples_mic_frames_to_24k() -> None:
    """16 kHz mic frames must arrive at the connection as 24 kHz PCM16."""
    appended: list[str] = []

    class FakeInputBuffer:
        async def append(self, audio: str) -> None:
            appended.append(audio)

    class FakeConnection:
        input_audio_buffer = FakeInputBuffer()

    handler = _make_handler()
    handler.connection = FakeConnection()

    frame = (16000, np.arange(1600, dtype=np.int16))
    await handler.receive(frame)

    assert len(appended) == 1
    decoded = np.frombuffer(base64.b64decode(appended[0]), dtype=np.int16)
    assert decoded.shape[0] == 2400


@pytest.mark.asyncio
async def test_emit_resamples_model_audio_to_output_device_rate(monkeypatch: Any) -> None:
    """24 kHz model audio must reach the playback path at the device rate, not 1.5x slow."""
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
    """Transcript payloads and empty emissions must pass through untouched."""
    handler = _make_handler()
    handler.deps.reachy_mini.media.get_output_audio_samplerate.return_value = 16000
    outputs = AdditionalOutputs({"role": "assistant", "content": "hi"})
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=outputs))

    assert await handler.emit() is outputs

    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=None))
    assert await handler.emit() is None


@pytest.mark.asyncio
async def test_emit_falls_back_to_16k_when_device_rate_unavailable(monkeypatch: Any) -> None:
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


@pytest.mark.asyncio
async def test_receive_monoizes_stereo_frames_before_upload() -> None:
    """Channels-last stereo float frames must reach the server as mono 24 kHz PCM16."""
    appended: list[str] = []

    class FakeInputBuffer:
        async def append(self, audio: str) -> None:
            appended.append(audio)

    class FakeConnection:
        input_audio_buffer = FakeInputBuffer()

    handler = _make_handler()
    handler.connection = FakeConnection()

    left = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    stereo = np.stack([left, np.zeros_like(left)], axis=1)  # (N, 2), channels-last
    await handler.receive((16000, stereo))

    assert len(appended) == 1
    decoded = np.frombuffer(base64.b64decode(appended[0]), dtype=np.int16)
    assert decoded.shape[0] == 2400  # mono, resampled 16k -> 24k
    assert np.abs(decoded).max() > 0  # the left channel survived, not the silent one


def test_desk_companion_ko_profile_exists_and_speaks_korean() -> None:
    """The Korean desk companion profile must exist and greet in Korean."""
    from reachy_mini_conversation_app.profile_store import read_profile_from_directory

    profile_path = DEFAULT_PROFILES_DIRECTORY / "desk_companion_ko" / "profile.md"
    assert profile_path.is_file()

    content = profile_path.read_text(encoding="utf-8")
    assert content.startswith("+++")
    assert "schema_version = 1" in content
    assert "한국어" in content
    # The shutdown rule must explicitly demand the go_to_sleep tool call —
    # without it the model says goodbye verbally and never sleeps.
    assert "go_to_sleep 도구를 호출" in content

    profile = read_profile_from_directory("desk_companion_ko", profile_path.parent)
    assert profile.greeting is not None
    assert "한국어" in profile.greeting
