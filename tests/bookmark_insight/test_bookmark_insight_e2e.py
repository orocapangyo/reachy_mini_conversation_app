"""End-to-end: Chrome fixture → sync → summarize (mock HTTP) → cluster (FakeLLM) → note → tools.

This is the 합류 test of §6.4: tracks A, B and C running together through the
app-side tools, with no network and no LLM key.
"""

import asyncio
from typing import Any
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

import httpx
import pytest

import bookmark_insight.summarize.fetch as fetch_mod
import bookmark_insight.summarize.runner as runner_mod
from bookmark_insight import api
from bookmark_insight.store import read_note, iter_bookmarks
from bookmark_insight.session import SESSION_MANAGER
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.get_insight import GetInsight
from reachy_mini_conversation_app.tools.get_bookmark import GetBookmark
from reachy_mini_conversation_app.tools.build_insight import BuildInsight
from reachy_mini_conversation_app.tools.finish_insight import FinishInsight
from reachy_mini_conversation_app.tools.list_bookmarks import ListBookmarks


TODAY = date(2026, 11, 6)
PAGE = (
    "<html><head><meta charset='utf-8'><title>t</title></head><body><article>"
    "<p>WebRTC 연결이 끊겼을 때 ICE restart와 세션 재협상을 어떻게 설계하는지 다룬다.</p>"
    "<p>네트워크 전환(Wi-Fi에서 LTE로)과 앱 백그라운드 복귀는 원인이 달라서 복구 전략도 구분해야 한다.</p>"
    "<p>재접속 중에도 미디어 트랙을 유지하면 사용자는 끊김을 거의 느끼지 못한다.</p>"
    "<p>ICE restart가 실패하면 전체 재협상으로 넘어가는 2단계 전략이 가장 안정적이었다.</p>"
    "<p>마지막으로 모바일 브라우저별 차이와 타임아웃 값을 표로 정리한다.</p>"
    "</article></body></html>"
)


def _deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


def _handler(request: httpx.Request) -> httpx.Response:
    if "rust" in request.url.path:
        return httpx.Response(404, text="nope")
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=PAGE)


@pytest.fixture
def e2e_env(empty_repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty repo + fixture Chrome data, mock HTTP, no sleeps, fixed today, clean session."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", str(empty_repo))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR", str(chrome_user_data))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_PROFILES", "Default")
    monkeypatch.delenv("BOOKMARK_INSIGHT_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(api, "_today", lambda: TODAY)
    monkeypatch.setattr(runner_mod, "_sleep", lambda _s: None)
    monkeypatch.setattr(
        fetch_mod, "build_client", lambda transport=None: httpx.Client(transport=httpx.MockTransport(_handler))
    )
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()
    yield empty_repo
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()


@pytest.mark.asyncio
async def test_full_pipeline_through_the_tools(e2e_env: Path) -> None:
    """Generating → build_insight → ready → follow-ups → finish, all against fresh data."""
    deps = _deps()
    kwargs: dict[str, Any] = {"since": "2026-10-31", "until": "2026-11-06"}

    first = await GetInsight()(deps, **kwargs)
    # The repo is empty, but get_insight syncs Chrome first: the fixture bookmarks are now
    # in the repo and there is no note yet → generating (bookmark_count = 3 in the period).
    assert first["status"] == "generating" and first["bookmark_count"] == 3
    assert "sync_warning" not in first

    built = await BuildInsight()(deps, **kwargs)
    assert built.get("error") is None, built
    assert built["status"] == "ready"
    assert built["profile"] == "Default"
    assert "sync_warning" not in built
    topics = [cluster["topic"] for cluster in built["clusters"]]
    assert topics, built
    note_path = Path(built["note_path"])
    assert note_path.is_file() and note_path.parent.name == "Default"

    # Sync mirrored the fixture: private excluded, duplicate URL merged, other profile untouched.
    synced = {bookmark.url: bookmark for bookmark in iter_bookmarks(_settings_from_env(), "Default")}
    assert "https://example.com/secret/diary" not in synced
    assert sorted(synced["https://example.com/webrtc/reconnect"].tags) == ["개발", "읽을거리"]
    assert not (e2e_env / "bookmarks" / "Profile 1").exists()
    # Summaries: HTML pages summarized, the 404 one marked fetch_failed and listed as unavailable.
    assert synced["https://example.com/webrtc/reconnect"].summary_status == "ok"
    rust = synced["https://example.com/rust/async-intro"]
    assert rust.summary_status == "fetch_failed"
    assert rust.guid in built["unavailable"]
    assert (e2e_env / ".bookmark_insight" / "usage.jsonl").is_file()

    # Second call: the note is fresh → ready without rebuilding, and a session is open.
    again = await GetInsight()(deps, **kwargs)
    assert again["status"] == "ready"
    assert [c["topic"] for c in again["clusters"]] == topics
    assert SESSION_MANAGER.current() is not None

    listed = await ListBookmarks()(deps, **kwargs)
    assert listed["count"] == 3 and listed["bookmarks"][0]["date"] == "2026-11-05"
    one = await GetBookmark()(deps, id=synced["https://example.com/webrtc/reconnect"].guid)
    assert one["summary_status"] == "ok" and "WebRTC" in one["summary"]

    done = await FinishInsight()(deps, conclusion="재접속은 백그라운드 복귀 케이스부터 본다.")
    assert done["ok"] is True
    note, _ = read_note(note_path)
    assert len(note.conversations) == 1 and "백그라운드 복귀" in note.conversations[0]
    assert SESSION_MANAGER.current() is None


@pytest.mark.asyncio
async def test_backfill_in_chunks_through_the_tools(e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M7: cap 1 per call → partial → partial (resume) → ready, with get_insight reporting partial in between."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_BATCH_MAX_SUMMARIES", "1")
    monkeypatch.setenv("BOOKMARK_INSIGHT_CALL_INTERVAL_S", "0")
    deps = _deps()
    kwargs: dict[str, Any] = {"since": "2026-10-31", "until": "2026-11-06"}

    assert (await GetInsight()(deps, **kwargs))["status"] == "generating"

    first = await BuildInsight()(deps, **kwargs)
    assert first.get("error") is None, first
    assert first["status"] == "partial"
    assert first["summarized_count"] == 1 and first["pending_count"] == 2 and first["daily_cap_reached"] is False
    assert "2건은 아직 안 봤어" in first["next_action"]
    note_path = Path(first["note_path"])
    partial_note, rendered = read_note(note_path)
    assert partial_note.complete is False and len(partial_note.pending) == 2
    assert "## 미요약" in rendered and "아직 요약하지 않은 북마크 2건" in rendered
    assert SESSION_MANAGER.current() is not None

    # Asking again before continuing: the partial note is current, so no rebuild is forced.
    meanwhile = await GetInsight()(deps, **kwargs)
    assert meanwhile["status"] == "partial"
    assert meanwhile["summarized_count"] == 1 and meanwhile["pending_count"] == 2
    assert "build_insight(since='2026-10-31', until='2026-11-06', profile='Default')" in meanwhile["next_action"]

    # "이어서 정리해 줘" → the next chunk; then the last one completes the note.
    second = await BuildInsight()(deps, **kwargs)
    assert second["status"] == "partial" and second["summarized_count"] == 2 and second["pending_count"] == 1
    third = await BuildInsight()(deps, **kwargs)
    assert third["status"] == "ready" and "pending_count" not in third
    complete_note, rendered = read_note(note_path)
    assert complete_note.complete is True and complete_note.pending == [] and "## 미요약" not in rendered
    assert len(complete_note.bookmark_ids) == 3
    from bookmark_insight.store import bookmarks_in_period
    from bookmark_insight.periods import Period

    week = Period(since=date(2026, 10, 31), until=date(2026, 11, 6))
    statuses = {b.url: b.summary_status for b in bookmarks_in_period(_settings_from_env(), "Default", week)}
    assert statuses["https://example.com/rust/async-intro"] == "fetch_failed"
    assert len(statuses) == 3 and all(status != "pending" for status in statuses.values())
    assert (await GetInsight()(deps, **kwargs))["status"] == "ready"


def _settings_from_env() -> Any:
    from bookmark_insight.settings import load_settings

    return load_settings()


def test_repo_lock_is_reentrant(empty_repo: Path) -> None:
    """A batch may nest repo_lock (sync inside build) without deadlocking on the lock file."""
    from bookmark_insight.store import repo_lock
    from bookmark_insight.settings import BookmarkInsightSettings

    settings = BookmarkInsightSettings(bookmark_repo=empty_repo)

    async def _nested() -> bool:
        with repo_lock(settings, timeout_s=1.0):
            with repo_lock(settings, timeout_s=1.0):
                return (empty_repo / ".bookmark_insight" / "lock").is_file()

    assert asyncio.run(_nested()) is True
    assert not (empty_repo / ".bookmark_insight" / "lock").exists()
