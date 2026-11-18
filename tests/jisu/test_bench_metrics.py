"""Tests for retrieval and latency metrics."""

from __future__ import annotations

import pytest

from jisu.bench.memory.dataset import BenchQuery
from jisu.bench.memory.metrics import aggregate, percentile, score_query, scores_by_tag, summarize_latency


def _q(expected: tuple[str, ...], tags: tuple[str, ...] = ("t",)) -> BenchQuery:
    return BenchQuery(id="q", text="x", expected=expected, tags=tags)


def test_score_query_positive() -> None:
    """Rank, hit@1/k and recall@k follow their definitions, cut at k."""
    result = score_query(_q(("a", "b")), ["x", "a", "y", "b"], 3, latency_ms=1.0)
    assert result.rank == 2
    assert (result.hit_at_1, result.hit_at_k) == (False, True)
    assert result.recall_at_k == pytest.approx(0.5)  # b is at rank 4 > k
    assert result.returned == ("x", "a", "y") and result.passed


def test_score_query_negative() -> None:
    """Negative queries only score false positives."""
    assert score_query(_q(()), [], 5, latency_ms=0).passed
    hit = score_query(_q(()), ["a"], 5, latency_ms=0)
    assert hit.false_positive and not hit.passed and not hit.hit_at_k


def test_aggregate_and_tags() -> None:
    """Hit metrics use positive queries, FP rate uses negative ones."""
    results = [
        score_query(_q(("a",), ("x",)), ["a"], 5, latency_ms=0),  # rank 1
        score_query(_q(("a",), ("x", "y")), ["b", "a"], 5, latency_ms=0),  # rank 2
        score_query(_q(("a",), ("y",)), [], 5, latency_ms=0),  # miss
        score_query(_q((), ("negative",)), ["a"], 5, latency_ms=0),  # false positive
    ]
    scores = aggregate(results)
    assert (scores.queries, scores.positives, scores.negatives) == (4, 3, 1)
    assert scores.hit_at_1 == pytest.approx(1 / 3)
    assert scores.hit_at_k == pytest.approx(2 / 3)
    assert scores.mrr == pytest.approx((1 + 0.5 + 0) / 3)
    assert scores.false_positive_rate == 1.0
    by_tag = scores_by_tag(results)
    assert list(by_tag) == ["negative", "x", "y"]
    assert by_tag["y"].hit_at_k == pytest.approx(0.5)
    assert by_tag["negative"].hit_at_k is None


def test_percentiles_and_latency() -> None:
    """Linear interpolation between ranks; empty input gives None."""
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([10.0, 0.0, 20.0], 95) == pytest.approx(19.0)
    latency = summarize_latency([1.0, 3.0])
    assert (latency.count, latency.mean_ms, latency.p50_ms) == (2, 2.0, 2.0)
