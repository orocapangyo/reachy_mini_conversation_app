"""Summarize run: candidate selection, caps, fetch, LLM call and bookmark write (§2.5 steps 1–2).

One run processes the bookmarks it is given, in order, under ``repo_lock``:

1. skip ``ok`` (cached), archived, and ``fetch_failed`` still in back-off;
2. mark excluded-URL bookmarks ``fetch_failed`` without any network call;
3. for the rest, within the per-run and per-day caps, fetch → summarize →
   write via ``modify_bookmark``; failures never abort the run. A model failure
   (503, timeout) leaves the bookmark ``pending`` with ``summary_error`` set and
   the run moves on; ``MAX_CONSECUTIVE_LLM_FAILURES`` in a row stop the run.

``_sleep`` is module-level so tests can replace it; the daily counter lives in
``<state_dir>/summarize_counter.json`` so an interrupted batch resumes safely.
The per-run cap, the daily cap and the call interval come from
``BookmarkInsightSettings`` (``batch_max_summaries``, ``daily_max_summaries``,
``call_interval_s``); the report flags which cap stopped the run so the
insight builder can tell a partial note from a complete one (M7).
"""

from __future__ import annotations
import json
import time
import logging
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlsplit

import httpx

from bookmark_insight.llm import BatchLLM
from bookmark_insight.store import repo_lock, modify_bookmark, atomic_write_text
from bookmark_insight.models import FETCH_FAILED_SUMMARY_TEXT, Bookmark
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.summarize import (
    RETRY_BACKOFF_DAYS,
    MAX_CONSECUTIVE_LLM_FAILURES,
    SummarizeReport,
)
from bookmark_insight.summarize.fetch import FetchResult, fetch_url, client_scope
from bookmark_insight.summarize.usage import log_usage
from bookmark_insight.summarize.sections import has_real_summary, replace_summary_section


logger = logging.getLogger(__name__)

COUNTER_FILENAME = "summarize_counter.json"
SUMMARY_ERROR_MAX_CHARS = 200

_sleep = time.sleep  # monkeypatched in tests


class _LLMCallError(RuntimeError):
    """The batch model failed for one bookmark (503, timeout, quota); the run records it and continues."""


# --- time helpers -------------------------------------------------------------------


def resolve_now(now: datetime | None) -> datetime:
    """Return an aware UTC ``datetime`` (``None`` = real time; naive input = local time)."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    return now.astimezone(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """Format like ``store.utc_now_iso`` (second precision, ``Z`` suffix)."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_date(moment: datetime) -> date:
    """Local calendar date of ``moment`` (front matter ``summarized_at`` and the daily counter)."""
    return moment.astimezone().date()


def parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO timestamp (``Z`` allowed); naive values count as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


# --- candidate selection -------------------------------------------------------------


def skip_reason(bookmark: Bookmark, now: datetime) -> str | None:
    """Return why ``bookmark`` is not processed this run, or ``None`` when it is a candidate."""
    if bookmark.archived:
        return "archived"
    if bookmark.path is None:
        return "not loaded from disk"
    if bookmark.summary_status == "ok":
        return "already summarized"
    if bookmark.summary_status == "fetch_failed":
        attempts = bookmark.fetch_attempts
        if attempts >= len(RETRY_BACKOFF_DAYS):
            return f"gave up after {attempts} attempts"
        last = parse_iso_utc(bookmark.last_fetch_at)
        if last is not None and attempts >= 1:
            due = last + _backoff_delta(attempts)
            if due > now:
                return f"retry back-off until {iso_utc(due)}"
    return None


def _backoff_delta(attempts: int) -> timedelta:
    return timedelta(days=RETRY_BACKOFF_DAYS[min(attempts, len(RETRY_BACKOFF_DAYS)) - 1])


def is_excluded_url(url: str, patterns: tuple[str, ...]) -> bool:
    """Substring match of any pattern against the URL host or the full URL (§2.3 제외 URL 패턴)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    lowered = url.lower()
    return any(needle and (needle in host or needle in lowered) for needle in (p.strip().lower() for p in patterns))


# --- daily counter ------------------------------------------------------------------------


def counter_path(settings: BookmarkInsightSettings) -> Path:
    """Path of the persisted daily counter."""
    return settings.state_dir / COUNTER_FILENAME


def load_daily_count(settings: BookmarkInsightSettings, today: date) -> int:
    """Return today's summary count (0 when the file is missing, unreadable or from another day)."""
    path = counter_path(settings)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("date") != today.isoformat():
        return 0
    try:
        return max(0, int(data.get("count", 0)))
    except (TypeError, ValueError):
        return 0


def save_daily_count(settings: BookmarkInsightSettings, today: date, count: int) -> None:
    """Persist today's summary count."""
    atomic_write_text(counter_path(settings), json.dumps({"date": today.isoformat(), "count": count}) + "\n")


# --- bookmark mutations ----------------------------------------------------------------


def _apply_success(bookmark: Bookmark, summary: str, now: datetime) -> Bookmark:
    bookmark.body = replace_summary_section(bookmark.body, summary)
    bookmark.summary_status = "ok"
    bookmark.summarized_at = local_date(now).isoformat()
    bookmark.fetch_attempts += 1
    bookmark.last_fetch_at = iso_utc(now)
    bookmark.summary_error = None
    return bookmark


def _apply_llm_failure(bookmark: Bookmark, reason: str) -> Bookmark:
    """Keep the bookmark ``pending`` (the page was fine) and remember why the model call failed."""
    bookmark.summary_error = reason[:SUMMARY_ERROR_MAX_CHARS]
    return bookmark


def _apply_failure(bookmark: Bookmark, now: datetime) -> Bookmark:
    if not has_real_summary(bookmark.body):
        bookmark.body = replace_summary_section(bookmark.body, FETCH_FAILED_SUMMARY_TEXT)
    bookmark.summary_status = "fetch_failed"
    bookmark.fetch_attempts += 1
    bookmark.last_fetch_at = iso_utc(now)
    return bookmark


def _mark_failed(bookmark: Bookmark, now: datetime, report: SummarizeReport, reason: str) -> None:
    logger.info("Bookmark %s fetch failed: %s", bookmark.guid, reason)
    modify_bookmark(Path(bookmark.path), lambda fresh: _apply_failure(fresh, now))
    report.failed.append(bookmark.guid)


# --- the run -------------------------------------------------------------------------------


class _Run:
    """Mutable state of one summarize run."""

    def __init__(
        self,
        settings: BookmarkInsightSettings,
        llm: BatchLLM,
        now: datetime,
        client: httpx.Client | None,
    ) -> None:
        """Capture the run inputs and start an empty report."""
        self.settings = settings
        self.llm = llm
        self.now = now
        self.client = client
        self.report = SummarizeReport()
        self.today = local_date(now)
        self.daily_count = load_daily_count(settings, self.today)
        self.daily_max = max(0, settings.daily_max_summaries)
        self.call_interval_s = max(0.0, float(settings.call_interval_s))
        self.calls_this_run = 0
        self.consecutive_llm_failures = 0

    def warn(self, message: str) -> None:
        """Record a warning in the report and the log."""
        logger.warning("%s", message)
        self.report.warnings.append(message)

    def summarize_text(self, bookmark: Bookmark, text: str) -> str:
        """Call the model (respecting the call interval) and log its usage."""
        if self.calls_this_run > 0:
            _sleep(self.call_interval_s)
        clipped = text[: self.settings.body_max_chars]
        try:
            result = self.llm.summarize(title=bookmark.title, url=bookmark.url, text=clipped)
        except Exception as exc:
            self.calls_this_run += 1  # the interval still applies after a failed call
            raise _LLMCallError(f"LLM summarize failed for {bookmark.guid}: {exc}") from exc
        self.calls_this_run += 1
        self.daily_count += 1
        save_daily_count(self.settings, self.today, self.daily_count)
        log_usage(
            self.settings, profile=bookmark.profile, op="summarize", usage=result.usage, bookmark_id=bookmark.guid
        )
        return result.text.strip()

    def process(self, bookmark: Bookmark, http: httpx.Client) -> None:
        """Fetch, summarize and write one bookmark; a bad page only marks it ``fetch_failed``."""
        fetched: FetchResult = fetch_url(bookmark.url, client=http)
        if not fetched.ok:
            if fetched.status_code == 429:
                wait = f"{fetched.retry_after_s:g}s" if fetched.retry_after_s is not None else "unspecified"
                self.warn(f"429 from {bookmark.url} (Retry-After: {wait})")
            _mark_failed(bookmark, self.now, self.report, fetched.reason or "fetch failed")
            return
        summary = self.summarize_text(bookmark, fetched.text)
        if not summary:
            self.warn(f"LLM returned an empty summary for {bookmark.guid}; left pending")
            self.report.skipped.append(bookmark.guid)
            return
        modify_bookmark(Path(bookmark.path), lambda fresh: _apply_success(fresh, summary, self.now))
        self.report.summarized.append(bookmark.guid)

    def execute(self, bookmarks: list[Bookmark]) -> SummarizeReport:
        """Run the whole pipeline over ``bookmarks``."""
        candidates: list[Bookmark] = []
        for bookmark in bookmarks:
            reason = skip_reason(bookmark, self.now)
            if reason is None:
                candidates.append(bookmark)
            else:
                logger.debug("Skipping bookmark %s: %s", bookmark.guid, reason)
                self.report.skipped.append(bookmark.guid)

        fetchable: list[Bookmark] = []
        for bookmark in candidates:
            if is_excluded_url(bookmark.url, self.settings.exclude_url_patterns):
                _mark_failed(bookmark, self.now, self.report, "url matches an exclude pattern")
            else:
                fetchable.append(bookmark)

        cap = max(0, self.settings.batch_max_summaries)
        queue, overflow = fetchable[:cap], fetchable[cap:]
        if overflow:
            self.report.batch_cap_reached = True
            self.warn(f"batch cap {cap} reached; {len(overflow)} bookmark(s) left for a later run")
            self.report.skipped.extend(item.guid for item in overflow)

        with client_scope(self.client) as http:
            for index, bookmark in enumerate(queue):
                if self.daily_count >= self.daily_max:
                    rest = queue[index:]
                    self.report.daily_cap_reached = True
                    self.warn(f"daily cap {self.daily_max} reached; {len(rest)} bookmark(s) left for tomorrow")
                    self.report.skipped.extend(item.guid for item in rest)
                    break
                try:
                    self.process(bookmark, http)
                    self.consecutive_llm_failures = 0
                except _LLMCallError as exc:
                    reason = str(exc)
                    self.consecutive_llm_failures += 1
                    self.warn(reason)
                    self.report.llm_failed.append(bookmark.guid)
                    modify_bookmark(Path(bookmark.path), lambda fresh: _apply_llm_failure(fresh, reason))
                    if self.consecutive_llm_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                        rest = queue[index + 1 :]
                        self.warn(
                            f"{self.consecutive_llm_failures} model failures in a row; "
                            f"abandoning {len(rest)} remaining bookmark(s)"
                        )
                        self.report.skipped.extend(item.guid for item in rest)
                        break
                except Exception as exc:  # one bookmark must never kill the batch (§4 안정성)
                    self.warn(f"bookmark {bookmark.guid} failed unexpectedly: {exc}")
                    self.report.skipped.append(bookmark.guid)
        return self.report


def summarize_bookmarks(
    settings: BookmarkInsightSettings,
    llm: BatchLLM,
    bookmarks: list[Bookmark],
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> SummarizeReport:
    """Fetch and summarize every bookmark that still needs it (one locked run; never raises per bookmark).

    ``client`` lets tests inject an ``httpx.Client`` built on ``httpx.MockTransport``;
    ``now`` pins the clock for back-off and ``summarized_at``.
    """
    moment = resolve_now(now)
    with repo_lock(settings):
        return _Run(settings, llm, moment, client).execute(list(bookmarks))
