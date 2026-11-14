"""List non-archived bookmarks in a period, newest first (design doc §2.2). Wraps ``bookmark_insight.api.list_bookmarks``."""

from __future__ import annotations
import time
import logging
from typing import Any

from bookmark_insight import api
from bookmark_insight.api import MAX_LIMIT, DEFAULT_LIMIT, LIST_NEXT_ACTION
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools.get_insight import PERIOD_PROPERTIES
from reachy_mini_conversation_app.tools._bookmark_insight_common import log_args, elapsed_ms, profile_hint


logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_LIMIT", "LIST_NEXT_ACTION", "MAX_LIMIT", "ListBookmarks"]


class ListBookmarks(Tool):
    """Secondary lookup for bookmarks that the insight note does not mention."""

    name = "list_bookmarks"
    description = (
        "List the user's bookmarks saved in a period (newest first; archived ones excluded). Secondary "
        "tool: use it when something is not in the insight note. Use days (default 7) or since/until, "
        "never both. "
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            **PERIOD_PROPERTIES,
            "limit": {
                "type": "integer",
                "description": f"Maximum number of bookmarks to return. Default {DEFAULT_LIMIT}.",
            },
        },
        "required": [],
    }

    def __init__(self) -> None:
        """Append the configured profile names to the description."""
        self.description = self.description + profile_hint()

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return the compact bookmark list for the period."""
        started = time.perf_counter()
        logger.info("list_bookmarks start: %s", log_args(kwargs))
        result = api.list_bookmarks(
            days=kwargs.get("days"),
            since=kwargs.get("since"),
            until=kwargs.get("until"),
            profile=kwargs.get("profile"),
            limit=kwargs.get("limit"),
        )
        logger.info("list_bookmarks done in %.0f ms: count=%s", elapsed_ms(started), result.get("count", "error"))
        return result
