"""Return the Bookmark Insight note for a period immediately (design doc §2.2).

Thin wrapper over ``bookmark_insight.api.get_insight``. Never does heavy work: a
missing or stale note yields ``status: generating`` and tells the model to call
``build_insight`` (the pomodoro focus→break hand-off pattern), so the voice
loop is never blocked by fetching or LLM calls.

M7: a partial note (``complete: false``) whose bookmark set is still current is
not stale; it is returned as ``status: partial`` with the counts and an offer
to continue. While a ``build_insight`` for the same profile and period is
running in this process the answer is ``status: backfilling`` (no session).
"""

from __future__ import annotations
import time
import logging
from typing import Any

from bookmark_insight import api
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools._bookmark_insight_common import log_args, elapsed_ms, profile_hint


logger = logging.getLogger(__name__)

PERIOD_PROPERTIES: dict[str, Any] = {
    "days": {
        "type": "integer",
        "description": "Number of days back from until (inclusive). Default 7. Do not combine with since.",
    },
    "since": {"type": "string", "description": "Start date YYYY-MM-DD (inclusive). Do not combine with days."},
    "until": {"type": "string", "description": "End date YYYY-MM-DD (inclusive). Defaults to today."},
    "profile": {"type": "string", "description": "Chrome profile folder name. Omit for the default profile."},
}


class GetInsight(Tool):
    """Read the insight note for a period, or hand off generation to ``build_insight``."""

    name = "get_insight"
    description = (
        "Get the Bookmark Insight (topic clusters, correlations, one question each) for the user's "
        "bookmarks in a period. Returns immediately. status 'ready' has the clusters; 'generating' "
        "means you must call build_insight next; 'empty' means no bookmarks in the period; 'partial' has "
        "the clusters of the bookmarks seen so far plus pending_count not yet summarized (call build_insight "
        "again when the user asks to continue); 'backfilling' means build_insight is running right now. "
        "Use days (default 7) or since/until, never both. "
    )
    parameters_schema = {
        "type": "object",
        "properties": dict(PERIOD_PROPERTIES),
        "required": [],
    }

    def __init__(self) -> None:
        """Append the configured profile names to the description (model must not invent them)."""
        self.description = self.description + profile_hint()

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return ready / partial / backfilling / generating / empty for the requested period."""
        started = time.perf_counter()
        logger.info("get_insight start: %s", log_args(kwargs))
        result = api.get_insight(
            days=kwargs.get("days"),
            since=kwargs.get("since"),
            until=kwargs.get("until"),
            profile=kwargs.get("profile"),
        )
        logger.info("get_insight done in %.0f ms: status=%s", elapsed_ms(started), result.get("status", "error"))
        return result
