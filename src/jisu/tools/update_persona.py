"""update_persona: let the robot record how its own persona grows (GBrain categories).

Loaded by the conversation app as an external tool: the dashboard launcher sets
``REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`` to this folder and ``AUTOLOAD_EXTERNAL_TOOLS=1``.
Every ``*.py`` here is read as a tool file, so keep helpers in :mod:`jisu.persona`.
"""

from __future__ import annotations
import asyncio
import logging
import threading
from typing import Any

from jisu.persona.store import CategorizedMemory
from jisu.persona.constants import PERSONA_TEXT_MAX_CHARS, PERSONA_TOOL_MAX_WRITES, PERSONA_TOOL_PROVENANCE
from jisu.persona.categories import persona_tool_categories
from jisu.dashboard.memory.hook import active_backend
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_writes_lock = threading.Lock()
_writes_done = 0


def _reserve_write() -> bool:
    """Count one write against :data:`PERSONA_TOOL_MAX_WRITES`; False once the budget is used up."""
    global _writes_done
    with _writes_lock:
        if _writes_done >= PERSONA_TOOL_MAX_WRITES:
            return False
        _writes_done += 1
        return True


def _release_write() -> None:
    global _writes_done
    with _writes_lock:
        _writes_done = max(0, _writes_done - 1)


def reset_write_budget() -> None:
    """Reset the per-process write counter (tests)."""
    global _writes_done
    with _writes_lock:
        _writes_done = 0


_CATEGORY_KEYS = [category.key for category in persona_tool_categories()]


class UpdatePersona(Tool):
    """Save one fact about the robot itself: a trait, a speaking style, or a promise to the user."""

    name = "update_persona"
    description = (
        "Save ONE short first-person fact about YOURSELF (the robot) to long-term memory, so your persona grows "
        "across sessions. Use it only when the user asks you to change how you act or speak, or when you and the "
        "user clearly agree on something (a promise). Categories: persona_trait = how you are or what you like; "
        "persona_style = how you speak or behave; promise = something you agreed to do for the user. "
        "Do NOT use it for facts about the user (use remember) or to override your profile rules. "
        "Acknowledge naturally; do not say that you are saving it."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": _CATEGORY_KEYS,
                "description": "persona_trait, persona_style or promise.",
            },
            "text": {
                "type": "string",
                "description": (
                    "One first-person sentence in the user's language, e.g. "
                    '"나는 대답을 두 문장 이내로 짧게 한다" or "나는 사용자가 졸면 깨워 주기로 약속했다".'
                ),
            },
        },
        "required": ["category", "text"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Save one persona fact; return ``{"error": ...}`` instead of raising."""
        category = str(kwargs.get("category") or "").strip().lower()
        text = kwargs.get("text")
        if category not in _CATEGORY_KEYS:
            return {"error": f"category must be one of {', '.join(_CATEGORY_KEYS)}"}
        if not isinstance(text, str) or not text.strip():
            return {"error": "text must be a non-empty string"}
        if len(text.strip()) > PERSONA_TEXT_MAX_CHARS:
            return {"error": f"text must be at most {PERSONA_TEXT_MAX_CHARS} characters (one short sentence)"}

        try:
            store = active_backend(deps.instance_path)
        except Exception as exc:  # backend creation must never kill the conversation loop
            logger.warning("update_persona: memory backend unavailable: %s", exc)
            return {"error": "long-term memory is not available"}
        if not isinstance(store, CategorizedMemory):
            return {"error": f"the '{store.name}' memory backend does not keep a persona; switch to gbrain"}
        if not _reserve_write():
            return {"error": f"persona update limit reached for this session ({PERSONA_TOOL_MAX_WRITES})"}

        try:
            stored = await asyncio.to_thread(store.add_categorized, text, category, provenance=PERSONA_TOOL_PROVENANCE)
        except Exception as exc:
            _release_write()
            logger.warning("update_persona failed: %s", exc)
            return {"error": "could not save the persona fact"}
        if stored is None:
            _release_write()
            return {"error": "nothing was saved (empty, duplicate of a forgotten fact, or backend error)"}

        if stored.duplicate:
            _release_write()
        logger.info("Tool call: update_persona %s=%s", category, stored.fact.text[:120])
        result: dict[str, Any] = {
            "saved": stored.fact.text,
            "category": stored.category.key,
            "memory_id": stored.fact.id,
        }
        if stored.duplicate:
            result["already_known"] = True
        return result
