"""Run the memory retrieval benchmark.

:func:`run_memory_bench` is the entry point for every front end: the CLI today,
the dashboard later (run it in a worker thread and relay :class:`BenchEvent`s,
the way the dashboard relays logs).
"""

from __future__ import annotations
import sys
import time
import platform
import tempfile
from typing import TypeVar
from pathlib import Path
from datetime import datetime
from dataclasses import field, asdict, dataclass
from collections.abc import Callable

from jisu.bench.memory.report import BenchReport, SubjectResult, SkippedSubject
from jisu.bench.memory.dataset import Dataset, BenchQuery, match_key, load_dataset
from jisu.bench.memory.metrics import (
    QueryResult,
    aggregate,
    score_query,
    scores_by_tag,
    summarize_latency,
)
from jisu.bench.memory.subjects import Subject, GBrainFactory, GBrainSubject, build_subject, create_gbrain
from jisu.dashboard.memory.base import BackendUnavailableError
from jisu.bench.memory.constants import DEFAULT_K, SUBJECT_NAMES, DEFAULT_REPEAT, DEFAULT_DATASET


@dataclass(frozen=True)
class BenchConfig:
    """What to run."""

    dataset: Path = DEFAULT_DATASET
    subjects: tuple[str, ...] = SUBJECT_NAMES
    k: int = DEFAULT_K
    # Search passes per query; accuracy uses the first, latency uses all of them.
    repeat: int = DEFAULT_REPEAT

    def __post_init__(self) -> None:
        """Reject values that would make the numbers meaningless."""
        if self.k < 1:
            raise ValueError("k must be at least 1")
        if self.repeat < 1:
            raise ValueError("repeat must be at least 1")
        if not self.subjects:
            raise ValueError("at least one subject is required")


@dataclass(frozen=True)
class BenchEvent:
    """Progress notification.

    ``kind`` is one of: ``subject_start``, ``subject_ready``, ``fact_added``, ``query_done``,
    ``subject_done``, ``subject_skipped``. ``done``/``total`` count facts or queries.
    """

    kind: str
    subject: str
    done: int = 0
    total: int = 0
    message: str = ""


EventHandler = Callable[[BenchEvent], None]


@dataclass
class _Samples:
    add: list[float] = field(default_factory=list)
    search: list[float] = field(default_factory=list)
    prompt: list[float] = field(default_factory=list)


T = TypeVar("T")


def _timed_ms(action: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    value = action()
    return value, (time.perf_counter() - started) * 1000.0


def _run_query(subject: Subject, dataset_ids: dict[str, str], query: BenchQuery, k: int) -> QueryResult:
    outcome, elapsed = _timed_ms(lambda: subject.search(query.text, k))
    returned = [dataset_ids.get(match_key(text), f"?{text}") for text in outcome.texts]
    return score_query(query, returned, k, latency_ms=elapsed, note=outcome.note)


def _measure_subject(subject: Subject, dataset: Dataset, config: BenchConfig, emit: EventHandler) -> SubjectResult:
    samples = _Samples()
    added = failed = 0
    for index, fact in enumerate(dataset.facts, start=1):
        started = time.perf_counter()
        ok = subject.add(fact)
        samples.add.append((time.perf_counter() - started) * 1000.0)
        if ok:
            added += 1
        else:
            failed += 1
        emit(BenchEvent("fact_added", subject.name, index, len(dataset.facts)))

    category_match: float | None = None
    if subject.categorized:
        stored = subject.stored_categories()
        expected = {match_key(fact.text): fact.category for fact in dataset.facts}
        present = [key for key in expected if key in stored]
        category_match = sum(stored[key] == expected[key] for key in present) / len(present) if present else None

    dataset_ids = dataset.fact_id_for_text()
    results: list[QueryResult] = []
    for index, query in enumerate(dataset.queries, start=1):
        result = _run_query(subject, dataset_ids, query, config.k)
        results.append(result)
        samples.search.append(result.latency_ms)
        for _ in range(config.repeat - 1):
            samples.search.append(_run_query(subject, dataset_ids, query, config.k).latency_ms)
        emit(BenchEvent("query_done", subject.name, index, len(dataset.queries)))

    for _ in range(config.repeat):
        samples.prompt.append(_timed_ms(subject.format_prompt)[1])

    return SubjectResult(
        name=subject.name,
        categorized=subject.categorized,
        setup_ms=subject.setup_ms,
        facts_added=added,
        facts_failed=failed,
        category_match=category_match,
        scores=aggregate(results),
        tags=scores_by_tag(results),
        latency={
            "add": summarize_latency(samples.add),
            "search": summarize_latency(samples.search),
            "prompt": summarize_latency(samples.prompt),
        },
        queries=results,
    )


def _environment(subjects: list[Subject]) -> dict[str, object]:
    gbrain_version = next((s.backend.version() for s in subjects if isinstance(s, GBrainSubject)), "")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gbrain_version": gbrain_version or None,
    }


def run_memory_bench(
    config: BenchConfig,
    *,
    on_event: EventHandler | None = None,
    workdir: Path | None = None,
    gbrain_factory: GBrainFactory = create_gbrain,
) -> BenchReport:
    """Run every subject on the dataset and return the report (not saved).

    Each subject gets a fresh store under ``workdir`` (a temporary directory,
    removed afterwards, when not given). A subject that cannot start is recorded
    in ``report.skipped``; the others still run. Raises
    :class:`~jisu.bench.memory.dataset.DatasetError` for a bad dataset.
    """
    emit: EventHandler = on_event or (lambda _event: None)
    dataset = load_dataset(config.dataset)
    started_at = datetime.now().astimezone()
    clock = time.perf_counter()
    results: list[SubjectResult] = []
    skipped: list[SkippedSubject] = []
    ran: list[Subject] = []

    with tempfile.TemporaryDirectory(prefix="jisu-memory-bench-") as tmp:
        # GBrain refuses content roots under a symlink (macOS /var -> /private/var).
        root = (workdir or Path(tmp)).resolve()
        for name in config.subjects:
            emit(BenchEvent("subject_start", name))
            try:
                subject = build_subject(name, root / name, gbrain_factory)
            except (KeyError, BackendUnavailableError) as exc:
                reason = str(exc.args[0]) if exc.args else str(exc)
                skipped.append(SkippedSubject(name, reason))
                emit(BenchEvent("subject_skipped", name, message=reason))
                continue
            emit(BenchEvent("subject_ready", name, message=f"setup {subject.setup_ms:.0f} ms"))
            ran.append(subject)
            results.append(_measure_subject(subject, dataset, config, emit))
            emit(BenchEvent("subject_done", name))
        environment = _environment(ran)

    return BenchReport(
        started_at=started_at.isoformat(timespec="seconds"),
        duration_s=round(time.perf_counter() - clock, 3),
        dataset={
            "name": dataset.name,
            "title": dataset.title,
            "path": str(dataset.path) if dataset.path else None,
            "facts": len(dataset.facts),
            "queries": len(dataset.queries),
        },
        config={**asdict(config), "dataset": str(config.dataset), "subjects": list(config.subjects)},
        environment=environment,
        subjects=results,
        skipped=skipped,
    )
