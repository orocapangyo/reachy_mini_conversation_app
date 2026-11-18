"""Tests for running the memory benchmark (JSON for real, GBrain through the fake CLI)."""

from __future__ import annotations
from pathlib import Path
from collections.abc import Callable

import pytest
from gbrain_fakes import FakeGBrain

from jisu.bench.memory.runner import BenchEvent, BenchConfig, run_memory_bench
from jisu.dashboard.memory.base import BackendUnavailableError
from jisu.dashboard.memory.gbrain_backend import GBrainMemoryBackend


DATASET = """
schema_version = 1
title = "mini"

[[facts]]
id = "dog"
category = "user_fact"
text = "사용자의 강아지 이름은 모찌다"

[[facts]]
id = "joke"
category = "persona_trait"
text = "나는 짧은 농담을 좋아한다"

[[queries]]
id = "dog_q"
text = "강아지"
expected = ["dog"]
tags = ["short_word"]

[[queries]]
id = "joke_q"
text = "농담"
expected = ["joke"]
tags = ["short_word", "persona"]

[[queries]]
id = "para_q"
text = "반려견"
expected = ["dog"]
tags = ["paraphrase"]

[[queries]]
id = "none_q"
text = "비밀번호"
expected = []
"""


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """Write the mini dataset."""
    path = tmp_path / "mini.toml"
    path.write_text(DATASET, encoding="utf-8")
    return path


def _fake_factory(fakes: list[FakeGBrain], *, rank_questions: bool = True) -> Callable[[Path], GBrainMemoryBackend]:
    def build(home: Path) -> GBrainMemoryBackend:
        fake = FakeGBrain()
        fake.rank_questions = rank_questions
        fakes.append(fake)
        binary = home.parent / "fake-gbrain"
        home.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("", encoding="utf-8")
        brain = GBrainMemoryBackend(runner=fake, binary=str(binary), home=home, require_ready=False)
        brain.initialize()
        return brain

    return build


def test_json_subject_scores(dataset: Path, tmp_path: Path) -> None:
    """Keyword search finds both literal queries, misses the paraphrase, no false positive."""
    events: list[BenchEvent] = []
    report = run_memory_bench(
        BenchConfig(dataset=dataset, subjects=("json",), k=3, repeat=2),
        on_event=events.append,
        workdir=tmp_path / "work",
    )
    [json_result] = report.subjects
    assert json_result.facts_added == 2 and json_result.category_match is None
    assert json_result.scores.hit_at_k == pytest.approx(2 / 3)
    assert json_result.scores.false_positive_rate == 0.0
    assert json_result.tags["paraphrase"].hit_at_k == 0.0
    assert json_result.latency["search"].count == 4 * 2  # every query, repeat times
    assert [q.query_id for q in json_result.failures()] == ["para_q"]
    assert report.dataset == {"name": "mini", "title": "mini", "path": str(dataset), "facts": 2, "queries": 4}

    kinds = [event.kind for event in events]
    assert kinds[:2] == ["subject_start", "subject_ready"] and kinds[-1] == "subject_done"
    assert kinds.count("fact_added") == 2 and kinds.count("query_done") == 4


def test_gbrain_fallback_vs_native(dataset: Path, tmp_path: Path) -> None:
    """When GBrain ranks nothing, the app path recovers through keywords; native scores 0."""
    fakes: list[FakeGBrain] = []
    report = run_memory_bench(
        BenchConfig(dataset=dataset, subjects=("gbrain", "gbrain-native"), k=3, repeat=1),
        workdir=tmp_path / "work",
        gbrain_factory=_fake_factory(fakes, rank_questions=False),
    )
    app_path, native = report.subject("gbrain"), report.subject("gbrain-native")
    assert app_path is not None and native is not None
    assert len(fakes) == 2  # one isolated brain per subject
    assert app_path.scores.hit_at_k == pytest.approx(2 / 3)
    assert native.scores.hit_at_k == 0.0
    assert app_path.category_match == 1.0
    assert report.environment["gbrain_version"] == "gbrain 0.0.0-fake"
    # Facts went in with their category's entity/kind.
    stored = {fact["fact"]: (fact["entity_slug"], fact["kind"]) for fact in fakes[0].facts}
    assert stored["나는 짧은 농담을 좋아한다"] == ("reachy-mini", "belief")


def test_unavailable_gbrain_is_skipped(dataset: Path, tmp_path: Path) -> None:
    """A missing gbrain skips its subjects with the reason; json still runs."""

    def unavailable(_home: Path) -> GBrainMemoryBackend:
        raise BackendUnavailableError("gbrain executable not found")

    events: list[BenchEvent] = []
    report = run_memory_bench(
        BenchConfig(dataset=dataset, subjects=("json", "gbrain"), repeat=1),
        on_event=events.append,
        workdir=tmp_path / "work",
        gbrain_factory=unavailable,
    )
    assert [s.name for s in report.subjects] == ["json"]
    assert [(s.name, s.reason) for s in report.skipped] == [("gbrain", "gbrain executable not found")]
    assert any(e.kind == "subject_skipped" and e.subject == "gbrain" for e in events)
    assert report.environment["gbrain_version"] is None


def test_config_validation(dataset: Path) -> None:
    """Nonsense settings are rejected up front."""
    with pytest.raises(ValueError):
        BenchConfig(dataset=dataset, k=0)
    with pytest.raises(ValueError):
        BenchConfig(dataset=dataset, repeat=0)
    with pytest.raises(ValueError):
        BenchConfig(dataset=dataset, subjects=())


def test_gbrain_init_errors_are_shortened() -> None:
    """A GBrain JSON error envelope becomes one readable line."""
    from jisu.bench.memory.subjects import _short_gbrain_error

    envelope = 'gbrain init failed (exit 1): {"error": "local_conflict", "message": "not a real directory"}\nmore'
    assert _short_gbrain_error(envelope) == "gbrain init failed (local_conflict): not a real directory"
    assert _short_gbrain_error("boom\nsecond line") == "boom"
