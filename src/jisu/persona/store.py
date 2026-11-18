"""Contract for memory stores that keep categories (GBrain does; the upstream JSON store does not)."""

from __future__ import annotations
from typing import Protocol, runtime_checkable

from jisu.persona.categories import CategorizedFact
from reachy_mini_conversation_app.memory import MemoryFact


@runtime_checkable
class CategorizedMemory(Protocol):
    """A memory store that saves and lists facts by category.

    Like the plain memory backends, these methods log and return empty/None on
    ordinary failures instead of raising, so the conversation loop survives.
    """

    name: str

    def list_categorized(self) -> list[CategorizedFact]:
        """Return stored facts with their categories, newest first."""
        ...

    def add_categorized(self, text: str, category: str, *, provenance: str | None = None) -> CategorizedFact | None:
        """Store one fact in ``category``; None if nothing was saved (raise KeyError for an unknown category)."""
        ...

    def forget_by_id(self, fact_id: str) -> MemoryFact | None:
        """Remove the fact with this id; return it, or None if no active fact had that id."""
        ...

    def search_categorized(self, query: str, limit: int) -> list[CategorizedFact]:
        """Return up to ``limit`` facts relevant to ``query``, with their categories."""
        ...
