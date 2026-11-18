"""Tuning constants for the memory retrieval benchmark."""

import os
from pathlib import Path

from reachy_mini_conversation_app.memory import memory_path_for_instance


# --- run defaults ---------------------------------------------------------------
DEFAULT_K = 5
DEFAULT_REPEAT = 3
SUBJECT_NAMES = ("json", "gbrain", "gbrain-native")
LATENCY_PERCENTILES = (50, 95)
BENCH_PROVENANCE = "jisu memory benchmark"

# --- datasets ---------------------------------------------------------------------
DATASET_SCHEMA_VERSION = 1
DATASETS_DIR = Path(__file__).parent / "datasets"
DEFAULT_DATASET = DATASETS_DIR / "ko_desk_v1.toml"
NEGATIVE_TAG = "negative"

# --- reports ----------------------------------------------------------------------
REPORT_SCHEMA_VERSION = 1
RESULTS_DIR_ENV = "JISU_BENCH_RESULTS_DIR"
RESULTS_SUBDIR = ("jisu-bench", "memory")
REPORT_TIME_FORMAT = "%Y%m%d-%H%M%S"


def default_results_dir() -> Path:
    """Where reports are saved: ``$JISU_BENCH_RESULTS_DIR`` or ``<app data>/jisu-bench/memory``."""
    override = os.getenv(RESULTS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return memory_path_for_instance(None).parent.joinpath(*RESULTS_SUBDIR)
