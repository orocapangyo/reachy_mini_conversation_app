"""OpenAI Realtime API backend: a thin subclass of the Hugging Face handler.

Only the Hugging Face-specific attachment points are overridden (client/auth,
24 kHz audio format, voice catalog, model selection). The conversation loop,
tool plumbing, and idle policy are inherited unchanged.
"""

import re
import time
import base64
import asyncio
import logging
from typing import Any, Tuple, Sequence

import numpy as np
from openai import AsyncOpenAI
from numpy.typing import NDArray
from openai.types.realtime import (
    RealtimeAudioConfigParam,
    RealtimeAudioConfigInputParam,
    RealtimeSessionCreateRequestParam,
)
from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad

from reachy_mini_conversation_app.config import OPENAI_REALTIME_URL, DEFAULT_OPENAI_VOICE, config
from reachy_mini_conversation_app.prompts import get_session_voice
from reachy_mini_conversation_app.streaming import audio_to_int16
from reachy_mini_conversation_app.tools.core_tools import ToolSpec
from reachy_mini_conversation_app.conversation_handler import HandlerOutput
from reachy_mini_conversation_app.huggingface_realtime import (
    HuggingFaceRealtimeHandler,
    _build_openai_compatible_client_from_realtime_url,
)


logger = logging.getLogger(__name__)

# gpt-realtime voice catalog. marin and cedar are the newest, most natural voices.
OPENAI_AVAILABLE_VOICES: list[str] = [
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "marin",
    "sage",
    "shimmer",
    "verse",
]

# Lookup for case-insensitive voice normalization, shared by every resolver below.
_VOICE_BY_LOWERCASE: dict[str, str] = {candidate.lower(): candidate for candidate in OPENAI_AVAILABLE_VOICES}

# The OpenAI realtime endpoint only accepts 24 kHz PCM16.
OPENAI_TARGET_SAMPLE_RATE: int = 24000

# Rate assumed when the media backend cannot report its output sample rate.
FALLBACK_OUTPUT_SAMPLE_RATE: int = 16000

# 3-tap binomial low-pass applied before downsampling to limit aliasing.
_ANTI_ALIAS_KERNEL = np.array([0.25, 0.5, 0.25], dtype=np.float64)

# Wake phrases matched against user transcripts while in standby.
DEFAULT_WAKE_PHRASES: tuple[str, ...] = ("깨어나", "리치미니", "일어나")

# Prompt queued after waking so the model opens the conversation itself.
WAKE_GREETING_PROMPT = (
    "(사용자가 방금 웨이크 워드로 너를 깨웠다. 한국어로 짧고 반갑게 인사하고 무엇을 도울지 물어보라.)"
)

# Prompt queued when user speaks during standby without a wake phrase.
STANDBY_SLEEPING_PROMPT = (
    "(너는 지금 수면(절전 대기) 모드이다. 깨어있지 않으므로 사용자의 명령을 수행할 수 없다. "
    "한국어로 '현재 수면 중입니다. 대화하시려면 리치야, 안녕, 또는 일어나라고 불러 깨워주세요.'라고 "
    "차분하고 짧게 한 문장으로만 안내하라. 절대 도구를 호출하거나 깨어나지 마라.)"
)


def _normalize_for_wake(text: str) -> str:
    """Strip whitespace/punctuation and lowercase so STT spelling variants match."""
    return re.sub(r"[\W_]+", "", text.lower())


def matches_wake_phrase(transcript: str, phrases: Sequence[str]) -> bool:
    """Return whether the transcript loosely contains any wake phrase."""
    normalized = _normalize_for_wake(transcript)
    if not normalized:
        return False
    for phrase in phrases:
        normalized_phrase = _normalize_for_wake(phrase)
        if normalized_phrase and normalized_phrase in normalized:
            return True
    return False


def configured_wake_phrases() -> list[str]:
    """Return REACHY_MINI_WAKE_PHRASES as a list, defaulting to the Korean set."""
    raw = (getattr(config, "REACHY_MINI_WAKE_PHRASES", None) or "").strip()
    phrases = [phrase.strip() for phrase in raw.split(",") if phrase.strip()]
    return phrases or list(DEFAULT_WAKE_PHRASES)


def _default_openai_voice() -> str:
    """Return the configured default OpenAI voice, validated against the catalog."""
    configured = (getattr(config, "OPENAI_VOICE", None) or "").strip()
    normalized = _VOICE_BY_LOWERCASE.get(configured.lower())
    if normalized is not None:
        return normalized
    if configured:
        logger.warning("Ignoring unsupported OPENAI_VOICE %r; using %s", configured, DEFAULT_OPENAI_VOICE)
    return DEFAULT_OPENAI_VOICE


def _pcm_24k_format() -> dict[str, object]:
    """Return a fresh 24 kHz PCM16 audio-format payload."""
    return {"type": "audio/pcm", "rate": OPENAI_TARGET_SAMPLE_RATE}


def resample_pcm(
    audio: NDArray[np.int16],
    source_rate: int,
    target_rate: int,
) -> NDArray[np.int16]:
    """Linearly resample mono int16 PCM between the mic/speaker and OpenAI's 24 kHz.

    Used both ways: 16 kHz mic -> 24 kHz uplink, and 24 kHz model audio -> the
    output device rate. Downsampling first runs a cheap 3-tap low-pass so the
    content above the new Nyquist frequency does not alias; upsampling needs no
    filter.

    Known limitation: resampling happens per frame and each frame is
    interpolated over its own endpoints, so frame boundaries are not
    phase-continuous. The artifact is inaudible at speech frame sizes and
    avoiding it would require carrying filter state across calls.
    """
    if audio.size == 0 or source_rate == target_rate:
        return audio
    audio_float = audio.astype(np.float64)
    if target_rate < source_rate:
        audio_float = np.convolve(audio_float, _ANTI_ALIAS_KERNEL, mode="same")
    target_length = max(1, round(audio.shape[0] * target_rate / source_rate))
    source_positions = np.arange(audio.shape[0], dtype=np.float64)
    target_positions = np.linspace(0.0, audio.shape[0] - 1, num=target_length)
    resampled = np.interp(target_positions, source_positions, audio_float)
    return np.round(resampled).astype(np.int16)


class OpenAIRealtimeHandler(HuggingFaceRealtimeHandler):
    """Realtime stream handler for the OpenAI Realtime API."""

    SAMPLE_RATE = OPENAI_TARGET_SAMPLE_RATE

    _output_sample_rate: int | None = None
    _standby: bool = False
    _standby_loop: "asyncio.AbstractEventLoop | None" = None
    _last_standby_notice_time: float = 0.0
    # Sound-direction watcher, injected by main.py when DoA gaze is enabled.
    sound_watcher: Any = None

    # --- standby (sleep-wait) state machine ---------------------------------

    @property
    def in_standby(self) -> bool:
        """Return whether the handler is in sleep-wait (transcription-only) mode."""
        return self._standby

    def request_standby(self) -> dict[str, Any]:
        """Thread-safe standby entry for the synchronous go_to_sleep tool path."""
        loop = self._standby_loop
        if loop is None or not loop.is_running():
            return {"error": "standby unavailable: no active realtime session"}
        future = asyncio.run_coroutine_threadsafe(self.enter_standby(), loop)
        try:
            return future.result(timeout=30.0)
        except Exception as e:
            logger.error("Failed to enter standby: %s", e)
            return {"error": f"standby failed: {type(e).__name__}: {e}"}

    async def enter_standby(self) -> dict[str, Any]:
        """Sleep pose + transcription-only session; wake phrases resume later."""
        if self._standby:
            return {"status": "standby", "message": "Already in standby."}
        logger.info("Entering standby: sleep pose, listening for wake phrases %s", configured_wake_phrases())
        self._standby = True
        try:
            self.deps.movement_manager.stop(reset_to_neutral=False)
            await asyncio.to_thread(self.deps.reachy_mini.goto_sleep)
        except Exception as e:
            logger.warning("Standby sleep motion failed: %s", e)
        await self._update_turn_detection(transcription_only=True)
        return {
            "status": "standby",
            "message": "Reachy is now sleeping and will wake on the wake phrase. Do not respond further.",
        }

    async def wake_from_standby(self, *, greet: bool = True) -> None:
        """Wake motion, restore auto-responses, and optionally greet the user.

        greet=False is used when something else is about to speak (e.g. a timer
        completion announcement) so the robot does not double-greet.
        """
        if not self._standby:
            return
        logger.info("Waking from standby (greet=%s)", greet)
        self._standby = False
        try:
            self.deps.movement_manager.start()
        except Exception as e:
            logger.warning("Failed to restart movement manager after standby: %s", e)
        try:
            await asyncio.to_thread(self._run_wake_up_motion)
        except Exception as e:
            logger.warning("Wake-up movement failed: %s", e)
        await self._gaze_toward_wake_caller()
        await self._update_turn_detection(transcription_only=False)
        if greet:
            await self._send_wake_greeting()

    async def _handle_tool_result(self, completed_tool: Any) -> None:
        """Wake first when a background tool (e.g. a timer) finishes during standby.

        Tool-result announcements bypass the standby response mute, so without
        this the robot would talk while still in the sleep pose. go_to_sleep is
        excluded — its completion is what enters standby in the first place.
        """
        from reachy_mini_conversation_app.tools.tool_constants import ToolState

        if (
            self._standby
            and getattr(completed_tool, "tool_name", "") != "go_to_sleep"
            and getattr(completed_tool, "status", None) in (ToolState.COMPLETED, ToolState.FAILED)
        ):
            logger.info(
                "Background tool %r finished during standby; waking to announce",
                getattr(completed_tool, "tool_name", "?"),
            )
            await self.wake_from_standby(greet=False)
        await super()._handle_tool_result(completed_tool)

    def _run_wake_up_motion(self) -> None:
        """Enable motors and run the SDK wake-up move (app_lifecycle pattern)."""
        robot = self.deps.reachy_mini
        robot.enable_motors()
        robot.wake_up()

    async def _gaze_toward_wake_caller(self) -> None:
        """Look toward where the wake phrase came from, when DoA data is fresh."""
        watcher = self.sound_watcher
        if watcher is None:
            return
        try:
            from reachy_mini_conversation_app.sound_direction import (
                WAKE_GAZE_MAX_AGE_S,
                queue_gaze,
                doa_angle_to_head_yaw,
            )

            angle = watcher.recent_speech_angle(WAKE_GAZE_MAX_AGE_S)
            if angle is None:
                return
            yaw = doa_angle_to_head_yaw(angle)
            await asyncio.to_thread(queue_gaze, self.deps.movement_manager, self.deps.reachy_mini, yaw)
            logger.info("Wake gaze toward DoA angle %.2f rad (head yaw %.2f rad)", angle, yaw)
        except Exception as e:
            logger.warning("Wake gaze failed: %s", e)

    async def _update_turn_detection(self, *, transcription_only: bool) -> None:
        """Switch server VAD between transcription-only and normal conversation."""
        if not self.connection:
            return
        if transcription_only:
            turn_detection = ServerVad(type="server_vad", create_response=False, interrupt_response=False)
        else:
            # Explicit create_response=True: the server merges session updates,
            # so omitting the key would keep the standby False in effect.
            turn_detection = ServerVad(type="server_vad", create_response=True, interrupt_response=True)
        try:
            await self.connection.session.update(
                session=RealtimeSessionCreateRequestParam(
                    type="realtime",
                    audio=RealtimeAudioConfigParam(
                        input=RealtimeAudioConfigInputParam(turn_detection=turn_detection),
                    ),
                ),
            )
        except Exception as e:
            logger.warning("Failed to update session turn detection: %s", e)

    async def _send_wake_greeting(self) -> None:
        """Queue a prompt so the model greets the user right after waking."""
        if not self.connection:
            return
        try:
            await self.connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": WAKE_GREETING_PROMPT}],
                },
            )
            await self._safe_response_create()
        except Exception as e:
            logger.warning("Failed to queue wake greeting: %s", e)

    async def _send_standby_sleeping_notice(self) -> None:
        """Inform the user that the robot is currently sleeping and needs a wake phrase."""
        if not self._standby or not self.connection:
            return
        now = time.monotonic()
        if now - self._last_standby_notice_time < 4.0:
            return
        self._last_standby_notice_time = now
        try:
            logger.info("Non-wake speech received during standby; sending sleeping notice")
            await self.connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": STANDBY_SLEEPING_PROMPT}],
                },
            )
            await self._safe_response_create()
        except Exception as e:
            logger.warning("Failed to queue standby sleeping notice: %s", e)

    def _emit_transcript(self, role: str, text: str, final: bool = True) -> None:
        """Watch user transcripts for wake phrases or commands while in standby."""
        if self._standby and role == "user" and final:
            loop = self._standby_loop
            if matches_wake_phrase(text, configured_wake_phrases()):
                if loop is not None and loop.is_running():
                    loop.create_task(self.wake_from_standby())
                else:
                    logger.warning("Wake phrase heard but no running session loop to wake on")
            elif len(text.strip()) >= 2:
                if loop is not None and loop.is_running():
                    loop.create_task(self._send_standby_sleeping_notice())
                else:
                    logger.warning("Non-wake speech heard during standby but no running session loop")
        super()._emit_transcript(role, text, final)

    def _idle_behavior_ready(self) -> bool:
        """Suppress idle behaviors entirely while sleeping in standby."""
        if self._standby:
            return False
        return super()._idle_behavior_ready()

    # --- client/session -----------------------------------------------------

    def _get_output_sample_rate(self) -> int:
        """Return (and cache) the playback device rate, falling back to 16 kHz."""
        cached = self._output_sample_rate
        if cached is not None:
            return cached

        rate = FALLBACK_OUTPUT_SAMPLE_RATE
        try:
            reported = self.deps.reachy_mini.media.get_output_audio_samplerate()
        except Exception as e:
            logger.warning("Could not read the output sample rate (%s); assuming %d Hz", e, rate)
        else:
            if isinstance(reported, int) and not isinstance(reported, bool) and reported > 0:
                rate = reported
            else:
                logger.warning("Ignoring invalid output sample rate %r; assuming %d Hz", reported, rate)

        self._output_sample_rate = rate
        logger.info("Playback sample rate: %d Hz (model audio is %d Hz)", rate, self.SAMPLE_RATE)
        return rate

    async def emit(self) -> HandlerOutput:
        """Emit queued output, resampling model audio to the playback device rate.

        The playback path downstream (`console.py` -> `push_audio_sample`) drops
        the rate from the emitted tuple and feeds a fixed-rate sink, so 24 kHz
        model audio would otherwise play back slowed down. Resample here, where
        the rate is still known.
        """
        handler_output = await super().emit()
        if not isinstance(handler_output, tuple):
            return handler_output

        source_rate, audio = handler_output
        target_rate = self._get_output_sample_rate()
        if source_rate == target_rate or audio.size == 0:
            return handler_output

        flat = np.asarray(audio).reshape(-1)
        resampled = resample_pcm(flat, source_rate, target_rate)
        if audio.ndim == 2:
            resampled = resampled.reshape(1, -1)
        return (target_rate, resampled)

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build the OpenAI realtime client from OPENAI_API_KEY."""
        self._standby_loop = asyncio.get_running_loop()
        api_key = (getattr(config, "OPENAI_API_KEY", None) or "").strip()
        if not api_key:
            raise RuntimeError("CONVERSATION_BACKEND=openai requires OPENAI_API_KEY to be set")
        client, connect_query = _build_openai_compatible_client_from_realtime_url(
            OPENAI_REALTIME_URL,
            api_key,
        )
        connect_query["model"] = config.OPENAI_REALTIME_MODEL
        self._realtime_connect_query = connect_query
        logger.info("Using OpenAI realtime endpoint (model=%s)", config.OPENAI_REALTIME_MODEL)
        return client

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the inherited session config adjusted to OpenAI's 24 kHz PCM."""
        session = super()._get_session_config(tool_specs)
        # Separate dicts: the SDK may mutate either format in place.
        session["audio"]["input"]["format"] = _pcm_24k_format()  # type: ignore[typeddict-item]
        session["audio"]["output"]["format"] = _pcm_24k_format()  # type: ignore[typeddict-item]
        return session

    async def get_available_voices(self) -> list[str]:
        """Return the OpenAI realtime voice catalog."""
        return list(OPENAI_AVAILABLE_VOICES)

    def _resolve_backend_voice(
        self,
        voice: str | None,
        *,
        source: str,
        fallback: str | None = None,
    ) -> str | None:
        """Return an OpenAI-supported voice, optionally falling back when unsupported."""
        voice_value = (voice or "").strip()
        if not voice_value:
            return fallback

        normalized_voice = _VOICE_BY_LOWERCASE.get(voice_value.lower())
        if normalized_voice is not None:
            return normalized_voice

        logger.warning(
            "Ignoring unsupported %s %r; expected one of %s",
            source,
            voice,
            OPENAI_AVAILABLE_VOICES,
        )
        return fallback

    def get_current_voice(self) -> str:
        """Return the voice currently selected for this handler."""
        default_voice = _default_openai_voice()
        voice = self._voice_override or get_session_voice(default=default_voice)
        return self._resolve_backend_voice(voice, source="session voice", fallback=default_voice) or default_voice

    async def change_voice(self, voice: str) -> str:
        """Change the voice, reconnecting the session because OpenAI locks it after audio."""
        default_voice = _default_openai_voice()
        resolved_voice = (
            self._resolve_backend_voice(voice, source="requested voice", fallback=default_voice) or default_voice
        )
        self._voice_override = resolved_voice
        if self.connection is not None:
            # OpenAI rejects session.update voice changes once a response has produced
            # audio, and the startup greeting guarantees it has. Restart instead, the
            # same way the parent handler recovers in apply_personality().
            try:
                await self._restart_session()
                return f"Voice changed to {resolved_voice}; reconnecting session."
            except Exception as e:
                logger.warning("Failed to restart session for voice change: %s", e)
                return "Voice change failed. Will take effect on next connection."
        return "Voice changed. Will take effect on next connection."

    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Resample mic audio to 24 kHz and forward it to the realtime server."""
        if not self.connection:
            return

        source_rate, audio_frame = frame
        if audio_frame.size == 0:
            return

        # Mono-ize using the same conventions as the parent handler; copied from
        # huggingface_realtime.py:965-971 — keep in sync when merging upstream.
        if audio_frame.ndim == 2:
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]
        audio_frame = np.asarray(audio_frame).reshape(-1)

        audio_frame = audio_to_int16(audio_frame)
        audio_frame = resample_pcm(audio_frame, source_rate, self.SAMPLE_RATE)

        try:
            audio_message = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return
