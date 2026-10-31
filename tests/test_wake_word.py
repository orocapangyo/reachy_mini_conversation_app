"""Tests for WakeWordListener, wake engines, config variables, and phrase matching.

All async tests use asyncio.wait_for to prevent infinite hangs.
"""

from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.audio.wake_word import (
    DEFAULT_WAKE_PHRASES,
    WAKE_CONSECUTIVE_CHUNKS,
    WakeWordListener,
    matches_wake_phrase,
    configured_wake_phrases,
)
from reachy_mini_conversation_app.audio.wake_engines import MockWakeEngine, create_wake_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _silent_frame(sample_rate: int = 16_000, duration_ms: int = 100) -> np.ndarray:
    """Return a silent int16 PCM frame."""
    n = int(sample_rate * duration_ms / 1000)
    return np.zeros(n, dtype=np.int16)


def _make_listener(
    engine: Any,
    on_wake: Any,
    loop: asyncio.AbstractEventLoop | None = None,
) -> WakeWordListener:
    return WakeWordListener(engine=engine, on_wake=on_wake, loop=loop)


# ---------------------------------------------------------------------------
# matches_wake_phrase — re-exported from openai_realtime, now canonical in wake_word
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transcript",
    ["깨어나 리치미니", "리치 미니 일어나!", "야, 깨어나.", "리치미니야 안녕", "이제 일어나볼까"],
)
def test_matches_wake_phrase_positive(transcript: str) -> None:
    """Spacing and punctuation variants of wake phrases must match."""
    assert matches_wake_phrase(transcript, ["깨어나", "리치미니", "일어나"]) is True


@pytest.mark.parametrize("transcript", ["안녕하세요", "오늘 날씨 어때", "", "   "])
def test_matches_wake_phrase_negative(transcript: str) -> None:
    """Unrelated or empty transcripts must not wake the robot."""
    assert matches_wake_phrase(transcript, ["깨어나", "리치미니", "일어나"]) is False


def test_configured_wake_phrases_env_override(monkeypatch: Any) -> None:
    """REACHY_MINI_WAKE_PHRASES replaces the default list; blank falls back."""
    monkeypatch.setattr(config, "REACHY_MINI_WAKE_PHRASES", "굿모닝, 헬로")
    assert configured_wake_phrases() == ["굿모닝", "헬로"]

    monkeypatch.setattr(config, "REACHY_MINI_WAKE_PHRASES", "")
    assert configured_wake_phrases() == list(DEFAULT_WAKE_PHRASES)


# ---------------------------------------------------------------------------
# test_standby.py imports from openai_realtime — verify re-export is intact
# ---------------------------------------------------------------------------


def test_openai_realtime_reexports_wake_phrase_utils() -> None:
    """matches_wake_phrase and configured_wake_phrases must still be importable from openai_realtime."""
    from reachy_mini_conversation_app.openai_realtime import (
        DEFAULT_WAKE_PHRASES as dwp,
    )
    from reachy_mini_conversation_app.openai_realtime import (
        matches_wake_phrase as mwp,
    )
    from reachy_mini_conversation_app.openai_realtime import (  # noqa: PLC0415
        configured_wake_phrases as cwp,
    )

    assert callable(mwp)
    assert callable(cwp)
    assert dwp == DEFAULT_WAKE_PHRASES
    # Functional check: the re-exported functions must work
    assert mwp("리치미니야", ["리치미니"]) is True


# ---------------------------------------------------------------------------
# Config new variables
# ---------------------------------------------------------------------------


def test_config_wake_word_enabled_default() -> None:
    """REACHY_MINI_WAKE_WORD_ENABLED must exist and be a bool."""
    assert hasattr(config, "REACHY_MINI_WAKE_WORD_ENABLED")
    # default is False (env not set in test environment)
    assert config.REACHY_MINI_WAKE_WORD_ENABLED in (True, False)


def test_config_wake_engine_default() -> None:
    """REACHY_MINI_WAKE_ENGINE must exist and default to a non-empty string."""
    assert hasattr(config, "REACHY_MINI_WAKE_ENGINE")
    # default is "openwakeword" when env is unset
    val = config.REACHY_MINI_WAKE_ENGINE
    assert isinstance(val, str)
    assert val  # non-empty


def test_config_wake_model_path_default() -> None:
    """REACHY_MINI_WAKE_MODEL_PATH must exist and be a string."""
    assert hasattr(config, "REACHY_MINI_WAKE_MODEL_PATH")
    assert isinstance(config.REACHY_MINI_WAKE_MODEL_PATH, str)


# ---------------------------------------------------------------------------
# MockWakeEngine
# ---------------------------------------------------------------------------


def test_mock_engine_never_detects_without_trigger() -> None:
    """MockWakeEngine without a trigger_fn must never return a phrase."""
    engine = MockWakeEngine()
    for _ in range(10):
        assert engine.process(_silent_frame()) is None


def test_mock_engine_triggers_on_nth_call() -> None:
    """MockWakeEngine trigger_fn is called with call count and its return drives detection."""
    engine = MockWakeEngine(trigger_fn=lambda n: "reachy mini" if n == 3 else None)
    assert engine.process(_silent_frame()) is None
    assert engine.process(_silent_frame()) is None
    assert engine.process(_silent_frame()) == "reachy mini"
    assert engine.process(_silent_frame()) is None


def test_mock_engine_reset_restarts_counter() -> None:
    """reset() resets the call counter so trigger_fn counts from 1 again."""
    engine = MockWakeEngine(trigger_fn=lambda n: "reachy mini" if n == 2 else None)
    engine.process(_silent_frame())
    assert engine.process(_silent_frame()) == "reachy mini"
    engine.reset()
    assert engine.process(_silent_frame()) is None  # counter reset, n=1 now
    assert engine.process(_silent_frame()) == "reachy mini"


# ---------------------------------------------------------------------------
# WakeWordListener — state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listener_start_stop_idempotent() -> None:
    """start/stop may be called multiple times without error."""
    engine = MockWakeEngine()
    on_wake = MagicMock()
    listener = _make_listener(engine, on_wake)

    listener.start()
    listener.start()  # second start is a no-op
    listener.stop()
    listener.stop()  # second stop is a no-op


@pytest.mark.asyncio
async def test_feed_after_stop_is_dropped() -> None:
    """feed() after stop() must not enqueue frames (listener is not running)."""
    called: list[str] = []
    engine = MockWakeEngine(trigger_fn=lambda _n: "reachy mini")
    listener = _make_listener(engine, lambda p: called.append(p))

    listener.start()
    listener.stop()

    # feed after stop — worker is no longer running
    for _ in range(5):
        listener.feed(16_000, _silent_frame())

    await asyncio.sleep(0.1)
    assert called == [], "on_wake must not fire after stop()"


@pytest.mark.asyncio
async def test_listener_delivers_wake_to_event_loop() -> None:
    """When the engine triggers, on_wake is called via the event loop."""
    loop = asyncio.get_running_loop()
    detected: list[str] = []
    # Detection requires WAKE_CONSECUTIVE_CHUNKS consecutive engine hits.
    engine = MockWakeEngine(trigger_fn=lambda _n: "reachy mini")
    listener = WakeWordListener(engine=engine, on_wake=lambda p: detected.append(p), loop=loop)
    listener.start()

    try:
        for _ in range(WAKE_CONSECUTIVE_CHUNKS):
            listener.feed(16_000, _silent_frame(duration_ms=200))
        await asyncio.wait_for(
            _wait_for_condition(lambda: len(detected) >= 1),
            timeout=5.0,
        )
    finally:
        listener.stop()

    assert detected == ["reachy mini"]


@pytest.mark.asyncio
async def test_single_isolated_hit_does_not_wake() -> None:
    """A lone above-threshold chunk (momentary spike) must NOT fire on_wake.

    Real-speech finding: partial utterances ("리치", "미니") can cross the
    threshold for one chunk; the consecutive-chunks guard filters them.
    """
    loop = asyncio.get_running_loop()
    detected: list[str] = []

    # Hit on chunk 2 only — surrounded by misses, never consecutive.
    # Construct with an explicit 2-chunk guard (production default is 1).
    engine = MockWakeEngine(trigger_fn=lambda n: "reachy mini" if n == 2 else None)
    listener = WakeWordListener(
        engine=engine,
        on_wake=lambda p: detected.append(p),
        loop=loop,
        consecutive_chunks=2,
    )
    listener.start()

    try:
        for _ in range(6):
            listener.feed(16_000, _silent_frame(duration_ms=50))
        await asyncio.sleep(0.3)
    finally:
        listener.stop()

    assert detected == [], "isolated single-chunk hit must be filtered by the consecutive guard"


@pytest.mark.asyncio
async def test_listener_cooldown_suppresses_rapid_rerdetection() -> None:
    """A second detection within WAKE_COOLDOWN_S must be suppressed."""
    loop = asyncio.get_running_loop()
    detected: list[str] = []

    # Always triggers
    engine = MockWakeEngine(trigger_fn=lambda _n: "reachy mini")
    listener = WakeWordListener(engine=engine, on_wake=lambda p: detected.append(p), loop=loop)
    listener.start()

    try:
        # Feed enough frames to trigger multiple times in rapid succession
        for _ in range(10):
            listener.feed(16_000, _silent_frame(duration_ms=50))

        await asyncio.wait_for(
            _wait_for_condition(lambda: len(detected) >= 1),
            timeout=5.0,
        )
        # Give a bit more time to check for unwanted extra fires
        await asyncio.sleep(0.3)
    finally:
        listener.stop()

    # Cooldown is 3 s, so only 1 detection should have passed through
    assert len(detected) == 1, f"Expected 1 detection (cooldown), got {len(detected)}"


@pytest.mark.asyncio
async def test_stop_resets_cooldown() -> None:
    """stop() resets the cooldown so the next start() can detect immediately."""
    loop = asyncio.get_running_loop()
    detected: list[str] = []

    engine = MockWakeEngine(trigger_fn=lambda _n: "reachy mini")
    listener = WakeWordListener(engine=engine, on_wake=lambda p: detected.append(p), loop=loop)

    # First run
    listener.start()
    for _ in range(WAKE_CONSECUTIVE_CHUNKS):
        listener.feed(16_000, _silent_frame())
    await asyncio.wait_for(_wait_for_condition(lambda: len(detected) >= 1), timeout=5.0)
    listener.stop()

    # Second run — cooldown must be cleared by stop()
    listener.start()
    for _ in range(WAKE_CONSECUTIVE_CHUNKS):
        listener.feed(16_000, _silent_frame())
    await asyncio.wait_for(_wait_for_condition(lambda: len(detected) >= 2), timeout=5.0)
    listener.stop()

    assert len(detected) == 2


# ---------------------------------------------------------------------------
# Factory — import failure fallback
# ---------------------------------------------------------------------------


def test_factory_mock() -> None:
    """create_wake_engine('mock') must return a MockWakeEngine."""
    engine = create_wake_engine("mock")
    assert isinstance(engine, MockWakeEngine)


def test_factory_unknown_name_returns_none() -> None:
    """create_wake_engine with an unknown name must return None without raising."""
    result = create_wake_engine("nonexistent_engine_xyz")
    assert result is None


def test_factory_openwakeword_empty_path_falls_back_to_bundled_model(monkeypatch: Any) -> None:
    """An unset model path must load the repo-bundled reachy_mini_v2 model."""
    monkeypatch.setattr(config, "REACHY_MINI_WAKE_MODEL_PATH", "")
    result = create_wake_engine("openwakeword")
    assert result is not None
    assert result._label.startswith("reachy_mini_v")  # type: ignore[union-attr]
    result.close()  # type: ignore[union-attr]


def _fake_feature_models(tmp_path: Any) -> dict[str, dict[str, str]]:
    """Feature-model table pointing at tmp_path, shaped like openwakeword.FEATURE_MODELS."""
    return {
        name: {
            "model_path": str(tmp_path / f"{name}.tflite"),
            "download_url": f"https://example.invalid/{name}.tflite",
        }
        for name in ("embedding", "melspectrogram")
    }


def test_ensure_feature_models_downloads_only_missing(monkeypatch: Any, tmp_path: Any) -> None:
    """Missing feature models are fetched in the requested format; present ones are skipped."""
    import openwakeword  # noqa: PLC0415

    from reachy_mini_conversation_app.audio import wake_engines  # noqa: PLC0415

    monkeypatch.setattr(openwakeword, "FEATURE_MODELS", _fake_feature_models(tmp_path))
    (tmp_path / "embedding.onnx").write_bytes(b"present")
    with patch("openwakeword.utils.download_file") as download:
        wake_engines._ensure_feature_models("onnx")

    download.assert_called_once_with("https://example.invalid/melspectrogram.onnx", str(tmp_path))


def test_ensure_feature_models_wraps_download_failure(monkeypatch: Any, tmp_path: Any) -> None:
    """A failed download surfaces as RuntimeError so create_wake_engine falls back to None."""
    import openwakeword  # noqa: PLC0415

    from reachy_mini_conversation_app.audio import wake_engines  # noqa: PLC0415

    monkeypatch.setattr(openwakeword, "FEATURE_MODELS", _fake_feature_models(tmp_path))
    with patch("openwakeword.utils.download_file", side_effect=OSError("offline")):
        with pytest.raises(RuntimeError, match="offline"):
            wake_engines._ensure_feature_models("onnx")


def test_factory_openwakeword_bad_explicit_path_returns_none(monkeypatch: Any) -> None:
    """A configured-but-missing model path returns None, without crashing."""
    monkeypatch.setattr(config, "REACHY_MINI_WAKE_MODEL_PATH", "/nonexistent/model.onnx")
    result = create_wake_engine("openwakeword")
    assert result is None


def test_factory_openwakeword_import_failure_returns_none(monkeypatch: Any) -> None:
    """Factory returns None (with warning) when openwakeword is not installed."""
    monkeypatch.setattr(config, "REACHY_MINI_WAKE_MODEL_PATH", "/fake/model.tflite")

    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openwakeword" or name.startswith("openwakeword."):
            raise ImportError("openwakeword not installed (mocked)")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        result = create_wake_engine("openwakeword")

    assert result is None


# ---------------------------------------------------------------------------
# OpenWakeWordEngine guard — constructor raises RuntimeError on bad path
# ---------------------------------------------------------------------------


def test_oww_engine_raises_on_missing_model_file() -> None:
    """OpenWakeWordEngine.__init__ raises RuntimeError when model file is absent."""
    try:
        import openwakeword  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("openwakeword not installed — skipping constructor guard test")

    from reachy_mini_conversation_app.audio.wake_engines import OpenWakeWordEngine  # noqa: PLC0415

    with pytest.raises(RuntimeError, match="model file not found"):
        OpenWakeWordEngine(model_path="/nonexistent/path/model.tflite")


# ---------------------------------------------------------------------------
# test_standby.py regression: openai_realtime re-exports must not break standby
# ---------------------------------------------------------------------------


def test_standby_imports_still_work() -> None:
    """Ensure test_standby.py's imports from openai_realtime remain functional."""
    from reachy_mini_conversation_app.openai_realtime import (  # noqa: PLC0415
        matches_wake_phrase,
        configured_wake_phrases,
    )

    assert matches_wake_phrase("깨어나 리치미니", ["깨어나", "리치미니", "일어나"]) is True
    assert matches_wake_phrase("안녕하세요", ["깨어나", "리치미니", "일어나"]) is False
    assert isinstance(configured_wake_phrases(), list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _wait_for_condition(condition: Any, poll_interval: float = 0.05) -> None:
    """Busy-poll until condition() returns True."""
    while not condition():
        await asyncio.sleep(poll_interval)
