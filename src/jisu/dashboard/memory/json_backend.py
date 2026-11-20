"""Default backend: the conversation app's own ``memory.v1.json`` store, unchanged.

The upstream functions are bound at import time. The memory hook replaces the
names at the call sites (prompts / tools), never in ``memory`` itself, so these
stay the real implementation and the JSON backend behaves exactly like upstream.
"""

from __future__ import annotations
from pathlib import Path

from jisu.dashboard.memory.base import keyword_search
from reachy_mini_conversation_app.memory import (
    MemoryFact,
    ForgetMemoryResult,
    add_memory_fact,
    list_memory_facts,
    clear_memory_facts,
    forget_memory_fact,
    format_memory_for_prompt,
)


class JsonMemoryBackend:
    """Delegates to ``reachy_mini_conversation_app.memory`` (JSON file per instance)."""

    name = "json"

    def __init__(self, instance_path: str | Path | None = None) -> None:
        """Bind the backend to one app instance directory (None = XDG data dir)."""
        self._instance_path = instance_path

    def list_facts(self) -> list[MemoryFact]:
        """Return stored facts, newest first."""
        return list_memory_facts(self._instance_path)

    def add_fact(self, text: str) -> MemoryFact | None:
        """Store one fact, deduplicating exact case-insensitive matches."""
        return add_memory_fact(self._instance_path, text)

    def forget_fact(self, query: str | None) -> ForgetMemoryResult:
        """Remove a fact by case-insensitive substring query."""
        return forget_memory_fact(self._instance_path, query=query)

    def clear(self) -> None:
        """Remove all stored facts."""
        clear_memory_facts(self._instance_path)

    def search(self, query: str, limit: int) -> list[MemoryFact]:
        """Keyword search over the stored facts."""
        return keyword_search(self.list_facts(), query, limit)

    def format_for_prompt(self) -> str:
        """Return the upstream prompt fragment."""
        return format_memory_for_prompt(self._instance_path)
