"""Memory categories: who a fact is about (GBrain ``entity``) and what it is (GBrain ``kind``).

The table below is the single source of truth for the categories. GBrain stores
the ``entity``/``kind`` pair natively, so a category is never written as text:
it is recovered from those two fields on recall (see :func:`category_for`).

To add a category, append a :class:`MemoryCategory` to :data:`CATEGORIES`. The
dashboard (filter chips, add form) and the prompt sections pick it up from here.
"""

from __future__ import annotations
from dataclasses import dataclass

from jisu.persona.constants import USER_ENTITY, REACHY_ENTITY
from reachy_mini_conversation_app.memory import MemoryFact


# UI groups (the dashboard's filter chips), in display order.
GROUP_PERSONA = "persona"
GROUP_PROMISE = "promise"
GROUP_USER = "user"
GROUP_LABELS: dict[str, str] = {
    GROUP_PERSONA: "페르소나",
    GROUP_PROMISE: "약속",
    GROUP_USER: "사용자",
}


@dataclass(frozen=True)
class MemoryCategory:
    """One kind of long-term memory."""

    key: str
    entity: str
    kind: str
    group: str
    label: str
    icon: str
    # Whether the LLM may write it through the update_persona tool.
    persona_tool: bool = False


CATEGORIES: tuple[MemoryCategory, ...] = (
    MemoryCategory("persona_trait", REACHY_ENTITY, "belief", GROUP_PERSONA, "Reachy · 성격", "🤖", persona_tool=True),
    MemoryCategory(
        "persona_style", REACHY_ENTITY, "preference", GROUP_PERSONA, "Reachy · 말투", "🤖", persona_tool=True
    ),
    MemoryCategory("promise", REACHY_ENTITY, "commitment", GROUP_PROMISE, "약속", "🤝", persona_tool=True),
    MemoryCategory("user_preference", USER_ENTITY, "preference", GROUP_USER, "사용자 · 취향", "🙂"),
    MemoryCategory("user_behavior", USER_ENTITY, "event", GROUP_USER, "사용자 · 행동", "🙂"),
    MemoryCategory("user_fact", USER_ENTITY, "fact", GROUP_USER, "사용자 · 기타", "🙂"),
)

# What the upstream ``remember`` tool (and anything without a category) saves as.
DEFAULT_CATEGORY_KEY = "user_fact"
# A robot fact whose kind matches no category (e.g. written by hand with kind "fact").
FALLBACK_REACHY_CATEGORY_KEY = "persona_trait"

_BY_KEY: dict[str, MemoryCategory] = {category.key: category for category in CATEGORIES}
_BY_PAIR: dict[tuple[str, str], MemoryCategory] = {(c.entity, c.kind): c for c in CATEGORIES}


@dataclass(frozen=True)
class CategorizedFact:
    """A stored fact together with its category."""

    fact: MemoryFact
    category: MemoryCategory
    # True when the store already had this fact (nothing new was written).
    duplicate: bool = False


def get_category(key: str | None) -> MemoryCategory:
    """Return the category for ``key`` (None/empty = default); raise KeyError if unknown."""
    normalized = (key or "").strip().lower() or DEFAULT_CATEGORY_KEY
    try:
        return _BY_KEY[normalized]
    except KeyError:
        known = ", ".join(_BY_KEY)
        raise KeyError(f"unknown memory category {key!r} (known: {known})") from None


def persona_tool_categories() -> list[MemoryCategory]:
    """Return the categories the update_persona tool may write, in table order."""
    return [category for category in CATEGORIES if category.persona_tool]


def category_for(entity: str | None, kind: str | None) -> MemoryCategory:
    """Classify a recalled fact from its GBrain ``entity_slug`` and ``kind``.

    Facts saved before categories existed have no entity: they are user facts.
    """
    entity_slug = (entity or "").strip().lower()
    fact_kind = (kind or "").strip().lower()
    exact = _BY_PAIR.get((entity_slug, fact_kind))
    if exact is not None:
        return exact
    if entity_slug == REACHY_ENTITY:
        return _BY_KEY[FALLBACK_REACHY_CATEGORY_KEY]
    if not entity_slug or entity_slug == USER_ENTITY:
        user_kind = _BY_PAIR.get((USER_ENTITY, fact_kind))
        if user_kind is not None:
            return user_kind
    return _BY_KEY[DEFAULT_CATEGORY_KEY]


def matches_filter(category: MemoryCategory, selector: str | None) -> bool:
    """Return whether ``category`` passes a filter given as a category key or a group name."""
    wanted = (selector or "").strip().lower()
    return not wanted or wanted in (category.key, category.group)


def categories_json() -> dict[str, object]:
    """Describe the categories and groups for the dashboard."""
    return {
        "default": DEFAULT_CATEGORY_KEY,
        "groups": [{"key": key, "label": label} for key, label in GROUP_LABELS.items()],
        "categories": [
            {
                "key": c.key,
                "label": c.label,
                "icon": c.icon,
                "group": c.group,
                "entity": c.entity,
                "kind": c.kind,
                "personaTool": c.persona_tool,
            }
            for c in CATEGORIES
        ],
    }
