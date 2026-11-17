"""Memory backend contract shared by every long-term memory implementation."""

from __future__ import annotations
from typing import Protocol, runtime_checkable
from collections.abc import Sequence

from reachy_mini_conversation_app.memory import MemoryFact, ForgetMemoryResult, normalize_memory_text


PROMPT_HEADER_LINES = (
    "Things you remember about the user (use this context naturally,",
    "do not recite the list verbatim):",
)


class BackendUnavailableError(RuntimeError):
    """Raised when a backend cannot be used (missing binary, not initialized, ...)."""


@runtime_checkable
class MemoryBackend(Protocol):
    """Long-term memory store used by the remember/forget tools and the prompt.

    Implementations must never raise from the conversation-facing methods for
    ordinary failures (log and return an empty/None result instead), so a broken
    store cannot take the realtime conversation loop down.
    """

    name: str

    def list_facts(self) -> list[MemoryFact]:
        """Return stored facts, newest first."""
        ...

    def add_fact(self, text: str) -> MemoryFact | None:
        """Store one fact and return it (or the existing duplicate); None if nothing was saved."""
        ...

    def forget_fact(self, query: str | None) -> ForgetMemoryResult:
        """Remove the first fact whose text contains ``query`` (case-insensitive)."""
        ...

    def clear(self) -> None:
        """Remove every stored fact."""
        ...

    def search(self, query: str, limit: int) -> list[MemoryFact]:
        """Return up to ``limit`` facts relevant to ``query``."""
        ...

    def format_for_prompt(self) -> str:
        """Return the prompt fragment injected before the session instructions."""
        ...


def format_facts_for_prompt(facts: Sequence[MemoryFact]) -> str:
    """Render facts exactly like ``memory.format_memory_for_prompt`` does."""
    if not facts:
        return ""
    bullets = "\n".join(f"- {fact.text}" for fact in facts)
    return "\n".join([*PROMPT_HEADER_LINES, bullets])


def substring_candidates(facts: Sequence[MemoryFact], query: str | None) -> tuple[MemoryFact, ...]:
    """Return facts whose text contains the normalized query, preserving order."""
    normalized_query = normalize_memory_text(query or "").lower()
    if not normalized_query:
        return ()
    return tuple(fact for fact in facts if normalized_query in fact.text.lower())


def keyword_search(facts: Sequence[MemoryFact], query: str, limit: int) -> list[MemoryFact]:
    """Rank facts by how many query words they contain (simple local fallback search)."""
    words = [word for word in normalize_memory_text(query).lower().split() if word]
    if not words or limit <= 0:
        return []
    scored: list[tuple[int, int, MemoryFact]] = []
    for index, fact in enumerate(facts):
        lowered = fact.text.lower()
        score = sum(1 for word in words if word in lowered)
        if score > 0:
            scored.append((-score, index, fact))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [fact for _score, _index, fact in scored[:limit]]
