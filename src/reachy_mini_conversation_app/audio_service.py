"""Audio input manager and microphone device selection service.

Enables enumeration of available audio capture hardware, real-time input device switching,
and low-latency audio sample acquisition via GStreamer for speech interaction.
"""

import os
import time
import logging
import platform
import threading
from typing import Any, Optional

import numpy as np
import numpy.typing as npt


logger = logging.getLogger(__name__)


class AudioDeviceService:
    """Manages active microphone input source and audio frame capture."""

    SAMPLE_RATE = 16000
    CHANNELS = 2
    _instance: Optional["AudioDeviceService"] = None

    def __init__(self) -> None:
        """Initialize the audio device service with thread safety."""
        self._lock = threading.Lock()
        self._active_device_id: str = "auto"
        self._cached_devices: list[dict[str, str]] = []
        self._last_device_scan_time: float = 0.0
        self._pipeline: Optional[Any] = None
        self._sink: Optional[Any] = None
        self._gst_initialized: bool = False
        env_threshold = os.getenv("REACHY_MINI_NOISE_GATE_THRESHOLD")
        init_threshold = 0.0
        if env_threshold:
            try:
                init_threshold = max(0.0, min(1.0, float(env_threshold)))
            except ValueError:
                pass
        self._noise_gate_threshold: float = init_threshold
        self._gate_hold_time_s: float = 0.35
        self._last_speech_time: float = 0.0

    @classmethod
    def get_instance(cls) -> "AudioDeviceService":
        """Return the singleton instance of AudioDeviceService."""
        if cls._instance is None:
            cls._instance = AudioDeviceService()
        return cls._instance

    def _ensure_gst(self) -> bool:
        """Ensure GStreamer runtime is initialized."""
        if self._gst_initialized:
            return True
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
            self._gst_initialized = True
            return True
        except Exception as e:
            logger.warning("Failed to initialize GStreamer for audio service: %s", e)
            return False

    def list_devices(self, force_refresh: bool = False) -> list[dict[str, str]]:
        """List all available physical audio capture devices."""
        now = time.time()
        if not force_refresh and self._cached_devices and (now - self._last_device_scan_time < 10.0):
            return list(self._cached_devices)

        devices: list[dict[str, str]] = [
            {"id": "auto", "name": "기본 마이크 (시스템 기본값)"},
        ]

        if not self._ensure_gst():
            return devices

        try:
            from reachy_mini.media.device_detection import gst_monitor_devices

            raw_devices = gst_monitor_devices("Audio/Source")
            current_os = platform.system()

            for dev in raw_devices:
                props = dev.properties
                name = dev.display_name or "Unknown Microphone"

                if current_os == "Windows":
                    if props.get("device.api") != "wasapi2":
                        continue
                    # Skip Windows loopback (render) audio sources
                    if props.get("wasapi2.device.loopback", "false").lower() == "true":
                        continue
                    if "Default Audio" in name:
                        continue
                    dev_id = props.get("device.id", "")
                    if dev_id:
                        devices.append({"id": dev_id, "name": name})

                elif current_os == "Linux":
                    if props.get("device.class") == "monitor":
                        continue
                    dev_id = props.get("node.name") or props.get("device.string", "")
                    if dev_id:
                        devices.append({"id": dev_id, "name": name})

                elif current_os == "Darwin":
                    dev_id = props.get("unique-id", "")
                    if dev_id:
                        devices.append({"id": dev_id, "name": name})

        except Exception as e:
            logger.warning("Error enumerating audio devices: %s", e)

        with self._lock:
            self._cached_devices = devices
            self._last_device_scan_time = now
        return devices

    def get_active_device_id(self) -> str:
        """Return the current active microphone device identifier."""
        return self._active_device_id

    def select_device(self, device_id: str) -> bool:
        """Select active microphone capture device."""
        with self._lock:
            if self._active_device_id == device_id:
                return True

            logger.info("Switching active microphone to: %s", device_id)
            self._stop_pipeline_locked()
            self._active_device_id = device_id

            if device_id == "auto":
                return True

            success = self._start_pipeline_locked(device_id)
            if not success:
                logger.warning("Failed starting custom mic pipeline for %s, falling back to auto", device_id)
                self._active_device_id = "auto"
                return False
            return True

    def _start_pipeline_locked(self, device_id: str) -> bool:
        """Start GStreamer audio capture pipeline for the specified device ID."""
        if not self._ensure_gst():
            return False

        try:
            from gi.repository import Gst

            current_os = platform.system()
            if current_os == "Windows":
                src_desc = f'wasapi2src device="{device_id}"'
            elif current_os == "Darwin":
                src_desc = f'osxaudiosrc device="{device_id}"'
            else:
                src_desc = f'pulsesrc device="{device_id}"'

            pipeline_str = (
                f"{src_desc} ! audioconvert ! audioresample ! "
                f'capsfilter caps="audio/x-raw,rate={self.SAMPLE_RATE},'
                f'channels={self.CHANNELS},format=F32LE,layout=interleaved" ! '
                "appsink name=micsink drop=true max-buffers=200"
            )

            pipe = Gst.parse_launch(pipeline_str)
            sink = pipe.get_by_name("micsink")
            if sink is None:
                pipe.set_state(Gst.State.NULL)
                return False

            ret = pipe.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                pipe.set_state(Gst.State.NULL)
                return False

            self._pipeline = pipe
            self._sink = sink
            return True
        except Exception as e:
            logger.error("Error creating audio pipeline for device %s: %s", device_id, e)
            return False

    def _stop_pipeline_locked(self) -> None:
        """Stop and release current capture pipeline."""
        if self._pipeline is not None:
            try:
                from gi.repository import Gst

                pipe = self._pipeline
                if isinstance(pipe, Gst.Pipeline):
                    pipe.set_state(Gst.State.NULL)
            except Exception as e:
                logger.debug("Error releasing audio pipeline: %s", e)
            finally:
                self._pipeline = None
                self._sink = None

    def get_audio_sample(self) -> Optional[npt.NDArray[np.float32]]:
        """Pull the next audio frame from the active custom microphone."""
        if self._active_device_id == "auto" or self._sink is None:
            return None

        try:
            from gi.repository import Gst

            sample = self._sink.emit("pull-sample")
            if sample is None:
                return None

            buf = sample.get_buffer()
            if buf is None:
                return None

            success, map_info = buf.map(Gst.MapFlags.READ)
            if not success:
                return None

            try:
                data = np.frombuffer(map_info.data, dtype=np.float32).reshape(-1, self.CHANNELS)
                return data.copy()
            finally:
                buf.unmap(map_info)

        except Exception as e:
            logger.debug("Failed pulling audio sample: %s", e)
            return None

    def get_noise_gate_threshold(self) -> float:
        """Return the current noise gate threshold between 0.0 and 1.0."""
        with self._lock:
            return self._noise_gate_threshold

    def set_noise_gate_threshold(self, threshold: float) -> float:
        """Set the noise gate threshold (clamped between 0.0 and 1.0)."""
        clamped = max(0.0, min(1.0, float(threshold)))
        with self._lock:
            self._noise_gate_threshold = clamped
            logger.info("Noise gate threshold set to %.2f", clamped)
            return self._noise_gate_threshold

    def process_noise_gate(self, audio_data: np.ndarray) -> tuple[np.ndarray, bool]:
        """Apply software noise gate to incoming audio chunk.

        Returns a tuple of (processed_audio, is_gated).
        When the audio level is below the threshold and hold time has expired,
        silence (zeros) is returned with is_gated=True.
        """
        with self._lock:
            threshold = self._noise_gate_threshold

        if threshold <= 0.0:
            return audio_data, False

        try:
            if audio_data.dtype != np.float32:
                float_data = audio_data.astype(np.float32) / 32768.0
            else:
                float_data = audio_data
            rms = float(np.sqrt(np.mean(np.square(float_data)))) if float_data.size else 0.0
            # Matches audio meter level gain for 1-to-1 threshold calibration
            normalized = min(1.0, rms * 6.0)
        except Exception:
            return audio_data, False

        now = time.monotonic()
        if normalized >= threshold:
            with self._lock:
                self._last_speech_time = now
            return audio_data, False

        with self._lock:
            in_hold = (now - self._last_speech_time) < self._gate_hold_time_s

        if in_hold:
            return audio_data, False

        return np.zeros_like(audio_data), True

    def close(self) -> None:
        """Release audio capture resources."""
        with self._lock:
            self._stop_pipeline_locked()
