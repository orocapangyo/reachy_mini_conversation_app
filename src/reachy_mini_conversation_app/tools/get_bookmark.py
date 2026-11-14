"""Return one bookmark's front matter and summary by guid (design doc §2.2). Wraps ``bookmark_insight.api.get_bookmark``."""

from __future__ import annotations
import time
import logging
from typing import Any

from bookmark_insight import api
from bookmark_insight.api import BOOKMARK_NEXT_ACTION, extract_summary
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools._bookmark_insight_common import elapsed_ms


logger = logging.getLogger(__name__)

__all__ = ["BOOKMARK_NEXT_ACTION", "GetBookmark", "extract_summary"]


class GetBookmark(Tool):
    """Fetch one bookmark (title, url, tags, date, summary) for follow-up questions."""

    name = "get_bookmark"
    description = (
        "Get one bookmark's details and its stored summary by id (the id from get_insight clusters or "
        "list_bookmarks). Use it to answer follow-up questions such as 'what was that article about?'. "
        "No profile argument: ids are global."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Bookmark id (guid) from get_insight or list_bookmarks."},
        },
        "required": ["id"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Look the bookmark up across all profiles and return its summary."""
        started = time.perf_counter()
        logger.info("get_bookmark start: id=%s", kwargs.get("id"))
        result = api.get_bookmark(id=kwargs.get("id"))
        logger.info("get_bookmark done in %.0f ms: %s", elapsed_ms(started), "error" if "error" in result else "ok")
        return result
