"""Benchmark report: the JSON contract shared by the CLI and (later) the dashboard.

Top-level keys of a saved report (``schema_version`` 1)::

    schema_version, started_at (ISO 8601), duration_s,
    dataset {name, title, path, facts, queries},
    config {k, repeat, subjects},
    environment {python, platform, gbrain_version},
    subjects [ {name, categorized, setup_ms, facts_added, facts_failed, category_match,
                scores {...}, tags {tag: scores}, latency {add|search|prompt: {...}},
                queries [ {query_id, text, tags, expected, returned, rank, hit_at_1, hit_at_k,
                           recall_at_k, false_positive, latency_ms, note} ]} ],
    skipped [ {name, reason} ]
"""

from __future__ import annotations
import json
from typing import Any
from pathlib import Path
from datetime import datetime
from dataclasses import field, asdict, dataclass

from jisu.bench.memory.metrics import Scores, Latency, QueryResult
from jisu.bench.memory.constants import REPORT_TIME_FORMAT, REPORT_SCHEMA_VERSION, default_results_dir


class ReportError(ValueError):
    """Raised when a report file cannot be read."""


@dataclass
class SubjectResult:
    """Everything measured for one subject."""

    name: str
    categorized: bool
    setup_ms: float
    facts_added: int
    facts_failed: int
    # Share of stored facts whose category read back unchanged (None if the store has no categories).
    category_match: float | None
    scores: Scores
    tags: dict[str, Scores]
    latency: dict[str, Latency]
    queries: list[QueryResult]

    def failures(self) -> list[QueryResult]:
        """Return queries that missed (positive) or returned something (negative)."""
        return [q for q in self.queries if not q.passed]


@dataclass(frozen=True)
class SkippedSubject:
    """A subject that could not run, and why."""

    name: str
    reason: str


@dataclass
class BenchReport:
    """One benchmark run."""

    started_at: str
    duration_s: float
    dataset: dict[str, Any]
    config: dict[str, Any]
    environment: dict[str, Any]
    subjects: list[SubjectResult] = field(default_factory=list)
    skipped: list[SkippedSubject] = field(default_factory=list)
    schema_version: int = REPORT_SCHEMA_VERSION

    def subject(self, name: str) -> SubjectResult | None:
        """Return the result for ``name`` if it ran."""
        return next((s for s in self.subjects if s.name == name), None)

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable report."""
        return asdict(self)


# -- JSON -> objects ----------------------------------------------------------------


def _scores(raw: dict[str, Any]) -> Scores:
    return Scores(**raw)


def _query(raw: dict[str, Any]) -> QueryResult:
    return QueryResult(
        **{**raw, "tags": tuple(raw["tags"]), "expected": tuple(raw["expected"]), "returned": tuple(raw["returned"])}
    )


def _subject(raw: dict[str, Any]) -> SubjectResult:
    return SubjectResult(
        name=raw["name"],
        categorized=raw["categorized"],
        setup_ms=raw["setup_ms"],
        facts_added=raw["facts_added"],
        facts_failed=raw["facts_failed"],
        category_match=raw["category_match"],
        scores=_scores(raw["scores"]),
        tags={tag: _scores(value) for tag, value in raw["tags"].items()},
        latency={key: Latency(**value) for key, value in raw["latency"].items()},
        queries=[_query(item) for item in raw["queries"]],
    )


def report_from_json(raw: dict[str, Any]) -> BenchReport:
    """Rebuild a report from its JSON form (raises ReportError)."""
    if raw.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportError(f"unsupported report schema_version {raw.get('schema_version')!r}")
    try:
        return BenchReport(
            started_at=raw["started_at"],
            duration_s=raw["duration_s"],
            dataset=raw["dataset"],
            config=raw["config"],
            environment=raw["environment"],
            subjects=[_subject(item) for item in raw["subjects"]],
            skipped=[SkippedSubject(**item) for item in raw["skipped"]],
        )
    except (KeyError, TypeError) as exc:
        raise ReportError(f"malformed report: {exc}") from exc


# -- files -------------------------------------------------------------------------


def report_filename(report: BenchReport) -> str:
    """``<YYYYmmdd-HHMMSS>-<dataset>.json`` from the run's start time."""
    started = datetime.fromisoformat(report.started_at)
    return f"{started.strftime(REPORT_TIME_FORMAT)}-{report.dataset.get('name', 'dataset')}.json"


def save_report(report: BenchReport, directory: Path | None = None) -> Path:
    """Write the report as JSON and return its path."""
    target_dir = directory or default_results_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / report_filename(report)
    path.write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_report(path: Path) -> BenchReport:
    """Read a saved report (raises ReportError)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReportError(f"{path}: not a report object")
    return report_from_json(raw)


@dataclass(frozen=True)
class ReportSummary:
    """One line of the saved-reports list (what a dashboard list would show)."""

    path: Path
    started_at: str
    dataset: str
    k: int
    # Subject name -> hit@k (None if it had no positive queries).
    hit_at_k: dict[str, float | None]
    skipped: tuple[str, ...]


def list_reports(directory: Path | None = None) -> list[ReportSummary]:
    """Return saved report summaries, newest first (unreadable files are left out)."""
    source = directory or default_results_dir()
    if not source.is_dir():
        return []
    summaries: list[ReportSummary] = []
    for path in source.glob("*.json"):
        try:
            report = load_report(path)
        except ReportError:
            continue
        summaries.append(
            ReportSummary(
                path=path,
                started_at=report.started_at,
                dataset=str(report.dataset.get("name", "")),
                k=int(report.config.get("k", 0)),
                hit_at_k={s.name: s.scores.hit_at_k for s in report.subjects},
                skipped=tuple(s.name for s in report.skipped),
            )
        )
    return sorted(summaries, key=lambda item: item.started_at, reverse=True)
