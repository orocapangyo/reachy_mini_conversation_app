"""Plain-text tables for the CLI (the dashboard renders the JSON report itself)."""

from __future__ import annotations
import unicodedata
from collections.abc import Sequence

from jisu.bench.memory.report import BenchReport, ReportSummary


def _width(text: str) -> int:
    """Terminal width: East Asian wide characters (Hangul) take two columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int, *, right: bool = False) -> str:
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Left-align the first column, right-align the rest."""
    widths = [max(_width(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(_pad(cell, widths[i], right=i > 0) for i, cell in enumerate(cells))

    return "\n".join([line(headers), "  ".join("-" * w for w in widths), *(line(row) for row in rows)])


def pct(value: float | None) -> str:
    """0.8333 -> '83.3%'; None -> '-'."""
    return "-" if value is None else f"{value * 100:.1f}%"


def ms(value: float | None) -> str:
    """Milliseconds with a sensible precision; None -> '-'."""
    if value is None:
        return "-"
    return f"{value:.2f}" if value < 10 else f"{value:.0f}"


def render_summary(report: BenchReport) -> str:
    """Accuracy and latency per subject."""
    k = report.config.get("k")
    headers = ["subject", "hit@1", f"hit@{k}", f"recall@{k}", "MRR", "FP", "search p50", "p95", "add p50", "setup"]
    rows = [
        [
            s.name,
            pct(s.scores.hit_at_1),
            pct(s.scores.hit_at_k),
            pct(s.scores.recall_at_k),
            "-" if s.scores.mrr is None else f"{s.scores.mrr:.3f}",
            pct(s.scores.false_positive_rate),
            ms(s.latency["search"].p50_ms),
            ms(s.latency["search"].p95_ms),
            ms(s.latency["add"].p50_ms),
            ms(s.setup_ms),
        ]
        for s in report.subjects
    ]
    return render_table(headers, rows) + "\n(latency in ms; FP = negative queries that returned something)"


def render_tags(report: BenchReport) -> str:
    """hit@k per tag (false-positive rate for the negative tag)."""
    tags = sorted({tag for s in report.subjects for tag in s.tags})
    headers = ["tag", "n", *(s.name for s in report.subjects)]
    rows = []
    for tag in tags:
        counts = {s.tags[tag].queries for s in report.subjects if tag in s.tags}
        cells = []
        for s in report.subjects:
            scores = s.tags.get(tag)
            if scores is None:
                cells.append("-")
            elif scores.positives:
                cells.append(pct(scores.hit_at_k))
            else:
                cells.append(f"FP {pct(scores.false_positive_rate)}")
        rows.append([tag, str(max(counts) if counts else 0), *cells])
    return render_table(headers, rows)


def render_failures(report: BenchReport) -> str:
    """Missed (or falsely answered) queries per subject."""
    blocks = []
    for s in report.subjects:
        failures = s.failures()
        lines = [f"[{s.name}] {len(failures)} / {len(s.queries)} failed"]
        for q in failures:
            got = ", ".join(q.returned) or "(none)"
            want = ", ".join(q.expected) or "(nothing)"
            note = f"  · {q.note}" if q.note else ""
            lines.append(f"  - {q.query_id} '{q.text}': want {want} | got {got}{note}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_report(report: BenchReport, *, detail: bool = False) -> str:
    """Full CLI view of a report."""
    header = (
        f"dataset {report.dataset.get('name')} ({report.dataset.get('facts')} facts, "
        f"{report.dataset.get('queries')} queries) · k={report.config.get('k')} "
        f"· repeat={report.config.get('repeat')} · {report.started_at} · {report.duration_s:.1f}s"
    )
    if report.environment.get("gbrain_version"):
        header += f"\n{report.environment['gbrain_version']}"
    parts = [header]
    if report.subjects:
        parts += ["", render_summary(report), "", render_tags(report)]
        categorized = [s for s in report.subjects if s.category_match is not None]
        if categorized:
            parts.append(
                "\ncategory round-trip: " + ", ".join(f"{s.name} {pct(s.category_match)}" for s in categorized)
            )
    for skipped in report.skipped:
        parts.append(f"\nskipped {skipped.name}: {skipped.reason}")
    if detail and report.subjects:
        parts += ["", render_failures(report)]
    return "\n".join(parts)


def render_report_list(summaries: Sequence[ReportSummary]) -> str:
    """Render saved report summaries, newest first."""
    if not summaries:
        return "(no saved reports)"
    rows = [
        [
            s.started_at,
            s.dataset,
            str(s.k),
            "  ".join(f"{name} {pct(value)}" for name, value in s.hit_at_k.items()),
            s.path.name,
        ]
        for s in summaries
    ]
    return render_table(["started", "dataset", "k", "hit@k", "file"], rows)
