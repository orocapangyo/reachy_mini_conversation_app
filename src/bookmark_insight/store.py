r"""Repository I/O shared by every track (§2.4 쓰기 안전, §2.5 note lifecycle).

Rules implemented here so tracks do not re-implement them:

- every write is temp file + ``os.replace`` (atomic), UTF-8, ``\\n`` newlines;
- ``PermissionError`` (Obsidian holding the file on Windows) is retried;
- ``modify_bookmark`` is the only sanctioned read-modify-write path: it
  re-reads when the mtime changed under it, so a user's edit is never clobbered;
- ``repo_lock`` serializes sync / summarizer / insight batch on one repository;
- directory listings are NFC-normalized before comparison (macOS NFD vs NFC).
"""

from __future__ import annotations
import os
import json
import time
import logging
import tempfile
import threading
import unicodedata
from typing import Any, Callable, Iterator
from pathlib import Path
from datetime import date, datetime, timezone
from contextlib import contextmanager

from bookmark_insight.models import (
    CONVERSATION_HEADING_PREFIX,
    Bookmark,
    InsightNote,
    InsightCluster,
)
from bookmark_insight.periods import Period
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.frontmatter import BlockList, join_frontmatter, split_frontmatter


logger = logging.getLogger(__name__)

WRITE_RETRIES = 5
WRITE_RETRY_DELAY_S = 0.2
LOCK_TIMEOUT_S = 60.0
LOCK_STALE_S = 600.0
LOCK_FILENAME = "lock"

_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_LOCK_DEPTH: dict[str, int] = {}  # re-entrancy depth per repo (guarded by that repo's RLock)


def nfc(text: str) -> str:
    """NFC-normalize a path component or title."""
    return unicodedata.normalize("NFC", text)


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp with ``Z`` suffix, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- atomic file writes ------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\r\n", "\n")
    last_error: OSError | None = None
    for attempt in range(WRITE_RETRIES):
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:  # Windows: file open elsewhere
            last_error = exc
            time.sleep(WRITE_RETRY_DELAY_S * (attempt + 1))
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
    assert last_error is not None
    raise last_error


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON object as a line (usage log, conversation log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- repository lock -----------------------------------------------------------


def _process_lock(repo: Path) -> threading.RLock:
    key = str(repo.resolve())
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


@contextmanager
def repo_lock(
    settings: BookmarkInsightSettings, timeout_s: float = LOCK_TIMEOUT_S, process_timeout_s: float | None = None
) -> Iterator[None]:
    """Serialize writers on one repository (in-process RLock + cross-process lock file).

    Re-entrant: a caller that already holds the lock (same thread) can nest
    ``repo_lock`` freely; only the outermost level touches the lock file, so a
    batch can wrap sync + summarize + insight in one critical section.
    ``timeout_s`` bounds the wait for another *process*; ``process_timeout_s``
    (default: wait forever) bounds the wait for another *thread* of this
    process, so an interactive caller can give up fast with ``TimeoutError``.
    """
    repo = settings.require_repo()
    key = str(repo.resolve())
    lock_path = settings.state_dir / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    plock = _process_lock(repo)
    if not plock.acquire(timeout=-1 if process_timeout_s is None else process_timeout_s):
        raise TimeoutError(f"bookmark_insight repository is busy in another thread of this process: {repo}")
    try:
        if _LOCK_DEPTH.get(key, 0) > 0:
            _LOCK_DEPTH[key] += 1
            try:
                yield
            finally:
                _LOCK_DEPTH[key] -= 1
            return
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()} {utc_now_iso()}\n")
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > LOCK_STALE_S:
                    logger.warning("Removing stale bookmark_insight lock (%.0fs old): %s", age, lock_path)
                    lock_path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"bookmark_insight repository is locked: {lock_path}")
                time.sleep(0.1)
        _LOCK_DEPTH[key] = 1
        try:
            yield
        finally:
            _LOCK_DEPTH[key] = 0
            lock_path.unlink(missing_ok=True)
    finally:
        plock.release()


# --- bookmarks ---------------------------------------------------------------------


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def bookmark_from_text(text: str, path: Path | None = None) -> Bookmark:
    """Parse a bookmark markdown document."""
    data, body = split_frontmatter(text)
    try:
        raw_date = str(data["date"])
        bookmark = Bookmark(
            title=str(data.get("title", "")),
            url=str(data["url"]),
            guid=str(data["guid"]),
            profile=str(data.get("profile", "")),
            date=date.fromisoformat(raw_date[:10]),
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid bookmark front matter in {path}: {exc}") from exc
    tags = data.get("tags") or []
    bookmark.tags = [str(tag) for tag in tags] if isinstance(tags, list) else [str(tags)]
    bookmark.date_added_utc = data.get("date_added_utc")
    bookmark.date_source = "sync" if data.get("date_source") == "sync" else "chrome"
    bookmark.profile_name = data.get("profile_name")
    bookmark.last_used = data.get("last_used")
    bookmark.archived = _parse_bool(data.get("archived", False))
    status = str(data.get("summary_status") or "pending")
    bookmark.summary_status = status if status in ("pending", "ok", "fetch_failed") else "pending"  # type: ignore[assignment]
    bookmark.summarized_at = data.get("summarized_at")
    bookmark.fetch_attempts = int(data.get("fetch_attempts") or 0)
    bookmark.last_fetch_at = data.get("last_fetch_at")
    raw_error = data.get("summary_error")
    bookmark.summary_error = str(raw_error) if raw_error else None
    bookmark.body = body
    bookmark.path = path
    return bookmark


def bookmark_to_text(bookmark: Bookmark) -> str:
    """Serialize a bookmark to markdown."""
    return join_frontmatter(bookmark.to_frontmatter(), bookmark.body)


def read_bookmark(path: Path) -> Bookmark:
    """Load one bookmark file."""
    return bookmark_from_text(path.read_text(encoding="utf-8"), path)


def write_bookmark(bookmark: Bookmark, path: Path | None = None) -> Path:
    """Write a bookmark atomically. Prefer ``modify_bookmark`` for existing files."""
    target = Path(path) if path is not None else Path(bookmark.path)
    atomic_write_text(target, bookmark_to_text(bookmark))
    bookmark.path = target
    return target


def modify_bookmark(path: Path, mutate: Callable[[Bookmark], Bookmark | None]) -> Bookmark:
    """Read-modify-write with mtime check (§2.4 쓰기 안전).

    ``mutate`` receives the freshly read bookmark and returns the bookmark to
    write (or ``None`` to skip). If the file changed between read and write,
    the mutation is re-applied on the new content.
    """
    for _ in range(WRITE_RETRIES):
        before = path.stat().st_mtime_ns
        current = read_bookmark(path)
        updated = mutate(current)
        if updated is None:
            return current
        if path.stat().st_mtime_ns != before:
            continue  # someone wrote meanwhile; re-read and re-apply
        write_bookmark(updated, path)
        return updated
    raise RuntimeError(f"could not update {path}: file kept changing")


def iter_bookmark_files(settings: BookmarkInsightSettings, profile: str) -> Iterator[Path]:
    """Yield ``bookmarks/<profile>/**/*.md`` with NFC-normalized paths."""
    root = settings.profile_bookmarks_dir(profile)
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("."):
            continue
        yield Path(nfc(str(path)))


def iter_bookmarks(
    settings: BookmarkInsightSettings, profile: str, *, include_archived: bool = False
) -> Iterator[Bookmark]:
    """Yield parsed bookmarks of one profile (archived excluded by default)."""
    for path in iter_bookmark_files(settings, profile):
        try:
            bookmark = read_bookmark(path)
        except ValueError as exc:
            logger.warning("Skipping unreadable bookmark %s: %s", path, exc)
            continue
        if bookmark.archived and not include_archived:
            continue
        yield bookmark


def bookmarks_in_period(settings: BookmarkInsightSettings, profile: str, period: Period) -> list[Bookmark]:
    """Non-archived bookmarks whose ``date`` falls inside ``period``, newest first."""
    found = [bookmark for bookmark in iter_bookmarks(settings, profile) if period.contains(bookmark.date)]
    found.sort(key=lambda item: (item.date, item.title), reverse=True)
    return found


def list_profiles(settings: BookmarkInsightSettings) -> list[str]:
    """Profile folder names present under ``bookmarks/``."""
    root = settings.bookmarks_dir
    if not root.is_dir():
        return []
    return sorted(nfc(path.name) for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))


def find_bookmark(settings: BookmarkInsightSettings, guid: str) -> Bookmark | None:
    """Locate a bookmark by guid across all profiles (guid is global)."""
    needle = guid.strip().lower()
    for profile in list_profiles(settings):
        for path in iter_bookmark_files(settings, profile):
            if needle[:6] and needle[:6] not in path.stem.lower():
                continue
            try:
                bookmark = read_bookmark(path)
            except ValueError:
                continue
            if bookmark.guid.lower() == needle:
                return bookmark
    return None


# --- insight notes ---------------------------------------------------------------


def note_path(settings: BookmarkInsightSettings, profile: str, period: Period) -> Path:
    """``insights/<profile>/<since>_<until>.md``."""
    return settings.profile_insights_dir(profile) / f"{period.key}.md"


def _split_conversations(body: str) -> tuple[str, list[str]]:
    """Split the body into (rendered insight text, ["## 대화 기록 ..." sections])."""
    sections: list[str] = []
    head_lines: list[str] = []
    current: list[str] | None = None
    for line in body.split("\n"):
        if line.startswith(CONVERSATION_HEADING_PREFIX):
            if current is not None:
                sections.append("\n".join(current).rstrip("\n"))
            current = [line]
        elif current is not None:
            current.append(line)
        else:
            head_lines.append(line)
    if current is not None:
        sections.append("\n".join(current).rstrip("\n"))
    return "\n".join(head_lines).rstrip("\n"), sections


def note_from_text(text: str, path: Path | None = None) -> tuple[InsightNote, str]:
    """Parse an insight note into (note, rendered insight body without conversations)."""
    data, body = split_frontmatter(text)
    try:
        note = InsightNote(
            profile=str(data["profile"]),
            since=date.fromisoformat(str(data["since"])[:10]),
            until=date.fromisoformat(str(data["until"])[:10]),
            generated_at=str(data.get("generated_at", "")),
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid insight note front matter in {path}: {exc}") from exc
    raw_clusters = data.get("clusters") or []
    note.clusters = [
        InsightCluster(
            topic=str(item.get("topic", "")),
            bookmark_ids=[str(x) for x in item.get("bookmark_ids", [])],
            summary=str(item.get("summary", "")),
            correlation=str(item.get("correlation", "")),
            question=str(item.get("question", "")),
        )
        for item in raw_clusters
        if isinstance(item, dict)
    ]
    note.unavailable = [str(x) for x in data.get("unavailable") or []]
    note.related = [str(x) for x in data.get("related") or []]  # absent in notes written outside a vault / before M10
    note.bookmark_ids = [str(x) for x in data.get("bookmark_ids") or []]
    note.complete = _parse_bool(data.get("complete", True))
    note.pending = [str(x) for x in data.get("pending") or []]
    rendered, conversations = _split_conversations(body)
    note.conversations = conversations
    note.path = path
    return note, rendered


def note_to_text(note: InsightNote, rendered_body: str) -> str:
    """Serialize a note: machine-readable front matter + human body + preserved conversations."""
    data: dict[str, Any] = {
        "profile": note.profile,
        "since": note.since.isoformat(),
        "until": note.until.isoformat(),
        "generated_at": note.generated_at,
        "complete": note.complete,
        "bookmark_ids": list(note.bookmark_ids),
        "unavailable": list(note.unavailable),
    }
    if note.related:  # Obsidian graph/backlink edges; only when the note sits in a vault (render.note_links_enabled)
        data["related"] = BlockList(note.related)
    data["clusters"] = [cluster.to_dict() for cluster in note.clusters]
    if note.pending:  # only partial notes carry the list (complete notes stay byte-compatible with M4)
        data["pending"] = list(note.pending)
    body = rendered_body.rstrip("\n") + "\n"
    for section in note.conversations:
        body += "\n" + section.rstrip("\n") + "\n"
    return join_frontmatter(data, body)


def read_note(path: Path) -> tuple[InsightNote, str]:
    """Load a note file."""
    return note_from_text(path.read_text(encoding="utf-8"), path)


def write_note(note: InsightNote, rendered_body: str, path: Path | None = None) -> Path:
    """Write a note atomically."""
    target = Path(path) if path is not None else Path(note.path)
    atomic_write_text(target, note_to_text(note, rendered_body))
    note.path = target
    return target


def append_conversation_section(path: Path, heading_time: str, conclusion: str, transcript: str | None = None) -> None:
    """Append ``## 대화 기록 (<time>)`` with the LLM conclusion and an optional collapsible transcript."""
    text = path.read_text(encoding="utf-8")
    section = f"{CONVERSATION_HEADING_PREFIX} ({heading_time})\n\n{conclusion.strip()}\n"
    if transcript:
        section += f"\n<details><summary>대화 원문</summary>\n\n{transcript.strip()}\n\n</details>\n"
    if not text.endswith("\n"):
        text += "\n"
    atomic_write_text(path, text + "\n" + section)


def note_is_stale(note: InsightNote, bookmarks: list[Bookmark]) -> bool:
    """§2.5: a note is stale when bookmarks in its period changed after generation."""
    covered = set(note.bookmark_ids)
    current = {bookmark.guid for bookmark in bookmarks}
    if covered != current:
        return True
    generated = note.generated_at
    if not generated:
        return True
    for bookmark in bookmarks:
        if bookmark.summarized_at and bookmark.summarized_at > generated[:10] and bookmark.guid in note.unavailable:
            return True
    return False
