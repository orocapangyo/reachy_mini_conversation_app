"""Tests for the memory category table and its GBrain entity/kind mapping."""

from __future__ import annotations

import pytest

from jisu.persona.constants import USER_ENTITY, REACHY_ENTITY
from jisu.persona.categories import (
    CATEGORIES,
    GROUP_LABELS,
    DEFAULT_CATEGORY_KEY,
    category_for,
    get_category,
    matches_filter,
    categories_json,
    persona_tool_categories,
)


def test_category_table_is_consistent() -> None:
    """Keys and (entity, kind) pairs are unique, and every group has a label."""
    assert len({c.key for c in CATEGORIES}) == len(CATEGORIES)
    assert len({(c.entity, c.kind) for c in CATEGORIES}) == len(CATEGORIES)
    assert {c.group for c in CATEGORIES} <= set(GROUP_LABELS)
    assert {c.entity for c in CATEGORIES} == {REACHY_ENTITY, USER_ENTITY}


def test_get_category_default_and_unknown() -> None:
    """Empty means the default user fact; unknown keys raise KeyError."""
    assert get_category(None).key == DEFAULT_CATEGORY_KEY
    assert get_category("  Persona_Trait ").key == "persona_trait"
    with pytest.raises(KeyError):
        get_category("diary")


@pytest.mark.parametrize(
    ("entity", "kind", "expected"),
    [
        ("reachy-mini", "belief", "persona_trait"),
        ("reachy-mini", "preference", "persona_style"),
        ("reachy-mini", "commitment", "promise"),
        ("reachy-mini", "fact", "persona_trait"),  # robot fact with no matching kind
        ("user", "event", "user_behavior"),
        (None, "preference", "user_preference"),  # saved without an entity
        (None, None, "user_fact"),  # legacy fact
        ("someone-else", "belief", "user_fact"),
    ],
)
def test_category_for_recalled_facts(entity: str | None, kind: str | None, expected: str) -> None:
    """Recalled facts are classified from entity_slug + kind."""
    assert category_for(entity, kind).key == expected


def test_filters_and_tool_categories() -> None:
    """Filters accept a category key or a group; only robot categories are tool-writable."""
    trait = get_category("persona_trait")
    assert matches_filter(trait, None) and matches_filter(trait, "persona") and matches_filter(trait, "persona_trait")
    assert not matches_filter(trait, "user")
    assert [c.key for c in persona_tool_categories()] == ["persona_trait", "persona_style", "promise"]
    assert all(c.entity == REACHY_ENTITY for c in persona_tool_categories())


def test_categories_json_shape() -> None:
    """The dashboard gets every category and group in table order."""
    data = categories_json()
    assert data["default"] == DEFAULT_CATEGORY_KEY
    assert [c["key"] for c in data["categories"]] == [c.key for c in CATEGORIES]  # type: ignore[index, union-attr]
    assert [g["key"] for g in data["groups"]] == list(GROUP_LABELS)  # type: ignore[index, union-attr]
