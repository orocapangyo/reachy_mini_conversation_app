"""Memory retrieval benchmark: how well each memory store finds Korean facts, and how fast.

Public API (used by the CLI now and meant for the dashboard later)::

    from jisu.bench.memory import BenchConfig, run_memory_bench, save_report, list_reports, load_report

    report = run_memory_bench(BenchConfig(subjects=("json", "gbrain")), on_event=print)
    path = save_report(report)
"""

from jisu.bench.memory.report import (
    BenchReport,
    ReportError,
    ReportSummary,
    SubjectResult,
    SkippedSubject,
    load_report,
    save_report,
    list_reports,
)
from jisu.bench.memory.runner import BenchEvent, BenchConfig, run_memory_bench
from jisu.bench.memory.dataset import Dataset, BenchFact, BenchQuery, DatasetError, load_dataset


__all__ = [
    "BenchConfig",
    "BenchEvent",
    "BenchFact",
    "BenchQuery",
    "BenchReport",
    "Dataset",
    "DatasetError",
    "ReportError",
    "ReportSummary",
    "SkippedSubject",
    "SubjectResult",
    "list_reports",
    "load_dataset",
    "load_report",
    "run_memory_bench",
    "save_report",
]
