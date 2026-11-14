"""The "훑어보는 중" progress signal: chimes, ticks and wiggles around a long Bookmark Insight batch."""

from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.bookmark_insight_progress as progress
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


def _deps_with_media(rate: int = 24000) -> tuple[ToolDependencies, MagicMock]:
    robot = MagicMock()
    robot.media.get_output_audio_samplerate.return_value = rate
    robot.get_current_head_pose.return_value = np.eye(4, dtype=np.float32)
    robot.get_current_joint_positions.return_value = ([0.0], [0.1, -0.1])
    return ToolDependencies(reachy_mini=robot, movement_manager=MagicMock()), robot


def _pushed(robot: MagicMock) -> list[Any]:
    return [call.args[0] for call in robot.media.push_audio_sample.call_args_list]


def test_tones_are_float32_mono_with_fades() -> None:
    """Synthesized tones have the requested length, stay within the gain and start/end near silence."""
    wave = progress.tone(660.0, 0.1, 16000)
    assert wave.dtype == np.float32 and wave.ndim == 1 and len(wave) == 1600
    assert float(np.abs(wave).max()) <= progress.PROGRESS_GAIN + 1e-6
    assert abs(float(wave[0])) < 1e-6 and abs(float(wave[-1])) < 1e-3
    assert len(progress.start_chime(16000)) == 2 * len(wave) * 12 // 10
    assert len(progress.done_chime(16000)) > len(progress.tick_tone(16000))


@pytest.mark.asyncio
async def test_signal_plays_start_and_done_and_wiggles() -> None:
    """Enter → start chime + wiggle; exit → antennas rest + done chime (ticks need more time)."""
    deps, robot = _deps_with_media()
    async with progress.working_signal(deps):
        assert robot.media.push_audio_sample.call_count == 1  # start chime
        assert deps.movement_manager.queue_move.call_count == 1  # wiggle
    pushed = _pushed(robot)
    assert len(pushed) == 2
    assert len(pushed[0]) == len(progress.start_chime(24000))
    assert len(pushed[1]) == len(progress.done_chime(24000))
    assert deps.movement_manager.queue_move.call_count == 2  # wiggle + rest


@pytest.mark.asyncio
async def test_ticks_fire_while_working() -> None:
    """With a short tick interval, ticks (tone + wiggle) repeat until the block ends."""
    deps, robot = _deps_with_media()
    async with progress.working_signal(deps, tick_interval_s=0.01):
        await asyncio.sleep(0.08)
    pushed = _pushed(robot)
    ticks = [wave for wave in pushed if len(wave) == len(progress.tick_tone(24000))]
    assert len(ticks) >= 3
    assert deps.movement_manager.queue_move.call_count >= 1 + len(ticks)


@pytest.mark.asyncio
async def test_media_errors_are_swallowed_and_block_result_kept() -> None:
    """Audio / motion failures never reach the caller; the wrapped block still runs."""
    deps, robot = _deps_with_media()
    robot.media.push_audio_sample.side_effect = RuntimeError("audio sink gone")
    deps.movement_manager.queue_move.side_effect = RuntimeError("motion dead")
    ran = False
    async with progress.working_signal(deps):
        ran = True
    assert ran


@pytest.mark.asyncio
async def test_block_exception_propagates_without_done_chime() -> None:
    """An error inside the block propagates; the done chime is skipped, the ticker is cancelled."""
    deps, robot = _deps_with_media()
    with pytest.raises(ValueError):
        async with progress.working_signal(deps, tick_interval_s=0.01):
            raise ValueError("pipeline exploded")
    assert all(len(wave) != len(progress.done_chime(24000)) for wave in _pushed(robot))


@pytest.mark.asyncio
async def test_noop_without_media() -> None:
    """A robot without media (tests, headless) → nothing played, nothing queued, block runs."""
    robot = MagicMock(spec=[])  # no .media attribute
    deps = ToolDependencies(reachy_mini=robot, movement_manager=MagicMock())
    async with progress.working_signal(deps):
        pass
    deps.movement_manager.queue_move.assert_not_called()


def test_output_rate_falls_back_when_media_cannot_report() -> None:
    """A media that reports -1 (audio not initialized) uses the fallback rate."""
    deps, robot = _deps_with_media(rate=-1)
    signal = progress._Signal(deps)
    assert signal._rate == progress.PROGRESS_FALLBACK_RATE
