"""M5 (track C): the app-side Bookmark Insight tools wrap the package per design doc §2.2."""

from __future__ import annotations
import json
import time
import asyncio
from typing import Any
from pathlib import Path
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

import bookmark_insight.sync as sync_mod
import bookmark_insight.insight as insight_mod
import reachy_mini_conversation_app.tools as tools_pkg
import reachy_mini_conversation_app.tools.build_insight as build_insight_mod
import reachy_mini_conversation_app.bookmark_insight_transcript_log as transcript_log_mod
from bookmark_insight import api
from bookmark_insight.sync import SyncReport
from bookmark_insight.store import note_path, read_note, read_bookmark, modify_bookmark
from bookmark_insight.models import Bookmark, InsightNote
from bookmark_insight.periods import Period
from bookmark_insight.session import SESSION_MANAGER
from bookmark_insight.settings import BookmarkInsightSettings
from reachy_mini_conversation_app.config import list_tool_module_names
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools.get_insight import GetInsight
from reachy_mini_conversation_app.tools.get_bookmark import GetBookmark, extract_summary
from reachy_mini_conversation_app.tools.build_insight import BuildInsight
from reachy_mini_conversation_app.tools.finish_insight import FinishInsight
from reachy_mini_conversation_app.tools.list_bookmarks import ListBookmarks
from reachy_mini_conversation_app.bookmark_insight_transcript_log import TranscriptLogger


TODAY = date(2026, 11, 6)
WEEK = Period(since=date(2026, 10, 31), until=date(2026, 11, 6))
WEBRTC_ID = "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05"
ICE_ID = "a1b2c3d4-0001-4000-8000-000000000001"
RUST_ID = "a1b2c3d4-0002-4000-8000-000000000002"
TOOL_NAMES = ("build_insight", "finish_insight", "get_bookmark", "get_insight", "list_bookmarks")
TOOL_CLASSES: tuple[type[Tool], ...] = (BuildInsight, FinishInsight, GetBookmark, GetInsight, ListBookmarks)


def _deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


@pytest.fixture(autouse=True)
def _fixed_today_and_clean_session(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pin ``today``, keep sessions from leaking, and hold the read-tools' Chrome sync throttle.

    The read tools sync Chrome before reading (§2.4). The fixture Chrome data holds
    more bookmarks than the sample repo, so an unthrottled sync would make every
    "ready" expectation stale; tests that want the sync call ``reset_sync_throttle``.
    """
    monkeypatch.setattr(api, "_today", lambda: TODAY)
    monkeypatch.setattr(transcript_log_mod, "TRANSCRIPT_LOG", None)  # conclusion-only unless a test installs one
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()
    api.reset_building()
    api._LAST_SYNC_AT["Default"] = time.monotonic()
    yield
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()
    api.reset_building()


@pytest.fixture
def env(repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the tools at the sample repository and fake Chrome data, ``Default`` as the only allowed profile."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", str(repo))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR", str(chrome_user_data))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_PROFILES", "Default")
    monkeypatch.delenv("BOOKMARK_INSIGHT_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return repo


def _bookmark_files(repo: Path) -> list[Path]:
    return sorted((repo / "bookmarks" / "Default").rglob("*.md"))


def _fixture_note(settings: BookmarkInsightSettings, profile: str, period: Period) -> InsightNote:
    note, _ = read_note(note_path(settings, profile, period))
    return note


# --- configuration -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_cls", TOOL_CLASSES[:1] + TOOL_CLASSES[2:])
async def test_unconfigured_repo_returns_error(monkeypatch: pytest.MonkeyPatch, tool_cls: type[Tool]) -> None:
    """Without BOOKMARK_INSIGHT_BOOKMARK_REPO every repository-backed tool fails softly."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    result = await tool_cls()(_deps(), id="x")
    assert "BOOKMARK_INSIGHT_BOOKMARK_REPO" in result["error"]
    assert "next_action" in result


# --- get_insight -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_insight_ready_returns_fixture_clusters_and_opens_session(env: Path) -> None:
    """A fresh note for the period is returned as-is, fast, and the session opens."""
    started = time.perf_counter()
    result = await GetInsight()(_deps(), days=7)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.5
    assert result["status"] == "ready"
    assert result["today"] == "2026-11-06"
    assert result["profile"] == "Default"
    assert result["period"] == {"since": "2026-10-31", "until": "2026-11-06"}
    assert [cluster["topic"] for cluster in result["clusters"]] == ["WebRTC 재접속", "Rust 비동기"]
    assert result["unavailable"] == [RUST_ID]
    assert result["note_path"].endswith("2026-10-31_2026-11-06.md")
    assert "질문" in result["next_action"] and "제목만 보고" in result["next_action"]

    session = SESSION_MANAGER.current()
    assert session is not None
    assert session.profile == "Default"
    assert session.note_path == Path(result["note_path"])


@pytest.mark.asyncio
async def test_get_insight_default_args_equal_last_seven_days(env: Path) -> None:
    """Omitting every argument means days=7 ending today."""
    result = await GetInsight()(_deps())
    assert result["status"] == "ready"
    assert result["period"] == WEEK.to_dict()


@pytest.mark.asyncio
async def test_get_insight_empty_period_uses_korean_empty_line(env: Path) -> None:
    """A period with no bookmarks returns status empty and the §3.2 sentence with that period."""
    result = await GetInsight()(_deps(), since="2026-09-01", until="2026-09-07")

    assert result["status"] == "empty"
    assert result["profile"] == "Default"
    assert result["today"] == "2026-11-06"
    assert "2026-09-01부터 2026-09-07까지 동안은 새로 저장한 게 없네. 요즘 관심 있는 건 뭐야?" in result["next_action"]
    assert SESSION_MANAGER.current() is None


def test_empty_period_label_defaults_to_last_week() -> None:
    """Without since/until the empty line says 지난 일주일 (or 지난 N일)."""
    assert api.resolve_period_args(None, None, None).label() == "지난 일주일"
    assert api.resolve_period_args(7, None, None).label() == "지난 일주일"
    assert api.resolve_period_args(30, None, None).label() == "지난 30일"


@pytest.mark.asyncio
async def test_get_insight_stale_note_hands_off_to_build_insight(env: Path) -> None:
    """Removing a bookmark the note covers makes it stale: generating + exact build_insight call."""
    _bookmark_files(env)[0].unlink()

    result = await GetInsight()(_deps(), days=7)

    assert result["status"] == "generating"
    assert result["bookmark_count"] == 2
    assert result["period"] == WEEK.to_dict()
    assert "잠깐 훑어볼게" in result["next_action"]
    assert "build_insight(since='2026-10-31', until='2026-11-06', profile='Default')" in result["next_action"]
    assert SESSION_MANAGER.current() is None


@pytest.mark.asyncio
async def test_get_insight_missing_note_is_generating(env: Path) -> None:
    """A period with bookmarks but no note is generating."""
    result = await GetInsight()(_deps(), since="2026-11-03", until="2026-11-04")
    assert result["status"] == "generating"
    assert result["bookmark_count"] == 2
    assert "build_insight(since='2026-11-03', until='2026-11-04', profile='Default')" in result["next_action"]


@pytest.mark.asyncio
async def test_get_insight_unknown_profile_lists_available(env: Path) -> None:
    """Profiles outside the allow list are rejected with the usable names."""
    result = await GetInsight()(_deps(), profile="Profile 1")
    assert result["error"] == "unknown profile"
    assert result["available"] == ["Default"]
    assert "프로필" in result["next_action"]


@pytest.mark.asyncio
async def test_get_insight_days_and_since_together_is_error(env: Path) -> None:
    """Days and since are exclusive (PeriodError → error dict)."""
    result = await GetInsight()(_deps(), days=7, since="2026-11-01")
    assert "error" in result
    assert "not both" in result["error"]

    result = await GetInsight()(_deps(), since="not-a-date")
    assert "since" in result["error"]


@pytest.mark.asyncio
async def test_get_insight_profiles_fall_back_to_repo_folders(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no allow list, the profile folders in the repository are allowed."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_CHROME_PROFILES")
    result = await GetInsight()(_deps(), profile="Nope")
    assert result["available"] == ["Default"]
    result = await GetInsight()(_deps())
    assert result["profile"] == "Default"


def test_tool_descriptions_name_the_allowed_profiles(env: Path) -> None:
    """The description tells the model which profile names exist (so it never invents one)."""
    for tool_cls in (GetInsight, BuildInsight, ListBookmarks):
        description = tool_cls().description
        assert "profile is optional" in description
        assert "Default" in description


# --- build_insight ------------------------------------------------------------------------


def _fake_build(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
    return _fixture_note(settings, profile, period)


@pytest.mark.asyncio
async def test_build_insight_ready_after_pipeline(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline result (here the fixture note) comes back as ready with a session open."""
    calls: list[tuple[str, Period]] = []

    def _recording_build(
        settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any
    ) -> InsightNote:
        calls.append((profile, period))
        return _fixture_note(settings, profile, period)

    monkeypatch.setattr(insight_mod, "build_insight", _recording_build)

    result = await BuildInsight()(_deps(), since="2026-10-31", until="2026-11-06")

    assert calls == [("Default", WEEK)]
    assert result["status"] == "ready"
    assert result["profile"] == "Default"
    assert result["today"] == "2026-11-06"
    assert len(result["clusters"]) == 2
    assert "질문" in result["next_action"] or "question" in result["next_action"]
    assert "sync_warning" not in result  # real sync against the fixture Chrome data: no warnings
    assert len(_bookmark_files(env)) > 3  # sync mirrored the fixture Chrome bookmarks into the repo
    session = SESSION_MANAGER.current()
    assert session is not None and session.note_path == Path(result["note_path"])


@pytest.mark.asyncio
async def test_build_insight_pipeline_error_is_reported(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any package exception becomes {"error": ...}; the loop never sees a raise."""

    def _boom(*_: Any, **__: Any) -> InsightNote:
        raise RuntimeError("cluster model exploded")

    monkeypatch.setattr(insight_mod, "build_insight", _boom)
    result = await BuildInsight()(_deps(), days=7)
    assert "cluster model exploded" in result["error"]
    assert SESSION_MANAGER.current() is None


@pytest.mark.asyncio
async def test_build_insight_continues_when_sync_fails(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sync failure (NotImplementedError or any error) is a warning, not a failure."""
    monkeypatch.setattr(insight_mod, "build_insight", _fake_build)

    def _not_implemented(*_: Any, **__: Any) -> Any:
        raise NotImplementedError("track A pending")

    monkeypatch.setattr(sync_mod, "sync_profiles", _not_implemented)
    result = await BuildInsight()(_deps(), days=7)
    assert result["status"] == "ready"
    assert "track A pending" in result["sync_warning"]

    def _crash(*_: Any, **__: Any) -> Any:
        raise OSError("chrome locked")

    monkeypatch.setattr(sync_mod, "sync_profiles", _crash)
    result = await BuildInsight()(_deps(), days=7)
    assert result["status"] == "ready"
    assert "chrome locked" in result["sync_warning"]


@pytest.mark.asyncio
async def test_build_insight_empty_note_is_empty_status(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A note without a path (nothing written) maps to status empty."""

    def _empty(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
        return InsightNote(profile=profile, since=period.since, until=period.until, generated_at="")

    monkeypatch.setattr(insight_mod, "build_insight", _empty)
    result = await BuildInsight()(_deps(), since="2026-09-01", until="2026-09-07")
    assert result["status"] == "empty"
    assert "2026-09-01부터 2026-09-07까지" in result["next_action"]


@pytest.mark.asyncio
async def test_build_insight_validates_before_running(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown profile / bad period never reach the pipeline."""
    monkeypatch.setattr(insight_mod, "build_insight", _boom_if_called)
    assert (await BuildInsight()(_deps(), profile="Profile 1"))["error"] == "unknown profile"
    assert "not both" in (await BuildInsight()(_deps(), days=3, since="2026-11-01"))["error"]


def _boom_if_called(*_: Any, **__: Any) -> InsightNote:
    raise AssertionError("pipeline must not run")


@pytest.mark.asyncio
async def test_build_insight_cancellation_propagates(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """task_cancel must be able to stop the build: CancelledError is not swallowed."""

    async def _cancelled(*_: Any, **__: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(build_insight_mod.asyncio, "to_thread", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await BuildInsight()(_deps(), days=7)


# --- get_bookmark ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bookmark_by_guid_is_fast_and_returns_summary(env: Path) -> None:
    """Lookup by guid returns the front matter and the text under ## 요약."""
    started = time.perf_counter()
    result = await GetBookmark()(_deps(), id=WEBRTC_ID)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert result["id"] == WEBRTC_ID
    assert result["profile"] == "Default"
    assert result["title"] == "WebRTC 재접속 처리 패턴"
    assert result["url"] == "https://example.com/webrtc/reconnect"
    assert result["tags"] == ["개발", "읽을거리"]
    assert result["date"] == "2026-11-03"
    assert result["summary_status"] == "ok"
    assert result["summary"].startswith("WebRTC 연결이 끊겼을 때")
    assert "## 요약" not in result["summary"]
    assert result["today"] == "2026-11-06"
    assert "요약" in result["next_action"]


@pytest.mark.asyncio
async def test_get_bookmark_fetch_failed_rust(env: Path) -> None:
    """The fetch_failed bookmark reports its status and placeholder summary."""
    result = await GetBookmark()(_deps(), id=RUST_ID)
    assert result["summary_status"] == "fetch_failed"
    assert result["summary"] == "본문을 가져오지 못했다. 제목 기준으로만 다룸."
    assert result["title"].startswith("Rust: async/await 입문")


@pytest.mark.asyncio
async def test_get_bookmark_unknown_or_missing_id(env: Path) -> None:
    """Unknown ids and a missing id argument are soft errors."""
    assert "not found" in (await GetBookmark()(_deps(), id="00000000-0000-0000-0000-000000000000"))["error"]
    assert "error" in await GetBookmark()(_deps())


def test_extract_summary_stops_at_next_heading() -> None:
    """Only the ## 요약 section is returned; later sections and the heading are dropped."""
    body = "intro\n## 요약\n첫 줄\n둘째 줄\n\n## 메모\n사용자 메모\n"
    assert extract_summary(body) == "첫 줄\n둘째 줄"
    assert extract_summary("no heading here\n") == "no heading here"


# --- list_bookmarks ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_bookmarks_newest_first_with_limit(env: Path) -> None:
    """Bookmarks come newest first; limit truncates; count follows."""
    result = await ListBookmarks()(_deps(), days=7)
    assert result["today"] == "2026-11-06"
    assert result["profile"] == "Default"
    assert result["count"] == 3
    assert [item["id"] for item in result["bookmarks"]] == [RUST_ID, ICE_ID, WEBRTC_ID]
    assert set(result["bookmarks"][0]) == {"id", "title", "url", "tags", "date", "summary_status"}
    assert "next_action" in result

    limited = await ListBookmarks()(_deps(), days=7, limit=2)
    assert limited["count"] == 2
    assert [item["id"] for item in limited["bookmarks"]] == [RUST_ID, ICE_ID]


@pytest.mark.asyncio
async def test_list_bookmarks_excludes_archived(env: Path) -> None:
    """Archiving a bookmark through the store hides it from the list."""
    target = next(path for path in _bookmark_files(env) if "7f3a9c" in path.name)

    def _archive(bookmark: Bookmark) -> Bookmark:
        bookmark.archived = True
        return bookmark

    modify_bookmark(target, _archive)
    assert read_bookmark(target).archived is True

    result = await ListBookmarks()(_deps(), days=7)
    assert result["count"] == 2
    assert WEBRTC_ID not in [item["id"] for item in result["bookmarks"]]


@pytest.mark.asyncio
async def test_list_bookmarks_errors(env: Path) -> None:
    """Profile and period validation match get_insight."""
    assert (await ListBookmarks()(_deps(), profile="Work"))["error"] == "unknown profile"
    assert "error" in await ListBookmarks()(_deps(), days=2, since="2026-11-01")
    assert "limit" in (await ListBookmarks()(_deps(), limit="many"))["error"]


# --- finish_insight ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_insight_without_session_is_error(env: Path) -> None:
    """Nothing to close → error, nothing written."""
    result = await FinishInsight()(_deps(), conclusion="끝")
    assert result["error"] == "no open insight session"


@pytest.mark.asyncio
async def test_finish_insight_appends_conversation_and_closes(env: Path) -> None:
    """After get_insight, finishing appends ## 대화 기록 with the conclusion and closes the session."""
    opened = await GetInsight()(_deps(), days=7)
    assert opened["status"] == "ready"
    path = Path(opened["note_path"])
    before, _ = read_note(path)
    assert len(before.conversations) == 1

    result = await FinishInsight()(
        _deps(), conclusion="ICE restart 2단계 전략을 먼저 써 보기로 했다.", reason="confirmed"
    )

    assert result["ok"] is True
    assert result["note_path"] == str(path)
    assert result["reason"] == "confirmed"
    assert "인사" in result["next_action"]
    assert FinishInsight.needs_response is True
    assert SESSION_MANAGER.current() is None

    after, _ = read_note(path)
    assert len(after.conversations) == 2
    assert after.conversations[-1].startswith("## 대화 기록 (2026-")
    assert "ICE restart 2단계 전략을 먼저 써 보기로 했다." in after.conversations[-1]
    assert "<details>" not in after.conversations[-1]  # no transcript log installed → conclusion only
    assert after.clusters == before.clusters  # front matter untouched

    # Safety net: a premature explicit finish followed by the real one (2026-11-07 live test) must
    # not lose the real conclusion → recorded in the same note once; a third call is an error.
    again = await FinishInsight()(_deps(), conclusion="진짜 결론은 이거다.")
    assert again["ok"] is True and again["reopened_last"] is True and again["note_path"] == str(path)
    third, _ = read_note(path)
    assert len(third.conversations) == 3 and "진짜 결론은 이거다." in third.conversations[-1]
    assert (await FinishInsight()(_deps(), conclusion="third time"))["error"] == "no open insight session"


@pytest.mark.asyncio
async def test_finish_insight_attaches_collapsed_transcript(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M8: with a transcript log installed the section carries the session's lines in a <details> block."""
    log = TranscriptLogger(tmp_path / "conv")
    monkeypatch.setattr(transcript_log_mod, "TRANSCRIPT_LOG", log)
    log.on_transcript("user", "이전 대화", final=True)  # before the session → not attached
    time.sleep(0.002)

    opened = await GetInsight()(_deps(), days=7)
    path = Path(opened["note_path"])
    log.on_transcript("user", "이번 주 뭐 있어?", final=True)
    log.on_transcript("assistant", "WebRTC 묶음이 있어요. 궁금한 게 뭐예요?", final=False)  # delta: dropped
    log.on_transcript("assistant", "WebRTC 묶음이 있어요. 궁금한 게 뭐예요?", final=True)

    result = await FinishInsight()(_deps(), conclusion="결론.")
    assert result["ok"] is True
    note, _ = read_note(path)
    section = note.conversations[-1]
    assert section.startswith("## 대화 기록 (") and "결론." in section
    assert "<details><summary>대화 원문</summary>" in section and section.rstrip().endswith("</details>")
    assert "사용자: 이번 주 뭐 있어?" in section and "리치미니: WebRTC 묶음이 있어요. 궁금한 게 뭐예요?" in section
    assert section.count("리치미니:") == 1 and "이전 대화" not in section

    # Safety net path (session already closed, finish again): the last session's lines are attached too.
    log.on_transcript("user", "아 맞다, 진짜 결론.", final=True)
    again = await FinishInsight()(_deps(), conclusion="진짜 결론.")
    assert again["reopened_last"] is True
    last = read_note(path)[0].conversations[-1]
    assert "진짜 결론." in last and "아 맞다, 진짜 결론." in last and "이전 대화" not in last


@pytest.mark.asyncio
async def test_finish_insight_rejects_auto_and_bad_reasons(env: Path) -> None:
    """'auto' belongs to the app-side state machine; other unknown reasons are errors too."""
    await GetInsight()(_deps(), days=7)
    assert (await FinishInsight()(_deps(), conclusion="x", reason="auto"))[
        "error"
    ] == "reason 'auto' is reserved for the app"
    assert "reason" in (await FinishInsight()(_deps(), conclusion="x", reason="bored"))["error"]
    assert "conclusion" in (await FinishInsight()(_deps(), conclusion="  "))["error"]
    assert SESSION_MANAGER.current() is not None  # invalid calls do not close the session


# --- sync before read (a bookmark saved a second ago must be visible) ------------------------

NEW_ID = "c0ffee00-0000-4000-8000-000000000099"
WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000


def _add_chrome_bookmark(chrome: Path, title: str, url: str, guid: str, when: datetime) -> None:
    path = chrome / "Default" / "Bookmarks"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["roots"]["bookmark_bar"]["children"].append(
        {
            "type": "url",
            "name": title,
            "url": url,
            "guid": guid,
            "id": "999",
            "date_added": str(int(when.timestamp() * 1_000_000) + WEBKIT_EPOCH_OFFSET_US),
        }
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_read_tools_sync_chrome_before_reading(env: Path, chrome_user_data: Path) -> None:
    """A bookmark just saved in Chrome shows up in list_bookmarks and makes get_insight regenerate."""
    _add_chrome_bookmark(
        chrome_user_data,
        "오늘 저장한 글",
        "https://example.com/just-now",
        NEW_ID,
        datetime(2026, 11, 6, 9, tzinfo=timezone.utc),
    )
    api.reset_sync_throttle()
    listed = await ListBookmarks()(_deps(), days=7)
    assert NEW_ID in {item["id"] for item in listed["bookmarks"]}

    api.reset_sync_throttle()
    result = await GetInsight()(_deps(), days=7)
    assert result["status"] == "generating"  # the note no longer covers the period
    assert SESSION_MANAGER.current() is None


@pytest.mark.asyncio
async def test_sync_before_read_is_throttled(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two reads within SYNC_MIN_INTERVAL_S sync Chrome once."""
    calls: list[list[str] | None] = []

    def _record(settings: BookmarkInsightSettings, *, profiles: list[str] | None = None, **_: Any) -> SyncReport:
        calls.append(profiles)
        return SyncReport()

    monkeypatch.setattr(sync_mod, "sync_profiles", _record)
    api.reset_sync_throttle()
    await ListBookmarks()(_deps(), days=7)
    await GetInsight()(_deps(), days=7)
    assert calls == [["Default"]]


@pytest.mark.asyncio
async def test_sync_failure_does_not_break_read_tools(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome unreadable → warning in the result, the tools still answer from the repository."""

    def _crash(*_: Any, **__: Any) -> SyncReport:
        raise OSError("chrome locked")

    monkeypatch.setattr(sync_mod, "sync_profiles", _crash)
    api.reset_sync_throttle()
    result = await GetInsight()(_deps(), days=7)
    assert result["status"] == "ready" and "chrome locked" in result["sync_warning"]
    api.reset_sync_throttle()
    assert (await ListBookmarks()(_deps(), days=7))["count"] == 3


# --- finish_insight safety net after an app-side close ---------------------------------------


@pytest.mark.asyncio
async def test_finish_insight_after_auto_close_records_in_last_note(env: Path) -> None:
    """The app closed the session (idle / sleep) but the model still finishes → conclusion lands in that note."""
    opened = await GetInsight()(_deps(), days=7)
    path = Path(opened["note_path"])
    assert SESSION_MANAGER.auto_finish("슬립 전환") is not None

    result = await FinishInsight()(_deps(), conclusion="늦게 남긴 결론", reason="explicit")

    assert result["ok"] is True and result["reopened_last"] is True and result["note_path"] == str(path)
    after, _ = read_note(path)
    assert "늦게 남긴 결론" in after.conversations[-1] and "자동 종료" in after.conversations[-2]
    # the net is consumed: a further call without a new get_insight is an error
    assert (await FinishInsight()(_deps(), conclusion="again"))["error"] == "no open insight session"


# --- build_insight when the batch model is down ----------------------------------------------


@pytest.mark.asyncio
async def test_build_insight_degraded_note_is_ready_with_pending_titles(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded (previous) note is served as ready with the uncovered titles and a session."""

    def _degraded(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
        note = _fixture_note(settings, profile, period)
        note.degraded = True
        note.pending_titles = ["새 글"]
        return note

    monkeypatch.setattr(insight_mod, "build_insight", _degraded)
    result = await BuildInsight()(_deps(), since="2026-10-31", until="2026-11-06")
    assert result["status"] == "ready" and result["degraded"] is True and result["pending_titles"] == ["새 글"]
    assert "지난번 정리" in result["next_action"] and "1건" in result["next_action"]
    assert SESSION_MANAGER.current() is not None


@pytest.mark.asyncio
async def test_build_insight_model_outage_error_has_friendly_next_action(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No previous note + model down → error with the apologetic next_action; a plain bug has none."""

    def _down(*_: Any, **__: Any) -> InsightNote:
        raise insight_mod.ClusterUnavailableError("cluster model unavailable: 503 UNAVAILABLE")

    monkeypatch.setattr(insight_mod, "build_insight", _down)
    result = await BuildInsight()(_deps(), days=7)
    assert "503" in result["error"] and "바빠서" in result["next_action"]

    def _bug(*_: Any, **__: Any) -> InsightNote:
        raise KeyError("clusters")

    monkeypatch.setattr(insight_mod, "build_insight", _bug)
    assert "next_action" not in await BuildInsight()(_deps(), days=7)


@pytest.mark.asyncio
async def test_build_insight_plays_progress_signal(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The working signal wraps the pipeline: start chime before, done chime after."""
    monkeypatch.setattr(insight_mod, "build_insight", _fake_build)
    deps = _deps()
    deps.reachy_mini.media.get_output_audio_samplerate.return_value = 16000
    result = await BuildInsight()(deps, since="2026-10-31", until="2026-11-06")
    assert result["status"] == "ready"
    assert deps.reachy_mini.media.push_audio_sample.call_count == 2


# --- discovery / schema ---------------------------------------------------------------------


def test_tool_modules_are_discoverable_and_well_formed() -> None:
    """Every tool file is auto-discoverable, named after its tool, and has a tags-free schema."""
    tools_dir = Path(tools_pkg.__file__).parent
    discovered = list_tool_module_names(tools_dir)
    for name in TOOL_NAMES:
        assert name in discovered
    assert "_bookmark_insight_common" not in discovered

    for tool_cls in TOOL_CLASSES:
        tool = tool_cls()
        assert issubclass(tool_cls, Tool)
        assert tool.name in TOOL_NAMES
        assert isinstance(tool.description, str) and tool.description
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "tags" not in schema["properties"]
        spec = tool.spec()
        assert spec["name"] == tool.name

    assert set(GetInsight().parameters_schema["properties"]) == {"days", "since", "until", "profile"}
    assert set(BuildInsight().parameters_schema["properties"]) == {"days", "since", "until", "profile"}
    assert set(ListBookmarks().parameters_schema["properties"]) == {"days", "since", "until", "profile", "limit"}
    assert set(GetBookmark().parameters_schema["properties"]) == {"id"}
    assert set(FinishInsight().parameters_schema["properties"]) == {"conclusion", "reason"}
    assert FinishInsight().parameters_schema["properties"]["reason"]["enum"] == ["explicit", "confirmed"]


# --- M7 backfill: partial / backfilling statuses -----------------------------------------------

PENDING_A = "ba5ea000-0001-4000-8000-00000000000a"
PENDING_B = "ba5eb000-0002-4000-8000-00000000000b"
BUILD_CALL = "build_insight(since='2026-10-31', until='2026-11-06', profile='Default')"


def _add_pending(repo: Path, title: str, guid: str, day: date) -> Path:
    """Write a pending bookmark into the sample repo's Default profile."""
    from bookmark_insight.store import write_bookmark

    target = repo / "bookmarks" / "Default" / str(day.year) / f"{day.isoformat()} {title} {guid[:6]}.md"
    bookmark = Bookmark(title=title, url=f"https://example.com/{guid[:6]}", guid=guid, profile="Default", date=day)
    return write_bookmark(bookmark, target)


def _write_partial_fixture_note(settings: BookmarkInsightSettings, pending: list[str]) -> Path:
    """Turn the fixture week note into a partial one that covers the fixture bookmarks plus ``pending``."""
    from bookmark_insight.store import write_note, bookmarks_in_period
    from bookmark_insight.insight import render_note_body

    note = _fixture_note(settings, "Default", WEEK)
    note.complete = False
    note.pending = list(pending)
    note.bookmark_ids = [WEBRTC_ID, ICE_ID, RUST_ID] + list(pending)
    path = note_path(settings, "Default", WEEK)
    write_note(note, render_note_body(note, bookmarks_in_period(settings, "Default", WEEK)), path)
    return path


def _partial_note(settings: BookmarkInsightSettings, profile: str, period: Period, *, daily_cap: bool) -> InsightNote:
    note = _fixture_note(settings, profile, period)
    note.complete = False
    note.pending = [PENDING_A, PENDING_B]
    note.bookmark_ids = [WEBRTC_ID, ICE_ID, RUST_ID, PENDING_A, PENDING_B]
    note.daily_cap_reached = daily_cap
    return note


@pytest.mark.asyncio
async def test_build_insight_partial_result_has_counts_and_offers_to_continue(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial note comes back as status partial with the counts, a session and the continue instruction."""
    monkeypatch.setattr(
        insight_mod, "build_insight", lambda s, llm, p, period, **_: _partial_note(s, p, period, daily_cap=False)
    )
    result = await BuildInsight()(_deps(), since="2026-10-31", until="2026-11-06")
    assert result["status"] == "partial"
    assert result["summarized_count"] == 3 and result["pending_count"] == 2 and result["daily_cap_reached"] is False
    assert len(result["clusters"]) == 2 and result["unavailable"] == [RUST_ID]
    assert "2건은 아직 안 봤어" in result["next_action"] and "이어서 정리해" in result["next_action"]
    assert BUILD_CALL in result["next_action"] and "질문" in result["next_action"]
    assert "내일" not in result["next_action"]
    session = SESSION_MANAGER.current()
    assert session is not None and session.note_path == Path(result["note_path"])

    # Daily cap: the notice says tomorrow and does not hand out the build_insight call.
    monkeypatch.setattr(
        insight_mod, "build_insight", lambda s, llm, p, period, **_: _partial_note(s, p, period, daily_cap=True)
    )
    capped = await BuildInsight()(_deps(), since="2026-10-31", until="2026-11-06")
    assert capped["status"] == "partial" and capped["daily_cap_reached"] is True
    assert "오늘 요약 한도" in capped["next_action"] and "내일" in capped["next_action"]
    assert BUILD_CALL not in capped["next_action"]


@pytest.mark.asyncio
async def test_build_insight_partial_without_clusters_only_apologizes(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing summarized yet (daily cap hit before the first bookmark) → one-sentence notice, no clusters."""

    def _bare(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
        note = _partial_note(settings, profile, period, daily_cap=True)
        note.clusters, note.unavailable = [], []
        note.bookmark_ids = [PENDING_A, PENDING_B]
        return note

    monkeypatch.setattr(insight_mod, "build_insight", _bare)
    result = await BuildInsight()(_deps(), days=7)
    assert result["status"] == "partial" and result["clusters"] == []
    assert result["summarized_count"] == 0 and result["pending_count"] == 2
    assert "아직 못 봤어" in result["next_action"] and "내일" in result["next_action"]


@pytest.mark.asyncio
async def test_get_insight_partial_note_is_not_stale_and_offers_to_continue(env: Path) -> None:
    """Partial (current bookmark set) → session + counts + continue offer; finished batch → ready."""
    from bookmark_insight.settings import load_settings

    settings = load_settings()
    _add_pending(env, "미요약 글 하나", PENDING_A, date(2026, 11, 6))
    _add_pending(env, "미요약 글 둘", PENDING_B, date(2026, 11, 6))
    path = _write_partial_fixture_note(settings, [PENDING_A, PENDING_B])

    result = await GetInsight()(_deps(), days=7)
    assert result["status"] == "partial"
    assert result["summarized_count"] == 3 and result["pending_count"] == 2 and result["daily_cap_reached"] is False
    assert [cluster["topic"] for cluster in result["clusters"]] == ["WebRTC 재접속", "Rust 비동기"]
    assert "2건은 아직 안 봤어" in result["next_action"] and BUILD_CALL in result["next_action"]
    assert "제목만 보고" in result["next_action"]  # the ready talk rules follow the notice
    session = SESSION_MANAGER.current()
    assert session is not None and session.note_path == path

    # A new bookmark in the period makes even a partial note stale → generating.
    extra = _add_pending(env, "방금 저장", "ba5ec000-0003-4000-8000-00000000000c", date(2026, 11, 5))
    assert (await GetInsight()(_deps(), days=7))["status"] == "generating"
    extra.unlink()

    # Everything summarized but the note never finalized (interrupted after the last chunk) → generating.
    for guid in (PENDING_A, PENDING_B):
        target = next(p for p in _bookmark_files(env) if guid[:6] in p.name)

        def _ok(bookmark: Bookmark) -> Bookmark:
            bookmark.summary_status = "ok"
            bookmark.body = "## 요약\n요약됨\n"
            return bookmark

        modify_bookmark(target, _ok)
    SESSION_MANAGER.reset()
    pending_done = await GetInsight()(_deps(), days=7)
    assert pending_done["status"] == "generating" and SESSION_MANAGER.current() is None

    # The finalized (complete) note is ready as before.
    note, _ = read_note(path)
    note.complete, note.pending = True, []
    from bookmark_insight.store import write_note

    write_note(note, "# 완료\n", path)
    assert (await GetInsight()(_deps(), days=7))["status"] == "ready"


@pytest.mark.asyncio
async def test_get_insight_partial_reports_daily_cap_from_counter(env: Path) -> None:
    """The daily counter at the cap turns the partial offer into "내일 이어서"."""
    from bookmark_insight.settings import load_settings
    from bookmark_insight.summarize.runner import save_daily_count

    settings = load_settings()
    _add_pending(env, "미요약 글", PENDING_A, date(2026, 11, 6))
    _write_partial_fixture_note(settings, [PENDING_A])
    save_daily_count(settings, date.today(), settings.daily_max_summaries)

    result = await GetInsight()(_deps(), days=7)
    assert result["status"] == "partial" and result["daily_cap_reached"] is True
    assert "오늘 요약 한도" in result["next_action"] and BUILD_CALL not in result["next_action"]


@pytest.mark.asyncio
async def test_get_insight_is_backfilling_while_build_insight_runs(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """While build_insight runs for the same profile/period, get_insight answers backfilling without a session."""
    import threading

    release = threading.Event()

    def _slow_build(
        settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any
    ) -> InsightNote:
        release.wait(5.0)
        return _fixture_note(settings, profile, period)

    monkeypatch.setattr(insight_mod, "build_insight", _slow_build)
    task = asyncio.create_task(BuildInsight()(_deps(), since="2026-10-31", until="2026-11-06"))
    for _ in range(200):
        if api.is_building("Default", WEEK):
            break
        await asyncio.sleep(0.01)
    assert api.is_building("Default", WEEK)

    meanwhile = await GetInsight()(_deps(), days=7)
    assert meanwhile["status"] == "backfilling"
    assert meanwhile["period"] == WEEK.to_dict() and meanwhile["profile"] == "Default"
    assert "아직 훑는 중이야" in meanwhile["next_action"] and "잠깐만" in meanwhile["next_action"]
    assert SESSION_MANAGER.current() is None
    # Another period is not affected.
    assert (await GetInsight()(_deps(), since="2026-11-03", until="2026-11-04"))["status"] == "generating"

    release.set()
    built = await task
    assert built["status"] == "ready" and not api.is_building("Default", WEEK)
    assert (await GetInsight()(_deps(), days=7))["status"] == "ready"


@pytest.mark.asyncio
async def test_building_registry_is_cleared_after_a_failed_build(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pipeline error must not leave the period marked as building forever."""

    def _boom(*_: Any, **__: Any) -> InsightNote:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(insight_mod, "build_insight", _boom)
    assert "error" in await BuildInsight()(_deps(), days=7)
    assert not api.is_building("Default", WEEK)
    assert (await GetInsight()(_deps(), days=7))["status"] == "ready"
