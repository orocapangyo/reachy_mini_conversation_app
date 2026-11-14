"""Wake word detection — engine-neutral listener and STT phrase-matching utilities.

Architecture (핵심 결정 1 & 2):
  - WakeWordEngine: Protocol that any acoustic engine must satisfy.
  - WakeWordListener: Owns a single worker thread; the caller feeds raw PCM frames
    via feed() (non-blocking enqueue).  Inference and the on_wake callback stay off
    the asyncio event loop.
  - matches_wake_phrase / configured_wake_phrases / DEFAULT_WAKE_PHRASES: STT
    transcript-based matching utilities originally in openai_realtime.py.  They are
    *moved here* because they are a wake-word concern shared by every handler, not an
    OpenAI-specific one.  openai_realtime re-exports them for backward compatibility.
"""

from __future__ import annotations
import re
import time
import queue
import logging
import threading
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from collections.abc import Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app import config as _config_module


if TYPE_CHECKING:
    import asyncio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STT transcript matching utilities (moved from openai_realtime.py)
# ---------------------------------------------------------------------------

# Default wake phrases used when REACHY_MINI_WAKE_PHRASES is unset or empty.
# These are matched against STT transcripts — *not* used by the acoustic engine.
# The acoustic engine's detection vocabulary is fixed at training time.
DEFAULT_WAKE_PHRASES: tuple[str, ...] = ("깨어나", "리치미니", "일어나")


def _normalize_for_wake(text: str) -> str:
    """Strip whitespace/punctuation and lowercase so STT spelling variants match."""
    return re.sub(r"[\W_]+", "", text.lower())


def matches_wake_phrase(transcript: str, phrases: Sequence[str]) -> bool:
    """Return whether the transcript loosely contains any wake phrase.

    Used for STT-transcript-based wake detection (e.g. OpenAI standby path).
    This is distinct from acoustic engine detection — the acoustic engine does not
    use this function.
    """
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
    raw = (getattr(_config_module.config, "REACHY_MINI_WAKE_PHRASES", None) or "").strip()
    phrases = [phrase.strip() for phrase in raw.split(",") if phrase.strip()]
    return phrases or list(DEFAULT_WAKE_PHRASES)


# ---------------------------------------------------------------------------
# WakeWordEngine Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WakeWordEngine(Protocol):
    """Protocol for a local acoustic wake-word engine.

    Implementations hold their own internal streaming state between process()
    calls; the listener supplies frames in order.  The listener owns threading.
    """

    def process(self, pcm_16k_mono: NDArray[np.int16]) -> str | None:
        """Process one chunk of 16 kHz mono int16 PCM.

        Returns the detected wake phrase/label if triggered, else None.
        The engine may buffer multiple frames internally.
        """
        ...

    def reset(self) -> None:
        """Reset internal streaming state (e.g., after a detection or stop)."""
        ...

    def close(self) -> None:
        """Release resources (TFLite interpreter, file handles, etc.)."""
        ...


# ---------------------------------------------------------------------------
# WakeWordListener
# ---------------------------------------------------------------------------

# Fixed-size frame queue: oldest frame is dropped when the queue is full so that
# feed() is always non-blocking.
_FEED_QUEUE_MAXSIZE: int = 64

# After a wake detection, suppress re-detection for this many seconds.
WAKE_COOLDOWN_S: float = 3.0

# A detection only counts after this many *consecutive* detecting chunks.
# Default 1 = off: at the bundled v4 model's high threshold (0.9997) even a real
# "리치미니" only crosses for a single 80 ms chunk, so requiring 2 killed
# detection entirely in live testing (2026-10-27).  The mechanism is retained
# and tunable per-listener for future models trained at a lower threshold where
# a full phrase sustains over several chunks but partials ("리치", "미니") don't.
WAKE_CONSECUTIVE_CHUNKS: int = 1

# Target sample rate expected by the engine.
_ENGINE_SAMPLE_RATE: int = 16_000

# openWakeWord expects fixed 80 ms (1280-sample @ 16 kHz) chunks.  The app's mic
# pipeline delivers variable-size frames, so the worker buffers to this size
# before calling the engine — undersized frames otherwise flood onnxruntime with
# "Invalid input shape" errors and make scores erratic.
_ENGINE_CHUNK_SAMPLES: int = 1280


def _to_mono_1d(pcm: NDArray[np.int16]) -> NDArray[np.int16]:
    """Collapse any (N, C) / (C, N) stereo frame to a flat mono 1-D array.

    The mic pipeline may deliver 2-D stereo frames; feeding those into the
    buffer's 1-D concatenate throws every time, silently dropping all audio
    (observed as "0 chunks processed, no wake detection" during standby).
    """
    arr = np.asarray(pcm)
    if arr.ndim == 1:
        return arr
    # Channels-last (N, C) or channels-first (C, N) → average the short axis.
    if arr.shape[0] <= arr.shape[-1]:
        arr = arr.T  # make it (N, C)
    mono: NDArray[Any] = arr.mean(axis=1) if (arr.ndim == 2 and arr.shape[1] > 1) else arr.reshape(-1)
    return np.round(mono).reshape(-1).astype(np.int16)


def _resample_to_16k(pcm: NDArray[np.int16], source_rate: int) -> NDArray[np.int16]:
    """Resample arbitrary-rate mono int16 PCM to 16 kHz using openai_realtime.resample_pcm."""
    if source_rate == _ENGINE_SAMPLE_RATE:
        return pcm
    # Lazy import to avoid circular dependency; openai_realtime imports from here.
    from reachy_mini_conversation_app.openai_realtime import resample_pcm  # noqa: PLC0415

    return resample_pcm(pcm, source_rate, _ENGINE_SAMPLE_RATE)


class WakeWordListener:
    """Listens for wake words by accepting raw PCM frames from the audio pipeline.

    Usage::

        listener = WakeWordListener(engine=engine, on_wake=callback, loop=loop)
        listener.start()
        # In the audio record loop:
        listener.feed(sample_rate, pcm_int16)
        # On shutdown:
        listener.stop()

    Threading model:
      - feed() enqueues frames without blocking (oldest frame dropped when full).
      - A single worker thread dequeues, resamples, and calls engine.process().
      - on_wake is delivered via loop.call_soon_threadsafe so it runs in the
        asyncio event loop, never from the worker thread.
    """

    def __init__(
        self,
        on_wake: Callable[[str], None],
        engine: "WakeWordEngine | None" = None,
        engine_factory: "Callable[[], WakeWordEngine | None] | None" = None,
        loop: "asyncio.AbstractEventLoop | None" = None,
        consecutive_chunks: int = WAKE_CONSECUTIVE_CHUNKS,
    ) -> None:
        """Attach callback and either an engine or a lazy engine factory.

        Pass ``engine_factory`` to defer engine construction (e.g. loading the
        onnxruntime model) until the first ``start()`` — this keeps a heavy native
        library out of app startup, where concurrent GStreamer/MuJoCo init has
        caused intermittent segfaults.  The model loads on the first sleep instead.
        """
        self._engine = engine
        self._engine_factory = engine_factory
        self._on_wake = on_wake
        self._loop = loop
        self._consecutive_chunks = max(1, consecutive_chunks)

        self._queue: queue.Queue[tuple[int, NDArray[np.int16]]] = queue.Queue(maxsize=_FEED_QUEUE_MAXSIZE)
        self._running = False
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_wake_time: float = 0.0
        self._consecutive_hits: int = 0
        self._diag_chunks: int = 0  # diagnostic: total chunks fed to the engine
        self._diag_peak: float = 0.0  # diagnostic: peak score since last report

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the listener worker thread.  Idempotent — safe to call twice.

        Lazily builds the engine from the factory on first start if one was not
        supplied up front.  If the engine cannot be created, logs and stays
        stopped rather than raising.
        """
        with self._lock:
            if self._running:
                return
            if self._engine is None:
                if self._engine_factory is None:
                    logger.warning("WakeWordListener.start: no engine and no factory; cannot start.")
                    return
                try:
                    self._engine = self._engine_factory()
                except Exception as exc:
                    logger.warning("WakeWordListener.start: engine factory raised: %s", exc)
                    self._engine = None
                if self._engine is None:
                    logger.warning("WakeWordListener.start: engine factory returned None; wake detection off.")
                    return
            self._running = True
            self._last_wake_time = 0.0
            self._consecutive_hits = 0
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="wake-word-worker",
                daemon=True,
            )
            self._worker.start()
        logger.debug("WakeWordListener started")

    def stop(self) -> None:
        """Stop the listener and reset cooldown.  Idempotent — safe to call twice."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._last_wake_time = 0.0
            self._consecutive_hits = 0

        # Unblock the worker if it is waiting on an empty queue.
        try:
            self._queue.put_nowait((-1, np.array([], dtype=np.int16)))
        except queue.Full:
            pass

        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None

        # Drain residual frames.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        if self._engine is not None:
            self._engine.reset()
        logger.debug("WakeWordListener stopped")

    # ------------------------------------------------------------------
    # Feed (called from audio pipeline — must be non-blocking)
    # ------------------------------------------------------------------

    def feed(self, sample_rate: int, pcm: NDArray[np.int16]) -> None:
        """Enqueue a PCM frame for wake-word processing.

        Non-blocking: if the queue is full the oldest frame is dropped to make room.
        Frames are silently dropped when the listener is not running.
        """
        if not self._running:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._queue.put_nowait((sample_rate, pcm))
        except queue.Full:
            pass

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """Dequeue, resample, buffer to fixed chunks, and run engine inference."""
        buffer = np.empty(0, dtype=np.int16)
        engine = self._engine  # set by start() before the worker spawns; never None here
        if engine is None:  # defensive; start() guarantees this
            return
        while self._running:
            try:
                sample_rate, pcm = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Sentinel for stop()
            if sample_rate == -1:
                break

            if not self._running:
                break

            try:
                pcm_16k = _resample_to_16k(_to_mono_1d(pcm), sample_rate)
                buffer = np.concatenate([buffer, pcm_16k])
            except Exception as exc:
                logger.debug("WakeWordListener: resample error (dropped): %s", exc)
                continue

            # Feed the engine in fixed 80 ms chunks; keep the remainder buffered.
            result = None
            while buffer.shape[0] >= _ENGINE_CHUNK_SAMPLES:
                chunk = buffer[:_ENGINE_CHUNK_SAMPLES]
                buffer = buffer[_ENGINE_CHUNK_SAMPLES:]
                try:
                    hit = engine.process(chunk)
                except Exception as exc:
                    logger.debug("WakeWordListener: engine error (dropped chunk): %s", exc)
                    continue
                # Diagnostic: periodically report throughput + peak score so we can
                # tell "no mic audio" (0 chunks) from "audio but score too low".
                self._diag_chunks += 1
                self._diag_peak = max(self._diag_peak, getattr(engine, "last_score", 0.0))
                if self._diag_chunks % 25 == 0:
                    logger.info(
                        "WakeWordListener diag: %d chunks processed, peak score %.4f (threshold path)",
                        self._diag_chunks,
                        self._diag_peak,
                    )
                    self._diag_peak = 0.0
                if hit is not None:
                    result = hit

            if result is None:
                self._consecutive_hits = 0
                continue

            self._consecutive_hits += 1
            if self._consecutive_hits < self._consecutive_chunks:
                logger.debug(
                    "WakeWordListener: %r hit %d/%d (awaiting consecutive confirmation)",
                    result,
                    self._consecutive_hits,
                    self._consecutive_chunks,
                )
                continue

            if result is not None:
                now = time.monotonic()
                if now - self._last_wake_time < WAKE_COOLDOWN_S:
                    logger.debug(
                        "WakeWordListener: wake phrase %r suppressed (cooldown %.1fs remaining)",
                        result,
                        WAKE_COOLDOWN_S - (now - self._last_wake_time),
                    )
                    continue
                self._last_wake_time = now
                self._consecutive_hits = 0
                logger.info(
                    "WakeWordListener: wake phrase detected %r at %.3f",
                    result,
                    now,
                )
                self._dispatch_wake(result)

    def _dispatch_wake(self, phrase: str) -> None:
        """Deliver on_wake callback to the asyncio event loop (thread-safe)."""
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._on_wake, phrase)
                return
            except RuntimeError:
                pass  # loop closed — fall through to direct call

        # No loop or loop closed: call directly (e.g. in tests without a loop).
        try:
            self._on_wake(phrase)
        except Exception as exc:
            logger.error("WakeWordListener: on_wake callback raised: %s", exc)
