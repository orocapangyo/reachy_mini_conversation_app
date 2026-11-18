"""Tests for the jisu-owned update_persona tool (loaded as an external tool)."""

from __future__ import annotations
import asyncio
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock
from collections.abc import Iterator

import pytest
from gbrain_fakes import FakeGBrain, make_gbrain

from jisu.tools import TOOLS_DIR
from jisu.tools import update_persona as tool_module
from jisu.persona.constants import PERSONA_TOOL_MAX_WRITES, PERSONA_TOOL_PROVENANCE
from jisu.dashboard.memory.json_backend import JsonMemoryBackend
from reachy_mini_conversation_app.config import list_tool_module_names
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


@pytest.fixture(autouse=True)
def _fresh_budget() -> Iterator[None]:
    tool_module.reset_write_budget()
    yield
    tool_module.reset_write_budget()


def _deps(tmp_path: Path) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), instance_path=tmp_path)


def _call(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(tool_module.UpdatePersona()(_deps(tmp_path), **kwargs))


def test_tools_dir_exposes_only_tool_files() -> None:
    """The external-tool loader sees exactly the update_persona tool here."""
    assert list_tool_module_names(TOOLS_DIR) == ["update_persona"]
    spec = tool_module.UpdatePersona().spec()
    assert spec["name"] == "update_persona"
    assert spec["parameters"]["properties"]["category"]["enum"] == ["persona_trait", "persona_style", "promise"]


def test_saves_persona_fact_in_gbrain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid call stores the fact under the robot entity with the tool provenance."""
    fake = FakeGBrain()
    backend = make_gbrain(tmp_path, fake)
    monkeypatch.setattr(tool_module, "active_backend", lambda _path: backend)

    result = _call(tmp_path, category="persona_style", text="나는 대답을 두 문장 이내로 한다")
    assert result == {"saved": "나는 대답을 두 문장 이내로 한다", "category": "persona_style", "memory_id": "1"}
    stored = fake.facts[0]
    assert (stored["entity_slug"], stored["kind"], stored["provenance"]) == (
        "reachy-mini",
        "preference",
        PERSONA_TOOL_PROVENANCE,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"category": "user_fact", "text": "x"}, "category must be one of"),
        ({"category": "promise", "text": "   "}, "non-empty"),
        ({"category": "promise", "text": "가" * 500}, "at most"),
    ],
)
def test_invalid_arguments_return_errors(tmp_path: Path, kwargs: dict[str, Any], message: str) -> None:
    """Bad arguments come back as {"error": ...}, never as an exception."""
    assert message in _call(tmp_path, **kwargs)["error"]


def test_json_backend_has_no_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the JSON fallback the tool explains instead of saving a plain fact."""
    monkeypatch.setattr(tool_module, "active_backend", lambda path: JsonMemoryBackend(path))
    assert "switch to gbrain" in _call(tmp_path, category="promise", text="나는 A")["error"]
    assert not (tmp_path / "memory.v1.json").exists()


def test_write_budget_and_failed_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed saves do not use up the budget; successful ones stop at the limit."""
    fake = FakeGBrain()
    backend = make_gbrain(tmp_path, fake)
    monkeypatch.setattr(tool_module, "active_backend", lambda _path: backend)

    fake.fail_ops = {"remember"}
    assert "nothing was saved" in _call(tmp_path, category="promise", text="나는 실패")["error"]
    fake.fail_ops = set()

    for index in range(PERSONA_TOOL_MAX_WRITES):
        assert "saved" in _call(tmp_path, category="persona_trait", text=f"나는 특징 {index}")
    assert "limit reached" in _call(tmp_path, category="persona_trait", text="나는 하나 더")["error"]


def test_already_known_fact_does_not_use_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating a known persona fact reports it and keeps the write budget."""
    backend = make_gbrain(tmp_path, FakeGBrain())
    monkeypatch.setattr(tool_module, "active_backend", lambda _path: backend)
    assert "already_known" not in _call(tmp_path, category="promise", text="나는 A")
    for _ in range(PERSONA_TOOL_MAX_WRITES + 2):
        assert _call(tmp_path, category="promise", text="나는 A")["already_known"] is True
    assert "saved" in _call(tmp_path, category="promise", text="나는 B")
