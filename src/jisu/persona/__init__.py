"""Categorized long-term memory and the GBrain-managed persona layer.

``profiles/<name>/profile.md`` keeps the fixed identity (tools, voice, greeting,
base character, rules). This package adds what grows over time, stored in GBrain
by category: the robot's own traits/style, its promises, and facts about the user.
It does not depend on the dashboard, so other features can reuse it.
"""

from jisu.persona.seed import SeedFact, SeedResult, SeedFormatError, load_seed, apply_seed
from jisu.persona.store import CategorizedMemory
from jisu.persona.prompt import format_categorized_prompt
from jisu.persona.categories import (
    CATEGORIES,
    MemoryCategory,
    CategorizedFact,
    category_for,
    get_category,
    categories_json,
)


__all__ = [
    "CATEGORIES",
    "CategorizedFact",
    "CategorizedMemory",
    "MemoryCategory",
    "SeedFact",
    "SeedFormatError",
    "SeedResult",
    "apply_seed",
    "categories_json",
    "category_for",
    "format_categorized_prompt",
    "get_category",
    "load_seed",
]
