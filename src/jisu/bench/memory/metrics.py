"""Retrieval and latency metrics.

For a positive query (expected facts E, returned ids R in rank order, cut at k):

- ``hit@1``: R[0] is in E
- ``hit@k``: any of R is in E
- ``recall@k``: |R ∩ E| / |E|
- ``MRR``: mean of 1 / (rank of the first expected fact), 0 when none is returned

For a negative query (E empty) the only score is ``false_positive``: R is not empty.
Aggregates use positive queries for hit/recall/MRR and negative ones for the false-positive rate.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from collections.abc import Sequence

from jisu.bench.memory.dataset import BenchQuery
from jisu.bench.memory.constants import LATENCY_PERCENTILES


@dataclass(frozen=True)
class QueryResult:
    """One query's outcome for one subject."""

    query_id: str
    text: str
    tags: tuple[str, ...]
    expected: tuple[str, ...]
    # Dataset fact ids in rank order; a result that matches no dataset fact is "?<text>".
    returned: tuple[str, ...]
    rank: int | None
    hit_at_1: bool
    hit_at_k: bool
    recall_at_k: float
    false_positive: bool
    latency_ms: float
    note: str = ""

    @property
    def negative(self) -> bool:
        """True when nothing should have been returned."""
        return not self.expected

    @property
    def passed(self) -> bool:
        """Hit within k for positive queries, nothing returned for negative ones."""
        return not self.false_positive if self.negative else self.hit_at_k


def score_query(
    query: BenchQuery, returned: Sequence[str], k: int, *, latency_ms: float, note: str = ""
) -> QueryResult:
    """Score one query from the dataset ids a subject returned."""
    top = tuple(returned[:k])
    expected = set(query.expected)
    rank = next((index + 1 for index, fact_id in enumerate(top) if fact_id in expected), None)
    found = expected.intersection(top)
    return QueryResult(
        query_id=query.id,
        text=query.text,
        tags=query.tags,
        expected=query.expected,
        returned=top,
        rank=rank,
        hit_at_1=bool(top) and top[0] in expected,
        hit_at_k=rank is not None,
        recall_at_k=len(found) / len(expected) if expected else 0.0,
        false_positive=query.negative and bool(top),
        latency_ms=latency_ms,
        note=note,
    )


@dataclass(frozen=True)
class Scores:
    """Aggregated retrieval quality (rates are 0..1; None when there were no such queries)."""

    queries: int
    positives: int
    negatives: int
    hit_at_1: float | None
    hit_at_k: float | None
    recall_at_k: float | None
    mrr: float | None
    false_positive_rate: float | None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(results: Sequence[QueryResult]) -> Scores:
    """Aggregate query results."""
    positives = [r for r in results if not r.negative]
    negatives = [r for r in results if r.negative]
    return Scores(
        queries=len(results),
        positives=len(positives),
        negatives=len(negatives),
        hit_at_1=_mean([float(r.hit_at_1) for r in positives]),
        hit_at_k=_mean([float(r.hit_at_k) for r in positives]),
        recall_at_k=_mean([r.recall_at_k for r in positives]),
        mrr=_mean([1.0 / r.rank if r.rank else 0.0 for r in positives]),
        false_positive_rate=_mean([float(r.false_positive) for r in negatives]),
    )


def scores_by_tag(results: Sequence[QueryResult]) -> dict[str, Scores]:
    """Aggregate per tag (a query counts for each of its tags), tags sorted by name."""
    tags = sorted({tag for result in results for tag in result.tags})
    return {tag: aggregate([r for r in results if tag in r.tags]) for tag in tags}


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolated percentile (``pct`` in 0..100); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(frozen=True)
class Latency:
    """Latency summary in milliseconds."""

    count: int
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None


def summarize_latency(samples_ms: Sequence[float]) -> Latency:
    """Summarize latency samples (percentiles from :data:`LATENCY_PERCENTILES`)."""
    p50, p95 = (percentile(samples_ms, pct) for pct in LATENCY_PERCENTILES)
    return Latency(count=len(samples_ms), mean_ms=_mean(list(samples_ms)), p50_ms=p50, p95_ms=p95)
