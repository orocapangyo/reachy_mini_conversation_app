"""M0: contracts, fixtures and shared store behave as the design doc says."""

import json
import unicodedata
from typing import Any
from pathlib import Path
from datetime import date

import pytest

from bookmark_insight.llm import FakeLLM, SummaryInput, parse_clusters
from bookmark_insight.store import (
    note_path,
    read_note,
    write_note,
    find_bookmark,
    list_profiles,
    note_is_stale,
    read_bookmark,
    iter_bookmarks,
    write_bookmark,
    modify_bookmark,
    bookmark_to_text,
    bookmarks_in_period,
    append_conversation_section,
)
from bookmark_insight.models import Bookmark, InsightNote, InsightCluster
from bookmark_insight.periods import PeriodError, resolve_period
from bookmark_insight.settings import BookmarkInsightSettings, load_settings
from bookmark_insight.frontmatter import join_frontmatter, split_frontmatter


TODAY = date(2026, 11, 6)


# --- settings ---------------------------------------------------------------------


def test_load_settings_defaults_and_csv_lists(tmp_path: Path) -> None:
    """Env mapping → settings; empty allow list means last-used profile; defaults applied."""
    settings = load_settings({"BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path)})
    assert settings.bookmark_repo == tmp_path
    assert settings.chrome_profiles == ()
    assert settings.default_profile is None
    assert settings.exclude_folders == ("private",)
    assert "192.168." in settings.exclude_url_patterns
    assert settings.resolved_insights_dir == tmp_path / "insights"
    assert settings.resolved_usage_log == tmp_path / ".bookmark_insight" / "usage.jsonl"
    assert settings.body_max_chars == 20_000

    multi = load_settings(
        {
            "BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path),
            "BOOKMARK_INSIGHT_CHROME_PROFILES": "Default, Profile 1",
            "BOOKMARK_INSIGHT_EXCLUDE_URL_PATTERNS": "wiki.corp.example",
        }
    )
    assert multi.chrome_profiles == ("Default", "Profile 1")
    assert multi.default_profile == "Default"
    assert multi.exclude_url_patterns[-1] == "wiki.corp.example"


def test_unconfigured_settings_raise_clearly() -> None:
    """Tools convert this into {"error": ...}."""
    with pytest.raises(RuntimeError, match="BOOKMARK_INSIGHT_BOOKMARK_REPO"):
        BookmarkInsightSettings(bookmark_repo=None).require_repo()


# --- periods ------------------------------------------------------------------------


def test_resolve_period_rules() -> None:
    """Days counts back inclusively; since/until are ISO; contradictions raise."""
    weekly = resolve_period(today=TODAY)
    assert weekly.since == date(2026, 10, 31) and weekly.until == TODAY
    assert weekly.key == "2026-10-31_2026-11-06"
    three = resolve_period(days=3, today=TODAY)
    assert three.since == date(2026, 11, 4)
    explicit = resolve_period(since="2026-10-01", until="2026-10-31", today=TODAY)
    assert explicit.key == "2026-10-01_2026-10-31"
    with pytest.raises(PeriodError):
        resolve_period(days=3, since="2026-10-01", today=TODAY)
    with pytest.raises(PeriodError):
        resolve_period(since="2026/10/01", today=TODAY)
    with pytest.raises(PeriodError):
        resolve_period(since="2026-12-01", until="2026-11-01", today=TODAY)


# --- front matter -------------------------------------------------------------------


def test_frontmatter_roundtrip_keeps_korean_and_special_characters() -> None:
    """Titles with quotes/colons survive; dates come back as ISO strings."""
    data: dict[str, Any] = {
        "title": 'Rust: async/await 입문 | 1부 "Future란?" <정리>',
        "tags": ["개발", "읽을거리"],
        "date": "2026-11-05",
        "archived": False,
        "fetch_attempts": 1,
    }
    text = join_frontmatter(data, "## 요약\n본문\n")
    loaded, body = split_frontmatter(text)
    assert loaded == data
    assert body == "## 요약\n본문\n"
    assert split_frontmatter("no front matter") == ({}, "no front matter")


# --- bookmark store -------------------------------------------------------------------


def test_sample_repo_loads_and_filters_by_period(settings: BookmarkInsightSettings) -> None:
    """Fixture bookmarks parse; period filter and archived flag behave."""
    bookmarks = list(iter_bookmarks(settings, "Default"))
    assert {b.guid[:8] for b in bookmarks} == {"7f3a9c2e", "a1b2c3d4"}
    assert len(bookmarks) == 3
    weekly = bookmarks_in_period(settings, "Default", resolve_period(today=TODAY))
    assert [b.date.isoformat() for b in weekly] == ["2026-11-05", "2026-11-04", "2026-11-03"]
    assert bookmarks_in_period(settings, "Default", resolve_period(since="2026-01-01", until="2026-01-31")) == []
    assert list_profiles(settings) == ["Default"]
    found = find_bookmark(settings, "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05")
    assert found is not None and found.title == "WebRTC 재접속 처리 패턴"
    assert found.summary_status == "ok" and found.body.startswith("## 요약")
    assert find_bookmark(settings, "ffffffff-0000-0000-0000-000000000000") is None


def test_bookmark_roundtrip_and_modify_preserves_body(settings: BookmarkInsightSettings) -> None:
    """Write → read is lossless; modify_bookmark only changes what the mutator changes."""
    original = read_bookmark(next(iter(sorted(settings.profile_bookmarks_dir("Default").rglob("*.md")))))
    assert read_bookmark(original.path).to_frontmatter() == original.to_frontmatter()
    assert bookmark_to_text(original).startswith("---\ntitle:")

    def archive(bookmark: Bookmark) -> Bookmark:
        bookmark.archived = True
        bookmark.tags = ["개발", "읽을거리", "보관"]
        return bookmark

    updated = modify_bookmark(original.path, archive)
    reread = read_bookmark(original.path)
    assert reread.archived is True and reread.tags[-1] == "보관"
    assert reread.body == original.body == updated.body
    assert list(iter_bookmarks(settings, "Default")) and all(
        not b.archived for b in iter_bookmarks(settings, "Default")
    )
    assert len(list(iter_bookmarks(settings, "Default", include_archived=True))) == 3


def test_write_bookmark_creates_profile_folders_and_nfc_paths(empty_repo: Path) -> None:
    """A fresh bookmark lands under bookmarks/<profile>/ and is found back via NFC listing."""
    settings = BookmarkInsightSettings(bookmark_repo=empty_repo, chrome_profiles=("Profile 1",))
    title_nfd = unicodedata.normalize("NFD", "한글 제목")
    bookmark = Bookmark(
        title="한글 제목",
        url="https://example.com/x",
        guid="c0ffee00-0000-4000-8000-000000000000",
        profile="Profile 1",
        date=date(2026, 11, 1),
        tags=["개발"],
    )
    path = settings.profile_bookmarks_dir("Profile 1") / "2026" / f"2026-11-01 {title_nfd} c0ffee.md"
    write_bookmark(bookmark, path)
    listed = list(iter_bookmarks(settings, "Profile 1"))
    assert len(listed) == 1
    assert unicodedata.is_normalized("NFC", str(listed[0].path))
    assert list_profiles(settings) == ["Profile 1"]


# --- insight note store ------------------------------------------------------------------


def test_sample_note_parses_and_conversation_sections_are_preserved(settings: BookmarkInsightSettings) -> None:
    """Front matter clusters round-trip; '## 대화 기록' sections survive a rewrite."""
    period = resolve_period(today=TODAY)
    path = note_path(settings, "Default", period)
    assert path.name == "2026-10-31_2026-11-06.md" and path.is_file()
    note, rendered = read_note(path)
    assert note.profile == "Default" and note.since == date(2026, 10, 31)
    assert [cluster.topic for cluster in note.clusters] == ["WebRTC 재접속", "Rust 비동기"]
    assert note.unavailable == ["a1b2c3d4-0002-4000-8000-000000000002"]
    assert len(note.conversations) == 1 and note.conversations[0].startswith("## 대화 기록 (2026-11-06 10:12)")
    assert "## 대화 기록" not in rendered and rendered.startswith("# 북마크 인사이트")

    tool_shape = note.to_tool_result()
    assert set(tool_shape) == {"profile", "period", "note_path", "clusters", "unavailable"}
    assert tool_shape["clusters"][0]["question"].endswith("?")

    write_note(note, "# 다시 렌더\n", path)
    again, rendered_again = read_note(path)
    assert rendered_again == "# 다시 렌더"
    assert again.conversations == note.conversations
    append_conversation_section(path, "2026-11-07 09:00", "두 번째 결론.", transcript="나: 안녕\n봇: 안녕")
    third, _ = read_note(path)
    assert len(third.conversations) == 2
    assert "<details>" in third.conversations[1]


def test_note_staleness_rule(settings: BookmarkInsightSettings) -> None:
    """A note is stale when the bookmark set of its period changed."""
    period = resolve_period(today=TODAY)
    note, _ = read_note(note_path(settings, "Default", period))
    current = bookmarks_in_period(settings, "Default", period)
    assert note_is_stale(note, current) is False
    assert note_is_stale(note, current[:-1]) is True
    fresh = InsightNote(profile="Default", since=period.since, until=period.until, generated_at="")
    assert note_is_stale(fresh, current) is True


# --- fake LLM + fixtures -------------------------------------------------------------------


def test_chrome_fixture_shape(fixtures_dir: Path) -> None:
    """The fake Chrome data has the roots, profiles and edge cases track A relies on."""
    local_state = json.loads((fixtures_dir / "chrome_user_data" / "Local State").read_text(encoding="utf-8"))
    assert set(local_state["profile"]["info_cache"]) == {"Default", "Profile 1", "Profile 2"}
    assert local_state["profile"]["last_used"] == "Default"
    bookmarks = json.loads((fixtures_dir / "chrome_user_data" / "Default" / "Bookmarks").read_text(encoding="utf-8"))
    assert set(bookmarks["roots"]) == {"bookmark_bar", "other", "synced"}
    urls = [node["url"] for node in _walk(bookmarks["roots"]) if node.get("type") == "url"]
    assert urls.count("https://example.com/webrtc/reconnect") == 2  # duplicate URL across folders
    assert any(node.get("date_added") == "0" for node in _walk(bookmarks["roots"]))
    assert not (fixtures_dir / "chrome_user_data" / "Profile 2").exists()  # listed but absent on disk


def _walk(roots: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    stack = list(roots.values())
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.get("children", []))
    return nodes


def test_fake_llm_replays_fixture_and_falls_back(fake_llm_responses: dict[str, Any]) -> None:
    """Canned summaries/clusters replay; unknown URLs get a deterministic excerpt."""
    llm = FakeLLM(fake_llm_responses)
    canned = llm.summarize(title="t", url="https://example.com/webrtc/reconnect", text="x" * 500)
    assert canned.text.startswith("WebRTC 연결이")
    assert canned.usage.input_tokens == 500
    fallback = llm.summarize(title="제목", url="https://example.com/unknown", text="첫 문장. 둘째 문장.")
    assert fallback.text.startswith("제목: 첫 문장.")
    clusters = llm.cluster([SummaryInput(id="a", title="A", url="u", tags=("개발",), date="2026-11-01", summary="s")])
    assert [cluster.topic for cluster in clusters.clusters] == ["WebRTC 재접속", "Rust 비동기"]
    assert len(llm.calls) == 3
    assert parse_clusters(json.dumps(fake_llm_responses["clusters"]))[0].bookmark_ids[0].startswith("7f3a9c2e")
    with pytest.raises(ValueError):
        parse_clusters({"nope": []})


def test_insight_cluster_contract_shape() -> None:
    """The cluster dict has exactly the §2.2 keys."""
    cluster = InsightCluster(topic="t", bookmark_ids=["a"], summary="s", correlation="c", question="q?")
    assert set(cluster.to_dict()) == {"topic", "bookmark_ids", "summary", "correlation", "question"}


def test_state_dir_renames_legacy_deskmate_folder_once(tmp_path: Path) -> None:
    """A repo from before the package rename keeps its state: ``.deskmate/`` becomes ``.bookmark_insight/``."""
    legacy = tmp_path / ".deskmate"
    (legacy / "conversations").mkdir(parents=True)
    (legacy / "usage.jsonl").write_text("{}\n", encoding="utf-8")
    settings = load_settings({"BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path)})

    assert settings.state_dir == tmp_path / ".bookmark_insight"
    assert not legacy.exists()
    assert (tmp_path / ".bookmark_insight" / "usage.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert settings.resolved_conversation_log_dir == tmp_path / ".bookmark_insight" / "conversations"

    # a repo that already has the new folder is left alone even if an old one reappears next to it
    (tmp_path / ".deskmate").mkdir()
    assert settings.state_dir == tmp_path / ".bookmark_insight"
    assert (tmp_path / ".deskmate").is_dir()
