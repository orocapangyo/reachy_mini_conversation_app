import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Put Reachy to sleep and stop the current app."""

    name = "go_to_sleep"
    description = (
        "Use when you are sure the user wants Reachy to go to sleep, stop the current app, shut down this app, "
        "or end the conversation. Do not use for idle turns, sleepy emotions, silence, or ambiguous requests."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Put Reachy to sleep and request app shutdown."""
        if deps.go_to_sleep is None:
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep -> Triggering Reachy Dream Cycle 🌙")

        # Trigger Reachy Dream Cycle in background upon sleeping
        try:
            from reachy_mini_conversation_app.reachy_vault import vault_instance
            import time
            sleep_time = time.strftime("%Y-%m-%d %H:%M:%S")
            vault_instance.execute_dream_cycle(
                recent_transcripts=[],
                robot_sleep_timestamp=sleep_time
            )
            # Sync dream consolidation fact with gbrain so the web dashboard lights up!
            from reachy_mini_conversation_app.gbrain_adapter import gbrain_instance
            gbrain_instance.remember_fact(
                f"Reachy Mini entered sleep mode and consolidated daily memories at {sleep_time}.",
                entity="user_hong",
                provenance="reachy_dream_cycle",
            )
        except Exception as dream_err:
            logger.warning("Dream cycle background trigger notice: %s", dream_err)

        try:
            return await asyncio.to_thread(deps.go_to_sleep)
        except Exception as e:
            logger.error("go_to_sleep failed: %s", e)
            return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
