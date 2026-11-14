"""Workarounds for the Reachy Mini SDK playback pipeline.

The SDK mixes pushed audio and a silence source in an ``audiomixer`` whose
inputs are pinned to F32LE at the SDK rate, but whose output is left
unconstrained. When the output device wants another format (e.g. a laptop
speaker at S16LE/48 kHz in simulation), the mixer converts every input buffer
itself, and GStreamer 1.28's per-pad conversion intermittently segfaults on the
first pushed buffer (NULL dereference in ``do_convert_out``). Pinning the mixer
output to its input format moves the conversion to the ``audioconvert`` /
``audioresample`` elements already downstream, which avoids that code path.

Drop this once the SDK pins the mixer output itself.
"""

from __future__ import annotations
import logging
from typing import Any


MIXER_FACTORY_NAME = "audiomixer"


def pin_playback_mixer_caps(robot: object, *, logger: logging.Logger | None = None) -> bool:
    """Insert a capsfilter after the SDK playback mixer, matching its input format.

    Must run before the media pipeline starts (``start_recording``/``start_playing``).
    Returns True when the capsfilter was inserted; never raises.
    """
    log = logger or logging.getLogger(__name__)
    audio: Any = getattr(getattr(robot, "media", None), "audio", None)
    pipeline: Any = getattr(audio, "_pipeline", None)
    if pipeline is None:
        log.debug("Skipping playback mixer caps pin: SDK audio pipeline is unavailable.")
        return False

    try:
        from gi.repository import Gst  # noqa: PLC0415

        mixer = next(
            (
                element
                for element in pipeline.iterate_elements()
                if element.get_factory() is not None and element.get_factory().get_name() == MIXER_FACTORY_NAME
            ),
            None,
        )
        if mixer is None:
            log.debug("Skipping playback mixer caps pin: no audiomixer in the SDK pipeline.")
            return False

        peer = mixer.get_static_pad("src").get_peer()
        if peer is None:
            log.debug("Skipping playback mixer caps pin: audiomixer output is not linked.")
            return False
        downstream = peer.get_parent_element()
        if downstream.get_factory() is not None and downstream.get_factory().get_name() == "capsfilter":
            return True  # already pinned (by us or a fixed SDK)

        if pipeline.get_state(0)[1] != Gst.State.NULL:
            log.warning("Skipping playback mixer caps pin: SDK audio pipeline already started.")
            return False

        caps = Gst.Caps.from_string(
            f"audio/x-raw,format=F32LE,channels={audio.CHANNELS},rate={audio.SAMPLE_RATE},layout=interleaved"
        )
        capsfilter = Gst.ElementFactory.make("capsfilter")
        capsfilter.set_property("caps", caps)
        pipeline.add(capsfilter)
        mixer.unlink(downstream)
        if not (mixer.link(capsfilter) and capsfilter.link(downstream)):
            # Restore the original link so playback keeps working without the pin.
            mixer.unlink(capsfilter)
            pipeline.remove(capsfilter)
            mixer.link(downstream)
            log.warning("Skipping playback mixer caps pin: could not relink the SDK pipeline.")
            return False
    except Exception as exc:
        log.warning("Skipping playback mixer caps pin: %s", exc)
        return False

    log.info("Pinned SDK playback mixer output to %s", caps.to_string())
    return True
