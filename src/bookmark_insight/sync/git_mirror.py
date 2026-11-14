"""Optional git mirror of the bookmark repository (design doc §2.3 깃 미러, 부록 A 4, M9).

Off by default (``BOOKMARK_INSIGHT_GIT_MIRROR``). Nothing in the package reads the
git log: the front matter ``date`` is the source of truth for "when was this
bookmarked" and the mirror only *reproduces* that timeline so ``git log``
reads like a diary. One commit per bookmark file, dated at the bookmark's
``date_added_utc`` (or its ``date`` at local noon) in chronological order,
so the history can be rebuilt from scratch at any time with the same result.

Commit vocabulary (subject prefixes are the idempotency key):

- ``bookmark: <title>``  first commit of a file, back-dated to the bookmark
- ``update: <title>``    later content change (tags, summary body), dated at the run
- ``archive: <title>``   the bookmark left Chrome, dated at the run

Only the ``git`` binary is used (``subprocess``, never ``shell=True``, paths as
POSIX relatives so Windows works). ``.bookmark_insight/`` (lock, usage log) is ignored.
Callers hold ``repo_lock``; both entry points acquire it themselves too (it is
re-entrant) so a CLI rebuild never races a sync.
"""

from __future__ import annotations
import os
import shutil
import logging
import subprocess
from typing import Iterable
from pathlib import Path
from datetime import time, datetime, timezone
from dataclasses import field, dataclass

from bookmark_insight.store import nfc, repo_lock, list_profiles, read_bookmark, iter_bookmarks, atomic_write_text
from bookmark_insight.models import Bookmark
from bookmark_insight.settings import BookmarkInsightSettings


logger = logging.getLogger(__name__)

# --- tuning constants ---------------------------------------------------------------
GIT_TIMEOUT_S = 120.0  # one git call; a rebuild of thousands of files is many calls, each short
COMMIT_LOCAL_TIME = time(12, 0)  # used when a bookmark has no ``date_added_utc``
MIRROR_AUTHOR_NAME = "Bookmark Insight"
MIRROR_AUTHOR_EMAIL = "bookmark_insight@localhost"
GITIGNORE_LINES: tuple[str, ...] = (".bookmark_insight/", ".tmp-*")
INIT_SUBJECT = "init: bookmark_insight git mirror"
BOOKMARK_PREFIX = "bookmark: "
UPDATE_PREFIX = "update: "
ARCHIVE_PREFIX = "archive: "


class GitMirrorError(RuntimeError):
    """``git`` is missing or a git command failed."""


@dataclass
class MirrorReport:
    """What one mirror run committed."""

    repo: Path
    initialized: bool = False  # a fresh ``git init`` happened
    bookmarks: int = 0  # ``bookmark:`` commits
    updates: int = 0  # ``update:`` commits
    archives: int = 0  # ``archive:`` commits
    skipped: int = 0  # files already in the history and unchanged
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Number of commits created by this run."""
        return self.bookmarks + self.updates + self.archives


@dataclass
class SyncChanges:
    """Bookmark files one sync run wrote (input of ``commit_changes``)."""

    created: list[Path] = field(default_factory=list)
    updated: list[Path] = field(default_factory=list)
    archived: list[Path] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Return whether anything was written."""
        return bool(self.created or self.updated or self.archived)


def git_executable() -> str | None:
    """Path of the ``git`` binary on PATH, or None."""
    return shutil.which("git")


def commit_datetime(bookmark: Bookmark, now: datetime) -> datetime:
    """When the bookmark's first commit is dated: ``date_added_utc`` in the local zone, else ``date`` at noon."""
    tz = now.tzinfo
    raw = (bookmark.date_added_utc or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(tz)
    return datetime.combine(bookmark.date, COMMIT_LOCAL_TIME, tzinfo=tz)


def _git_date(when: datetime) -> str:
    """ISO 8601 with offset, the format git accepts for ``GIT_*_DATE``."""
    return when.replace(microsecond=0).isoformat()


class _Repo:
    """Thin wrapper over the git binary for one repository."""

    def __init__(self, repo: Path, git: str) -> None:
        self.repo = repo.resolve()
        self.git = git
        self._identity: list[str] | None = None

    def run(
        self, *args: str, env: dict[str, str] | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run one git command in the repository and return the completed process."""
        full_env = dict(os.environ)
        full_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            full_env.update(env)
        command = [self.git, "-c", "core.quotepath=false", *args]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.repo),
                env=full_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitMirrorError(f"git {' '.join(args[:2])} failed: {exc}") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise GitMirrorError(f"git {' '.join(args[:2])} failed ({result.returncode}): {detail}")
        return result

    @property
    def initialized(self) -> bool:
        """Whether ``<repo>/.git`` exists."""
        return (self.repo / ".git").exists()

    def init(self) -> None:
        """``git init`` the repository."""
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run("init", "-q")

    def identity_args(self) -> list[str]:
        """``-c user.*`` overrides when the user has no git identity configured."""
        if self._identity is None:
            has_email = self.run("config", "--get", "user.email", check=False).returncode == 0
            self._identity = (
                []
                if has_email
                else ["-c", f"user.name={MIRROR_AUTHOR_NAME}", "-c", f"user.email={MIRROR_AUTHOR_EMAIL}"]
            )
        return self._identity

    def has_commits(self) -> bool:
        """Whether HEAD resolves (an empty repository has no commits yet)."""
        return self.run("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def history(self, pathspec: str) -> dict[str, str]:
        """Map each committed path under ``pathspec`` to the subject of its newest commit."""
        if not self.has_commits():
            return {}
        result = self.run("log", "--format=%x00%s", "--name-only", "--", pathspec)
        latest: dict[str, str] = {}
        subject = ""
        for line in result.stdout.split("\n"):
            if line.startswith("\x00"):
                subject = line[1:]
                continue
            path = nfc(line.strip())
            if path:
                latest.setdefault(path, subject)
        return latest

    def is_dirty(self, relpath: str) -> bool:
        """Whether the file differs from HEAD (modified, untracked or deleted)."""
        result = self.run("status", "--porcelain", "--untracked-files=all", "--", relpath)
        return bool(result.stdout.strip())

    def commit(self, relpaths: Iterable[str], subject: str, when: datetime) -> None:
        """Stage ``relpaths`` and commit them with author and committer dates set to ``when``."""
        self.run("add", "--", *relpaths)
        stamp = _git_date(when)
        args = [*self.identity_args(), "commit", "-q", "--no-verify", "--no-gpg-sign", "-m", subject]
        self.run(*args, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})


def _require_repo(settings: BookmarkInsightSettings) -> _Repo:
    """Return the git wrapper for the bookmark repository or raise when git is missing."""
    git = git_executable()
    if git is None:
        raise GitMirrorError("git executable not found on PATH (install git or turn BOOKMARK_INSIGHT_GIT_MIRROR off)")
    return _Repo(settings.require_repo(), git)


def _ensure_initialized(repo: _Repo, report: MirrorReport, first_date: datetime) -> None:
    """``git init`` when needed and commit ``.gitignore`` (dated at the oldest bookmark so the log stays ordered)."""
    if not repo.initialized:
        repo.init()
        report.initialized = True
        logger.info("Initialized git mirror in %s", repo.repo)
    ignore_path = repo.repo / ".gitignore"
    existing = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    missing = [line for line in GITIGNORE_LINES if line not in existing.splitlines()]
    if missing:  # new repo, or one initialized before the state folder was renamed
        prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
        atomic_write_text(ignore_path, prefix + "\n".join(missing) + "\n")
    if repo.is_dirty(".gitignore"):
        repo.commit([".gitignore"], INIT_SUBJECT, first_date)


def _relpath(repo: _Repo, path: Path) -> str:
    """Repository-relative POSIX path (NFC) of a bookmark file."""
    return nfc(Path(path).resolve().relative_to(repo.repo).as_posix())


def _local_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    return now.astimezone() if now.tzinfo is None else now


_Entry = tuple[datetime, str, Bookmark]  # (commit date, repo-relative path, bookmark)


def _mirror_entries(
    settings: BookmarkInsightSettings, repo: _Repo, entries: list[_Entry], now: datetime, report: MirrorReport
) -> None:
    """Sort ``entries`` chronologically and commit what each one is missing (shared tail of both entry points)."""
    entries.sort(key=lambda item: (item[0], item[1]))
    _ensure_initialized(repo, report, entries[0][0] if entries else now)
    history = repo.history(_relpath(repo, settings.bookmarks_dir) if settings.bookmarks_dir.exists() else "bookmarks")
    # Pass 1: back-dated ``bookmark:`` commits in date order. Pass 2: ``update:`` / ``archive:``
    # commits dated at the run, after every back-dated one so the log stays monotone.
    fresh: set[str] = set()
    for when, rel, bookmark in entries:
        if rel not in history:
            repo.commit([rel], BOOKMARK_PREFIX + (bookmark.title or rel), when)
            history[rel] = BOOKMARK_PREFIX + (bookmark.title or rel)
            report.bookmarks += 1
            fresh.add(rel)
    for _when, rel, bookmark in entries:
        _mirror_change(repo, history, rel, bookmark, now, report, fresh=rel in fresh)


def rebuild_history(
    settings: BookmarkInsightSettings, *, profile: str | None = None, now: datetime | None = None
) -> MirrorReport:
    """Create the missing commits so ``git log`` mirrors every bookmark file in date order.

    Idempotent: files already in the history are skipped unless their content
    changed (``update:``) or they were archived since (``archive:``). ``profile``
    restricts the pass to one profile folder; ``now`` dates the update/archive
    commits and carries the local timezone (system local when omitted).
    """
    now = _local_now(now)
    report = MirrorReport(repo=settings.require_repo())
    with repo_lock(settings):
        repo = _require_repo(settings)
        profiles = [profile] if profile else list_profiles(settings)
        entries: list[_Entry] = []
        for name in profiles:
            for bookmark in iter_bookmarks(settings, name, include_archived=True):
                try:
                    entries.append((commit_datetime(bookmark, now), _relpath(repo, Path(bookmark.path)), bookmark))
                except ValueError:
                    report.warnings.append(f"outside the repository, not mirrored: {bookmark.path}")
        _mirror_entries(settings, repo, entries, now, report)
    logger.info(
        "git mirror: %d commits (bookmarks=%d updates=%d archives=%d skipped=%d)",
        report.total,
        report.bookmarks,
        report.updates,
        report.archives,
        report.skipped,
    )
    return report


def commit_changes(
    settings: BookmarkInsightSettings, changes: SyncChanges, *, now: datetime | None = None
) -> MirrorReport:
    """Commit the files one sync run created / updated / archived (called by ``sync_profiles``)."""
    now = _local_now(now)
    report = MirrorReport(repo=settings.require_repo())
    if not changes:
        return report
    with repo_lock(settings):
        repo = _require_repo(settings)
        entries: list[_Entry] = []
        for path in [*changes.created, *changes.updated, *changes.archived]:
            try:
                bookmark = read_bookmark(Path(path))
                entries.append((commit_datetime(bookmark, now), _relpath(repo, Path(path)), bookmark))
            except (ValueError, OSError) as exc:
                report.warnings.append(f"not mirrored: {path}: {exc}")
        _mirror_entries(settings, repo, entries, now, report)
    return report


def _mirror_change(
    repo: _Repo,
    history: dict[str, str],
    rel: str,
    bookmark: Bookmark,
    now: datetime,
    report: MirrorReport,
    *,
    fresh: bool,
) -> None:
    """Add the run-dated ``archive:`` / ``update:`` commit a file needs, if any; ``history`` is updated in place."""
    title = bookmark.title or rel
    subject = history.get(rel, "")
    if fresh:
        # Just committed in pass 1. A file first met while already archived keeps that single
        # commit (it carries archived: true): the real archive time is unknown, so none is invented.
        return
    if not repo.is_dirty(rel):
        report.skipped += 1
        return
    if bookmark.archived and not subject.startswith(ARCHIVE_PREFIX):
        repo.commit([rel], ARCHIVE_PREFIX + title, now)
        history[rel] = ARCHIVE_PREFIX + title
        report.archives += 1
        return
    repo.commit([rel], UPDATE_PREFIX + title, now)
    history[rel] = UPDATE_PREFIX + title
    report.updates += 1
