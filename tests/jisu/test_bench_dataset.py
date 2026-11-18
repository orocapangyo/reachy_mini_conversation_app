"""Tests for the memory benchmark dataset loader."""

from __future__ import annotations
from typing import Any
from pathlib import Path

import pytest

from jisu.bench.memory.dataset import DatasetError, match_key, load_dataset, parse_dataset
from jisu.bench.memory.constants import DATASETS_DIR, DEFAULT_DATASET


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "title": "t",
        "facts": [{"id": "a", "category": "user_fact", "text": "사용자는 커피를 좋아한다"}],
        "queries": [{"id": "q", "text": "커피", "expected": ["a"], "tags": ["short_word"]}],
    }
    raw.update(overrides)
    return raw


def test_bundled_datasets_are_valid() -> None:
    """Every shipped dataset loads; the default one covers every category and has negatives."""
    for path in DATASETS_DIR.glob("*.toml"):
        assert load_dataset(path).queries, path.name
    dataset = load_dataset(DEFAULT_DATASET)
    assert len({fact.category for fact in dataset.facts}) == 6
    assert any(query.negative for query in dataset.queries)


def test_negative_queries_get_the_negative_tag() -> None:
    """An empty expected list marks the query negative even without the tag."""
    dataset = parse_dataset(_raw(queries=[{"id": "n", "text": "주식", "expected": []}]), name="d")
    assert dataset.queries[0].negative and dataset.queries[0].tags == ("negative",)


def test_match_key_normalizes_spacing_and_case() -> None:
    """Results are matched back to facts regardless of spacing/case."""
    assert match_key("  Lo-Fi   음악 ") == "lo-fi 음악"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"facts": []}, "facts"),
        ({"queries": []}, "queries"),
        ({"facts": [{"id": "a", "category": "diary", "text": "x"}]}, "unknown memory category"),
        (
            {
                "facts": [
                    {"id": "a", "category": "user_fact", "text": "x"},
                    {"id": "a", "category": "user_fact", "text": "y"},
                ]
            },
            "duplicate fact id",
        ),
        (
            {
                "facts": [
                    {"id": "a", "category": "user_fact", "text": "같은 문장"},
                    {"id": "b", "category": "user_fact", "text": " 같은  문장 "},
                ]
            },
            "same text",
        ),
        ({"queries": [{"id": "q", "text": "x", "expected": ["zzz"]}]}, "unknown facts"),
        ({"queries": [{"id": "q", "text": "  ", "expected": []}]}, "'text'"),
    ],
)
def test_invalid_datasets_are_rejected(overrides: dict[str, Any], message: str) -> None:
    """Problems are reported with a pointer to the bad entry."""
    with pytest.raises(DatasetError, match=message):
        parse_dataset(_raw(**overrides), name="d")


def test_load_dataset_reports_bad_toml(tmp_path: Path) -> None:
    """Unreadable TOML is a DatasetError, not a crash."""
    path = tmp_path / "bad.toml"
    path.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(path)
