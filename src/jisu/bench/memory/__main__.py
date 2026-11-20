"""Memory retrieval benchmark CLI.

python -m jisu.bench.memory                         # json, gbrain, gbrain-native; k=5; repeat=3; saves the report
python -m jisu.bench.memory --subjects json gbrain --k 3 --repeat 5
python -m jisu.bench.memory --dataset my.toml --no-save --detail
python -m jisu.bench.memory --list                  # saved reports
python -m jisu.bench.memory --show <file>           # print a saved report again
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path
from collections.abc import Sequence

from jisu.bench.memory.table import render_report, render_report_list
from jisu.bench.memory.report import ReportError, load_report, save_report, list_reports
from jisu.bench.memory.runner import BenchEvent, BenchConfig, run_memory_bench
from jisu.bench.memory.dataset import DatasetError
from jisu.bench.memory.constants import DEFAULT_K, SUBJECT_NAMES, DEFAULT_REPEAT, DEFAULT_DATASET, default_results_dir


EXIT_OK = 0
EXIT_NOTHING_RAN = 2


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jisu.bench.memory",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="dataset TOML")
    parser.add_argument("--subjects", nargs="+", choices=SUBJECT_NAMES, default=list(SUBJECT_NAMES))
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="results considered per query")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help="search passes per query (latency)")
    parser.add_argument("--out", type=Path, default=None, help=f"report folder (default {default_results_dir()})")
    parser.add_argument("--no-save", action="store_true", help="do not write the report file")
    parser.add_argument("--detail", action="store_true", help="list every failed query")
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    parser.add_argument("--list", action="store_true", help="list saved reports and exit")
    parser.add_argument("--show", type=Path, metavar="FILE", help="print a saved report and exit")
    return parser.parse_args(argv)


def _progress(event: BenchEvent) -> None:
    if event.kind == "subject_start":
        print(f"▶ {event.subject}", flush=True)
    elif event.kind in ("subject_ready", "subject_skipped"):
        mark = "  ready" if event.kind == "subject_ready" else "  skipped"
        print(f"{mark}: {event.message}", flush=True)
    elif event.kind in ("fact_added", "query_done") and event.done == event.total:
        label = "facts" if event.kind == "fact_added" else "queries"
        print(f"  {label} {event.done}/{event.total}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)
    if args.list:
        print(render_report_list(list_reports(args.out)))
        return EXIT_OK
    if args.show:
        try:
            print(render_report(load_report(args.show), detail=args.detail))
        except ReportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_NOTHING_RAN
        return EXIT_OK

    try:
        config = BenchConfig(dataset=args.dataset, subjects=tuple(args.subjects), k=args.k, repeat=args.repeat)
        report = run_memory_bench(config, on_event=None if args.quiet else _progress)
    except (DatasetError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOTHING_RAN

    print()
    print(render_report(report, detail=args.detail))
    if not report.subjects:
        return EXIT_NOTHING_RAN
    if not args.no_save:
        print(f"\nsaved {save_report(report, args.out)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
