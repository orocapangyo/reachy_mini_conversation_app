"""Tests for GeminiRealtimeHandler voice, persona, and say functionality (Task 4).

All tests run without network access. The FakeSession / FakeClient classes
simulate the google-genai AsyncSession interface used by the handler.
"""

from __future__ import annotations
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.gemini_realtime import (
    GEMINI_AVAILABLE_VOICES,
    GeminiRealtimeHandler,
)


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class FakeSession:
    """Simulates an open AsyncSession — records send_client_content calls."""

    def __init__(self) -> None:
        """Initialise the fake session with an empty call log."""
        self.sent_contents: list[dict[str, Any]] = []

    async def close(self) -> None:
        """No-op close to satisfy the interface."""

    async def send_client_content(self, *, turns: Any, turn_complete: bool) -> None:
        """Record the call arguments for later assertion."""
        self.sent_contents.append({"turns": turns, "turn_complete": turn_complete})


def _make_deps() -> MagicMock:
    deps = MagicMock()
    deps.reachy_mini.media.get_output_audio_samplerate.return_value = 24000
    return deps


def _make_handler(**kwargs: Any) -> GeminiRealtimeHandler:
    return GeminiRealtimeHandler(deps=_make_deps(), **kwargs)


# ---------------------------------------------------------------------------
# Voice normalization
# ---------------------------------------------------------------------------


class TestVoiceNormalization:
    """Voice name normalization and catalog lookup."""

    def test_lowercase_input_is_normalised(self) -> None:
        """Lower-case voice name must be resolved to catalog title-case."""
        handler = _make_handler(startup_voice="kore")
        assert handler.get_current_voice() == "Kore"

    def test_uppercase_input_is_normalised(self) -> None:
        """Upper-case voice name must be resolved to catalog title-case."""
        handler = _make_handler(startup_voice="FENRIR")
        assert handler.get_current_voice() == "Fenrir"

    def test_mixed_case_input_is_normalised(self) -> None:
        """Mixed-case voice name must be resolved to catalog title-case."""
        handler = _make_handler(startup_voice="aOeDe")
        assert handler.get_current_voice() == "Aoede"

    def test_all_catalog_voices_are_normalised(self) -> None:
        """Every catalog voice must survive a lower-case round-trip."""
        for voice in GEMINI_AVAILABLE_VOICES:
            handler = _make_handler(startup_voice=voice.lower())
            assert handler.get_current_voice() == voice

    def test_unsupported_voice_falls_back_to_config_default(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unsupported startup_voice leaves _voice_override as None (config fallback)."""
        handler = _make_handler(startup_voice="UnknownVoice")
        with caplog.at_level(logging.WARNING):
            # startup_voice was unsupported → _voice_override is None
            assert handler._voice_override is None
        assert "Ignoring unsupported Gemini voice" in caplog.text

    def test_unsupported_startup_voice_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unsupported startup_voice must emit a WARNING log."""
        with caplog.at_level(logging.WARNING):
            _make_handler(startup_voice="bogus-voice")
        assert "Ignoring unsupported Gemini voice" in caplog.text

    def test_none_startup_voice_uses_config(self) -> None:
        """No startup_voice leaves _voice_override unset, so config default is used."""
        handler = _make_handler(startup_voice=None)
        assert handler._voice_override is None
        voice = handler.get_current_voice()
        assert voice in GEMINI_AVAILABLE_VOICES or voice == "Kore"

    @pytest.mark.asyncio
    async def test_get_available_voices_returns_full_catalog(self) -> None:
        """get_available_voices must return every entry in GEMINI_AVAILABLE_VOICES."""
        voices = await _make_handler().get_available_voices()
        assert set(voices) == set(GEMINI_AVAILABLE_VOICES)
        assert len(voices) == len(GEMINI_AVAILABLE_VOICES)


# ---------------------------------------------------------------------------
# change_voice
# ---------------------------------------------------------------------------


class TestChangeVoice:
    """Behaviour of change_voice with and without an active session."""

    @pytest.mark.asyncio
    async def test_change_voice_no_connection_returns_deferred_message(self) -> None:
        """Without a live session, change_voice must store the voice and defer reconnect."""
        handler = _make_handler()
        result = await handler.change_voice("Puck")
        assert "Will take effect on next connection" in result
        assert handler._voice_override == "Puck"

    @pytest.mark.asyncio
    async def test_change_voice_normalises_name(self) -> None:
        """change_voice must normalise the name to catalog casing."""
        handler = _make_handler()
        await handler.change_voice("puck")
        assert handler._voice_override == "Puck"

    @pytest.mark.asyncio
    async def test_change_voice_with_connection_calls_restart(self) -> None:
        """With an active session, change_voice must call _restart_session."""
        handler = _make_handler()
        handler.connection = FakeSession()

        restart_called = False

        async def fake_restart() -> None:
            nonlocal restart_called
            restart_called = True
            handler.connection = FakeSession()

        handler._restart_session = fake_restart

        result = await handler.change_voice("Zephyr")
        assert restart_called
        assert handler._voice_override == "Zephyr"
        assert "reconnecting session" in result.lower()

    @pytest.mark.asyncio
    async def test_change_voice_unsupported_keeps_current_and_warns(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unsupported voice in change_voice must warn and keep the current voice."""
        handler = _make_handler(startup_voice="Kore")
        with caplog.at_level(logging.WARNING):
            result = await handler.change_voice("not-a-voice")
        assert "Unsupported Gemini voice" in caplog.text
        assert handler._voice_override == "Kore"
        assert "Will take effect on next connection" in result

    @pytest.mark.asyncio
    async def test_change_voice_with_connection_restart_failure_returns_safe_message(
        self,
    ) -> None:
        """If _restart_session raises, change_voice must return a safe fallback message."""
        handler = _make_handler()
        handler.connection = FakeSession()

        async def bad_restart() -> None:
            raise RuntimeError("restart failed")

        handler._restart_session = bad_restart

        result = await handler.change_voice("Leda")
        assert handler._voice_override == "Leda"
        assert "Will take effect on next connection" in result


# ---------------------------------------------------------------------------
# apply_personality
# ---------------------------------------------------------------------------


class TestApplyPersonality:
    """Behaviour of apply_personality with and without an active session."""

    @pytest.mark.asyncio
    async def test_apply_personality_no_connection_returns_deferred_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without a session, apply_personality must record the change and defer."""
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.set_custom_profile",
            lambda p: None,
        )
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.core_tools.initialize_tools",
            lambda force=False: None,
        )
        handler = _make_handler()
        result = await handler.apply_personality("friendly")
        assert "Will take effect on next connection" in result

    @pytest.mark.asyncio
    async def test_apply_personality_with_connection_calls_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With an active session, apply_personality must invoke _restart_session."""
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.set_custom_profile",
            lambda p: None,
        )
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.core_tools.initialize_tools",
            lambda force=False: None,
        )

        handler = _make_handler()
        handler.connection = FakeSession()

        restart_called = False

        async def fake_restart() -> None:
            nonlocal restart_called
            restart_called = True
            handler.connection = FakeSession()

        handler._restart_session = fake_restart

        result = await handler.apply_personality("serious")
        assert restart_called
        assert "restarted" in result.lower()

    @pytest.mark.asyncio
    async def test_apply_personality_tool_init_failure_restores_profile(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If tool init fails, apply_personality must restore the previous profile."""
        restored: list[Any] = []
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.set_custom_profile",
            lambda p: restored.append(p),
        )
        monkeypatch.setattr(
            "reachy_mini_conversation_app.gemini_realtime.core_tools.initialize_tools",
            MagicMock(side_effect=RuntimeError("tool init failed")),
        )
        handler = _make_handler()
        result = await handler.apply_personality("broken-profile")
        assert "Failed to apply personality" in result
        # First call sets profile; second call restores previous value.
        assert len(restored) == 2


# ---------------------------------------------------------------------------
# say
# ---------------------------------------------------------------------------


class TestSay:
    """Boundary conditions and happy path for say()."""

    @pytest.mark.asyncio
    async def test_say_empty_string_raises_value_error(self) -> None:
        """say() must raise ValueError for an empty string."""
        handler = _make_handler()
        handler.connection = FakeSession()
        with pytest.raises(ValueError, match="empty text"):
            await handler.say("")

    @pytest.mark.asyncio
    async def test_say_whitespace_only_raises_value_error(self) -> None:
        """say() must raise ValueError when the stripped text is empty."""
        handler = _make_handler()
        handler.connection = FakeSession()
        with pytest.raises(ValueError, match="empty text"):
            await handler.say("   ")

    @pytest.mark.asyncio
    async def test_say_no_session_raises_runtime_error(self) -> None:
        """say() must raise RuntimeError when no session is active."""
        handler = _make_handler()
        assert handler.connection is None
        with pytest.raises(RuntimeError, match="no active session"):
            await handler.say("hello")

    @pytest.mark.asyncio
    async def test_say_sends_correct_client_content(self) -> None:
        """say() must call send_client_content with the right role/parts/turn_complete."""
        handler = _make_handler()
        session = FakeSession()
        handler.connection = session

        await handler.say("Good morning!")

        assert len(session.sent_contents) == 1
        call = session.sent_contents[0]
        assert call["turn_complete"] is True
        turns = call["turns"]
        assert turns["role"] == "user"
        assert turns["parts"][0]["text"] == "Good morning!"

    @pytest.mark.asyncio
    async def test_say_strips_surrounding_whitespace(self) -> None:
        """say() must strip leading/trailing whitespace from the input text."""
        handler = _make_handler()
        session = FakeSession()
        handler.connection = session

        await handler.say("  hello world  ")

        call = session.sent_contents[0]
        assert call["turns"]["parts"][0]["text"] == "hello world"
