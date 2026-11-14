"""Track B (M3): URL fetch + per-bookmark summary written into ``## 요약`` (§2.5 steps 1–2).

Public entry point:

    summarize_bookmarks(settings, llm, bookmarks, *, now=None, client=None) -> SummarizeReport

Rules the implementation keeps (see design doc): fetch-stage 2MB cap,
binary content types fail fast, redirect limit 5, meta-charset first, bodies
under ``MIN_BODY_CHARS`` count as ``fetch_failed``, excluded URL patterns are
never fetched, ``fetch_failed`` retries back off (1 → 3 → 7 days, max 3),
``ok`` bookmarks are never re-summarized, every write goes through
``bookmark_insight.store.modify_bookmark``.

Submodules: ``fetch`` (HTTP + text extraction, injectable client), ``sections``
(``## 요약`` section edits), ``usage`` (token usage JSONL), ``runner`` (the run
itself; ``runner._sleep`` is the monkeypatch point for the call interval).
"""

from __future__ import annotations
from dataclasses import field, dataclass

from bookmark_insight.settings import DEFAULT_CALL_INTERVAL_S, DEFAULT_DAILY_MAX_SUMMARIES


# --- tuning constants ---
FETCH_MAX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 10.0
FETCH_MAX_REDIRECTS = 5
MIN_BODY_CHARS = 200
# Daily cap and call interval are settings since M7 (``BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES`` /
# ``BOOKMARK_INSIGHT_CALL_INTERVAL_S``); these module names remain as the defaults.
DAILY_MAX_SUMMARIES = DEFAULT_DAILY_MAX_SUMMARIES
CALL_INTERVAL_S = DEFAULT_CALL_INTERVAL_S
RETRY_BACKOFF_DAYS: tuple[int, ...] = (1, 3, 7)
# A model failure (503, timeout) marks that bookmark ``summary_error`` and the run
# continues; this many failures in a row mean the service is down → stop the run.
MAX_CONSECUTIVE_LLM_FAILURES = 3


@dataclass
class SummarizeReport:
    """Outcome of one summarize run."""

    summarized: list[str] = field(default_factory=list)  # guids now ok
    failed: list[str] = field(default_factory=list)  # guids marked fetch_failed this run
    llm_failed: list[str] = field(default_factory=list)  # fetched but the model call failed (still pending)
    skipped: list[str] = field(default_factory=list)  # already ok / excluded / backoff
    warnings: list[str] = field(default_factory=list)
    batch_cap_reached: bool = False  # more candidates than ``batch_max_summaries``: left for a later run
    daily_cap_reached: bool = False  # ``daily_max_summaries`` hit: the rest waits for tomorrow


# The runner reads the constants above, so it is imported once they exist.
from bookmark_insight.summarize.runner import summarize_bookmarks  # noqa: E402


__all__ = [
    "CALL_INTERVAL_S",
    "DAILY_MAX_SUMMARIES",
    "FETCH_MAX_BYTES",
    "FETCH_MAX_REDIRECTS",
    "FETCH_TIMEOUT_S",
    "MAX_CONSECUTIVE_LLM_FAILURES",
    "MIN_BODY_CHARS",
    "RETRY_BACKOFF_DAYS",
    "SummarizeReport",
    "summarize_bookmarks",
]
