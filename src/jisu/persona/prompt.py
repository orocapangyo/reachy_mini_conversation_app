"""Render categorized memory as prompt sections placed before the profile instructions.

``prompts.get_session_instructions()`` puts this text in front of the active
profile's instructions, so every heading reminds the model that the profile's
rules win when they conflict with what it remembers.
"""

from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Sequence

from jisu.persona.constants import (
    PROMPT_MAX_USER_FACTS,
    PROMPT_MAX_PERSONA_FACTS,
    PROMPT_MAX_PROMISE_FACTS,
)
from jisu.persona.categories import GROUP_USER, GROUP_PERSONA, GROUP_PROMISE, CategorizedFact


@dataclass(frozen=True)
class PromptSection:
    """One block of the memory prompt, filled from one category group."""

    group: str
    heading: str
    max_facts: int


PROMPT_SECTIONS: tuple[PromptSection, ...] = (
    PromptSection(
        GROUP_PERSONA,
        "[나(Reachy)에 대해 기억하는 것: 사용자와 지내며 쌓인 경험이다. "
        "아래 프로필 지시문과 부딪히면 지시문을 따른다]",
        PROMPT_MAX_PERSONA_FACTS,
    ),
    PromptSection(
        GROUP_PROMISE,
        "[사용자와 한 약속: 상황이 오면 먼저 지킨다]",
        PROMPT_MAX_PROMISE_FACTS,
    ),
    PromptSection(
        GROUP_USER,
        "[사용자에 대해 기억하는 것: 자연스럽게 활용하고 목록을 그대로 읽지 않는다]",
        PROMPT_MAX_USER_FACTS,
    ),
)


def format_categorized_prompt(facts: Sequence[CategorizedFact]) -> str:
    """Return the sectioned memory prompt (empty sections are left out; "" when no facts).

    ``facts`` are expected newest first; each section keeps its newest ``max_facts``.
    """
    blocks: list[str] = []
    for section in PROMPT_SECTIONS:
        lines = [f"- {item.fact.text}" for item in facts if item.category.group == section.group]
        if lines:
            blocks.append("\n".join([section.heading, *lines[: section.max_facts]]))
    return "\n\n".join(blocks)
