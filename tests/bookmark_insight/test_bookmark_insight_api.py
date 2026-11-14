"""M9: ``bookmark_insight.api`` is the single implementation behind the app tools and the MCP server."""

from __future__ import annotations
import json
import time
from typing import Any, Callable, Awaitable
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

import pytest

import bookmark_insight.insight as insight_mod
from bookmark_insight import api
from bookmark_insight.store import note_path, read_note
from bookmark_insight.models import InsightNote
from bookmark_insight.periods import Period
from bookmark_insight.session import SESSION_MANAGER
from bookmark_insight.settings import BookmarkInsightSettings
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.get_insight import GetInsight
from reachy_mini_conversation_app.tools.get_bookmark import GetBookmark
from reachy_mini_conversation_app.tools.build_insight import BuildInsight
from reachy_mini_conversation_app.tools.finish_insight import FinishInsight
from reachy_mini_conversation_app.tools.list_bookmarks import ListBookmarks


TODAY = date(2026, 11, 6)
WEBRTC_ID = "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05"
Caller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


async def _via_tool(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    tool = {
        "get_insight": GetInsight,
        "build_insight": BuildInsight,
        "get_bookmark": GetBookmark,
        "list_bookmarks": ListBookmarks,
        "finish_insight": FinishInsight,
    }[name]()
    return await tool(_deps(), **kwargs)


async def _via_api(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = getattr(api, name)(**kwargs)
    return result


CALLERS: list[Any] = [pytest.param(_via_tool, id="tool"), pytest.param(_via_api, id="api")]


@pytest.fixture(autouse=True)
def _fixed_today_and_clean_session(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(api, "_today", lambda: TODAY)
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()
    api._LAST_SYNC_AT["Default"] = time.monotonic()  # fixture Chrome holds more bookmarks than the sample repo
    yield
    SESSION_MANAGER.reset()
    api.reset_sync_throttle()


@pytest.fixture
def env(repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both hosts at the sample repository and fake Chrome data, ``Default`` as the only allowed profile."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", str(repo))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR", str(chrome_user_data))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_PROFILES", "Default")
    monkeypatch.delenv("BOOKMARK_INSIGHT_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return repo


def _fake_build(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
    note, _ = read_note(note_path(settings, profile, period))
    return note


# --- same results through both hosts -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "kwargs", "expect"),
    [
        ("get_insight", {"days": 7}, {"status": "ready", "profile": "Default"}),
        ("get_insight", {"since": "2026-09-01", "until": "2026-09-07"}, {"status": "empty"}),
        ("get_insight", {"since": "2026-11-03", "until": "2026-11-04"}, {"status": "generating", "bookmark_count": 2}),
        ("get_insight", {"profile": "Profile 1"}, {"error": "unknown profile", "available": ["Default"]}),
        ("get_insight", {"days": 7, "since": "2026-11-01"}, {"error": "use either days or since/until, not both"}),
        ("list_bookmarks", {"days": 7}, {"count": 3}),
        ("list_bookmarks", {"days": 7, "limit": 2}, {"count": 2}),
        ("list_bookmarks", {"limit": "x"}, {"error": "limit must be an integer, got 'x'"}),
        ("get_bookmark", {"id": WEBRTC_ID}, {"title": "WebRTC 재접속 처리 패턴", "summary_status": "ok"}),
        ("get_bookmark", {"id": "nope"}, {"error": "bookmark not found: nope"}),
        ("get_bookmark", {}, {"error": "id is required"}),
        ("finish_insight", {"conclusion": "x"}, {"error": "no open insight session"}),
        ("finish_insight", {"conclusion": "x", "reason": "auto"}, {"error": "reason 'auto' is reserved for the app"}),
        ("finish_insight", {"conclusion": ""}, {"error": "conclusion is required"}),
    ],
)
async def test_tool_and_api_return_the_same_result(
    env: Path, name: str, kwargs: dict[str, Any], expect: dict[str, Any]
) -> None:
    """Every operation gives byte-identical dicts whether called through the app tool or ``bookmark_insight.api``."""
    via_tool = await _via_tool(name, kwargs)
    SESSION_MANAGER.reset()
    via_api = await _via_api(name, kwargs)

    assert json.dumps(via_tool, ensure_ascii=False, sort_keys=True) == json.dumps(
        via_api, ensure_ascii=False, sort_keys=True
    )
    for key, value in expect.items():
        assert via_api[key] == value
    if "error" not in via_api:
        assert via_api["today"] == "2026-11-06" and via_api["next_action"]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", CALLERS)
async def test_build_insight_same_through_both_hosts(env: Path, monkeypatch: pytest.MonkeyPatch, call: Caller) -> None:
    """``build_insight`` (pipeline faked) returns ready with a session open from either host."""
    monkeypatch.setattr(insight_mod, "build_insight", _fake_build)
    result = await call("build_insight", {"since": "2026-10-31", "until": "2026-11-06"})
    assert result["status"] == "ready" and result["profile"] == "Default" and len(result["clusters"]) == 2
    assert result["next_action"] == api.BUILT_NEXT_ACTION
    assert SESSION_MANAGER.current() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("call", CALLERS)
async def test_build_insight_error_is_a_dict_from_both_hosts(
    env: Path, monkeypatch: pytest.MonkeyPatch, call: Caller
) -> None:
    """A pipeline failure never raises: the same error dict (with the outage wording) from either host."""

    def _down(*_: Any, **__: Any) -> InsightNote:
        raise insight_mod.ClusterUnavailableError("cluster model unavailable: 503 UNAVAILABLE")

    monkeypatch.setattr(insight_mod, "build_insight", _down)
    result = await call("build_insight", {"days": 7})
    assert "503" in result["error"] and result["next_action"] == api.BUSY_NEXT_ACTION
    assert result["profile"] == "Default"


@pytest.mark.asyncio
@pytest.mark.parametrize("call", CALLERS)
async def test_finish_insight_round_trip(env: Path, call: Caller) -> None:
    """get_insight opens the session, finish_insight closes it and writes the conclusion (either host)."""
    opened = await call("get_insight", {"days": 7})
    assert opened["status"] == "ready"
    result = await call("finish_insight", {"conclusion": "결론 문장", "reason": "confirmed"})
    assert result == {
        "ok": True,
        "note_path": opened["note_path"],
        "reason": "confirmed",
        "next_action": api.FINISH_NEXT_ACTION,
    }
    note, _ = read_note(Path(opened["note_path"]))
    assert "결론 문장" in note.conversations[-1]
    assert SESSION_MANAGER.current() is None


def test_api_carries_m7_statuses_for_every_host(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backfilling (registry) and partial (incomplete note) come from the package, not the app wrapper."""
    week = Period(since=date(2026, 10, 31), until=date(2026, 11, 6))
    with api.building("Default", week):
        meanwhile = api.get_insight(days=7)
    assert meanwhile["status"] == "backfilling" and meanwhile["next_action"] == api.BACKFILLING_NEXT_ACTION
    assert SESSION_MANAGER.current() is None
    assert not api.is_building("Default", week)

    def _partial(settings: BookmarkInsightSettings, llm: Any, profile: str, period: Period, **_: Any) -> InsightNote:
        note = _fake_build(settings, llm, profile, period)
        note.complete = False
        note.pending = ["ba5ea000-0001-4000-8000-00000000000a"]
        note.bookmark_ids = [*note.bookmark_ids, *note.pending]
        return note

    monkeypatch.setattr(insight_mod, "build_insight", _partial)
    result = api.build_insight(since="2026-10-31", until="2026-11-06")
    assert result["status"] == "partial" and result["pending_count"] == 1 and result["summarized_count"] == 3
    assert "1건은 아직 안 봤어" in result["next_action"]


def test_api_finish_insight_attaches_a_host_transcript(env: Path) -> None:
    """The host may pass a rendered transcript (M8); without one the section is conclusion only."""
    opened = api.get_insight(days=7)
    assert api.finish_insight(conclusion="결론", transcript="사용자: 안녕\n리치미니: 안녕하세요")["ok"] is True
    section = read_note(Path(opened["note_path"]))[0].conversations[-1]
    assert (
        "결론" in section and "<details><summary>대화 원문</summary>" in section and "리치미니: 안녕하세요" in section
    )

    api.get_insight(days=7)
    assert api.finish_insight(conclusion="둘째 결론")["ok"] is True
    last = read_note(Path(opened["note_path"]))[0].conversations[-1]
    assert "둘째 결론" in last and "<details>" not in last


def test_api_never_raises_on_unconfigured_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the repo path every operation answers with the configuration error and the settings-page guide."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    for name in ("get_insight", "build_insight", "list_bookmarks"):
        result = getattr(api, name)()
        assert "BOOKMARK_INSIGHT_BOOKMARK_REPO" in result["error"]
        assert result["settings_url"] == api.SETTINGS_URL
        assert result["settings_section"] == "Bookmark Insight"
        assert len(result["setup_steps"]) == 4
        assert result["restart_required"] is False
        assert "127.0.0.1 7860" in result["next_action"] and "Bookmark Insight" in result["next_action"]
    assert "BOOKMARK_INSIGHT_BOOKMARK_REPO" in api.get_bookmark(id="x")["error"]


def test_api_picks_up_the_repo_set_after_startup(
    repo: Path, chrome_user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuring the repo from the settings page needs no restart: the next call already reads it."""
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    assert "settings_url" in api.get_insight(days=7)
    monkeypatch.setenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", str(repo))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR", str(chrome_user_data))
    monkeypatch.setenv("BOOKMARK_INSIGHT_CHROME_PROFILES", "Default")
    result = api.get_insight(days=7)
    assert "error" not in result and result["status"] == "ready"


# --- MCP server --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_server_registers_the_five_tools_over_api(env: Path) -> None:
    """The FastMCP server lists exactly the five operations and routes a call to ``bookmark_insight.api``."""
    pytest.importorskip("mcp.server.fastmcp")
    from bookmark_insight import mcp_server

    server = mcp_server.create_server()
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == set(mcp_server.TOOL_NAMES)
    by_name = {tool.name: tool for tool in tools}
    assert "Default" in by_name["get_insight"].description  # profile hint from the configured allow list
    assert set(by_name["list_bookmarks"].inputSchema["properties"]) == {"days", "since", "until", "profile", "limit"}
    assert by_name["build_insight"].inputSchema["required"] == ["since", "until"]
    assert by_name["get_bookmark"].inputSchema["required"] == ["id"]

    _content, structured = await server.call_tool("get_bookmark", {"id": WEBRTC_ID})
    assert isinstance(structured, dict)
    assert structured["title"] == "WebRTC 재접속 처리 패턴" and structured["today"] == "2026-11-06"
    assert structured == api.get_bookmark(id=WEBRTC_ID)


def test_mcp_server_cli_parses_transport_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--http`` selects streamable HTTP with host/port; default is stdio."""
    pytest.importorskip("mcp.server.fastmcp")
    from bookmark_insight import mcp_server

    runs: list[tuple[str, str, int]] = []

    class _FakeServer:
        def __init__(self, host: str, port: int) -> None:
            self.host, self.port = host, port

        def run(self, transport: str = "stdio") -> None:
            runs.append((transport, self.host, self.port))

    monkeypatch.setattr(mcp_server, "create_server", lambda *, host, port: _FakeServer(host, port))
    assert mcp_server.main([]) == 0
    assert mcp_server.main(["--http", "--port", "9999"]) == 0
    assert runs == [("stdio", "127.0.0.1", 8765), ("streamable-http", "127.0.0.1", 9999)]

    def _missing(**_: Any) -> Any:
        raise RuntimeError(mcp_server.MISSING_MCP_MESSAGE)

    monkeypatch.setattr(mcp_server, "create_server", _missing)
    assert mcp_server.main([]) == 2
