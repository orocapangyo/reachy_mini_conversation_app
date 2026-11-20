"""Trace every tool call the conversation app runs, without editing it.

All backends (openai / huggingface / gemini) and idle-policy tool calls go
through ``BackgroundToolManager._run_tool``, which knows the call id, tool
name, raw JSON args and, once awaited, the status/result/error. The hook wraps
that method and prints one ``TRACE_MARKER``-prefixed JSON log line at start and
end. The dashboard launcher already captures the app's output, so the lines
reach the browser through ``/ws/logs`` and feed the chat's thinking panel.

Tracing never changes tool behavior: any failure while building or logging an
event is swallowed (debug log only).
"""

from __future__ import annotations
import json
import time
import asyncio
import logging
from typing import Any
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

from jisu.dashboard.constants import TRACE_MARKER, TRACE_MAX_ITEMS, TRACE_MAX_FIELD_CHARS
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)

# Method wrapped on BackgroundToolManager. If upstream renames it, install fails loudly.
HOOK_TARGET = "_run_tool"
_WRAPPED_FLAG = "__jisu_trace_original__"

RunTool = Callable[..., Awaitable[None]]


@dataclass
class InstalledTraceHook:
    """Handle returned by :func:`install_trace_hook`; call :meth:`uninstall` to restore."""

    original: RunTool

    def uninstall(self) -> None:
        """Restore the original ``BackgroundToolManager._run_tool``."""
        setattr(BackgroundToolManager, HOOK_TARGET, self.original)


def shorten(value: Any, max_chars: int = TRACE_MAX_FIELD_CHARS, max_items: int = TRACE_MAX_ITEMS) -> Any:
    """Return a JSON-safe copy of ``value`` with long strings and containers cut."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}…(+{len(value) - max_chars} chars)"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                out["…"] = f"+{len(value) - max_items} more keys"
                break
            out[str(key)] = shorten(item, max_chars, max_items)
        return out
    if isinstance(value, (list, tuple)):
        items = [shorten(item, max_chars, max_items) for item in list(value)[:max_items]]
        if len(value) > max_items:
            items.append(f"…(+{len(value) - max_items} more items)")
        return items
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return shorten(repr(value), max_chars, max_items)


def _parse_args(args_json: Any) -> Any:
    if not isinstance(args_json, str):
        return shorten(args_json)
    try:
        return shorten(json.loads(args_json or "{}"))
    except ValueError:
        return shorten(args_json)


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status))


def emit_trace(event: dict[str, Any]) -> None:
    """Log one trace event as a marker-prefixed JSON line (never raises)."""
    try:
        logger.info("%s%s", TRACE_MARKER, json.dumps(event, ensure_ascii=False, default=repr))
    except Exception as exc:  # tracing must never break the app
        logger.debug("trace emit failed: %s", exc)


def _start_event(background_tool: Any, routine: Any) -> dict[str, Any]:
    return {
        "event": "tool_start",
        "id": str(getattr(background_tool, "id", "")),
        "tool": str(getattr(background_tool, "tool_name", "")),
        "idle": bool(getattr(background_tool, "is_idle_tool_call", False)),
        "args": _parse_args(getattr(routine, "args_json_str", None)),
    }


def _end_event(background_tool: Any, duration_ms: int, failure: BaseException | None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "tool_end",
        "id": str(getattr(background_tool, "id", "")),
        "tool": str(getattr(background_tool, "tool_name", "")),
        "idle": bool(getattr(background_tool, "is_idle_tool_call", False)),
        "duration_ms": duration_ms,
    }
    if failure is not None:
        event["status"] = "cancelled" if isinstance(failure, asyncio.CancelledError) else "failed"
        event["error"] = shorten(f"{type(failure).__name__}: {failure}")
        return event
    event["status"] = _status_value(getattr(background_tool, "status", "unknown"))
    error = getattr(background_tool, "error", None)
    if error is not None:
        event["error"] = shorten(error)
    else:
        event["result"] = shorten(getattr(background_tool, "result", None))
    return event


def _safe(build: Callable[[], dict[str, Any]]) -> None:
    try:
        emit_trace(build())
    except Exception as exc:  # tracing must never break the app
        logger.debug("trace event build failed: %s", exc)


def _wrap(original: RunTool) -> RunTool:
    async def _run_tool(self: BackgroundToolManager, background_tool: Any, tool_call_routine: Any) -> None:
        _safe(lambda: _start_event(background_tool, tool_call_routine))
        started = time.perf_counter()
        failure: BaseException | None = None
        try:
            await original(self, background_tool, tool_call_routine)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _safe(lambda: _end_event(background_tool, duration_ms, failure))

    setattr(_run_tool, _WRAPPED_FLAG, original)
    _run_tool.__doc__ = getattr(original, "__doc__", None)
    return _run_tool


def install_trace_hook() -> InstalledTraceHook:
    """Wrap ``BackgroundToolManager._run_tool`` to emit tool-call trace lines.

    Idempotent: installing twice keeps a single wrapper. Raises RuntimeError if
    the upstream method no longer exists.
    """
    current = getattr(BackgroundToolManager, HOOK_TARGET, None)
    if current is None:
        raise RuntimeError(f"trace hook target BackgroundToolManager.{HOOK_TARGET} no longer exists")
    original: RunTool = getattr(current, _WRAPPED_FLAG, current)
    setattr(BackgroundToolManager, HOOK_TARGET, _wrap(original))
    logger.info("Tool trace hook installed (dashboard thinking panel)")
    return InstalledTraceHook(original=original)
