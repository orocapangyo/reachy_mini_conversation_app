"""Audible / visible "훑어보는 중" signal while a long Bookmark Insight batch runs.

``build_insight`` can take tens of seconds (page fetches, batch-model calls).
From the user's side the robot looked frozen (2026-11-07 live test), so this
module plays a short chime when the work starts, a quieter tick every
``PROGRESS_TICK_S`` seconds with a small antenna wiggle, and a rising "done"
chime at the end. Audio goes through the same path as assistant speech
(``reachy_mini.media.push_audio_sample``); motion through the movement
manager. Everything is best effort: no media → no-op, any failure is logged
at debug level and ignored. Tones are synthesized with numpy (no asset files).
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

import numpy as np

from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# --- tuning constants -----------------------------------------------------------
PROGRESS_TICK_S = 1.0  # seconds between ticks while working
PROGRESS_TONE_HZ: tuple[float, float] = (660.0, 880.0)  # start chime (low → high); done chime reuses them rising
PROGRESS_TONE_S = 0.12  # length of one tone
PROGRESS_TICK_TONE_S = 0.06  # shorter single tick
PROGRESS_GAIN = 0.2  # peak amplitude (float32 full scale = 1.0)
PROGRESS_FADE_S = 0.01  # fade in/out to avoid clicks
PROGRESS_FALLBACK_RATE = 16_000  # when the media cannot report its output sample rate
PROGRESS_MIN_RATE = 8_000  # anything below is not a real audio rate (e.g. -1 when audio is not initialized)
PROGRESS_ANTENNA_RAD = 0.35  # antenna wiggle amplitude
PROGRESS_WIGGLE_S = 0.5  # duration of one wiggle move


def tone(frequency_hz: float, duration_s: float, sample_rate: int, gain: float = PROGRESS_GAIN) -> Any:
    """Return a mono float32 sine with a short fade in/out."""
    count = max(1, int(sample_rate * duration_s))
    t = np.arange(count, dtype=np.float32) / float(sample_rate)
    wave = np.sin(2.0 * np.pi * frequency_hz * t).astype(np.float32) * np.float32(gain)
    fade = min(count // 2, int(sample_rate * PROGRESS_FADE_S))
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave


def start_chime(sample_rate: int) -> Any:
    """Two tones, low then high: "I'm on it"."""
    low, high = PROGRESS_TONE_HZ
    return np.concatenate([tone(low, PROGRESS_TONE_S, sample_rate), tone(high, PROGRESS_TONE_S, sample_rate)])


def tick_tone(sample_rate: int) -> Any:
    """One short, quieter tone: "still working"."""
    return tone(PROGRESS_TONE_HZ[0], PROGRESS_TICK_TONE_S, sample_rate, gain=PROGRESS_GAIN * 0.6)


def done_chime(sample_rate: int) -> Any:
    """Two rising tones: "done"."""
    low, high = PROGRESS_TONE_HZ
    return np.concatenate(
        [tone(high, PROGRESS_TONE_S, sample_rate), tone(high * 1.25, PROGRESS_TONE_S * 1.5, sample_rate)]
    )


class _Signal:
    """Best-effort audio + motion emitter bound to one ``ToolDependencies``."""

    def __init__(self, deps: ToolDependencies) -> None:
        self._media = getattr(getattr(deps, "reachy_mini", None), "media", None)
        self._robot = getattr(deps, "reachy_mini", None)
        self._movement = getattr(deps, "movement_manager", None)
        self._rate = self._output_rate()
        self._wiggle_sign = 1.0

    @property
    def enabled(self) -> bool:
        """Whether the robot media can play audio (the signal is a no-op otherwise)."""
        return self._media is not None and callable(getattr(self._media, "push_audio_sample", None))

    def _output_rate(self) -> int:
        getter = getattr(self._media, "get_output_audio_samplerate", None)
        if not callable(getter):
            return PROGRESS_FALLBACK_RATE
        try:
            rate = int(getter())
        except Exception:
            return PROGRESS_FALLBACK_RATE
        return rate if rate >= PROGRESS_MIN_RATE else PROGRESS_FALLBACK_RATE

    async def play(self, samples: Any) -> None:
        """Push samples to the robot speaker (never raises)."""
        media = self._media
        if media is None or not self.enabled:
            return
        try:
            await asyncio.to_thread(media.push_audio_sample, samples)
        except Exception as exc:
            logger.debug("progress signal: audio push failed (%s)", exc)

    def wiggle(self) -> None:
        """Queue a small antenna wiggle (alternating sides); never raises."""
        if self._movement is None or self._robot is None:
            return
        try:
            from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove

            head_pose = self._robot.get_current_head_pose()
            _, antennas = self._robot.get_current_joint_positions()
            amplitude = PROGRESS_ANTENNA_RAD * self._wiggle_sign
            self._wiggle_sign = -self._wiggle_sign
            move = GotoQueueMove(
                target_head_pose=head_pose,
                start_head_pose=head_pose,
                target_antennas=(amplitude, -amplitude),
                start_antennas=(float(antennas[0]), float(antennas[1])),
                duration=PROGRESS_WIGGLE_S,
            )
            self._movement.queue_move(move)
            self._movement.set_moving_state(PROGRESS_WIGGLE_S)
        except Exception as exc:
            logger.debug("progress signal: wiggle failed (%s)", exc)

    def rest(self) -> None:
        """Bring the antennas back to neutral; never raises."""
        if self._movement is None or self._robot is None:
            return
        try:
            from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove

            head_pose = self._robot.get_current_head_pose()
            _, antennas = self._robot.get_current_joint_positions()
            move = GotoQueueMove(
                target_head_pose=head_pose,
                start_head_pose=head_pose,
                target_antennas=(0.0, 0.0),
                start_antennas=(float(antennas[0]), float(antennas[1])),
                duration=PROGRESS_WIGGLE_S,
            )
            self._movement.queue_move(move)
        except Exception as exc:
            logger.debug("progress signal: rest failed (%s)", exc)

    async def ticker(self, interval_s: float) -> None:
        """Tick forever (cancelled by the context manager)."""
        while True:
            await asyncio.sleep(interval_s)
            await self.play(tick_tone(self._rate))
            self.wiggle()


@asynccontextmanager
async def working_signal(deps: ToolDependencies, *, tick_interval_s: float = PROGRESS_TICK_S) -> AsyncIterator[None]:
    """Chime + wiggle on enter, tick every ``tick_interval_s``, done chime on exit.

    A no-op when the robot has no media. Exceptions from the wrapped block
    propagate untouched (the done chime is skipped on failure).
    """
    signal = _Signal(deps)
    if not signal.enabled:
        yield
        return
    await signal.play(start_chime(signal._rate))
    signal.wiggle()
    task = asyncio.create_task(signal.ticker(tick_interval_s), name="bookmark-insight-progress-ticker")
    try:
        yield
    except BaseException:
        task.cancel()
        signal.rest()
        raise
    else:
        task.cancel()
        signal.rest()
        await signal.play(done_chime(signal._rate))
    finally:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
