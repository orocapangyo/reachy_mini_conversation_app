"""Contract tests for the dashboard's pluggable memory backends."""

from __future__ import annotations
import os
import json
from typing import Any
from pathlib import Path
from collections.abc import Mapping, Sequence

import pytest

from jisu.dashboard.memory import registry
from jisu.dashboard.memory.base import BackendUnavailableError, keyword_search, format_facts_for_prompt
from jisu.dashboard.memory.json_backend import JsonMemoryBackend
from jisu.dashboard.memory.gbrain_backend import (
    CommandResult,
    GBrainCallError,
    GBrainMemoryBackend,
    fact_from_wire,
    parse_json_object,
    resolve_gbrain_binary,
    ensure_brain_initialized,
)
from reachy_mini_conversation_app.memory import (
    MemoryFact,
    add_memory_fact,
    list_memory_facts,
    format_memory_for_prompt,
)


# --------------------------------------------------------------------------
# JSON backend: must behave exactly like the upstream memory module
# --------------------------------------------------------------------------


def test_json_backend_matches_upstream_store(tmp_path: Path) -> None:
    """The JSON backend reads and writes the same memory.v1.json as upstream."""
    backend = JsonMemoryBackend(tmp_path)
    first = backend.add_fact("  Likes   jazz  ")
    duplicate = backend.add_fact("likes jazz")
    add_memory_fact(tmp_path, "Prefers concise answers")

    assert first is not None and duplicate is not None
    assert duplicate.id == first.id
    assert [f.text for f in backend.list_facts()] == [f.text for f in list_memory_facts(tmp_path)]
    assert backend.format_for_prompt() == format_memory_for_prompt(tmp_path)


def test_json_backend_forget_search_clear(tmp_path: Path) -> None:
    """Forget removes by substring, search ranks by keywords, clear empties."""
    backend = JsonMemoryBackend(tmp_path)
    backend.add_fact("Likes jazz piano")
    backend.add_fact("Has a cat named Momo")

    assert [f.text for f in backend.search("cat momo", 5)] == ["Has a cat named Momo"]

    result = backend.forget_fact("jazz")
    assert result.removed is not None and result.removed.text == "Likes jazz piano"
    assert [f.text for f in backend.list_facts()] == ["Has a cat named Momo"]

    backend.clear()
    assert backend.list_facts() == []
    assert backend.format_for_prompt() == ""


def test_keyword_search_orders_by_hits_then_recency() -> None:
    """More matched words rank first; ties keep list (newest-first) order."""
    facts = [
        MemoryFact("a", "likes coffee", 3),
        MemoryFact("b", "likes green tea and coffee", 2),
        MemoryFact("c", "plays guitar", 1),
    ]
    assert [f.id for f in keyword_search(facts, "coffee tea", 10)] == ["b", "a"]
    assert keyword_search(facts, "   ", 10) == []
    assert keyword_search(facts, "coffee", 0) == []


def test_format_facts_matches_upstream_prompt(tmp_path: Path) -> None:
    """The shared prompt renderer produces the upstream text byte for byte."""
    add_memory_fact(tmp_path, "Likes jazz")
    add_memory_fact(tmp_path, "Lives in Seoul")
    assert format_facts_for_prompt(list_memory_facts(tmp_path)) == format_memory_for_prompt(tmp_path)
    assert format_facts_for_prompt([]) == ""


# --------------------------------------------------------------------------
# GBrain backend with a fake CLI
# --------------------------------------------------------------------------


class FakeGBrain:
    """In-memory stand-in for ``gbrain call <op> <json>``."""

    def __init__(self) -> None:
        """Initialize the test double."""
        self.facts: list[dict[str, Any]] = []
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail_ops: set[str] = set()
        self.next_id = 1

    def __call__(self, args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
        """Test double for ``__call__``."""
        argv = list(args)
        self.calls.append((argv, dict(env)))
        if argv[1] == "init":
            return CommandResult(0, '{"status":"ok"}', "")
        assert argv[1] == "call"
        op, params = argv[2], json.loads(argv[3])
        if op in self.fail_ops:
            return CommandResult(1, "", f"{op} exploded")
        if op == "remember":
            for fact in self.facts:
                if fact["fact"] == params["fact"]:
                    return self._ok({"id": fact["fact_id"], "status": "duplicate"})
            fact_id = str(self.next_id)
            self.next_id += 1
            self.facts.insert(
                0,
                {
                    "fact_id": fact_id,
                    "id": int(fact_id),
                    "fact": params["fact"],
                    "created_at": "2026-11-17T09:00:00+00:00",
                },
            )
            return self._ok({"id": fact_id, "status": "inserted"})
        if op == "recall":
            facts = list(self.facts)
            if "question" in params:
                words = params["question"].lower().split()
                facts = [f for f in facts if any(w in f["fact"].lower() for w in words)]
            return self._ok({"facts": facts[: params.get("limit", 60)], "total": len(facts)}, notice=True)
        if op == "forget":
            self.facts = [f for f in self.facts if f["fact_id"] != params["id"]]
            return self._ok({"id": params["id"], "status": "forgotten"})
        raise AssertionError(f"unexpected op {op}")

    @staticmethod
    def _ok(payload: dict[str, Any], notice: bool = False) -> CommandResult:
        body = json.dumps({"protocol_version": 1, **payload})
        if notice:
            body = "[AGENT] notice: keyword-only\n" + body
        return CommandResult(0, body, "")


def _gbrain(tmp_path: Path, fake: FakeGBrain, *, initialized: bool = True) -> GBrainMemoryBackend:
    home = tmp_path / "gbrain"
    binary = tmp_path / "fake-gbrain"
    binary.write_text("", encoding="utf-8")
    if initialized:
        (home / ".gbrain").mkdir(parents=True)
        (home / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
    return GBrainMemoryBackend(runner=fake, binary=str(binary), home=home, require_ready=initialized)


def test_gbrain_backend_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remember, recall, search, forget and prompt formatting through the CLI contract."""
    monkeypatch.setenv("DATABASE_URL", "postgres://shared")
    fake = FakeGBrain()
    backend = _gbrain(tmp_path, fake)

    first = backend.add_fact("  Likes   jazz ")
    second = backend.add_fact("Has a cat named Momo")
    duplicate = backend.add_fact("Likes jazz")
    assert first is not None and second is not None and duplicate is not None
    assert duplicate.id == first.id
    assert [f.text for f in backend.list_facts()] == ["Has a cat named Momo", "Likes jazz"]
    assert [f.text for f in backend.search("momo", 5)] == ["Has a cat named Momo"]
    assert "- Likes jazz" in backend.format_for_prompt()

    result = backend.forget_fact("JAZZ")
    assert result.removed is not None and result.removed.id == first.id
    assert [f.text for f in backend.list_facts()] == ["Has a cat named Momo"]

    backend.clear()
    assert backend.list_facts() == []

    _argv, env = fake.calls[-1]
    assert env["GBRAIN_HOME"] == str(tmp_path / "gbrain")
    assert "DATABASE_URL" not in env


def test_gbrain_backend_failures_do_not_raise(tmp_path: Path) -> None:
    """Conversation-facing methods log and return empty results when the CLI fails."""
    fake = FakeGBrain()
    backend = _gbrain(tmp_path, fake)
    backend.add_fact("Likes jazz")
    fake.fail_ops = {"remember", "recall", "forget"}

    assert backend.add_fact("Lives in Seoul") is None
    assert backend.list_facts() == []
    assert backend.search("jazz", 5) == []
    assert backend.format_for_prompt() == ""
    assert backend.forget_fact("jazz").removed is None
    with pytest.raises(GBrainCallError):
        backend.clear()


def test_gbrain_backend_forget_failure_keeps_fact(tmp_path: Path) -> None:
    """A failed forget reports nothing removed."""
    fake = FakeGBrain()
    backend = _gbrain(tmp_path, fake)
    backend.add_fact("Likes jazz")
    fake.fail_ops = {"forget"}
    assert backend.forget_fact("jazz").removed is None
    assert len(fake.facts) == 1


def test_gbrain_backend_requires_initialized_brain(tmp_path: Path) -> None:
    """An uninitialized brain is unavailable until initialize() runs."""
    fake = FakeGBrain()
    binary = tmp_path / "fake-gbrain"
    binary.write_text("", encoding="utf-8")
    with pytest.raises(BackendUnavailableError, match="not initialized"):
        GBrainMemoryBackend(runner=fake, binary=str(binary), home=tmp_path / "gbrain")

    backend = GBrainMemoryBackend(runner=fake, binary=str(binary), home=tmp_path / "gbrain", require_ready=False)
    assert backend.readiness_problem() is not None
    backend.initialize()
    init_argv, _env = fake.calls[-1]
    assert init_argv[1:] == [
        "init",
        "--pglite",
        "--no-embedding",
        "--search-mode",
        "conservative",
        "--non-interactive",
        "--json",
    ]


def test_gbrain_backend_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a gbrain executable the backend reports how to install it."""
    monkeypatch.setenv("JISU_GBRAIN_BIN", str(tmp_path / "does-not-exist"))
    with pytest.raises(BackendUnavailableError, match="gbrain executable not found"):
        GBrainMemoryBackend(tmp_path)


def test_parse_json_object_tolerates_notices() -> None:
    """Notice lines before/after the JSON envelope are skipped."""
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('[AGENT] hello {not json}\n{"b": [1, 2]}\ntrailer') == {"b": [1, 2]}
    assert parse_json_object("") is None
    assert parse_json_object("[1, 2]") is None


def test_fact_from_wire_variants() -> None:
    """Wire facts accept fact_id/id, ISO or numeric timestamps and skip junk."""
    iso = fact_from_wire({"fact_id": "7", "fact": " Likes  tea ", "created_at": "2026-11-17T00:00:00+00:00"})
    assert iso is not None and iso.id == "7" and iso.text == "Likes tea" and iso.created_at > 0
    numeric = fact_from_wire({"id": 9, "text": "x", "valid_from": 1_700_000_000})
    assert numeric is not None and numeric.created_at == 1_700_000_000_000
    assert fact_from_wire({"fact": "no id"}) is None
    assert fact_from_wire({"fact_id": "1", "fact": "   "}) is None
    assert fact_from_wire("nope") is None


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def test_registry_builtins_and_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Built-ins are listed, unknown names fail, and the env var picks the backend."""
    names = [info.name for info in registry.available_backends()]
    assert names[:2] == ["json", "gbrain"]
    assert isinstance(registry.create_backend("JSON", tmp_path), JsonMemoryBackend)
    with pytest.raises(KeyError):
        registry.create_backend("nope", tmp_path)
    assert registry.backend_status("nope", tmp_path)[0] is False

    monkeypatch.delenv("JISU_MEMORY_BACKEND", raising=False)
    assert registry.selected_backend_name() == "gbrain"
    monkeypatch.setenv("JISU_MEMORY_BACKEND", " GBrain ")
    assert registry.selected_backend_name() == "gbrain"


def test_registry_custom_backend(tmp_path: Path) -> None:
    """A new backend only needs a register_backend call."""
    registry.register_backend("custom-test", "Custom", JsonMemoryBackend, replace=True)
    try:
        assert registry.backend_status("custom-test", tmp_path) == (True, "ready")
        with pytest.raises(ValueError):
            registry.register_backend("custom-test", "Again", JsonMemoryBackend)
    finally:
        registry._REGISTRY.pop("custom-test", None)


def test_gbrain_binary_found_in_bun_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gbrain installed by `bun link` is found even when ~/.bun/bin is not on PATH."""
    bun_bin = tmp_path / "bun" / "bin"
    bun_bin.mkdir(parents=True)
    (bun_bin / "gbrain").write_text("", encoding="utf-8")
    monkeypatch.setenv("BUN_INSTALL", str(tmp_path / "bun"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert resolve_gbrain_binary("gbrain") == str(bun_bin / "gbrain")
    assert resolve_gbrain_binary("/abs/missing-gbrain") == "/abs/missing-gbrain"

    fake = FakeGBrain()
    home = tmp_path / "home"
    (home / ".gbrain").mkdir(parents=True)
    (home / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
    backend = GBrainMemoryBackend(runner=fake, binary=str(bun_bin / "gbrain"), home=home)
    backend.list_facts()
    _argv, env = fake.calls[-1]
    assert env["PATH"].split(os.pathsep)[0] == str(bun_bin)


def test_gbrain_error_envelope_message_is_reported(tmp_path: Path) -> None:
    """A GBrain error envelope surfaces its code and message, not just the code."""

    def refuse(args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
        envelope = {"error": "invalid_params", "code": "invalid_params", "message": "fact_withdrawn: forgotten"}
        return CommandResult(2, json.dumps(envelope), "Error [invalid_params]")

    backend = _gbrain(tmp_path, FakeGBrain())
    backend._runner = refuse
    with pytest.raises(GBrainCallError, match="invalid_params: fact_withdrawn"):
        backend.call("remember", {"fact": "x", "provenance": "y"})


def test_gbrain_search_falls_back_to_local_keywords(tmp_path: Path) -> None:
    """Korean 2-syllable words are dropped by GBrain's ranking; the local fallback still finds them."""

    class KoreanBlindGBrain(FakeGBrain):
        """Mimics GBrain dropping query words shorter than 3 characters."""

        def __call__(self, args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
            """Return no ranked facts for question recalls whose words are all short."""
            argv = list(args)
            if argv[1] == "call" and argv[2] == "recall":
                params = json.loads(argv[3])
                if "question" in params and all(len(w) < 3 for w in params["question"].split()):
                    return self._ok({"facts": [], "facts_degraded": {"reason": "no_query_terms"}})
            return super().__call__(args, env, timeout_s)

    backend = _gbrain(tmp_path, KoreanBlindGBrain())
    backend.add_fact("사용자는 매운 음식을 못 먹는다")
    backend.add_fact("사용자의 고양이 이름은 모모다")
    assert [f.text for f in backend.search("매운 음식", 5)] == ["사용자는 매운 음식을 못 먹는다"]
    assert [f.text for f in backend.search("고양이", 5)] == ["사용자의 고양이 이름은 모모다"]
    assert backend.search("없는 말", 5) == []


def test_ensure_brain_initialized(tmp_path: Path) -> None:
    """First use initializes the brain once; missing gbrain or a failed init only reports."""
    home = tmp_path / "gbrain"
    binary = tmp_path / "fake-gbrain"
    binary.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def init_runner(args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
        calls.append(list(args))
        (home / ".gbrain").mkdir(parents=True, exist_ok=True)
        (home / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
        return CommandResult(0, '{"status":"ok"}', "")

    brain = GBrainMemoryBackend(runner=init_runner, binary=str(binary), home=home, require_ready=False)
    message = ensure_brain_initialized(brain=brain)
    assert message is not None and message.startswith("INFO: GBrain initialized")
    assert ensure_brain_initialized(brain=brain) is None
    assert len(calls) == 1

    def failing_runner(args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
        return CommandResult(1, "", "boom")

    broken = GBrainMemoryBackend(runner=failing_runner, binary=str(binary), home=tmp_path / "b", require_ready=False)
    assert (ensure_brain_initialized(brain=broken) or "").startswith("ERROR:")

    missing = GBrainMemoryBackend(
        runner=failing_runner, binary=str(tmp_path / "nope"), home=tmp_path / "c", require_ready=False
    )
    assert (ensure_brain_initialized(brain=missing) or "").startswith("WARNING:")
