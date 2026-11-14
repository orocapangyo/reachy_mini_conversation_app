"""Tests for configuration helpers."""

import pytest

from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.config import (
    GEMINI_BACKEND,
    DEFAULT_GEMINI_VOICE,
    DEFAULT_GEMINI_LIVE_MODEL,
    refresh_runtime_config_from_env,
)


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ("45", 45.0),
        ("", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unset/blank falls back to the default
        ("soon", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unparseable falls back to the default
        ("0", None),  # non-positive disables the watchdog
        ("-1", None),
    ],
)
def test_resolve_app_timeout_minutes(monkeypatch, raw_value, expected) -> None:
    """The env timeout parses to minutes, falls back to the default, or disables on non-positive."""
    monkeypatch.setenv(config.APP_TIMEOUT_MINUTES_ENV, raw_value)

    assert config.resolve_app_timeout_minutes() == expected


def test_gemini_config_exposes_settings_with_defaults() -> None:
    """Config singleton must expose Gemini settings with safe non-empty defaults."""
    assert hasattr(config.config, "GEMINI_API_KEY")
    assert hasattr(config.config, "GEMINI_LIVE_MODEL")
    assert hasattr(config.config, "GEMINI_VOICE")
    assert config.config.GEMINI_LIVE_MODEL  # non-empty default
    assert config.config.GEMINI_VOICE  # non-empty default


def test_gemini_config_defaults_match_constants() -> None:
    """When no env vars are set, Gemini settings fall back to the module-level defaults."""
    original_model = config.config.GEMINI_LIVE_MODEL
    original_voice = config.config.GEMINI_VOICE
    try:
        config.config.GEMINI_LIVE_MODEL = DEFAULT_GEMINI_LIVE_MODEL
        config.config.GEMINI_VOICE = DEFAULT_GEMINI_VOICE
        assert config.config.GEMINI_LIVE_MODEL == DEFAULT_GEMINI_LIVE_MODEL
        assert config.config.GEMINI_VOICE == DEFAULT_GEMINI_VOICE
    finally:
        config.config.GEMINI_LIVE_MODEL = original_model
        config.config.GEMINI_VOICE = original_voice


def test_gemini_backend_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONVERSATION_BACKEND=gemini must reach the config singleton via refresh."""
    original_backend = config.config.CONVERSATION_BACKEND
    monkeypatch.setenv("CONVERSATION_BACKEND", "gemini")
    refresh_runtime_config_from_env()
    try:
        assert config.config.CONVERSATION_BACKEND == GEMINI_BACKEND
    finally:
        monkeypatch.delenv("CONVERSATION_BACKEND", raising=False)
        refresh_runtime_config_from_env()
    assert config.config.CONVERSATION_BACKEND == original_backend
