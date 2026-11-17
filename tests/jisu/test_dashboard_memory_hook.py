"""Tests for routing the conversation app's memory call sites to a backend."""

from __future__ import annotations
import importlib
from pathlib import Path
from collections.abc import Iterator

import pytest

from jisu.dashboard.memory import registry
from jisu.dashboard.memory.base import BackendUnavailableError, format_facts_for_prompt
from jisu.dashboard.memory.hook import HOOK_TARGETS, install_memory_hook
from reachy_mini_conversation_app.memory import MemoryFact, ForgetMemoryResult


class RecordingBackend:
    """Minimal in-memory backend that records which instance path it serves."""

    name = "recording"
    instances: list[str | Path | None] = []

    def __init__(self, instance_path: str | Path | None = None) -> None:
        """Initialize the test double."""
        self.facts: list[MemoryFact] = []
        RecordingBackend.instances.append(instance_path)

    def list_facts(self) -> list[MemoryFact]:
        """Test double for ``list_facts``."""
        return list(self.facts)

    def add_fact(self, text: str) -> MemoryFact | None:
        """Test double for ``add_fact``."""
        fact = MemoryFact(id=f"r{len(self.facts)}", text=text, created_at=1)
        self.facts.insert(0, fact)
        return fact

    def forget_fact(self, query: str | None) -> ForgetMemoryResult:
        """Test double for ``forget_fact``."""
        matches = tuple(f for f in self.facts if query and query in f.text)
        if not matches:
            return ForgetMemoryResult(removed=None, candidates=())
        self.facts.remove(matches[0])
        return ForgetMemoryResult(removed=matches[0], candidates=matches)

    def clear(self) -> None:
        """Test double for ``clear``."""
        self.facts.clear()

    def search(self, query: str, limit: int) -> list[MemoryFact]:
        """Test double for ``search``."""
        return [f for f in self.facts if query in f.text][:limit]

    def format_for_prompt(self) -> str:
        """Test double for ``format_for_prompt``."""
        return format_facts_for_prompt(self.facts)


def _unavailable(_instance_path: str | Path | None) -> RecordingBackend:
    raise BackendUnavailableError("not installed")


@pytest.fixture
def test_backends() -> Iterator[None]:
    """Provide the objects under test."""
    registry.available_backends()  # make sure built-ins are registered first
    registry.register_backend("recording", "Recording", RecordingBackend, replace=True)
    registry.register_backend("broken", "Broken", _unavailable, replace=True)
    RecordingBackend.instances = []
    yield
    registry._REGISTRY.pop("recording", None)
    registry._REGISTRY.pop("broken", None)


def _current(module_name: str, attr: str) -> object:
    return getattr(importlib.import_module(module_name), attr)


def test_hook_targets_exist_upstream() -> None:
    """Every call site the hook patches still exists in the conversation app."""
    for module_name, attr in HOOK_TARGETS:
        assert callable(_current(module_name, attr)), f"{module_name}.{attr}"


def test_json_backend_leaves_call_sites_untouched() -> None:
    """Selecting json keeps upstream behavior exactly (no patching)."""
    before = {target: _current(*target) for target in HOOK_TARGETS}
    hook = install_memory_hook("json")
    assert hook.originals == {}
    assert {target: _current(*target) for target in HOOK_TARGETS} == before


def test_hook_routes_tools_and_prompt(tmp_path: Path, test_backends: None) -> None:
    """remember/forget tools and the prompt builder go through the selected backend."""
    before = {target: _current(*target) for target in HOOK_TARGETS}
    hook = install_memory_hook("recording")
    try:
        remember_mod = importlib.import_module("reachy_mini_conversation_app.tools.remember")
        forget_mod = importlib.import_module("reachy_mini_conversation_app.tools.forget")
        prompts_mod = importlib.import_module("reachy_mini_conversation_app.prompts")

        stored = remember_mod.add_memory_fact(tmp_path, "Likes jazz")
        assert stored is not None and stored.id == "r0"
        assert "- Likes jazz" in prompts_mod.format_memory_for_prompt(tmp_path)
        assert forget_mod.forget_memory_fact(tmp_path, query="jazz").removed == stored
        assert prompts_mod.format_memory_for_prompt(tmp_path) == ""
        # One backend instance per instance path, shared by all call sites.
        assert RecordingBackend.instances == [tmp_path]
        # The JSON store on disk was not touched.
        assert not (tmp_path / "memory.v1.json").exists()
    finally:
        hook.uninstall()
    assert {target: _current(*target) for target in HOOK_TARGETS} == before


def test_unavailable_backend_falls_back_to_json(tmp_path: Path, test_backends: None) -> None:
    """A backend that cannot start degrades to the JSON store instead of crashing."""
    hook = install_memory_hook("broken")
    try:
        remember_mod = importlib.import_module("reachy_mini_conversation_app.tools.remember")
        stored = remember_mod.add_memory_fact(tmp_path, "Likes tea")
        assert stored is not None
        assert (tmp_path / "memory.v1.json").exists()
    finally:
        hook.uninstall()


def test_missing_target_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """If upstream renames a call site, installing the hook raises."""
    monkeypatch.setattr(
        "jisu.dashboard.memory.hook.HOOK_TARGETS",
        (("reachy_mini_conversation_app.prompts", "no_such_function"),),
    )
    with pytest.raises(RuntimeError, match="no longer exists"):
        install_memory_hook("recording")
