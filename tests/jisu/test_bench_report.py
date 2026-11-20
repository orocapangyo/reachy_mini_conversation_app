"""Tests for saving, loading and listing benchmark reports."""

from __future__ import annotations
import json
from pathlib import Path

import pytest

from jisu.bench.memory.report import BenchReport, ReportError, load_report, save_report, list_reports, report_filename
from jisu.bench.memory.runner import BenchConfig, run_memory_bench


DATASET = """
schema_version = 1
[[facts]]
id = "a"
category = "user_fact"
text = "사용자는 커피를 좋아한다"
[[queries]]
id = "q"
text = "커피"
expected = ["a"]
"""


def _report(tmp_path: Path, started_at: str | None = None) -> BenchReport:
    dataset = tmp_path / "tiny.toml"
    dataset.write_text(DATASET, encoding="utf-8")
    report = run_memory_bench(BenchConfig(dataset=dataset, subjects=("json",), repeat=1), workdir=tmp_path / "w")
    if started_at:
        report.started_at = started_at
    return report


def test_round_trip(tmp_path: Path) -> None:
    """A saved report loads back equal to the original."""
    report = _report(tmp_path, "2026-11-19T10:00:00+09:00")
    path = save_report(report, tmp_path / "out")
    assert path.name == "20261119-100000-tiny.json" == report_filename(report)
    assert load_report(path) == report


def test_list_reports_newest_first(tmp_path: Path) -> None:
    """Listing sorts by start time and skips files that are not reports."""
    out = tmp_path / "out"
    save_report(_report(tmp_path, "2026-11-18T09:00:00+09:00"), out)
    save_report(_report(tmp_path, "2026-11-19T09:00:00+09:00"), out)
    (out / "junk.json").write_text("[]", encoding="utf-8")
    summaries = list_reports(out)
    assert [s.started_at[:10] for s in summaries] == ["2026-11-19", "2026-11-18"]
    assert summaries[0].hit_at_k == {"json": 1.0} and summaries[0].dataset == "tiny"
    assert list_reports(tmp_path / "missing") == []


def test_rejects_other_schema_versions(tmp_path: Path) -> None:
    """A report from another schema version is refused, not misread."""
    path = save_report(_report(tmp_path), tmp_path / "out")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReportError, match="schema_version"):
        load_report(path)
    with pytest.raises(ReportError):
        load_report(tmp_path / "nope.json")
