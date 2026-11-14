"""Tests for the SDK playback mixer caps workaround."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any

import pytest

from reachy_mini_conversation_app.audio.playback_pipeline import pin_playback_mixer_caps


gi = pytest.importorskip("gi")
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


Gst.init(None)
if Gst.ElementFactory.find("audiomixer") is None:
    pytest.skip("GStreamer audiomixer plugin is unavailable", allow_module_level=True)


def _make_robot() -> tuple[Any, Any, Any]:
    """Build a fake robot whose SDK audio pipeline mimics silence -> audiomixer -> audioconvert."""
    pipeline = Gst.parse_launch(
        "audiotestsrc wave=silence is-live=true ! "
        "audio/x-raw,format=F32LE,channels=2,rate=16000,layout=interleaved ! "
        "audiomixer name=mix ! audioconvert name=conv ! fakesink sync=false"
    )
    audio = SimpleNamespace(_pipeline=pipeline, CHANNELS=2, SAMPLE_RATE=16000)
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    return robot, pipeline.get_by_name("mix"), pipeline.get_by_name("conv")


def _downstream_of(element: Any) -> Any:
    return element.get_static_pad("src").get_peer().get_parent_element()


def test_pin_inserts_capsfilter_matching_mixer_inputs() -> None:
    """The mixer output must be pinned to F32LE at the SDK rate, ahead of the original downstream."""
    robot, mixer, conv = _make_robot()

    assert pin_playback_mixer_caps(robot) is True

    capsfilter = _downstream_of(mixer)
    assert capsfilter.get_factory().get_name() == "capsfilter"
    assert _downstream_of(capsfilter) is conv
    caps = capsfilter.get_property("caps").get_structure(0)
    assert caps.get_value("format") == "F32LE"
    assert caps.get_value("rate") == 16000
    assert caps.get_value("channels") == 2

    pipeline = robot.media.audio._pipeline
    try:
        assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
        pipeline.get_state(5 * Gst.SECOND)
        negotiated = mixer.get_static_pad("src").get_current_caps().get_structure(0)
        assert negotiated.get_value("format") == "F32LE"
        assert negotiated.get_value("rate") == 16000
    finally:
        pipeline.set_state(Gst.State.NULL)


def test_pin_is_idempotent() -> None:
    """A second call must not stack another capsfilter."""
    robot, mixer, _ = _make_robot()

    assert pin_playback_mixer_caps(robot) is True
    first = _downstream_of(mixer)
    assert pin_playback_mixer_caps(robot) is True
    assert _downstream_of(mixer) is first


def test_pin_skips_started_pipeline() -> None:
    """Relinking a running pipeline is unsafe, so a started pipeline is left untouched."""
    robot, mixer, conv = _make_robot()
    pipeline = robot.media.audio._pipeline
    try:
        pipeline.set_state(Gst.State.PLAYING)
        pipeline.get_state(5 * Gst.SECOND)
        assert pin_playback_mixer_caps(robot) is False
        assert _downstream_of(mixer) is conv
    finally:
        pipeline.set_state(Gst.State.NULL)


def test_pin_skips_without_sdk_pipeline() -> None:
    """Robots without an SDK audio pipeline (e.g. no media backend) are skipped quietly."""
    assert pin_playback_mixer_caps(SimpleNamespace(media=None)) is False
    assert pin_playback_mixer_caps(SimpleNamespace(media=SimpleNamespace(audio=SimpleNamespace()))) is False
