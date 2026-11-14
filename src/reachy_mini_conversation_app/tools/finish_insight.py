"""Close the open insight conversation and record its conclusion (design doc §2.2, §3.3).

Wraps ``bookmark_insight.api.finish_insight``. The collapsed ``대화 원문`` (M8) is
app-side: it comes from the app's transcript log, so this wrapper renders the
lines of the session and hands them to the package as ``transcript``. The MCP
server has no transcript and passes none (conclusion only).
"""

from __future__ import annotations
import time
import logging
from typing import Any, ClassVar

from bookmark_insight import api
from bookmark_insight.api import ALLOWED_REASONS, FINISH_NEXT_ACTION, NO_SESSION_NEXT_ACTION
from bookmark_insight.session import SESSION_MANAGER
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools._bookmark_insight_common import elapsed_ms
from reachy_mini_conversation_app.bookmark_insight_transcript_log import get_transcript_log, render_transcript_lines


logger = logging.getLogger(__name__)

__all__ = ["ALLOWED_REASONS", "FINISH_NEXT_ACTION", "NO_SESSION_NEXT_ACTION", "FinishInsight"]


class FinishInsight(Tool):
    """End the insight conversation: append ``## 대화 기록`` to the note and close the session."""

    needs_response: ClassVar[bool] = True

    name = "finish_insight"
    description = (
        "Finish the current Bookmark Insight conversation. Call it when the user explicitly asks to wrap up "
        "(reason='explicit') or confirms ending it after a topic change (reason='confirmed'). Pass your "
        "conclusion of the conversation in a few sentences; it is written into the insight note. "
        "No profile argument: the open session knows which note to write."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "conclusion": {
                "type": "string",
                "description": "Your summary of the conversation's conclusions (a few sentences, Korean).",
            },
            "reason": {
                "type": "string",
                "enum": list(ALLOWED_REASONS),
                "description": "'explicit' when the user asked to finish, 'confirmed' when they agreed to end it.",
            },
        },
        "required": ["conclusion"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Validate the reason, close the session and append the conclusion (plus transcript) to the note."""
        started = time.perf_counter()
        logger.info("finish_insight start: reason=%s", kwargs.get("reason") or "explicit")
        # The session (open or last closed) tells where the transcript starts; the package closes it.
        session = SESSION_MANAGER.current() or SESSION_MANAGER.last_closed()
        transcript = self._transcript_since(session.started_wall) if session is not None else None
        result = api.finish_insight(
            conclusion=kwargs.get("conclusion"), reason=kwargs.get("reason"), transcript=transcript
        )
        logger.info("finish_insight done in %.0f ms: %s", elapsed_ms(started), "error" if "error" in result else "ok")
        return result

    @staticmethod
    def _transcript_since(started_wall: float) -> str | None:
        """Collapsible 대화 원문 for the note (M8), or ``None`` when no log / no lines (conclusion only)."""
        transcript_log = get_transcript_log()
        if transcript_log is None:
            return None
        try:
            rendered = render_transcript_lines(transcript_log.lines_since(started_wall))
        except Exception:
            logger.debug("finish_insight: transcript unavailable (ignored)", exc_info=True)
            return None
        return rendered or None
