"""Gemini Live Multimodal API backend.

Connects directly to the Google Gemini Live WebSocket endpoint (BidiGenerateContent)
using gemini-3.1-flash-live-preview for low-latency bidirectional voice, VAD, STT, TTS,
and tool calling.
"""

import json
import base64
import random
import asyncio
import logging
from typing import Any, Optional

import numpy as np
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from reachy_mini_conversation_app.config import (
    GEMINI_LIVE_URL,
    DEFAULT_GEMINI_VOICE,
    DEFAULT_GEMINI_LIVE_MODEL,
    config,
    set_custom_profile,
)
from reachy_mini_conversation_app.prompts import (
    get_session_voice,
    get_session_instructions,
    get_session_greeting_prompt,
)
from reachy_mini_conversation_app.streaming import AdditionalOutputs, audio_to_int16
from reachy_mini_conversation_app.openai_realtime import (
    WAKE_GREETING_PROMPT,
    FALLBACK_OUTPUT_SAMPLE_RATE,
    resample_pcm,
    matches_wake_phrase,
    configured_wake_phrases,
)
from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    get_tool_specs,
    initialize_tools,
)
from reachy_mini_conversation_app.conversation_handler import (
    QueueItem,
    AudioFrame,
    HandlerOutput,
    ConversationHandler,
)
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


logger = logging.getLogger(__name__)

# Available voices for Gemini Live.
GEMINI_AVAILABLE_VOICES: list[str] = [
    "Aoede",
    "Charon",
    "Fenrir",
    "Kore",
    "Puck",
    "Leda",
    "Orus",
    "Zephyr",
]

_VOICE_BY_LOWERCASE: dict[str, str] = {candidate.lower(): candidate for candidate in GEMINI_AVAILABLE_VOICES}

# Gemini Live streams model output audio at 24 kHz mono PCM16.
GEMINI_OUTPUT_SAMPLE_RATE: int = 24000

# Gemini Live expects microphone audio at 16 kHz mono PCM16.
GEMINI_INPUT_SAMPLE_RATE: int = 16000


def _default_gemini_voice() -> str:
    """Return the configured default Gemini voice, validated against the catalog."""
    configured = (getattr(config, "GEMINI_VOICE", None) or "").strip()
    normalized = _VOICE_BY_LOWERCASE.get(configured.lower())
    if normalized is not None:
        return normalized
    if configured:
        logger.warning("Ignoring unsupported GEMINI_VOICE %r; using %s", configured, DEFAULT_GEMINI_VOICE)
    return DEFAULT_GEMINI_VOICE


class GeminiRealtimeHandler(ConversationHandler):
    """Realtime stream handler for the Gemini Live API."""

    SAMPLE_RATE: int = GEMINI_OUTPUT_SAMPLE_RATE
    INPUT_SAMPLE_RATE: int = GEMINI_INPUT_SAMPLE_RATE

    _output_sample_rate: int | None = None
    _standby: bool = False
    _standby_loop: asyncio.AbstractEventLoop | None = None
    sound_watcher: Any = None

    def __init__(
        self,
        deps: ToolDependencies,
        instance_path: Optional[str] = None,
        startup_voice: Optional[str] = None,
    ):
        """Initialize the Gemini Live realtime handler."""
        super().__init__()
        self.deps = deps
        self.instance_path = instance_path
        self.model = (getattr(config, "GEMINI_LIVE_MODEL", None) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
        self._voice_override = self._normalize_startup_voice(startup_voice)
        self.ws: Any = None
        self.output_queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.tool_manager = BackgroundToolManager()
        self._connected_event = asyncio.Event()
        self._in_flight_tool_calls: set[str] = set()
        self._startup_greeting_sent = False

    def _is_connected(self) -> bool:
        """Return whether the WebSocket connection is open."""
        return self.ws is not None

    def _normalize_startup_voice(self, voice: str | None) -> str | None:
        """Return a valid persisted startup voice, or None."""
        return self._resolve_backend_voice(voice, source="persisted startup voice")

    def _resolve_backend_voice(
        self,
        voice: str | None,
        *,
        source: str,
        fallback: str | None = None,
    ) -> str | None:
        """Return a Gemini-supported voice, optionally falling back when unsupported."""
        voice_value = (voice or "").strip()
        if not voice_value:
            return fallback

        normalized = _VOICE_BY_LOWERCASE.get(voice_value.lower())
        if normalized is not None:
            return normalized

        if voice:
            logger.warning(
                "Ignoring unsupported %s %r; expected one of %s",
                source,
                voice,
                GEMINI_AVAILABLE_VOICES,
            )
        return fallback

    def get_current_voice(self) -> str:
        """Return the voice currently selected for this handler."""
        default_voice = _default_gemini_voice()
        voice = self._voice_override or get_session_voice(default=default_voice)
        return self._resolve_backend_voice(voice, source="session voice", fallback=default_voice) or default_voice

    async def get_available_voices(self) -> list[str]:
        """Return the Gemini Live voice catalog."""
        return list(GEMINI_AVAILABLE_VOICES)

    async def change_voice(self, voice: str) -> str:
        """Change the voice and reconnect the session."""
        default_voice = _default_gemini_voice()
        resolved_voice = (
            self._resolve_backend_voice(voice, source="requested voice", fallback=default_voice) or default_voice
        )
        self._voice_override = resolved_voice
        if self.ws is not None:
            try:
                await self._restart_session()
                return f"Voice changed to {resolved_voice}; reconnecting session."
            except Exception as e:
                logger.warning("Failed to restart session for voice change: %s", e)
                return "Voice change failed. Will take effect on next connection."
        return "Voice changed. Will take effect on next connection."

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality profile and reload tools and session instructions."""
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            get_session_instructions(self.instance_path)
            initialize_tools(force=True)
        except Exception as exc:
            set_custom_profile(previous_profile)
            logger.error("Failed to resolve personality %r: %s", profile, exc)
            return f"Failed to apply personality: {exc}"

        if self.ws is not None:
            try:
                await self._restart_session()
                return "Applied personality and restarted realtime session."
            except Exception as exc:
                logger.warning("Failed to restart session after apply: %s", exc)
                return "Applied personality. Will take effect on next connection."

        return "Applied personality. Will take effect on next connection."

    async def say(self, text: str) -> None:
        """Inject text as a user turn and have the model speak it."""
        text = (text or "").strip()
        if not text:
            raise ValueError("say: empty text")
        if not self.ws:
            raise RuntimeError("say: no active session")
        self._mark_activity("say")
        await self._send_user_turn(text)

    async def start_up(self) -> None:
        """Connect and run the Gemini Live realtime session with reconnection retries."""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self._run_session()
                return
            except (ConnectionClosedError, ConnectionClosedOK) as e:
                logger.warning("Gemini Live websocket closed (attempt %d/%d): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                    await asyncio.sleep(delay)
                    continue
                raise
            finally:
                self.ws = None
                self._connected_event.clear()

    async def shutdown(self) -> None:
        """Close the connection and stop background tool execution."""
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._connected_event.clear()
        await self.tool_manager.shutdown()

    async def _restart_session(self) -> None:
        """Force-close the current session and connect a fresh one in the background."""
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._connected_event.clear()
        asyncio.create_task(self.start_up(), name="gemini-session-restart")
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
            logger.info("Gemini Live session restarted and connected.")
        except asyncio.TimeoutError:
            logger.warning("Gemini Live restart timed out; continuing in background.")

    async def _run_session(self) -> None:
        """Establish WebSocket connection, send setup, and process server messages."""
        api_key = (getattr(config, "GEMINI_API_KEY", None) or "").strip().strip('"').strip("'")
        if not api_key:
            raise RuntimeError("CONVERSATION_BACKEND=gemini requires GEMINI_API_KEY to be set")

        url = f"{GEMINI_LIVE_URL}?key={api_key}"
        logger.info("Connecting to Gemini Live endpoint (model=%s)", self.model)

        async with websockets.connect(url, max_size=None) as ws:
            self.ws = ws
            self._standby_loop = asyncio.get_running_loop()
            self._connected_event.set()
            self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

            await self._send_setup(ws)
            raw_setup_resp = await ws.recv()
            if isinstance(raw_setup_resp, bytes):
                raw_setup_resp = raw_setup_resp.decode("utf-8")
            setup_resp = json.loads(raw_setup_resp)
            if "setupComplete" not in setup_resp:
                logger.warning("Unexpected response during session setup: %s", setup_resp)
            else:
                logger.info("Gemini Live session established with voice=%s", self.get_current_voice())

            await self._send_startup_greeting_prompt()

            async for raw_message in ws:
                await self._handle_server_message(raw_message)

    async def _send_setup(self, ws: Any) -> None:
        """Send the setup message declaring model, voice, instructions, and tools."""
        tool_specs = get_tool_specs()
        function_declarations = [
            {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            }
            for spec in tool_specs
        ]
        model_name = self.model
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"

        setup_payload: dict[str, Any] = {
            "model": model_name,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": self.get_current_voice(),
                        }
                    }
                },
            },
            "systemInstruction": {
                "parts": [{"text": get_session_instructions(self.instance_path)}],
            },
        }
        if function_declarations:
            setup_payload["tools"] = [{"functionDeclarations": function_declarations}]

        await ws.send(json.dumps({"setup": setup_payload}))

    async def _send_startup_greeting_prompt(self) -> None:
        """Queue the startup greeting prompt once connected."""
        if self._startup_greeting_sent or not self.ws:
            return
        greeting_prompt = get_session_greeting_prompt().strip()
        if not greeting_prompt:
            self._startup_greeting_sent = True
            return
        try:
            self._startup_greeting_sent = True
            self._mark_activity("startup_greeting_prompt")
            await self._send_user_turn(greeting_prompt)
            logger.info("Queued startup greeting prompt")
        except Exception as e:
            logger.warning("Failed to queue startup greeting prompt: %s", e)

    async def _send_user_turn(self, text: str) -> None:
        """Send user text to Gemini Live as clientContent."""
        if not self.ws:
            return
        msg = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": text}],
                    }
                ],
                "turnComplete": True,
            }
        }
        await self.ws.send(json.dumps(msg))

    async def _handle_server_message(self, raw_message: str | bytes) -> None:
        """Parse incoming JSON message and dispatch tool calls, audio, and transcripts."""
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        data = json.loads(raw_message)

        if "toolCall" in data:
            tc = data["toolCall"]
            for call in tc.get("functionCalls", []):
                call_id = str(call.get("id", ""))
                name = str(call.get("name", ""))
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
                args_json = json.dumps(args)
                logger.info("Received toolCall: %s (id=%s)", name, call_id)
                self._in_flight_tool_calls.add(call_id)
                routine = ToolCallRoutine(tool_name=name, args_json_str=args_json, deps=self.deps)
                await self.tool_manager.start_tool(call_id=call_id, tool_call_routine=routine, is_idle_tool_call=False)

        if "toolCallCancellation" in data:
            tcc = data["toolCallCancellation"]
            for call_id in tcc.get("ids", []):
                logger.info("Cancelling toolCall: %s", call_id)
                self._in_flight_tool_calls.discard(call_id)
                await self.tool_manager.cancel_tool(call_id)

        if "serverContent" in data:
            sc = data["serverContent"]

            if sc.get("interrupted"):
                logger.info("User interrupted model turn; clearing output buffer")
                self._drain_audio_output_queue()
                self._mark_activity("interrupted")
                self._emit_transcript("assistant", "[interrupted]", final=True)

            out_transcript = sc.get("outputTranscription")
            if out_transcript and "text" in out_transcript:
                text = out_transcript["text"]
                self._emit_transcript("assistant", text, final=False)
                await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": text}))
                self._mark_activity("model_speech")

            in_transcript = sc.get("inputTranscription")
            if in_transcript and "text" in in_transcript:
                user_text = in_transcript["text"]
                self._emit_transcript("user", user_text, final=True)
                await self.output_queue.put(AdditionalOutputs({"role": "user", "content": user_text}))
                self._mark_activity("user_speech")
                if self._standby and matches_wake_phrase(user_text, configured_wake_phrases()):
                    loop = self._standby_loop
                    if loop is not None and loop.is_running():
                        loop.create_task(self.wake_from_standby())

            if "modelTurn" in sc:
                for part in sc["modelTurn"].get("parts", []):
                    if "inlineData" in part:
                        inline = part["inlineData"]
                        mime = inline.get("mimeType", "")
                        raw_b64 = inline.get("data", "")
                        if "audio/pcm" in mime and raw_b64 and not self._standby:
                            pcm_bytes = base64.b64decode(raw_b64)
                            audio_chunk = np.frombuffer(pcm_bytes, dtype=np.int16)
                            if audio_chunk.size > 0:
                                await self.output_queue.put((self.SAMPLE_RATE, audio_chunk))
                                self._mark_activity("audio_out")
                    if "text" in part:
                        text_val = part["text"]
                        self._emit_transcript("assistant", text_val, final=False)
                        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": text_val}))

            if sc.get("turnComplete"):
                self._mark_activity("turn_complete")

    def _drain_audio_output_queue(self) -> None:
        """Drop pending audio chunks upon user interruption while retaining chat text."""
        retained: list[QueueItem] = []
        while not self.output_queue.empty():
            try:
                item = self.output_queue.get_nowait()
                if isinstance(item, AdditionalOutputs):
                    retained.append(item)
            except asyncio.QueueEmpty:
                break
        for item in retained:
            self.output_queue.put_nowait(item)

    async def _handle_tool_result(self, completed_tool: ToolNotification) -> None:
        """Process and submit tool results back to Gemini Live."""
        from reachy_mini_conversation_app.tools.tool_constants import ToolState

        if (
            self._standby
            and getattr(completed_tool, "tool_name", "") != "go_to_sleep"
            and getattr(completed_tool, "status", None) in (ToolState.COMPLETED, ToolState.FAILED)
        ):
            logger.info("Background tool finished during standby; waking to announce")
            await self.wake_from_standby(greet=False)

        if not self.ws:
            logger.warning("Connection closed before sending tool '%s' result", completed_tool.tool_name)
            return

        if completed_tool.error is not None:
            logger.error("Tool '%s' failed: %s", completed_tool.tool_name, completed_tool.error)
            tool_result_for_model = {"error": completed_tool.error}
        elif completed_tool.result is not None:
            result = completed_tool.result
            if isinstance(result, dict) and completed_tool.tool_name == "camera" and "b64_im" in result:
                sanitized = dict(result)
                b64_im = sanitized.pop("b64_im", None)
                sanitized["image_attached"] = True
                tool_result_for_model = sanitized
                if b64_im:
                    await self._send_image_frame(b64_im)
            else:
                tool_result_for_model = result if isinstance(result, dict) else {"result": result}
        else:
            tool_result_for_model = {"error": "No result returned from tool execution"}

        tool_response_msg = {
            "toolResponse": {
                "functionResponses": [
                    {
                        "id": str(completed_tool.id),
                        "name": completed_tool.tool_name,
                        "response": {
                            "output": tool_result_for_model,
                        },
                    }
                ]
            }
        }
        try:
            await self.ws.send(json.dumps(tool_response_msg))
            self._mark_activity("tool_result_sent")
        except Exception as e:
            logger.warning("Failed to send tool response: %s", e)

        await self.output_queue.put(
            AdditionalOutputs(
                {
                    "role": "assistant",
                    "content": json.dumps(tool_result_for_model),
                }
            )
        )
        if isinstance(completed_tool.id, str):
            self._in_flight_tool_calls.discard(completed_tool.id)

    async def _send_image_frame(self, b64_jpeg: str) -> None:
        """Send a camera JPEG frame via realtimeInput video."""
        if not self.ws:
            return
        msg = {
            "realtimeInput": {
                "video": {
                    "mimeType": "image/jpeg",
                    "data": b64_jpeg,
                }
            }
        }
        try:
            await self.ws.send(json.dumps(msg))
            logger.info("Sent camera frame to Gemini Live")
        except Exception as e:
            logger.warning("Failed to send camera frame to Gemini Live: %s", e)

    async def receive(self, frame: AudioFrame) -> None:
        """Resample mic audio to 16 kHz mono PCM16 and stream to Gemini Live."""
        if not self.ws:
            return

        source_rate, audio_frame = frame
        if audio_frame.size == 0:
            return

        if audio_frame.ndim == 2:
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]
        audio_frame = np.asarray(audio_frame).reshape(-1)
        audio_frame = audio_to_int16(audio_frame)

        if source_rate != self.INPUT_SAMPLE_RATE:
            audio_frame = resample_pcm(audio_frame, source_rate, self.INPUT_SAMPLE_RATE)

        try:
            b64_audio = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            msg = {
                "realtimeInput": {
                    "audio": {
                        "mimeType": f"audio/pcm;rate={self.INPUT_SAMPLE_RATE}",
                        "data": b64_audio,
                    }
                }
            }
            await self.ws.send(json.dumps(msg))
        except Exception as e:
            logger.debug("Dropping audio frame: %s", e)

    def _get_output_sample_rate(self) -> int:
        """Return (and cache) the playback device rate, falling back to 16 kHz."""
        cached = self._output_sample_rate
        if cached is not None:
            return cached

        rate = FALLBACK_OUTPUT_SAMPLE_RATE
        try:
            reported = self.deps.reachy_mini.media.get_output_audio_samplerate()
        except Exception as e:
            logger.warning("Could not read output sample rate (%s); assuming %d Hz", e, rate)
        else:
            if isinstance(reported, int) and not isinstance(reported, bool) and reported > 0:
                rate = reported
            else:
                logger.warning("Ignoring invalid output sample rate %r; assuming %d Hz", reported, rate)

        self._output_sample_rate = rate
        logger.info("Playback sample rate: %d Hz (model audio is %d Hz)", rate, self.SAMPLE_RATE)
        return rate

    async def emit(self) -> HandlerOutput:
        """Emit queued output, resampling 24 kHz Gemini audio to the playback device rate."""
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

    # --- Standby (sleep-wait) state machine -----------------------------------

    @property
    def in_standby(self) -> bool:
        """Return whether the handler is in sleep-wait mode."""
        return self._standby

    def request_standby(self) -> dict[str, Any]:
        """Thread-safe standby entry for synchronous tool path."""
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
        """Sleep pose; listen for wake phrases."""
        if self._standby:
            return {"status": "standby", "message": "Already in standby."}
        logger.info("Entering standby: sleep pose, listening for wake phrases %s", configured_wake_phrases())
        self._standby = True
        try:
            self.deps.movement_manager.stop(reset_to_neutral=False)
            await asyncio.to_thread(self.deps.reachy_mini.goto_sleep)
        except Exception as e:
            logger.warning("Standby sleep motion failed: %s", e)
        return {
            "status": "standby",
            "message": "Reachy is now sleeping and will wake on the wake phrase. Do not respond further.",
        }

    async def wake_from_standby(self, *, greet: bool = True) -> None:
        """Wake motion, enable responses, and optionally greet the user."""
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
        if greet:
            await self._send_wake_greeting()

    def _run_wake_up_motion(self) -> None:
        """Enable motors and run the SDK wake-up move."""
        robot = self.deps.reachy_mini
        robot.enable_motors()
        robot.wake_up()

    async def _gaze_toward_wake_caller(self) -> None:
        """Look toward where the wake phrase came from when DoA data is fresh."""
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

    async def _send_wake_greeting(self) -> None:
        """Queue a prompt so the model greets the user right after waking."""
        if not self.ws:
            return
        try:
            await self._send_user_turn(WAKE_GREETING_PROMPT)
        except Exception as e:
            logger.warning("Failed to queue wake greeting: %s", e)

    def _idle_behavior_ready(self) -> bool:
        """Suppress idle behaviors entirely while sleeping in standby."""
        if self._standby:
            return False
        return super()._idle_behavior_ready()
