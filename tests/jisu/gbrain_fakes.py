"""Shared test doubles: an in-memory stand-in for the ``gbrain`` CLI."""

from __future__ import annotations
import json
from typing import Any
from pathlib import Path
from collections.abc import Mapping, Sequence

from jisu.dashboard.memory.gbrain_backend import CommandResult, GBrainMemoryBackend


class FakeGBrain:
    """In-memory stand-in for ``gbrain call <op> <json>``."""

    def __init__(self) -> None:
        """Initialize the test double."""
        self.facts: list[dict[str, Any]] = []
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail_ops: set[str] = set()
        self.next_id = 1
        # False mimics GBrain's keyword mode dropping short (Korean) query words.
        self.rank_questions = True

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
                    "kind": params.get("kind", "fact"),
                    "entity_slug": params.get("entity"),
                    "provenance": params.get("provenance"),
                    "created_at": "2026-11-17T09:00:00+00:00",
                },
            )
            return self._ok({"id": fact_id, "status": "inserted", "entity_slug": params.get("entity")})
        if op == "recall":
            facts = list(self.facts)
            if "entity" in params:
                facts = [f for f in facts if f["entity_slug"] == params["entity"]]
            if "question" in params and not self.rank_questions:
                facts = []
            elif "question" in params:
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


def make_gbrain(tmp_path: Path, fake: FakeGBrain, *, initialized: bool = True) -> GBrainMemoryBackend:
    """Return a GBrain backend wired to ``fake`` under ``tmp_path`` (optionally initialized)."""
    home = tmp_path / "gbrain"
    binary = tmp_path / "fake-gbrain"
    binary.write_text("", encoding="utf-8")
    if initialized:
        (home / ".gbrain").mkdir(parents=True)
        (home / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
    return GBrainMemoryBackend(runner=fake, binary=str(binary), home=home, require_ready=initialized)
