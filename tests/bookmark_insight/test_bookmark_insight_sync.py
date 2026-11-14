"""Track A (M2): Chrome Bookmarks JSON → bookmarks/<profile>/YYYY/*.md per design doc §2.4."""

import json
import time
import unicodedata
from typing import Any
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

import pytest

from bookmark_insight.sync import SyncReport, sync_profiles, build_filename, build_bookmark_path
from bookmark_insight.store import read_bookmark, iter_bookmarks, modify_bookmark
from bookmark_insight.models import Bookmark
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.sync.chrome import default_user_data_dir
from bookmark_insight.sync.__main__ import main as sync_main


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=KST)
GUID_WEBRTC = "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05"
GUID_ICE = "a1b2c3d4-0001-4000-8000-000000000001"
GUID_RUST = "a1b2c3d4-0002-4000-8000-000000000002"
GUID_ZERO = "a1b2c3d4-0004-4000-8000-000000000004"
GUID_CSS = "a1b2c3d4-0006-4000-8000-000000000006"
EXPECTED_DEFAULT_FILES = {
    "2025/2025-03-14 작년에 저장한 CSS Grid 치트시트 a1b2c3.md",
    "2026/2026-11-03 WebRTC 재접속 처리 패턴 7f3a9c.md",
    "2026/2026-11-04 모바일 백그라운드 복귀 시 ICE 재협상 a1b2c3.md",
    "2026/2026-11-05 Rust async await 입문 1부 Future란 정리 a1b2c3.md",
    "2026/2026-11-06 date_added가 0인 북마크 a1b2c3.md",
}


def _settings(repo: Path, chrome: Path, *profiles: str, **overrides: Any) -> BookmarkInsightSettings:
    return BookmarkInsightSettings(
        bookmark_repo=repo, chrome_user_data_dir=chrome, chrome_profiles=profiles, **overrides
    )


def _relative_files(settings: BookmarkInsightSettings, profile: str) -> set[str]:
    root = settings.profile_bookmarks_dir(profile)
    return {unicodedata.normalize("NFC", path.relative_to(root).as_posix()) for path in root.rglob("*.md")}


def _by_guid(settings: BookmarkInsightSettings, profile: str) -> dict[str, Bookmark]:
    return {bookmark.guid: bookmark for bookmark in iter_bookmarks(settings, profile, include_archived=True)}


def _edit_chrome(chrome: Path, profile: str, mutate: Any) -> None:
    path = chrome / profile / "Bookmarks"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _dev_folder(data: dict[str, Any]) -> dict[str, Any]:
    folder: dict[str, Any] = data["roots"]["bookmark_bar"]["children"][0]
    assert folder["name"] == "개발"
    return folder


# --- first sync ---------------------------------------------------------------------


def test_first_sync_writes_expected_files_for_default(empty_repo: Path, chrome_user_data: Path) -> None:
    """N url nodes → expected files: private excluded, duplicate URL merged, zero date and old synced item kept."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    report = sync_profiles(settings, now=NOW)

    assert isinstance(report, SyncReport)
    assert report.profiles == ["Default"] and report.warnings == []
    assert report.created == {"Default": 5} and report.updated == {"Default": 0} and report.archived == {"Default": 0}
    assert report.excluded == {"Default": 1}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES
    assert not any("secret" in b.url for b in iter_bookmarks(settings, "Default", include_archived=True))

    bookmarks = _by_guid(settings, "Default")
    webrtc = bookmarks[GUID_WEBRTC]
    assert webrtc.tags == ["개발", "읽을거리"]
    assert webrtc.date == date(2026, 11, 3) and webrtc.date_added_utc == "2026-11-03T00:12:40Z"
    assert webrtc.date_source == "chrome" and webrtc.last_used == "2026-11-04"
    assert webrtc.profile == "Default" and webrtc.profile_name == "개인" and webrtc.archived is False
    assert webrtc.summary_status == "pending" and webrtc.body == ""
    assert "a1b2c3d4-0005-4000-8000-000000000005" not in bookmarks  # the copy contributes tags only

    zero = bookmarks[GUID_ZERO]
    assert zero.date == NOW.date() and zero.date_source == "sync" and zero.tags == []
    assert bookmarks[GUID_ICE].last_used is None
    assert bookmarks[GUID_RUST].title == 'Rust: async/await 입문 | 1부 "Future란?" <정리>'


def test_date_is_local_date_of_date_added(empty_repo: Path, chrome_user_data: Path) -> None:
    """2026-11-03T00:12:40Z is 2026-11-03 in UTC+9 but 2026-11-02 in UTC-1 (crosses midnight)."""
    west = timezone(timedelta(hours=-1))
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=datetime(2026, 11, 6, 12, 0, tzinfo=west))

    files = _relative_files(settings, "Default")
    assert "2026/2026-11-02 WebRTC 재접속 처리 패턴 7f3a9c.md" in files
    assert "2026/2026-11-05 Rust async await 입문 1부 Future란 정리 a1b2c3.md" in files
    webrtc = _by_guid(settings, "Default")[GUID_WEBRTC]
    assert webrtc.date == date(2026, 11, 2) and webrtc.date_added_utc == "2026-11-03T00:12:40Z"


# --- profiles ----------------------------------------------------------------------------


def test_profiles_outside_allow_list_are_never_read(empty_repo: Path, chrome_user_data: Path) -> None:
    """Allow list = Default: Profile 1 folder is not even created and is reported as skipped."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    report = sync_profiles(settings, now=NOW)
    assert report.skipped_profiles == ["Profile 1"]
    assert not settings.profile_bookmarks_dir("Profile 1").exists()
    assert sorted(p.name for p in settings.bookmarks_dir.iterdir()) == ["Default"]


def test_two_allowed_profiles_get_separate_folders_without_merging(empty_repo: Path, chrome_user_data: Path) -> None:
    """The same URL in two profiles yields one file per profile, each with its own guid."""
    settings = _settings(empty_repo, chrome_user_data, "Default", "Profile 1")
    report = sync_profiles(settings, now=NOW)
    assert report.profiles == ["Default", "Profile 1"] and report.skipped_profiles == []
    assert report.created == {"Default": 5, "Profile 1": 2}

    default_webrtc = _by_guid(settings, "Default")[GUID_WEBRTC]
    work = _by_guid(settings, "Profile 1")
    work_webrtc = work["b2c3d4e5-0001-4000-8000-000000000011"]
    assert default_webrtc.url == work_webrtc.url == "https://example.com/webrtc/reconnect"
    assert work_webrtc.tags == [] and work_webrtc.profile == "Profile 1" and work_webrtc.profile_name == "회사"
    assert "2026/2026-11-03 WebRTC 재접속 처리 패턴 b2c3d4.md" in _relative_files(settings, "Profile 1")
    assert any(b.url.startswith("http://192.168.10.5") for b in work.values())  # URL patterns only gate fetching


def test_empty_allow_list_means_last_used_profile_only(empty_repo: Path, chrome_user_data: Path) -> None:
    """No allow list → Local State ``profile.last_used`` (Default) is the only profile synced."""
    settings = _settings(empty_repo, chrome_user_data)
    report = sync_profiles(settings, now=NOW)
    assert report.profiles == ["Default"] and report.skipped_profiles == ["Profile 1"]
    assert not settings.profile_bookmarks_dir("Profile 1").exists()


def test_listed_but_missing_profile_warns_and_is_skipped(empty_repo: Path, chrome_user_data: Path) -> None:
    """Profile 2 is in info_cache but has no folder on disk → warning, the others still sync."""
    settings = _settings(empty_repo, chrome_user_data)
    report = sync_profiles(settings, profiles=["Default", "Profile 2"], now=NOW)
    assert report.profiles == ["Default"]
    assert len(report.warnings) == 1 and "Profile 2" in report.warnings[0]
    assert "Profile 2" not in report.skipped_profiles


def test_missing_user_data_dir_is_a_warning_not_an_error(empty_repo: Path, tmp_path: Path) -> None:
    """A wrong Chrome path produces a report with a warning and no profiles."""
    report = sync_profiles(_settings(empty_repo, tmp_path / "nope", "Default"), now=NOW)
    assert report.profiles == [] and report.warnings and "not found" in report.warnings[0]


def test_default_user_data_dir_per_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OS defaults: macOS Application Support, Windows LOCALAPPDATA, Linux ~/.config."""
    home = Path.home()
    assert default_user_data_dir("darwin") == home / "Library" / "Application Support" / "Google" / "Chrome"
    assert default_user_data_dir("linux") == home / ".config" / "google-chrome"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_user_data_dir("win32") == tmp_path / "Google" / "Chrome" / "User Data"


# --- filename rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("WebRTC 재접속 처리 패턴", "2026-11-05 WebRTC 재접속 처리 패턴 7f3a9c.md"),
        (
            'Rust: async/await 입문 | 1부 "Future란?" <정리>',
            "2026-11-05 Rust async await 입문 1부 Future란 정리 7f3a9c.md",
        ),
        ("a\\b*c?d", "2026-11-05 a bcd 7f3a9c.md"),
        ("[뉴비가이드] 로컬 LLM #정리 ^블록 | 별명", "2026-11-05 뉴비가이드 로컬 LLM 정리 블록 별명 7f3a9c.md"),
        ("  끝에 점과 공백...  ", "2026-11-05 끝에 점과 공백 7f3a9c.md"),
        ("CON", "2026-11-05 CON_ 7f3a9c.md"),
        ("lpt1", "2026-11-05 lpt1_ 7f3a9c.md"),
        ("", "2026-11-05 untitled 7f3a9c.md"),
        ("탭\t과\n개행", "2026-11-05 탭 과 개행 7f3a9c.md"),
    ],
)
def test_build_filename_table(title: str, expected: str) -> None:
    """Windows-forbidden and Obsidian link characters dropped, separators become spaces, reserved names suffixed, empty → untitled."""
    assert build_filename(date(2026, 11, 5), title, GUID_WEBRTC) == expected
    stem = expected[:-3]
    assert not any(char in stem for char in "[]#^|"), "a [[wikilink]] to this file would break"


def test_build_filename_normalizes_nfd_and_caps_length() -> None:
    """NFD input yields the same NFC name; a 200-char title is trimmed so the name is 120 chars."""
    nfd = unicodedata.normalize("NFD", "한글 제목")
    name = build_filename(date(2026, 11, 5), nfd, GUID_WEBRTC)
    assert name == build_filename(date(2026, 11, 5), "한글 제목", GUID_WEBRTC)
    assert unicodedata.is_normalized("NFC", name)

    long_name = build_filename(date(2026, 11, 5), "가" * 200, GUID_WEBRTC)
    assert len(long_name) == 120
    assert long_name.startswith("2026-11-05 가가") and long_name.endswith(" 7f3a9c.md")


def test_build_bookmark_path_respects_total_path_cap() -> None:
    """A deep repo path trims the title part further; the date and guid always survive."""
    deep_repo = Path("repo") / ("d" * 180)  # never touched on disk; keeps the length OS-independent
    settings = BookmarkInsightSettings(bookmark_repo=deep_repo)
    path = build_bookmark_path(settings, "Default", date(2026, 11, 5), "가" * 200, GUID_WEBRTC)
    assert len(str(path)) <= 240 and len(path.name) < 120
    assert path.parent == deep_repo / "bookmarks" / "Default" / "2026"
    assert path.name.startswith("2026-11-05 가가") and path.name.endswith(" 7f3a9c.md")

    hopeless = BookmarkInsightSettings(bookmark_repo=Path("repo") / ("e" * 230))
    minimal = build_bookmark_path(hopeless, "Default", date(2026, 11, 5), "제목", GUID_WEBRTC)
    assert minimal.name == "2026-11-05 7f3a9c.md"


# --- re-sync policy ----------------------------------------------------------------------------


def test_resync_is_idempotent(empty_repo: Path, chrome_user_data: Path) -> None:
    """A second run with unchanged Chrome data writes nothing."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)
    before = {
        path: path.read_text(encoding="utf-8") for path in settings.profile_bookmarks_dir("Default").rglob("*.md")
    }
    report = sync_profiles(settings, now=NOW + timedelta(days=1))
    assert report.created == {"Default": 0} and report.updated == {"Default": 0} and report.archived == {"Default": 0}
    assert {path: path.read_text(encoding="utf-8") for path in before} == before


def test_title_change_keeps_filename(empty_repo: Path, chrome_user_data: Path) -> None:
    """Renaming in Chrome updates ``title`` only; the file (and its [[link]]) stays."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)

    def rename(data: dict[str, Any]) -> None:
        _dev_folder(data)["children"][1]["name"] = "ICE 재협상 (수정)"

    _edit_chrome(chrome_user_data, "Default", rename)
    report = sync_profiles(settings, now=NOW)
    assert report.updated == {"Default": 1} and report.created == {"Default": 0}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES
    assert _by_guid(settings, "Default")[GUID_ICE].title == "ICE 재협상 (수정)"


def test_folder_move_updates_tags_but_keeps_date(empty_repo: Path, chrome_user_data: Path) -> None:
    """Moving a bookmark between folders changes ``tags`` only (부록 A 4)."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)

    def move(data: dict[str, Any]) -> None:
        node = _dev_folder(data)["children"].pop(1)
        node["date_added"] = "13438500000000000"
        data["roots"]["other"]["children"][0]["children"].append(node)

    _edit_chrome(chrome_user_data, "Default", move)
    sync_profiles(settings, now=NOW)
    moved = _by_guid(settings, "Default")[GUID_ICE]
    assert moved.tags == ["읽을거리"] and moved.date == date(2026, 11, 4)
    assert moved.date_added_utc == "2026-11-04T05:30:00Z"
    assert "2026/2026-11-04 모바일 백그라운드 복귀 시 ICE 재협상 a1b2c3.md" in _relative_files(settings, "Default")


def test_removed_from_chrome_is_archived_with_body_preserved(empty_repo: Path, chrome_user_data: Path) -> None:
    """Deleting in Chrome sets ``archived: true``; the file, its body and date stay; re-adding un-archives."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)
    ice_path = Path(_by_guid(settings, "Default")[GUID_ICE].path)

    def add_body(bookmark: Bookmark) -> Bookmark:
        bookmark.body = "## 요약\n사용자가 적은 메모.\n"
        bookmark.summary_status = "ok"
        return bookmark

    modify_bookmark(ice_path, add_body)
    removed_node: dict[str, Any] = {}

    def remove(data: dict[str, Any]) -> None:
        removed_node.update(_dev_folder(data)["children"].pop(1))

    _edit_chrome(chrome_user_data, "Default", remove)
    report = sync_profiles(settings, now=NOW)
    assert report.archived == {"Default": 1} and report.updated == {"Default": 0}
    archived = read_bookmark(ice_path)
    assert archived.archived is True and archived.body == "## 요약\n사용자가 적은 메모.\n"
    assert archived.summary_status == "ok" and archived.date == date(2026, 11, 4)
    assert GUID_ICE not in {b.guid for b in iter_bookmarks(settings, "Default")}
    assert sync_profiles(settings, now=NOW).archived == {"Default": 0}  # already archived: not counted again

    _edit_chrome(chrome_user_data, "Default", lambda data: _dev_folder(data)["children"].insert(1, removed_node))
    report = sync_profiles(settings, now=NOW)
    assert report.updated == {"Default": 1} and report.created == {"Default": 0}
    assert read_bookmark(ice_path).archived is False


def test_moved_into_private_folder_is_archived(empty_repo: Path, chrome_user_data: Path) -> None:
    """A bookmark that moves into ``private`` must vanish from insights: archived, counted as excluded."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)

    def hide(data: dict[str, Any]) -> None:
        node = _dev_folder(data)["children"].pop(2)
        data["roots"]["bookmark_bar"]["children"][1]["children"].append(node)

    _edit_chrome(chrome_user_data, "Default", hide)
    report = sync_profiles(settings, now=NOW)
    assert report.archived == {"Default": 1} and report.excluded == {"Default": 2}
    assert _by_guid(settings, "Default")[GUID_RUST].archived is True


def test_readded_bookmark_with_new_guid_reuses_existing_file(empty_repo: Path, chrome_user_data: Path) -> None:
    """Same normalized URL under a fresh guid → the existing file is updated, not duplicated."""
    settings = _settings(empty_repo, chrome_user_data, "Default")
    sync_profiles(settings, now=NOW)

    def readd(data: dict[str, Any]) -> None:
        node = _dev_folder(data)["children"][1]
        node["guid"] = "ffffffff-0001-4000-8000-000000000099"
        node["url"] = "HTTPS://Example.com/webrtc/ice-restart/#top"
        node["date_added"] = "13438500000000000"

    _edit_chrome(chrome_user_data, "Default", readd)
    report = sync_profiles(settings, now=NOW)
    assert report.created == {"Default": 0} and report.archived == {"Default": 0}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES
    assert GUID_ICE in _by_guid(settings, "Default")


def test_resync_keeps_summary_body_and_status(settings: BookmarkInsightSettings) -> None:
    """The sample repo (summarized files) survives a re-sync untouched except for new files."""
    before = read_bookmark(next(settings.profile_bookmarks_dir("Default").rglob("*7f3a9c.md")))
    report = sync_profiles(settings, now=NOW)
    assert report.created == {"Default": 2} and report.updated == {"Default": 0} and report.archived == {"Default": 0}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES

    after = read_bookmark(Path(before.path))
    assert after.body == before.body and after.body.startswith("## 요약")
    assert after.summary_status == "ok" and after.summarized_at == "2026-11-06"
    assert after.fetch_attempts == 1 and after.last_fetch_at == "2026-11-06T09:00:00Z"
    assert after.to_frontmatter() == before.to_frontmatter()
    rust = _by_guid(settings, "Default")[GUID_RUST]
    assert rust.summary_status == "fetch_failed" and rust.body.startswith("## 요약")


# --- filters ---------------------------------------------------------------------------------------


def test_sync_since_limits_first_sync(empty_repo: Path, chrome_user_data: Path) -> None:
    """Only bookmarks whose local ``date`` is on/after ``sync_since`` are created (zero date = sync date passes)."""
    settings = _settings(empty_repo, chrome_user_data, "Default", sync_since="2026-11-04")
    report = sync_profiles(settings, now=NOW)
    assert report.created == {"Default": 3}
    assert set(_by_guid(settings, "Default")) == {GUID_ICE, GUID_RUST, GUID_ZERO}

    invalid = _settings(empty_repo, chrome_user_data, "Default", sync_since="next week")
    report = sync_profiles(invalid, now=NOW)
    assert report.created == {"Default": 2} and any("sync_since" in w for w in report.warnings)


def test_include_folder_filter_uses_root_key_paths(empty_repo: Path, chrome_user_data: Path) -> None:
    """``bookmark_bar/개발`` keeps that subtree only; the 읽을거리 copy no longer contributes a tag."""
    settings = _settings(empty_repo, chrome_user_data, "Default", include_folders=("bookmark_bar/개발",))
    report = sync_profiles(settings, now=NOW)
    assert report.created == {"Default": 3} and report.excluded == {"Default": 1}
    bookmarks = _by_guid(settings, "Default")
    assert set(bookmarks) == {GUID_WEBRTC, GUID_ICE, GUID_RUST}
    assert bookmarks[GUID_WEBRTC].tags == ["개발"]

    whole_root = _settings(empty_repo, chrome_user_data, "Profile 1", include_folders=("synced", "other/"))
    assert sync_profiles(whole_root, now=NOW).created == {"Profile 1": 0}


def test_exclude_folder_matches_any_depth_case_insensitively(empty_repo: Path, chrome_user_data: Path) -> None:
    """Excluded folder names apply anywhere in the tree; nested bookmarks under them are never written."""
    settings = _settings(empty_repo, chrome_user_data, "Default", exclude_folders=("개발", "PRIVATE"))
    report = sync_profiles(settings, now=NOW)
    assert report.excluded == {"Default": 4} and report.created == {"Default": 3}
    by_url = {b.url: b for b in iter_bookmarks(settings, "Default")}
    survivor = by_url["https://example.com/webrtc/reconnect"]  # the 읽을거리 copy is now the only node
    assert survivor.guid == "a1b2c3d4-0005-4000-8000-000000000005" and survivor.tags == ["읽을거리"]
    assert GUID_WEBRTC not in _by_guid(settings, "Default")


# --- Chrome file robustness ---------------------------------------------------------------------------


def test_transient_parse_error_is_retried_once(
    empty_repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid JSON (Chrome mid-write) is retried after the sleep; valid content on the retry syncs normally."""
    bookmarks_path = chrome_user_data / "Default" / "Bookmarks"
    valid = bookmarks_path.read_text(encoding="utf-8")
    bookmarks_path.write_text(valid[: len(valid) // 2], encoding="utf-8")
    sleeps: list[float] = []

    def finish_chrome_write(seconds: float) -> None:
        sleeps.append(seconds)
        bookmarks_path.write_text(valid, encoding="utf-8")

    monkeypatch.setattr(time, "sleep", finish_chrome_write)
    settings = _settings(empty_repo, chrome_user_data, "Default")
    report = sync_profiles(settings, now=NOW)
    assert sleeps == [0.5] and report.warnings == [] and report.created == {"Default": 5}


def test_persistent_parse_error_warns_and_skips_profile(
    empty_repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Still broken after the retry → warning, profile skipped, nothing written."""
    (chrome_user_data / "Default" / "Bookmarks").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    report = sync_profiles(_settings(empty_repo, chrome_user_data, "Default", "Profile 1"), now=NOW)
    assert report.profiles == ["Profile 1"]
    assert len(report.warnings) == 1 and "Default" in report.warnings[0]
    assert not (empty_repo / "bookmarks" / "Default").exists()


def test_cli_prints_summary(
    empty_repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m bookmark_insight.sync`` reports per-profile counts and skipped profiles."""
    monkeypatch.chdir(empty_repo)  # no .env here
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    assert sync_main(["--repo", str(empty_repo), "--chrome-dir", str(chrome_user_data), "--profile", "Default"]) == 0
    out = capsys.readouterr().out
    assert "Default: created=5 updated=0 archived=0 excluded=1" in out
    assert "Profile 1: skipped" in out
    assert sync_main(["--chrome-dir", str(chrome_user_data)]) == 2


# --- AccountBookmarks (signed-in Chrome) ------------------------------------------------


def _split_into_account_bookmarks(chrome: Path, profile: str) -> None:
    """Move the ``other`` root of ``Bookmarks`` into a new ``AccountBookmarks`` file (both files present)."""
    path = chrome / profile / "Bookmarks"
    data = json.loads(path.read_text(encoding="utf-8"))
    account = json.loads(json.dumps(data))
    for key in ("bookmark_bar", "synced"):
        account["roots"][key]["children"] = []
    data["roots"]["other"]["children"] = []
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (chrome / profile / "AccountBookmarks").write_text(json.dumps(account, ensure_ascii=False), encoding="utf-8")


def test_account_bookmarks_file_is_read_and_merged(empty_repo: Path, chrome_user_data: Path) -> None:
    """``Bookmarks`` + ``AccountBookmarks`` of one profile are merged: same files and tags as a single file."""
    _split_into_account_bookmarks(chrome_user_data, "Default")
    settings = _settings(empty_repo, chrome_user_data, "Default")
    report = sync_profiles(settings, now=NOW)
    assert report.profiles == ["Default"] and report.warnings == []
    assert report.created == {"Default": 5} and report.excluded == {"Default": 1}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES
    assert _by_guid(settings, "Default")[GUID_WEBRTC].tags == ["개발", "읽을거리"]  # copy lives in the other file


def test_only_account_bookmarks_file_is_enough(empty_repo: Path, chrome_user_data: Path) -> None:
    """A signed-in profile without ``Bookmarks`` (only ``AccountBookmarks``) syncs exactly like before."""
    (chrome_user_data / "Default" / "Bookmarks").rename(chrome_user_data / "Default" / "AccountBookmarks")
    settings = _settings(empty_repo, chrome_user_data, "Default")
    report = sync_profiles(settings, now=NOW)
    assert report.profiles == ["Default"] and report.warnings == []
    assert report.created == {"Default": 5} and report.excluded == {"Default": 1}
    assert _relative_files(settings, "Default") == EXPECTED_DEFAULT_FILES


def test_cli_discover_lists_profiles_and_env_lines(
    chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--discover`` lists every profile with its bookmark files and prints the .env lines, without syncing."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    assert sync_main(["--discover", "--chrome-dir", str(chrome_user_data)]) == 0
    out = capsys.readouterr().out
    assert "Default" in out and "Bookmarks: 7 bookmarks" in out and "Profile 1" in out
    assert "Profile 2" in out and "no bookmark file" in out
    assert f"BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR={chrome_user_data}" in out
    assert "BOOKMARK_INSIGHT_CHROME_PROFILES=Default" in out
    assert not (chrome_user_data / "repo").exists()
