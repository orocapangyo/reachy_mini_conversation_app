"""Shared helpers for the Bookmark Insight tool wrappers.

Not a tool itself: the leading underscore keeps ``list_tool_module_names`` from
discovering it. The operations themselves live in ``bookmark_insight.api`` (shared with
the optional MCP server), including the M7 registry of running pipelines; this
module only holds what is specific to wrapping them as app ``Tool`` subclasses:
timing for the logs and the argument filter. The test hooks of ``bookmark_insight.api``
(``reset_building`` / ``reset_sync_throttle`` / ``is_building``) are re-exported
for convenience.
"""

from __future__ import annotations
import time
from typing import Any

from bookmark_insight.api import is_building, profile_hint, reset_building, reset_sync_throttle


__all__ = ["elapsed_ms", "is_building", "log_args", "profile_hint", "reset_building", "reset_sync_throttle"]


def elapsed_ms(started: float) -> float:
    """Milliseconds since ``started`` (a ``time.perf_counter()`` value)."""
    return (time.perf_counter() - started) * 1000.0


def log_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Tool kwargs without the host-injected ``tool_manager`` (for the start log line)."""
    return {key: value for key, value in kwargs.items() if key != "tool_manager"}
