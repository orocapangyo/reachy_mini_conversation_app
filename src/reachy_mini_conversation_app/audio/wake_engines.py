"""Concrete WakeWordEngine implementations.

- MockWakeEngine: scripted responses for tests and simulation (no model files needed).
- OpenWakeWordEngine: openwakeword-based acoustic engine with lazy import so the app
  starts even when openwakeword is not installed.
- create_wake_engine(): factory that returns a WakeWordEngine or None on failure.
"""

from __future__ import annotations
import logging
from typing import Any
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

# Confidence threshold for OpenWakeWord predictions.  0.9997 is the measured
# optimum for the bundled reachy_mini_v4 model (WINDOW=8, streaming-extracted
# features): synthetic eval (12 pos / 12 neg) via Model.predict int16 streaming
# gave pos=0.9999, worst-neg=0.9996, so 0.9997 is the only threshold that met
# the acceptance bar (DR=100%, FP=0).  Margin is thin (~0.0003) — revisit after
# real-speech validation.
_OWW_CONFIDENCE_THRESHOLD: float = 0.9997

# Model bundled with the repo (trained 2026-10-27, see audio/models/README.md).
_BUNDLED_MODEL_FILENAME = "models/reachy_mini_v4.onnx"


# ---------------------------------------------------------------------------
# MockWakeEngine
# ---------------------------------------------------------------------------


class MockWakeEngine:
    """Scripted wake engine for unit tests and simulation.

    Parameters
    ----------
    trigger_fn:
        Optional callable(call_count) -> str | None.  The engine invokes it on
        every process() call; return a phrase string to simulate a detection,
        or None for silence.  If not provided, the engine never detects anything.

    Example — detect on the 3rd call::

        MockWakeEngine(trigger_fn=lambda n: "reachy mini" if n == 3 else None)

    """

    def __init__(
        self,
        trigger_fn: Callable[[int], str | None] | None = None,
    ) -> None:
        """Initialize with an optional trigger callable."""
        self._trigger_fn = trigger_fn
        self._call_count: int = 0

    def process(self, pcm_16k_mono: NDArray[np.int16]) -> str | None:
        """Run the scripted detection scenario."""
        self._call_count += 1
        if self._trigger_fn is not None:
            return self._trigger_fn(self._call_count)
        return None

    def reset(self) -> None:
        """Reset call counter so scenarios restart cleanly after stop/start."""
        self._call_count = 0

    def close(self) -> None:
        """No resources to release."""


# ---------------------------------------------------------------------------
# OpenWakeWordEngine
# ---------------------------------------------------------------------------


class OpenWakeWordEngine:
    """openwakeword-based acoustic wake-word engine.

    The openwakeword package and a trained .tflite model are required.  Both are
    loaded lazily: missing package or missing model file causes a RuntimeError at
    construction time (not at import time), so the app can start and fall back to
    mock/disabled mode when openwakeword is not installed.

    Model path is read from config.REACHY_MINI_WAKE_MODEL_PATH.
    """

    def __init__(self, model_path: str) -> None:
        """Load the openwakeword model from model_path (raises RuntimeError on failure)."""
        # Lazy import — catches missing openwakeword at construction rather than
        # module load so other code can import wake_engines without the package.
        try:
            from openwakeword.model import Model  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "openwakeword is not installed.  Install it with: "
                "pip install openwakeword  "
                "or set REACHY_MINI_WAKE_ENGINE=mock to use the mock engine."
            ) from exc

        import os  # noqa: PLC0415

        if not os.path.isfile(model_path):
            raise RuntimeError(
                f"Wake word model file not found: {model_path!r}.  "
                "Train or download the model and set REACHY_MINI_WAKE_MODEL_PATH."
            )

        # Pick the inference framework from the model file extension — the
        # bundled model is ONNX; user-supplied .tflite models keep working.
        framework = "onnx" if model_path.endswith(".onnx") else "tflite"
        _ensure_feature_models(framework)
        self._model: Any = Model(wakeword_models=[model_path], inference_framework=framework)
        self._model_path = model_path
        self.last_score: float = 0.0  # most recent prediction score (diagnostics)
        # Derive the label from the model filename (stem without extension).
        import pathlib  # noqa: PLC0415

        self._label = pathlib.Path(model_path).stem

    def process(self, pcm_16k_mono: NDArray[np.int16]) -> str | None:
        """Run openWakeWord inference on one PCM chunk.

        Returns the model label when confidence >= threshold, else None.
        """
        # openwakeword's streaming path expects int16 PCM; float32 input in [-1, 1]
        # would be truncated to 0 by the internal int16 cast, producing silent audio.
        prediction = self._model.predict(pcm_16k_mono)
        score = float(prediction.get(self._label, 0.0))
        self.last_score = score  # exposed for diagnostic logging in the listener
        if score >= _OWW_CONFIDENCE_THRESHOLD:
            logger.info(
                "OpenWakeWordEngine: detected %r (score=%.3f)",
                self._label,
                score,
            )
            return self._label
        return None

    def reset(self) -> None:
        """Reset openwakeword streaming buffers."""
        try:
            self._model.reset()
        except AttributeError:
            pass  # older API versions may not expose reset()

    def close(self) -> None:
        """Release TFLite interpreter and model resources."""
        try:
            del self._model
        except AttributeError:
            pass


def _ensure_feature_models(framework: str) -> None:
    """Download openwakeword's shared feature models if they are missing.

    The openwakeword wheel ships without its melspectrogram/embedding models, so a
    fresh install cannot run any wake model until they are fetched (~2MB, once).
    Raises RuntimeError when the download fails so the factory can fall back.
    """
    import pathlib  # noqa: PLC0415

    import openwakeword  # noqa: PLC0415
    from openwakeword.utils import download_file  # noqa: PLC0415

    for feature in openwakeword.FEATURE_MODELS.values():
        local_path = pathlib.Path(feature["model_path"]).with_suffix(f".{framework}")
        if local_path.is_file():
            continue
        url = str(feature["download_url"]).rsplit(".", 1)[0] + f".{framework}"
        logger.info("Downloading openwakeword feature model %s", local_path.name)
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            download_file(url, str(local_path.parent))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to download openwakeword feature model {local_path.name!r}: {exc}.  "
                "Check the network connection or run openwakeword.utils.download_models() manually."
            ) from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_wake_engine(name: str) -> "MockWakeEngine | OpenWakeWordEngine | None":
    """Instantiate a WakeWordEngine by name.

    Supported names: ``"mock"`` and ``"openwakeword"``.

    Returns None (with a warning) when the engine cannot be loaded — the app
    continues without wake-word detection rather than crashing.
    """
    from reachy_mini_conversation_app import config as _cfg  # noqa: PLC0415

    normalized = name.strip().lower()

    if normalized == "mock":
        logger.info("create_wake_engine: using MockWakeEngine")
        return MockWakeEngine()

    if normalized == "openwakeword":
        model_path: str = (getattr(_cfg.config, "REACHY_MINI_WAKE_MODEL_PATH", None) or "").strip()
        if not model_path:
            # Fall back to the model bundled with the package.
            import pathlib  # noqa: PLC0415

            model_path = str(pathlib.Path(__file__).parent / _BUNDLED_MODEL_FILENAME)
        try:
            engine = OpenWakeWordEngine(model_path=model_path)
            logger.info("create_wake_engine: OpenWakeWordEngine loaded from %r", model_path)
            return engine
        except RuntimeError as exc:
            logger.warning("create_wake_engine: failed to load OpenWakeWordEngine: %s", exc)
            return None

    logger.warning("create_wake_engine: unknown engine name %r; wake-word detection disabled.", name)
    return None
