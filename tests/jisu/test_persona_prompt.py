"""Tests for the sectioned memory prompt."""

from __future__ import annotations

from jisu.persona.prompt import PROMPT_SECTIONS, format_categorized_prompt
from jisu.persona.categories import CategorizedFact, get_category
from reachy_mini_conversation_app.memory import MemoryFact


def _fact(text: str, category: str, index: int = 0) -> CategorizedFact:
    return CategorizedFact(MemoryFact(id=str(index), text=text, created_at=index), get_category(category))


def test_sections_in_order_and_empty_ones_left_out() -> None:
    """Persona comes first, then promises, then the user; empty sections disappear."""
    prompt = format_categorized_prompt(
        [
            _fact("사용자는 고양이를 키운다", "user_fact"),
            _fact("나는 졸면 깨워 주기로 했다", "promise"),
            _fact("나는 존댓말을 부드럽게 쓴다", "persona_style"),
        ]
    )
    headings = [section.heading for section in PROMPT_SECTIONS]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "아래 프로필 지시문과 부딪히면 지시문을 따른다" in prompt
    only_user = format_categorized_prompt([_fact("사용자는 고양이를 키운다", "user_fact")])
    assert only_user.splitlines()[0] == headings[2]
    assert format_categorized_prompt([]) == ""


def test_section_keeps_only_newest_facts() -> None:
    """Each section is capped at its max_facts (input is newest first)."""
    cap = PROMPT_SECTIONS[1].max_facts
    facts = [_fact(f"약속 {i}", "promise", i) for i in range(cap + 3)]
    lines = [line for line in format_categorized_prompt(facts).splitlines() if line.startswith("- ")]
    assert lines == [f"- 약속 {i}" for i in range(cap)]
