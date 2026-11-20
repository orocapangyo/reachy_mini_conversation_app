"""Benchmark dataset: facts to store and queries with their expected facts (TOML).

```toml
schema_version = 1
title = "..."

[[facts]]
id = "dog_name"
category = "user_fact"        # a jisu.persona category key
text = "사용자의 강아지 이름은 모찌다"

[[queries]]
id = "dog_short"
text = "강아지 이름"
expected = ["dog_name"]        # [] = negative query: nothing should come back
tags = ["multi_word"]
```
"""

from __future__ import annotations
import tomllib
from typing import Any
from pathlib import Path
from dataclasses import dataclass

from jisu.persona.categories import get_category
from jisu.bench.memory.constants import NEGATIVE_TAG, DATASET_SCHEMA_VERSION
from reachy_mini_conversation_app.memory import normalize_memory_text


class DatasetError(ValueError):
    """Raised when a dataset file is invalid."""


@dataclass(frozen=True)
class BenchFact:
    """One fact the benchmark stores."""

    id: str
    category: str
    text: str


@dataclass(frozen=True)
class BenchQuery:
    """One search and the facts it should find."""

    id: str
    text: str
    expected: tuple[str, ...]
    tags: tuple[str, ...]

    @property
    def negative(self) -> bool:
        """True when no fact should be returned."""
        return not self.expected


@dataclass(frozen=True)
class Dataset:
    """A loaded dataset."""

    name: str
    title: str
    facts: tuple[BenchFact, ...]
    queries: tuple[BenchQuery, ...]
    path: Path | None = None

    def fact_id_for_text(self) -> dict[str, str]:
        """Map normalized, lowercased fact text to fact id (how search results are matched back)."""
        return {match_key(fact.text): fact.id for fact in self.facts}


def match_key(text: str) -> str:
    """Normalize text the way backends store it, for matching results to dataset facts."""
    return normalize_memory_text(text).lower()


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DatasetError(f"{where} must be a list of strings")
    return tuple(item.strip() for item in value)


def _require_str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{where}: '{key}' must be a non-empty string")
    return value.strip()


def parse_dataset(raw: dict[str, Any], *, name: str, path: Path | None = None) -> Dataset:
    """Validate a parsed TOML document and build a :class:`Dataset`."""
    if raw.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise DatasetError(f"{name}: schema_version must be {DATASET_SCHEMA_VERSION}")
    raw_facts, raw_queries = raw.get("facts"), raw.get("queries")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise DatasetError(f"{name}: needs at least one [[facts]] entry")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise DatasetError(f"{name}: needs at least one [[queries]] entry")

    facts: list[BenchFact] = []
    seen_ids: set[str] = set()
    seen_texts: dict[str, str] = {}
    for index, entry in enumerate(raw_facts):
        where = f"{name}: facts[{index}]"
        if not isinstance(entry, dict):
            raise DatasetError(f"{where} must be a table")
        fact_id = _require_str(entry, "id", where)
        text = _require_str(entry, "text", where)
        category = _require_str(entry, "category", where).lower()
        try:
            get_category(category)
        except KeyError as exc:
            raise DatasetError(f"{where}: {exc.args[0]}") from exc
        if fact_id in seen_ids:
            raise DatasetError(f"{where}: duplicate fact id {fact_id!r}")
        key = match_key(text)
        if key in seen_texts:
            raise DatasetError(f"{where}: same text as fact {seen_texts[key]!r}")
        seen_ids.add(fact_id)
        seen_texts[key] = fact_id
        facts.append(BenchFact(id=fact_id, category=category, text=text))

    queries: list[BenchQuery] = []
    seen_queries: set[str] = set()
    for index, entry in enumerate(raw_queries):
        where = f"{name}: queries[{index}]"
        if not isinstance(entry, dict):
            raise DatasetError(f"{where} must be a table")
        query_id = _require_str(entry, "id", where)
        if query_id in seen_queries:
            raise DatasetError(f"{where}: duplicate query id {query_id!r}")
        seen_queries.add(query_id)
        expected = _string_list(entry.get("expected", []), f"{where}.expected")
        unknown = [fact_id for fact_id in expected if fact_id not in seen_ids]
        if unknown:
            raise DatasetError(f"{where}: expected refers to unknown facts {unknown}")
        tags = _string_list(entry.get("tags", []), f"{where}.tags")
        if not expected and NEGATIVE_TAG not in tags:
            tags = (*tags, NEGATIVE_TAG)
        queries.append(BenchQuery(id=query_id, text=_require_str(entry, "text", where), expected=expected, tags=tags))

    title = raw.get("title")
    return Dataset(
        name=name,
        title=title if isinstance(title, str) and title.strip() else name,
        facts=tuple(facts),
        queries=tuple(queries),
        path=path,
    )


def load_dataset(path: Path) -> Dataset:
    """Read and validate a dataset file; its name is the file stem."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DatasetError(f"{path}: {exc}") from exc
    return parse_dataset(raw, name=Path(path).stem, path=Path(path))
