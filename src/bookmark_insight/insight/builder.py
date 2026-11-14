"""Insight batch for one profile and period (§2.5 steps 3–4 and the note lifecycle).

``build_insight`` summarizes what still needs it, sends the summaries to
``llm.cluster``, validates the returned bookmark ids against the period,
renders the note and writes it atomically, keeping any ``## 대화 기록``
sections of a previous note at the same path.

When the cluster call fails (model busy, timeout) and a previous note exists
for the period, that note is returned unchanged with ``degraded=True`` and the
titles of the bookmarks it does not cover, so the conversation can go on with
yesterday's insight instead of an error. Without a previous note the error
propagates (``ClusterUnavailableError``).

Backfill (M7): the summarizer handles at most ``batch_max_summaries`` bookmarks
per call and never passes the daily cap. Whatever is still ``pending`` after
that is left out of the cluster call and listed in the note's ``pending`` front
matter with ``complete: false`` (a *partial* note). Calling ``build_insight``
again continues from the pending ones, because every summary is written per
bookmark as soon as it is made; once nothing is pending the note is complete.
"""

from __future__ import annotations
import logging
from datetime import datetime

import httpx

from bookmark_insight.llm import BatchLLM, SummaryInput
from bookmark_insight.store import note_path, read_note, repo_lock, write_note, bookmarks_in_period
from bookmark_insight.models import Bookmark, InsightNote, InsightCluster
from bookmark_insight.periods import Period
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.summarize import summarize_bookmarks
from bookmark_insight.insight.render import related_links, render_note_body, note_links_enabled
from bookmark_insight.summarize.usage import log_usage
from bookmark_insight.summarize.runner import iso_utc, local_date, resolve_now, load_daily_count
from bookmark_insight.summarize.sections import summary_text


logger = logging.getLogger(__name__)


class ClusterUnavailableError(RuntimeError):
    """The batch model could not cluster and there is no previous note to fall back on."""


def load_insight(settings: BookmarkInsightSettings, profile: str, period: Period) -> tuple[InsightNote, str] | None:
    """Return the stored note (and its rendered body) for ``profile``/``period`` if it exists."""
    path = note_path(settings, profile, period)
    if not path.is_file():
        return None
    try:
        return read_note(path)
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable insight note %s: %s", path, exc)
        return None


def cluster_inputs(bookmarks: list[Bookmark]) -> list[SummaryInput]:
    """Turn bookmarks into what the clustering step may see (summary only when ``ok``)."""
    items: list[SummaryInput] = []
    for bookmark in bookmarks:
        summary = summary_text(bookmark.body) if bookmark.summary_status == "ok" else None
        items.append(
            SummaryInput(
                id=bookmark.guid,
                title=bookmark.title,
                url=bookmark.url,
                tags=tuple(bookmark.tags),
                date=bookmark.date.isoformat(),
                summary=summary,
            )
        )
    return items


def sanitize_clusters(clusters: list[InsightCluster], valid_ids: set[str]) -> list[InsightCluster]:
    """Drop bookmark ids the model invented and clusters left empty (logged, never written)."""
    kept: list[InsightCluster] = []
    for cluster in clusters:
        ids: list[str] = []
        for bookmark_id in cluster.bookmark_ids:
            if bookmark_id in valid_ids and bookmark_id not in ids:
                ids.append(bookmark_id)
            elif bookmark_id not in valid_ids:
                logger.warning("Dropping unknown bookmark id %r from cluster %r", bookmark_id, cluster.topic)
        if not ids:
            logger.warning("Dropping cluster %r: no bookmark of the period left", cluster.topic)
            continue
        kept.append(
            InsightCluster(
                topic=cluster.topic,
                bookmark_ids=ids,
                summary=cluster.summary,
                correlation=cluster.correlation,
                question=cluster.question,
            )
        )
    return kept


def _chronological(bookmarks: list[Bookmark]) -> list[Bookmark]:
    return sorted(bookmarks, key=lambda item: (item.date, item.title))


def pending_bookmarks(bookmarks: list[Bookmark]) -> list[Bookmark]:
    """Bookmarks the batch has not summarized yet (``summary_status: pending``)."""
    return [bookmark for bookmark in bookmarks if bookmark.summary_status == "pending"]


def daily_cap_reached(settings: BookmarkInsightSettings, moment: datetime) -> bool:
    """Whether today's summary counter already sits at the daily cap (pending work waits for tomorrow)."""
    return load_daily_count(settings, local_date(moment)) >= max(0, settings.daily_max_summaries)


def build_insight(
    settings: BookmarkInsightSettings,
    llm: BatchLLM,
    profile: str,
    period: Period,
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> InsightNote:
    """Summarize (one chunk) + cluster what is summarized + write the note for one profile and period.

    An empty period returns a note with no clusters and ``path=None`` and
    writes nothing. ``client`` and ``now`` are forwarded to the summarizer.
    Bookmarks still ``pending`` after the chunk make the note partial
    (``complete=False``, ``pending`` ids, ``daily_cap_reached`` when the daily
    cap is what stopped the chunk); a later call continues from them.
    """
    moment = resolve_now(now)
    bookmarks = bookmarks_in_period(settings, profile, period)
    if not bookmarks:
        return InsightNote(profile=profile, since=period.since, until=period.until, generated_at=iso_utc(moment))

    report = summarize_bookmarks(settings, llm, bookmarks, now=moment, client=client)
    for warning in report.warnings:
        logger.warning("summarize: %s", warning)

    with repo_lock(settings):
        bookmarks = _chronological(bookmarks_in_period(settings, profile, period))
        pending = pending_bookmarks(bookmarks)
        pending_ids = {bookmark.guid for bookmark in pending}
        seen = [bookmark for bookmark in bookmarks if bookmark.guid not in pending_ids]
        existing = load_insight(settings, profile, period)
        clusters: list[InsightCluster] = []
        if seen:
            try:
                result = llm.cluster(cluster_inputs(seen))
            except Exception as exc:
                if existing is None:
                    raise ClusterUnavailableError(f"cluster model unavailable: {exc}") from exc
                previous = existing[0]
                covered = set(previous.bookmark_ids)
                previous.degraded = True
                previous.pending_titles = [bookmark.title for bookmark in bookmarks if bookmark.guid not in covered]
                logger.warning(
                    "cluster model unavailable (%s); serving the previous note with %d uncovered bookmark(s)",
                    exc,
                    len(previous.pending_titles),
                )
                return previous
            log_usage(settings, profile=profile, op="cluster", usage=result.usage)
            clusters = sanitize_clusters(result.clusters, {bookmark.guid for bookmark in seen})
        else:
            logger.warning(
                "nothing summarized yet for %s %s; writing a partial note without clusters", profile, period.key
            )
        note = InsightNote(
            profile=profile,
            since=period.since,
            until=period.until,
            generated_at=iso_utc(moment if now is not None else resolve_now(None)),
            clusters=clusters,
            unavailable=[bookmark.guid for bookmark in seen if bookmark.summary_status != "ok"],
            related=related_links(bookmarks) if note_links_enabled(settings) else [],
            bookmark_ids=[bookmark.guid for bookmark in bookmarks],
            complete=not pending,
            pending=[bookmark.guid for bookmark in pending],
            conversations=list(existing[0].conversations) if existing is not None else [],
            daily_cap_reached=bool(pending) and (report.daily_cap_reached or daily_cap_reached(settings, moment)),
        )
        if pending:
            logger.info(
                "partial insight note for %s %s: %d summarized, %d pending%s",
                profile,
                period.key,
                note.summarized_count,
                note.pending_count,
                " (daily cap reached)" if note.daily_cap_reached else "",
            )
        write_note(note, render_note_body(note, bookmarks), note_path(settings, profile, period))
    return note
