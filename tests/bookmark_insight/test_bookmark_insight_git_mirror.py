"""M9: optional git mirror — back-dated commits reconstruct the bookmark timeline from front matter ``date``."""

from __future__ import annotations
import json
import shutil
import subprocess
from typing import Any
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

import pytest

import bookmark_insight.sync.git_mirror as git_mirror
from bookmark_insight.sync import sync_profiles, rebuild_history
from bookmark_insight.store import read_bookmark, iter_bookmarks, modify_bookmark
from bookmark_insight.models import Bookmark
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.sync.__main__ import main as sync_main
from bookmark_insight.sync.git_mirror import SyncChanges, GitMirrorError, commit_changes, commit_datetime


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=KST)
LATER = datetime(2026, 11, 7, 9, 30, tzinfo=KST)
GUID_ICE = "a1b2c3d4-0001-4000-8000-000000000001"
GUID_WEBRTC = "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05"
SAMPLE_TITLES_IN_ORDER = [
    "WebRTC 재접속 처리 패턴",
    "모바일 백그라운드 복귀 시 ICE 재협상",
    'Rust: async/await 입문 | 1부 "Future란?" <정리>',
]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _log(repo: Path, *args: str) -> list[tuple[datetime, str]]:
    """Return the commits touching ``bookmarks/`` oldest first as (author date, subject)."""
    out = _git(repo, "log", "--reverse", "--format=%aI%x09%s", *args, "--", "bookmarks")
    rows: list[tuple[datetime, str]] = []
    for line in out.splitlines():
        stamp, subject = line.split("\t", 1)
        rows.append((datetime.fromisoformat(stamp), subject))
    return rows


def _settings(repo: Path, chrome: Path | None = None, **overrides: Any) -> BookmarkInsightSettings:
    return BookmarkInsightSettings(
        bookmark_repo=repo,
        chrome_user_data_dir=chrome,
        chrome_profiles=("Default",),
        default_profile="Default",
        **overrides,
    )


def _by_guid(settings: BookmarkInsightSettings) -> dict[str, Bookmark]:
    return {b.guid: b for b in iter_bookmarks(settings, "Default", include_archived=True)}


# --- rebuild_history ------------------------------------------------------------------------


def test_rebuild_history_is_chronological_and_matches_frontmatter_dates(repo: Path) -> None:
    """One ``bookmark:`` commit per file, dated at date_added_utc (local zone) in ascending order."""
    settings = _settings(repo)
    report = rebuild_history(settings, now=NOW)

    assert report.initialized is True
    assert (report.bookmarks, report.updates, report.archives, report.skipped) == (3, 0, 0, 0)
    assert report.total == 3 and report.warnings == []

    log = _log(repo)
    assert [subject for _, subject in log] == [f"bookmark: {title}" for title in SAMPLE_TITLES_IN_ORDER]
    stamps = [stamp for stamp, _ in log]
    assert stamps == sorted(stamps)
    by_title = {b.title: b for b in iter_bookmarks(settings, "Default")}
    for stamp, subject in log:
        bookmark = by_title[subject.removeprefix("bookmark: ")]
        assert stamp.date() == bookmark.date  # the log reproduces the front matter date
        assert stamp == datetime.fromisoformat(str(bookmark.date_added_utc).replace("Z", "+00:00"))
    # committer date equals author date; bookmarks are clean and .bookmark_insight/ is ignored (insights/ is not mirrored)
    assert _git(repo, "log", "-1", "--format=%cI").strip() == stamps[-1].isoformat()
    assert _git(repo, "status", "--porcelain", "--", "bookmarks", ".bookmark_insight", ".gitignore").strip() == ""
    assert ".bookmark_insight/" in (repo / ".gitignore").read_text(encoding="utf-8")


def test_rebuild_history_adds_missing_gitignore_lines_to_an_existing_file(repo: Path) -> None:
    """A repo whose ``.gitignore`` predates the state-folder rename gets the new line appended, nothing removed."""
    (repo / ".gitignore").write_text(".deskmate/\n.tmp-*\n", encoding="utf-8")
    rebuild_history(_settings(repo), now=NOW)

    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".deskmate/\n.tmp-*\n.bookmark_insight/\n"
    assert _git(repo, "status", "--porcelain", "--", ".gitignore").strip() == ""


def test_rebuild_history_is_idempotent(repo: Path) -> None:
    """A second run adds nothing: every file is already in the history."""
    settings = _settings(repo)
    rebuild_history(settings, now=NOW)
    before = _log(repo)

    report = rebuild_history(settings, now=LATER)
    assert report.total == 0 and report.skipped == 3 and report.initialized is False
    assert _log(repo) == before


def test_rebuild_history_records_archive_and_update_at_run_time(repo: Path) -> None:
    """Archived → ``archive:`` dated at the run; a body change → ``update:``; both idempotent afterwards."""
    settings = _settings(repo)
    rebuild_history(settings, now=NOW)
    bookmarks = _by_guid(settings)

    def archive(bookmark: Bookmark) -> Bookmark:
        bookmark.archived = True
        return bookmark

    def edit(bookmark: Bookmark) -> Bookmark:
        bookmark.body += "\n사용자가 적은 메모.\n"
        return bookmark

    modify_bookmark(Path(bookmarks[GUID_ICE].path), archive)
    modify_bookmark(Path(bookmarks[GUID_WEBRTC].path), edit)

    report = rebuild_history(settings, now=LATER)
    assert (report.bookmarks, report.updates, report.archives, report.skipped) == (0, 1, 1, 1)
    newest = _log(repo)[-2:]
    assert {subject for _, subject in newest} == {
        "archive: 모바일 백그라운드 복귀 시 ICE 재협상",
        "update: WebRTC 재접속 처리 패턴",
    }
    assert all(stamp == LATER for stamp, _ in newest)
    assert rebuild_history(settings, now=LATER).total == 0


def test_rebuild_history_keeps_a_never_seen_archived_file_to_one_commit(repo: Path) -> None:
    """A file first met while already archived gets only its back-dated ``bookmark:`` commit (no invented time)."""
    settings = _settings(repo)
    bookmarks = _by_guid(settings)

    def archive(bookmark: Bookmark) -> Bookmark:
        bookmark.archived = True
        return bookmark

    modify_bookmark(Path(bookmarks[GUID_ICE].path), archive)
    report = rebuild_history(settings, now=NOW)
    assert (report.bookmarks, report.archives, report.updates) == (3, 0, 0)
    log = _log(repo)
    assert [subject for _, subject in log] == [f"bookmark: {title}" for title in SAMPLE_TITLES_IN_ORDER]
    assert [stamp for stamp, _ in log] == sorted(stamp for stamp, _ in log)
    assert rebuild_history(settings, now=LATER).total == 0


def test_commit_datetime_falls_back_to_local_noon() -> None:
    """Without date_added_utc the commit is dated at ``date`` 12:00 in the run's zone."""
    bookmark = Bookmark(title="t", url="https://x", guid="g", profile="Default", date=date(2026, 11, 3))
    assert commit_datetime(bookmark, NOW) == datetime(2026, 11, 3, 12, 0, tzinfo=KST)
    bookmark.date_added_utc = "2026-11-03T00:12:40Z"
    assert commit_datetime(bookmark, NOW) == datetime(2026, 11, 3, 9, 12, 40, tzinfo=KST)
    bookmark.date_added_utc = "garbage"
    assert commit_datetime(bookmark, NOW) == datetime(2026, 11, 3, 12, 0, tzinfo=KST)


def test_rebuild_history_without_git_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No git binary → GitMirrorError (the CLI turns it into exit 1)."""
    monkeypatch.setattr(git_mirror, "git_executable", lambda: None)
    with pytest.raises(GitMirrorError, match="git executable"):
        rebuild_history(_settings(repo), now=NOW)


def test_commit_changes_with_nothing_to_do_is_a_noop(repo: Path) -> None:
    """An empty change set does not even initialize a repository."""
    report = commit_changes(_settings(repo), SyncChanges(), now=NOW)
    assert report.total == 0 and not (repo / ".git").exists()


# --- sync with git_mirror on -------------------------------------------------------------------


def _edit_chrome(chrome: Path, mutate: Any) -> None:
    path = chrome / "Default" / "Bookmarks"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_sync_with_git_mirror_commits_created_then_archived(empty_repo: Path, chrome_user_data: Path) -> None:
    """First sync: a back-dated commit per created file; removing one in Chrome adds ``archive:`` at sync time."""
    settings = _settings(empty_repo, chrome_user_data, git_mirror=True)
    report = sync_profiles(settings, now=NOW)

    assert report.created == {"Default": 5} and report.mirror_commits == {"Default": 5} and report.warnings == []
    log = _log(empty_repo)
    assert len(log) == 5 and all(subject.startswith("bookmark: ") for _, subject in log)
    stamps = [stamp for stamp, _ in log]
    assert stamps == sorted(stamps)
    by_title = {b.title: b for b in iter_bookmarks(settings, "Default")}
    assert all(stamp.date() == by_title[subject.removeprefix("bookmark: ")].date for stamp, subject in log)
    assert _git(empty_repo, "status", "--porcelain", "--", "bookmarks", ".bookmark_insight").strip() == ""

    # unchanged resync: no commits
    assert sync_profiles(settings, now=NOW).mirror_commits == {"Default": 0}

    def remove_ice(data: dict[str, Any]) -> None:
        folder = data["roots"]["bookmark_bar"]["children"][0]
        folder["children"] = [node for node in folder["children"] if node.get("guid") != GUID_ICE]

    _edit_chrome(chrome_user_data, remove_ice)
    report = sync_profiles(settings, now=LATER)
    assert report.archived == {"Default": 1} and report.mirror_commits == {"Default": 1}
    stamp, subject = _log(empty_repo)[-1]
    assert subject == "archive: 모바일 백그라운드 복귀 시 ICE 재협상" and stamp == LATER
    assert read_bookmark(Path(_by_guid(settings)[GUID_ICE].path)).archived is True


def test_sync_without_git_mirror_creates_no_repository(empty_repo: Path, chrome_user_data: Path) -> None:
    """Default (off): v1 behaviour, no .git, no mirror_commits entry."""
    report = sync_profiles(_settings(empty_repo, chrome_user_data), now=NOW)
    assert report.created == {"Default": 5} and report.mirror_commits == {}
    assert not (empty_repo / ".git").exists()


def test_sync_git_failure_is_a_warning_not_an_error(
    empty_repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The markdown files are the product: a missing git binary only adds a warning."""
    monkeypatch.setattr(git_mirror, "git_executable", lambda: None)
    report = sync_profiles(_settings(empty_repo, chrome_user_data, git_mirror=True), now=NOW)
    assert report.created == {"Default": 5} and report.mirror_commits == {}
    assert any("git mirror failed" in warning for warning in report.warnings)
    assert len(list(iter_bookmarks(_settings(empty_repo), "Default"))) == 5


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_git_rebuild_prints_commit_count(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--git-rebuild`` rebuilds without Chrome and reports the count; a rerun reports 0."""
    assert sync_main(["--git-rebuild", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "git mirror: 3 commits" in out and "repository initialized" in out and "total commits: 3" in out
    assert len(_log(repo)) == 3

    assert sync_main(["--git-rebuild", "--repo", str(repo), "--profile", "Default"]) == 0
    assert "Default: git mirror: 0 commits" in capsys.readouterr().out


def test_cli_git_rebuild_exit_codes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No repo → 2; git missing → 1 with a clear message."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    monkeypatch.chdir(repo)  # no .env here
    assert sync_main(["--git-rebuild"]) == 2
    monkeypatch.setattr(git_mirror, "git_executable", lambda: None)
    assert sync_main(["--git-rebuild", "--repo", str(repo)]) == 1
    assert "git executable" in capsys.readouterr().err


def test_cli_git_mirror_flag_forces_the_mirror_on(
    empty_repo: Path, chrome_user_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--git-mirror`` commits the sync's files even when BOOKMARK_INSIGHT_GIT_MIRROR is unset."""
    code = sync_main(
        ["--git-mirror", "--repo", str(empty_repo), "--chrome-dir", str(chrome_user_data), "--profile", "Default"]
    )
    assert code == 0
    assert "git_commits=5" in capsys.readouterr().out
    assert len(_log(empty_repo)) == 5
