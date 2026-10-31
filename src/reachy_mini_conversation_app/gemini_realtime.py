"""Gemini Live API realtime conversation backend."""

from __future__ import annotations
import json
import uuid
import random
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from google import genai
from google.genai import types, errors
from numpy.typing import NDArray

from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config, set_custom_profile
from reachy_mini_conversation_app.prompts import (
    get_session_instructions,
    get_session_greeting_prompt,
)
from reachy_mini_conversation_app.streaming import (
    AdditionalOutputs,
    audio_to_int16,
    normalize_audio_frame,
)
from reachy_mini_conversation_app.openai_realtime import resample_pcm
from reachy_mini_conversation_app.tools.core_tools import ToolSpec, ToolDependencies, get_tool_specs
from reachy_mini_conversation_app.conversation_handler import HandlerOutput, ConversationHandler
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


if TYPE_CHECKING:
    from google.genai.live import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants (adjust at module top before modifying session internals)
# ---------------------------------------------------------------------------
GEMINI_INPUT_SAMPLE_RATE: int = 16000
GEMINI_OUTPUT_SAMPLE_RATE: int = 24000
RECONNECT_MAX_ATTEMPTS: int = 5
RECONNECT_BASE_DELAY_S: float = 1.0
_FALLBACK_OUTPUT_SAMPLE_RATE: int = 16000

# Gemini Live prebuilt voice catalog (validate via real calls per Task 5)
GEMINI_AVAILABLE_VOICES: list[str] = [
    "Puck",
    "Charon",
    "Kore",
    "Fenrir",
    "Aoede",
    "Leda",
    "Orus",
    "Zephyr",
]


def _is_websocket_close(exc: BaseException) -> bool:
    """Return True if exc looks like a WebSocket close / connection-lost error."""
    type_name = type(exc).__name__
    module = getattr(type(exc), "__module__", "") or ""
    return "ConnectionClosed" in type_name or "websocket" in module.lower()


# ---------------------------------------------------------------------------
# Tool spec conversion helpers (Task 3)
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMA_KEYS: frozenset[str] = frozenset({"type", "description", "enum", "properties", "required", "items"})

_JSON_TYPE_TO_GEMINI: dict[str, "types.Type"] = {
    "string": types.Type.STRING,
    "number": types.Type.NUMBER,
    "integer": types.Type.INTEGER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
    "object": types.Type.OBJECT,
}


def _filter_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip JSON Schema keys not supported by Gemini FunctionDeclaration."""
    filtered: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            filtered[key] = {k: _filter_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            filtered[key] = _filter_schema(value)
        else:
            filtered[key] = value
    return filtered


def _build_schema(raw: dict[str, Any]) -> "types.Schema":
    """Convert a filtered JSON Schema dict to a types.Schema instance."""
    filtered = _filter_schema(raw)
    kwargs: dict[str, Any] = {}

    type_str = filtered.get("type")
    if isinstance(type_str, str):
        kwargs["type"] = _JSON_TYPE_TO_GEMINI.get(type_str.lower(), types.Type.OBJECT)

    if "description" in filtered:
        kwargs["description"] = filtered["description"]
    if "enum" in filtered:
        kwargs["enum"] = filtered["enum"]
    if "required" in filtered:
        kwargs["required"] = filtered["required"]
    if "properties" in filtered and isinstance(filtered["properties"], dict):
        kwargs["properties"] = {k: _build_schema(v) for k, v in filtered["properties"].items()}
    if "items" in filtered and isinstance(filtered["items"], dict):
        kwargs["items"] = _build_schema(filtered["items"])

    return types.Schema(**kwargs)


def tool_specs_to_gemini(tool_specs: list[ToolSpec]) -> list["types.Tool"]:
    """Convert OpenAI-format ToolSpec list to a single types.Tool with all declarations.

    JSON Schema keys not supported by Gemini are silently dropped (allowlist:
    type/description/enum/properties/required/items).
    """
    declarations: list[types.FunctionDeclaration] = []
    for spec in tool_specs:
        params_raw = spec.get("parameters", {})
        schema = _build_schema(params_raw)
        declarations.append(
            types.FunctionDeclaration(
                name=spec["name"],
                description=spec.get("description", ""),
                parameters=schema,
            )
        )
    if not declarations:
        return []
    return [types.Tool(function_declarations=declarations)]


def _sanitize_for_model(tool_name: str, tool_result: dict[str, Any]) -> dict[str, Any]:
    """Remove bulky transport-only fields before sending tool output back to the model.

    Mirrors HuggingFaceRealtimeHandler._sanitize_tool_result_for_model semantics.
    """
    if tool_name == "camera" and "b64_im" in tool_result:
        sanitized = dict(tool_result)
        sanitized.pop("b64_im", None)
        sanitized["image_attached"] = True
        return sanitized
    return tool_result


class GeminiRealtimeHandler(ConversationHandler):
    """Realtime stream handler for the Gemini Live API.

    Implements ConversationHandler directly (not via HF/OpenAI chain) because
    the Gemini Live SDK protocol differs entirely from the OpenAI Realtime API.
    """

    # AsyncStreamHandler requires SAMPLE_RATE so the framework delivers audio
    # at this rate to receive().
    SAMPLE_RATE: int = GEMINI_INPUT_SAMPLE_RATE

    _output_sample_rate: Optional[int] = None

    def __init__(
        self,
        deps: ToolDependencies,
        instance_path: Optional[str] = None,
        startup_voice: Optional[str] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            deps: Tool dependencies (reachy_mini, movement_manager, …).
            instance_path: Path used to resolve the persona profile.
            startup_voice: Optional voice name override for this session.

        """
        super().__init__()
        self.deps = deps
        self.instance_path = instance_path

        # Duck-typing contract: console.py _backend_connected() checks this.
        self.connection: "AsyncSession | None" = None

        from reachy_mini_conversation_app.conversation_handler import QueueItem

        self.output_queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.tool_manager: BackgroundToolManager = BackgroundToolManager()

        self._connected_event: asyncio.Event = asyncio.Event()
        self._startup_greeting_sent: bool = False
        self._receive_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

        # Local standby state (wake-word path): True while the Gemini session is
        # intentionally closed awaiting a wake word.  _run_handler_startup_loop in
        # console.py checks this flag to suppress auto-reconnect during standby.
        self._local_standby: bool = False

        # Event loop captured at start_up() time; used by request_local_standby()
        # to schedule the coroutine from the synchronous go_to_sleep_action path.
        self._asyncio_loop: "asyncio.AbstractEventLoop | None" = None

        # Persist the voice so it survives reconnects.
        self._voice_override: Optional[str] = self._resolve_gemini_voice(startup_voice)

        # Stored after start_up() so _restart_session() can reuse it.
        self._client: Optional[genai.Client] = None

        # Task 2b: turn-level state for transcription and speaking signals.
        self._is_speaking_current_turn: bool = False
        self._input_transcript_buffer: str = ""
        self._output_transcript_buffer: str = ""

        # Task 3: parallel tool-call batching state.
        # call_ids of all function_calls in the current batch that have not yet returned.
        self._in_flight_tool_calls: set[str] = set()
        # Accumulated FunctionResponse objects waiting for the batch to complete.
        self._pending_tool_responses: list[types.FunctionResponse] = []
        # Maps call_id → tool name (FunctionResponse requires name in addition to id).
        self._call_id_to_name: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_gemini_voice(self, voice: Optional[str]) -> Optional[str]:
        """Normalize a voice name against the Gemini catalog.

        Returns the canonical (title-cased) name, or None if the input is
        absent or unsupported (logs a warning in the latter case).
        """
        if not voice:
            return None
        by_lower = {v.lower(): v for v in GEMINI_AVAILABLE_VOICES}
        normalized = by_lower.get(voice.strip().lower())
        if normalized is None:
            logger.warning(
                "Ignoring unsupported Gemini voice %r; expected one of %s",
                voice,
                GEMINI_AVAILABLE_VOICES,
            )
        return normalized

    def _active_voice(self) -> str:
        """Return the voice that will be used for the next session."""
        if self._voice_override:
            return self._voice_override
        return (getattr(config, "GEMINI_VOICE", None) or "Kore").strip() or "Kore"

    def _get_output_sample_rate(self) -> int:
        """Return (and cache) the playback device sample rate."""
        cached = self._output_sample_rate
        if cached is not None:
            return cached

        rate = _FALLBACK_OUTPUT_SAMPLE_RATE
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
        logger.info(
            "Playback sample rate: %d Hz (model audio is %d Hz)",
            rate,
            GEMINI_OUTPUT_SAMPLE_RATE,
        )
        return rate

    # ------------------------------------------------------------------
    # ConversationHandler ABC — lifecycle
    # ------------------------------------------------------------------

    def _is_connected(self) -> bool:
        """Return whether the Gemini Live session is currently open."""
        return self.connection is not None

    async def start_up(self) -> None:
        """Connect to Gemini Live and run until shutdown, with exponential-backoff reconnects.

        Catches google.genai.errors.APIError and WebSocket close errors, which
        are expected during normal session-timeout reconnects. All other
        exceptions propagate immediately.
        """
        self._asyncio_loop = asyncio.get_running_loop()
        api_key = (getattr(config, "GEMINI_API_KEY", None) or "").strip()
        if not api_key:
            raise RuntimeError("CONVERSATION_BACKEND=gemini requires GEMINI_API_KEY to be set")

        client = genai.Client(api_key=api_key)
        self._client = client

        for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
            try:
                await self._run_live_session(client)
                return  # clean exit — don't retry
            except (errors.APIError, Exception) as exc:
                is_api_err = isinstance(exc, errors.APIError)
                is_ws_close = _is_websocket_close(exc)
                if not (is_api_err or is_ws_close):
                    raise

                # Local standby: session was intentionally closed by enter_local_standby().
                # exit_local_standby() will relaunch the session; do not reconnect here.
                if self._local_standby:
                    return

                logger.warning(
                    "Gemini Live session ended (attempt %d/%d): %s: %s",
                    attempt,
                    RECONNECT_MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt >= RECONNECT_MAX_ATTEMPTS:
                    raise

                delay = RECONNECT_BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
                logger.info("Reconnecting in %.1f s...", delay)
                await asyncio.sleep(delay)
            finally:
                self.connection = None
                self._connected_event.clear()

    async def _run_live_session(self, client: genai.Client) -> None:
        """Open a single Gemini Live session and run the receive loop until it ends."""
        model = (getattr(config, "GEMINI_LIVE_MODEL", None) or "gemini-3.8-live").strip()
        voice = self._active_voice()

        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=get_session_instructions(self.instance_path),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))
            ),
            # sliding_window is the validated minimum — empty config causes 1007
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            tools=tool_specs_to_gemini(get_tool_specs()),  # type: ignore[arg-type]
        )

        # Clear stale batch state from a previous session before starting.
        self._in_flight_tool_calls.clear()
        self._pending_tool_responses.clear()
        self._call_id_to_name.clear()

        self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])
        try:
            async with client.aio.live.connect(model=model, config=live_config) as session:
                self.connection = session
                self._connected_event.set()
                logger.info("Gemini Live session established (voice=%s, model=%s)", voice, model)

                await self._send_startup_greeting_prompt()

                self._receive_task = asyncio.create_task(self._receive_loop(session), name="gemini-receive-loop")
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass  # shutdown() cancelled the loop
        finally:
            self.connection = None
            self._connected_event.clear()
            await self.tool_manager.shutdown()

    async def _receive_loop(self, session: "AsyncSession") -> None:
        """Consume messages from the Gemini Live session until it closes.

        The SDK's receive() generator ends at every turn boundary (observed:
        greeting turn ends → generator exhausted), not when the session
        closes, so a single async-for would tear the whole session down after
        one turn — the field symptom was "session ended" after every reply or
        tool call. Keep re-entering receive() until the connection itself
        fails (raises ConnectionClosed/APIError) or shutdown() cancels us.
        """
        while True:
            yielded = False
            async for message in session.receive():
                yielded = True
                await self._process_message(message)
            if not yielded:
                # A turn boundary always carries at least one message; an empty
                # iteration means the stream is drained (session closing).
                break

    async def _process_message(self, message: types.LiveServerMessage) -> None:
        """Dispatch a single server message to the appropriate handler."""
        # Raw audio output — put on queue for emit() to consume
        if message.data:
            audio_bytes: bytes = message.data
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).reshape(1, -1)
            await self.output_queue.put((GEMINI_OUTPUT_SAMPLE_RATE, audio_array))
            # First audio chunk of a turn: signal speaking start
            if not self._is_speaking_current_turn:
                self._is_speaking_current_turn = True
                self.deps.movement_manager.set_speaking(True)
                self._mark_activity("assistant_audio_delta")

        sc = message.server_content
        if sc is not None:
            # User speech transcription: accumulate and emit partials; flush on turn_complete
            if sc.input_transcription is not None and sc.input_transcription.text:
                self._input_transcript_buffer += sc.input_transcription.text
                self._emit_transcript("user", self._input_transcript_buffer, final=False)
                await self.output_queue.put(
                    AdditionalOutputs({"role": "user_partial", "content": self._input_transcript_buffer})
                )
                self.deps.movement_manager.set_listening(True)

            # Assistant speech transcription: accumulate until turn_complete
            if sc.output_transcription is not None and sc.output_transcription.text:
                self._output_transcript_buffer += sc.output_transcription.text

            # Interruption: drain output queue + flush hardware player + stop speaking
            if sc.interrupted:
                while not self.output_queue.empty():
                    try:
                        self.output_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if self._clear_queue is not None:
                    self._clear_queue()
                self.deps.movement_manager.set_speaking(False)
                self._is_speaking_current_turn = False

            # Turn complete: finalize transcripts + stop speaking
            if sc.turn_complete:
                self.deps.movement_manager.set_speaking(False)
                self._is_speaking_current_turn = False

                # Flush accumulated user transcript as final
                if self._input_transcript_buffer:
                    user_text = self._input_transcript_buffer
                    self._input_transcript_buffer = ""
                    self._emit_transcript("user", user_text, final=True)
                    await self.output_queue.put(AdditionalOutputs({"role": "user", "content": user_text}))
                    self._mark_activity("user_transcription_completed")
                    self.deps.movement_manager.set_listening(False)

                # Flush accumulated assistant transcript as final
                if self._output_transcript_buffer:
                    assistant_text = self._output_transcript_buffer
                    self._output_transcript_buffer = ""
                    self._emit_transcript("assistant", assistant_text, final=True)
                    await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": assistant_text}))
                    self._mark_activity("assistant_transcript_done")

        if message.tool_call:
            for fc in message.tool_call.function_calls or []:
                # Use the SDK-provided id (filled as "call_<number>"); synthesize if absent.
                call_id: str = (fc.id or "").strip() or f"synthetic_{uuid.uuid4().hex[:8]}"
                self._in_flight_tool_calls.add(call_id)
                self._call_id_to_name[call_id] = fc.name or ""
                await self.tool_manager.start_tool(
                    call_id=call_id,
                    tool_call_routine=ToolCallRoutine(
                        tool_name=fc.name or "",
                        args_json_str=json.dumps(dict(fc.args) if fc.args else {}),
                        deps=self.deps,
                    ),
                    is_idle_tool_call=False,
                )
                await self.output_queue.put(AdditionalOutputs({"role": "tool_use", "content": f"Used tool {fc.name}"}))
            self._mark_activity("tool_call_received")

    async def _send_startup_greeting_prompt(self) -> None:
        """Inject the session greeting prompt once, immediately after the session opens.

        Uses the same send_client_content path as say() (spike-validated).
        The _startup_greeting_sent flag is per-handler (not per-session) so
        reconnects do not repeat the greeting — matching HF handler semantics.
        """
        if self._startup_greeting_sent or not self.connection:
            return
        greeting_prompt = get_session_greeting_prompt().strip()
        if not greeting_prompt:
            self._startup_greeting_sent = True
            return
        try:
            await self.connection.send_client_content(
                turns={"role": "user", "parts": [{"text": greeting_prompt}]},
                turn_complete=True,
            )
            self._startup_greeting_sent = True
            self._mark_activity("startup_greeting_prompt")
            logger.info("Sent startup greeting prompt")
        except Exception as e:
            logger.warning("Failed to send startup greeting prompt: %s", e)

    async def _handle_tool_result(self, completed_tool: ToolNotification) -> None:
        """Handle a completed tool, batching results until the full parallel group is ready.

        Parallel tool calls arrive in one LiveServerToolCall message and Gemini requires
        all FunctionResponses to be sent together — partial sends leave the model silent.
        This method accumulates results and flushes the batch only when every call_id in
        the current in-flight set has been resolved.
        """
        call_id = completed_tool.id
        tool_name = self._call_id_to_name.get(call_id, completed_tool.tool_name)

        # Build the result dict (errors become {"error": …}, not exceptions).
        if completed_tool.error:
            result_dict: dict[str, Any] = {"error": completed_tool.error}
        elif completed_tool.result is None:
            result_dict = {"error": f"No result returned from tool '{tool_name}'"}
        else:
            result_dict = _sanitize_for_model(completed_tool.tool_name, completed_tool.result)

        logger.debug(
            "Tool '%s' (id=%s) completed: error=%s",
            tool_name,
            call_id,
            completed_tool.error,
        )

        # Idle tool calls do not get sent to the model; UI output only.
        if completed_tool.is_idle_tool_call:
            self._in_flight_tool_calls.discard(call_id)
            return

        # Accumulate response; mark this call_id as resolved.
        self._pending_tool_responses.append(types.FunctionResponse(id=call_id, name=tool_name, response=result_dict))
        self._in_flight_tool_calls.discard(call_id)

        # Flush the batch only once every call in this group has completed.
        if self._in_flight_tool_calls:
            return

        responses = list(self._pending_tool_responses)
        self._pending_tool_responses.clear()

        session = self.connection
        if session is None:
            logger.warning(
                "_handle_tool_result: session disconnected — dropping %d tool response(s)",
                len(responses),
            )
            return

        try:
            await session.send_tool_response(function_responses=responses)
            self._mark_activity("tool_result_ready")
        except Exception as exc:
            logger.warning("send_tool_response failed — dropping %d response(s): %s", len(responses), exc)

    async def shutdown(self) -> None:
        """Shut down: cancel receive loop → close session → manager shutdown → drain queue."""
        # 1. Cancel the receive-loop task
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        self._receive_task = None

        # 2. Close the session
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception as e:
                logger.debug("session.close() during shutdown: %s", e)
            finally:
                self.connection = None

        # 3. Stop background tool manager
        await self.tool_manager.shutdown()

        # 4. Drain output queue so the audio path doesn't stall
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def enter_local_standby(self) -> dict[str, Any]:
        """Close the Gemini session for local wake-word standby without stopping the app.

        Sets _local_standby=True so that _run_handler_startup_loop (console.py)
        suppresses auto-reconnect while the robot is sleeping.  The caller is
        responsible for starting the WakeWordListener *after* this returns.
        """
        self._local_standby = True
        # Cancel the receive task WITHOUT awaiting it.  Awaiting here deadlocks:
        # the receive task's cleanup (_run_live_session finally) calls
        # tool_manager.shutdown(), which waits for the go_to_sleep tool to finish
        # — but go_to_sleep is what triggered this standby and is waiting on us.
        # Cancel-and-move-on breaks that circular wait; the task tears down on
        # its own once this coroutine yields.
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            self._receive_task = None
        # Close the session (triggers clean exit of _run_live_session).
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception as exc:
                logger.debug("enter_local_standby: session.close() raised: %s", exc)
            finally:
                self.connection = None
        self._connected_event.clear()
        logger.info("Gemini handler entered local standby; session closing, wake listener pending.")
        return {"status": "local_standby"}

    def request_local_standby(self) -> dict[str, Any]:
        """Schedule local standby from the synchronous go_to_sleep tool path.

        Fire-and-forget: the go_to_sleep tool must NOT block on standby completion,
        because standby's teardown (tool_manager.shutdown via _run_live_session's
        finally) waits for this very tool to finish — blocking here deadlocks the
        whole event loop.  We schedule enter_local_standby and return immediately;
        it runs on the loop once the tool returns and the tool manager releases it.
        """
        loop = self._asyncio_loop
        if loop is None or not loop.is_running():
            return {"error": "local_standby unavailable: no active event loop"}
        asyncio.run_coroutine_threadsafe(self.enter_local_standby(), loop)
        return {"status": "local_standby"}

    async def exit_local_standby(self) -> dict[str, Any]:
        """Lift local wake-word standby so the console startup loop reconnects.

        Only clears state — the session relaunch is owned by console.py's
        _run_handler_startup_loop, which is polling on this flag.  Resets the
        greeting flag so the reconnected session greets the user once on wakeup.
        """
        if not self._local_standby:
            return {"status": "not_in_standby"}
        self._startup_greeting_sent = False
        self._local_standby = False
        logger.info("Gemini handler exited local standby; console loop will reconnect.")
        return {"status": "awake"}

    async def _restart_session(self) -> None:
        """Force-close the current session and relaunch it in the background.

        Follows the same semantics as HuggingFaceRealtimeHandler._restart_session:
        close → None → clear event → background task → wait ≤5 s for reconnect.
        Requires start_up() to have run (self._client must be set).
        """
        if self._client is None:
            logger.warning("_restart_session: client not initialised yet; skipping restart.")
            return

        # 1. Close existing session
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception:
                pass
            finally:
                self.connection = None

        # 2. Clear connected event so waiter below can detect re-establishment
        self._connected_event.clear()

        # 3. Relaunch session as a fire-and-forget background task
        asyncio.create_task(self._run_live_session(self._client), name="gemini-session-restart")

        # 4. Wait briefly for the session to come back up
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
            logger.info("Gemini Live session restarted and connected.")
        except asyncio.TimeoutError:
            logger.warning("Gemini Live session restart timed out; continuing in background.")

    # ------------------------------------------------------------------
    # ConversationHandler ABC — audio I/O
    # ------------------------------------------------------------------

    async def receive(self, frame: tuple[int, NDArray[np.int16]]) -> None:
        """Receive a microphone audio frame and forward it to the Gemini Live session.

        Mono-izes, normalizes, and resamples to 16 kHz before sending.
        Drops the frame silently when no session is active.
        """
        if not self.connection:
            logger.debug("receive: no active session — dropping frame")
            return

        source_rate, audio_frame = frame
        if audio_frame.size == 0:
            return

        # Mono-ize (channels-last convention, same as HF handler)
        if audio_frame.ndim == 2:
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        audio_frame = audio_to_int16(normalize_audio_frame(audio_frame))

        # Resample to Gemini's expected 16 kHz input rate
        if source_rate != GEMINI_INPUT_SAMPLE_RATE and audio_frame.size > 0:
            audio_frame = resample_pcm(audio_frame.reshape(-1), source_rate, GEMINI_INPUT_SAMPLE_RATE)

        try:
            await self.connection.send_realtime_input(
                audio=types.Blob(
                    data=audio_frame.tobytes(),
                    mime_type=f"audio/pcm;rate={GEMINI_INPUT_SAMPLE_RATE}",
                )
            )
        except Exception as e:
            logger.debug("Dropping audio frame: session not ready (%s)", e)

    async def emit(self) -> HandlerOutput:
        """Emit queued output, resampling 24 kHz model audio to the device rate.

        Resampling happens only here — never in the receive loop — to avoid
        double-resampling.
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

    # ------------------------------------------------------------------
    # ConversationHandler ABC — voice / persona / say
    # (Task 4 stubs — minimal behaviour so ABC is satisfied)
    # ------------------------------------------------------------------

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality profile, restarting the session to pick up the new system prompt.

        Gemini Live sessions have no live-update path, so any profile change
        always requires a full session restart.
        """
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            core_tools.initialize_tools(force=True)
        except Exception as exc:
            set_custom_profile(previous_profile)
            logger.error("Failed to resolve personality %r: %s", profile, exc)
            return f"Failed to apply personality: {exc}"

        if self.connection is not None:
            try:
                await self._restart_session()
                return "Applied personality and restarted realtime session."
            except Exception as exc:
                logger.warning("Failed to restart session after apply_personality: %s", exc)
                return "Applied personality. Will take effect on next connection."

        logger.info(
            "Applied personality recorded: %s (no live connection; will apply on next session)",
            profile or "default",
        )
        return "Applied personality. Will take effect on next connection."

    async def get_available_voices(self) -> list[str]:
        """Return the Gemini Live prebuilt voice catalog."""
        return list(GEMINI_AVAILABLE_VOICES)

    def get_current_voice(self) -> str:
        """Return the voice name for the current (or next) session."""
        return self._active_voice()

    async def change_voice(self, voice: str) -> str:
        """Change the voice for the active or next session.

        Normalizes the name against the catalog, stores it as the override, and
        restarts the session when one is active (Gemini Live locks the voice at
        connection time).  Returns a human-readable status string.
        """
        resolved = self._resolve_gemini_voice(voice)
        if resolved is None:
            # Unsupported voice — fall back to current default and inform caller
            fallback = self._active_voice()
            logger.warning(
                "Unsupported Gemini voice %r; keeping current voice %r",
                voice,
                fallback,
            )
            resolved = fallback

        self._voice_override = resolved

        if self.connection is not None:
            try:
                await self._restart_session()
                return f"Voice changed to {resolved}; reconnecting session."
            except Exception as exc:
                logger.warning("Failed to restart session for voice change: %s", exc)
                return "Voice change failed. Will take effect on next connection."

        return "Voice changed. Will take effect on next connection."

    async def say(self, text: str) -> None:
        """Inject text as a user turn and have the model voice it.

        Uses send_client_content (spike-validated path). Raises if no session
        is active or if text is empty.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("say: empty text")
        if not self.connection:
            raise RuntimeError("say: no active session")
        await self.connection.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]},
            turn_complete=True,
        )
        self._mark_activity("say")
