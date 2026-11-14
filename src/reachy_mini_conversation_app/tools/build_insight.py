"""Run the full insight pipeline (sync → summarize → cluster → note) in the background.

Thin wrapper over ``bookmark_insight.api.run_build_pipeline``. Long-running: the
backends run every tool through ``BackgroundToolManager``, so the model only
sees the result when this coroutine returns, exactly like a pomodoro phase
ending. ``get_insight`` hands off to this tool when the note is missing or
stale. The progress chime (``working_signal``) is app-side and stays here.

M7: one call summarizes at most ``batch_max_summaries`` pending bookmarks (and
never past the daily cap) and writes the note from everything summarized so
far. Pending bookmarks left → ``status: partial`` with the counts; calling
again continues from them. While the pipeline runs the profile/period is
registered (in ``bookmark_insight.api``) so ``get_insight`` answers ``backfilling``.
"""

from __future__ import annotations
import time
import asyncio
import logging
from typing import Any

from bookmark_insight import api
from bookmark_insight.api import BUSY_NEXT_ACTION, BUILT_NEXT_ACTION, DEGRADED_NEXT_ACTION
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools.get_insight import PERIOD_PROPERTIES
from reachy_mini_conversation_app.bookmark_insight_progress import working_signal
from reachy_mini_conversation_app.tools._bookmark_insight_common import log_args, elapsed_ms, profile_hint


logger = logging.getLogger(__name__)

__all__ = ["BUILT_NEXT_ACTION", "BUSY_NEXT_ACTION", "BuildInsight", "DEGRADED_NEXT_ACTION"]


class BuildInsight(Tool):
    """Generate (or regenerate) the insight note for a period; returns when the note is written."""

    # Long-running tool: awaits the whole batch pipeline in a worker thread.
    # task_status / task_cancel work on it through the BackgroundToolManager.
    name = "build_insight"
    description = (
        "Build the Bookmark Insight note for a period (sync bookmarks, summarize, cluster). Call it only "
        "when get_insight returned status 'generating', or 'partial' and the user asks to continue, with "
        "the same since/until/profile. It takes a while; the result arrives when the note is ready (or "
        "'partial' when more bookmarks than one batch allows are still pending) and tells you how to start talking. "
    )
    parameters_schema = {
        "type": "object",
        "properties": dict(PERIOD_PROPERTIES),
        "required": [],
    }

    def __init__(self) -> None:
        """Append the configured profile names to the description."""
        self.description = self.description + profile_hint()

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Run the pipeline off the event loop and return ready / partial / empty / error."""
        started = time.perf_counter()
        logger.info("build_insight start: %s", log_args(kwargs))
        ctx = api.prepare_context(kwargs)
        if not isinstance(ctx, api.ToolContext):
            return ctx
        # CancelledError (task_cancel) is not an Exception subclass: it propagates through
        # run_build_pipeline's catch-all and this await.
        async with working_signal(deps):
            result = await asyncio.to_thread(api.run_build_pipeline, ctx)
        logger.info("build_insight done in %.0f ms: status=%s", elapsed_ms(started), result.get("status", "error"))
        return result
