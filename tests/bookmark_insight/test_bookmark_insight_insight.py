"""Track B (M4): note loading, rendering, regeneration and the cluster batch."""

import json
from typing import Any
from pathlib import Path
from datetime import date, datetime, timezone

import httpx
import pytest

from bookmark_insight.llm import FakeLLM, ClusterResult
from bookmark_insight.store import note_path, read_note, note_is_stale, write_bookmark, bookmarks_in_period
from bookmark_insight.models import Bookmark, InsightNote, InsightCluster
from bookmark_insight.insight import load_insight, build_insight, render_note_body
from bookmark_insight.periods import Period, resolve_period
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.insight.render import related_links, note_links_enabled
from bookmark_insight.summarize.fetch import build_client


TODAY = date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
WEBRTC_ID = "7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05"
ICE_ID = "a1b2c3d4-0001-4000-8000-000000000001"
RUST_ID = "a1b2c3d4-0002-4000-8000-000000000002"
WEBRTC_LINK = "[WebRTC 재접속 처리 패턴](https://example.com/webrtc/reconnect)"
RUST_LINK = '[Rust: async/await 입문 | 1부 "Future란?" <정리>](https://example.com/rust/async-intro)'
WEBRTC_STEM = "2026-11-03 WebRTC 재접속 처리 패턴 7f3a9c"
ICE_STEM = "2026-11-04 모바일 백그라운드 복귀 시 ICE 재협상 a1b2c3"
RUST_STEM = "2026-11-05 Rust async await 입문 1부 Future란 정리 a1b2c3"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never wait for the call interval in tests."""
    monkeypatch.setattr("bookmark_insight.summarize.runner._sleep", lambda seconds: None)


@pytest.fixture
def offline_client() -> httpx.Client:
    """HTTP client whose every request fails (the fixture repo must not need the network)."""
    return build_client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))


@pytest.fixture
def period() -> Period:
    """Return the fixture note's period."""
    return resolve_period(today=TODAY)


def test_load_insight_returns_fixture_note(settings: BookmarkInsightSettings, period: Period) -> None:
    """The sample note loads with its clusters and rendered body; a missing period is ``None``."""
    loaded = load_insight(settings, "Default", period)
    assert loaded is not None
    note, rendered = loaded
    assert note.path == note_path(settings, "Default", period)
    assert [cluster.topic for cluster in note.clusters] == ["WebRTC 재접속", "Rust 비동기"]
    assert rendered.startswith("# 북마크 인사이트 (2026-10-31 ~ 2026-11-06)")
    assert (
        load_insight(settings, "Default", resolve_period(since="2025-01-01", until="2025-01-07", today=TODAY)) is None
    )
    assert load_insight(settings, "Profile 1", period) is None


def test_render_matches_fixture_layout(settings: BookmarkInsightSettings, period: Period) -> None:
    """Rendering the fixture's front matter reproduces the fixture body exactly."""
    note, rendered = read_note(note_path(settings, "Default", period))
    bookmarks = bookmarks_in_period(settings, "Default", period)
    assert render_note_body(note, bookmarks) == rendered + "\n"


def test_render_edge_cases() -> None:
    """Unknown ids degrade to ``(id)``, LLM text is flattened, empty unavailable omits its section."""
    note = InsightNote(
        profile="Default",
        since=date(2026, 11, 1),
        until=date(2026, 11, 7),
        generated_at="2026-11-07T00:00:00Z",
        clusters=[
            InsightCluster(
                topic="주제\n## 가짜 제목",
                bookmark_ids=["unknown-id"],
                summary="첫 줄\n둘째 줄",
                correlation="관계",
                question="질문?",
            )
        ],
    )
    body = render_note_body(note, [])
    assert body == (
        "# 북마크 인사이트 (2026-11-01 ~ 2026-11-07)\n\n## 주제 ## 가짜 제목\n첫 줄 둘째 줄\n\n- (unknown-id)\n\n"
        "**관계**: 관계\n\n**질문**: 질문?\n"
    )
    assert "## 본문 미확보" not in body
    assert render_note_body(InsightNote("Default", date(2026, 11, 1), date(2026, 11, 7), ""), []) == (
        "# 북마크 인사이트 (2026-11-01 ~ 2026-11-07)\n"
    )


def test_render_bookmark_lines_are_web_links_with_escaped_brackets() -> None:
    """``- [title](url)``: brackets in the title are escaped, URLs with parentheses/spaces get ``<...>``, no URL = plain."""
    day = date(2026, 11, 7)
    bracketed = Bookmark("(펌)[뉴비가이드] 로컬 LLM [정리]", "https://arca.live/b/alpaca/1", "g1", "Default", day)
    parens = Bookmark("Wiki", "https://en.wikipedia.org/wiki/Foo_(bar)", "g2", "Default", day)
    spaced = Bookmark("Spaced", "https://example.com/a b", "g3", "Default", day)
    no_url = Bookmark("URL 없음", "", "g4", "Default", day)
    untitled = Bookmark("  ", "https://example.com/untitled", "g5", "Default", day)
    note = InsightNote(
        profile="Default",
        since=day,
        until=day,
        generated_at="",
        clusters=[InsightCluster("주제", ["g1", "g2", "g3", "g4", "g5", "ghost"], "s", "c", "q?")],
    )
    body = render_note_body(note, [bracketed, parens, spaced, no_url, untitled])
    assert "- [(펌)\\[뉴비가이드\\] 로컬 LLM \\[정리\\]](https://arca.live/b/alpaca/1)\n" in body
    assert "- [Wiki](<https://en.wikipedia.org/wiki/Foo_(bar)>)\n" in body
    assert "- [Spaced](<https://example.com/a b>)\n" in body
    assert "- URL 없음\n" in body
    assert "- [https://example.com/untitled](https://example.com/untitled)\n" in body
    assert "- (ghost)\n" in body
    assert "[[" not in body


def test_related_links_use_file_stems_and_skip_bookmarks_without_a_file(
    settings: BookmarkInsightSettings, period: Period
) -> None:
    """``related`` carries ``[[stem]]`` per file on disk (chronological input order, no duplicates, in-memory skipped)."""
    bookmarks = sorted(bookmarks_in_period(settings, "Default", period), key=lambda b: b.date)
    ghost = Bookmark("유령", "https://example.com/ghost", "ghost", "Default", date(2026, 11, 6))
    assert related_links(bookmarks + [ghost] + bookmarks[:1]) == [
        f"[[{WEBRTC_STEM}]]",
        f"[[{ICE_STEM}]]",
        f"[[{RUST_STEM}]]",
    ]
    assert related_links([]) == []


def test_note_links_enabled_auto_on_off(tmp_path: Path) -> None:
    """``auto`` follows the nearest ``.obsidian/`` above the note folder; ``on``/``off`` force it."""
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    inside = BookmarkInsightSettings(bookmark_repo=vault / "bookmarks")
    outside = BookmarkInsightSettings(bookmark_repo=tmp_path / "elsewhere")
    assert note_links_enabled(inside) is True
    assert note_links_enabled(outside) is False
    # BOOKMARK_INSIGHT_INSIGHTS_DIR decides when set: the notes live there, not in the repo.
    assert (
        note_links_enabled(BookmarkInsightSettings(bookmark_repo=tmp_path / "elsewhere", insights_dir=vault / "in"))
        is True
    )
    assert (
        note_links_enabled(BookmarkInsightSettings(bookmark_repo=vault / "bookmarks", insights_dir=tmp_path / "out"))
        is False
    )
    assert note_links_enabled(BookmarkInsightSettings(bookmark_repo=tmp_path / "elsewhere", note_links="on")) is True
    assert note_links_enabled(BookmarkInsightSettings(bookmark_repo=vault / "bookmarks", note_links="off")) is False
    assert note_links_enabled(BookmarkInsightSettings(bookmark_repo=None)) is False
    with pytest.raises(ValueError):
        BookmarkInsightSettings(bookmark_repo=None, note_links="maybe")


def test_related_front_matter_round_trips_and_is_omitted_outside_a_vault(
    settings: BookmarkInsightSettings, period: Period
) -> None:
    """A note with ``related`` writes one quoted ``[[link]]`` per line after ``unavailable`` and reads back; without it the key is absent."""
    from bookmark_insight.store import write_note

    note, rendered = read_note(note_path(settings, "Default", period))
    assert note.related == []  # fixture note predates ``related``: parser tolerates the missing key
    bookmarks = bookmarks_in_period(settings, "Default", period)
    note.related = related_links(bookmarks)
    path = settings.resolved_insights_dir / "Default" / "related.md"
    write_note(note, rendered, path)
    front_matter = path.read_text(encoding="utf-8").split("---")[1]
    related_block = "related:\n" + "".join(f'  - "{link}"\n' for link in note.related)
    assert f"unavailable: [{RUST_ID}]\n{related_block}clusters:\n" in front_matter
    reloaded, body = read_note(path)
    assert reloaded.related == note.related and body == rendered
    assert reloaded.clusters == note.clusters and reloaded.bookmark_ids == note.bookmark_ids

    reloaded.related = []
    write_note(reloaded, body, path)
    assert "related" not in path.read_text(encoding="utf-8").split("---")[1]


def test_build_insight_writes_related_only_inside_a_vault(
    tmp_path: Path,
    fixtures_dir: Path,
    fake_llm_responses: dict[str, Any],
    period: Period,
    offline_client: httpx.Client,
) -> None:
    """The batch adds ``related`` when the repo is under ``.obsidian/`` (auto) or forced on, never when off/outside."""
    import shutil

    def build(repo: Path, **overrides: Any) -> str:
        shutil.copytree(fixtures_dir / "repo_sample", repo)
        repo_settings = BookmarkInsightSettings(
            bookmark_repo=repo, chrome_profiles=("Default",), default_profile="Default", **overrides
        )
        note = build_insight(
            repo_settings, FakeLLM(fake_llm_responses), "Default", period, now=NOW, client=offline_client
        )
        return Path(note.path).read_text(encoding="utf-8").split("---")[1]

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    in_vault = build(vault / "bookmarks")
    assert f'  - "[[{WEBRTC_STEM}]]"\n  - "[[{ICE_STEM}]]"\n  - "[[{RUST_STEM}]]"\n' in in_vault
    assert "related" not in build(tmp_path / "plain")
    assert "related" in build(tmp_path / "forced", note_links="on")
    assert "related" not in build(vault / "muted", note_links="off")


def test_build_insight_empty_period_writes_nothing(
    settings: BookmarkInsightSettings, offline_client: httpx.Client
) -> None:
    """A period without bookmarks returns an empty note and leaves the disk untouched."""
    llm = FakeLLM()
    period = resolve_period(since="2025-01-01", until="2025-01-07", today=TODAY)
    note = build_insight(settings, llm, "Default", period, now=NOW, client=offline_client)
    assert note.path is None and note.clusters == [] and note.bookmark_ids == []
    assert note.profile == "Default" and note.since == date(2025, 1, 1)
    assert not note_path(settings, "Default", period).exists()
    assert llm.calls == []
    assert not settings.resolved_usage_log.exists()


def test_build_insight_regenerates_note_and_keeps_conversations(
    settings: BookmarkInsightSettings, fake_llm_responses: dict[str, Any], period: Period, offline_client: httpx.Client
) -> None:
    """Rebuilding the fixture week rewrites the note from the canned clusters and keeps ``## 대화 기록``."""
    llm = FakeLLM(fake_llm_responses)
    note = build_insight(settings, llm, "Default", period, now=NOW, client=offline_client)
    path = note_path(settings, "Default", period)
    assert note.path == path and path.is_file()
    assert note.generated_at == "2026-11-06T12:00:00Z" and note.complete is True

    reloaded, rendered = read_note(path)
    canned = fake_llm_responses["clusters"]["clusters"]
    assert [cluster.to_dict() for cluster in reloaded.clusters] == canned
    assert reloaded.bookmark_ids == [WEBRTC_ID, ICE_ID, RUST_ID]
    assert reloaded.unavailable == [RUST_ID]
    assert len(reloaded.conversations) == 1
    assert reloaded.conversations[0].startswith("## 대화 기록 (2026-11-06 10:12)")
    assert "ICE restart 2단계 전략" in reloaded.conversations[0]

    assert f"- {WEBRTC_LINK}" in rendered
    assert rendered.endswith(f"## 본문 미확보\n- {RUST_LINK}")
    assert rendered.count("## 본문 미확보") == 1
    assert "**질문**: 아바타 통화에서" in rendered

    current = bookmarks_in_period(settings, "Default", period)
    assert note_is_stale(reloaded, current) is False
    assert [call["op"] for call in llm.calls] == ["cluster"]  # the ok bookmarks are cached; Rust is in back-off
    assert llm.calls[0]["ids"] == [WEBRTC_ID, ICE_ID, RUST_ID]

    lines = [json.loads(line) for line in settings.resolved_usage_log.read_text(encoding="utf-8").splitlines()]
    assert [line["op"] for line in lines] == ["cluster"]
    assert lines[0]["profile"] == "Default" and "bookmark_id" not in lines[0]


def test_build_insight_drops_unknown_cluster_ids(
    settings: BookmarkInsightSettings, period: Period, offline_client: httpx.Client
) -> None:
    """Ids the model invented are removed; clusters left empty disappear."""

    class InventingLLM(FakeLLM):
        """Model that returns ids outside the period."""

        def cluster(self, items: Any) -> ClusterResult:
            self.calls.append({"op": "cluster"})
            return ClusterResult(
                clusters=[
                    InsightCluster("진짜", [WEBRTC_ID, "made-up", ICE_ID], "s", "c", "q?"),
                    InsightCluster("가짜", ["made-up-2"], "s", "c", "q?"),
                ]
            )

    note = build_insight(settings, InventingLLM(), "Default", period, now=NOW, client=offline_client)
    assert [cluster.topic for cluster in note.clusters] == ["진짜"]
    assert note.clusters[0].bookmark_ids == [WEBRTC_ID, ICE_ID]
    _, rendered = read_note(Path(note.path))
    assert "made-up" not in rendered and "(made-up)" not in rendered


def test_build_insight_summarizes_pending_bookmarks_first(
    settings: BookmarkInsightSettings, fake_llm_responses: dict[str, Any], period: Period, tmp_path: Path
) -> None:
    """A pending bookmark in the period is fetched and summarized before clustering."""
    page = (
        "<html><head><meta charset='utf-8'><title>CSS Grid</title></head><body><article><h1>CSS Grid 치트시트</h1>"
        + "".join(
            f"<p>grid-template-columns, grid-area, gap 같은 CSS Grid 속성과 레이아웃 패턴을 정리한 표 {i}.</p>"
            for i in range(8)
        )
        + "</article></body></html>"
    )
    client = build_client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=page.encode())
        )
    )
    guid = "c55c0000-0003-4000-8000-000000000003"
    pending = Bookmark(
        title="CSS Grid 치트시트",
        url="https://example.com/css/grid-cheatsheet",
        guid=guid,
        profile="Default",
        date=date(2026, 11, 6),
        tags=["개발", "CSS"],
    )
    write_bookmark(
        pending, settings.profile_bookmarks_dir("Default") / "2026" / f"2026-11-06 CSS Grid 치트시트 {guid[:6]}.md"
    )

    llm = FakeLLM(fake_llm_responses)
    note = build_insight(settings, llm, "Default", period, now=NOW, client=client)
    assert guid in note.bookmark_ids and guid not in note.unavailable
    assert [call["op"] for call in llm.calls] == ["summarize", "cluster"]
    summarized = next(b for b in bookmarks_in_period(settings, "Default", period) if b.guid == guid)
    assert summarized.summary_status == "ok" and "CSS Grid" in summarized.body
    lines = [json.loads(line) for line in settings.resolved_usage_log.read_text(encoding="utf-8").splitlines()]
    assert [line["op"] for line in lines] == ["summarize", "cluster"]


# --- model outage: previous note as fallback -----------------------------------------------


class _BusyLLM(FakeLLM):
    """Model whose cluster call is unavailable (503 / timeout)."""

    def cluster(self, items: list[Any]) -> ClusterResult:
        raise RuntimeError("503 UNAVAILABLE: high demand")


def _write_pending(settings: BookmarkInsightSettings, title: str, guid: str, day: date) -> Bookmark:
    bookmark = Bookmark(title=title, url=f"https://example.com/{guid[:6]}", guid=guid, profile="Default", date=day)
    target = settings.profile_bookmarks_dir("Default") / str(day.year) / f"{day.isoformat()} {title} {guid[:6]}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_bookmark(bookmark, target)
    return bookmark


def test_build_insight_serves_previous_note_when_cluster_model_is_down(
    settings: BookmarkInsightSettings, period: Period, offline_client: httpx.Client
) -> None:
    """Cluster failure + existing note → the old note comes back degraded, untouched on disk, with the new titles."""
    path = note_path(settings, "Default", period)
    before = path.read_text(encoding="utf-8")
    _write_pending(settings, "새 글", "deadbeef-0000-4000-8000-000000000001", date(2026, 11, 6))

    note = build_insight(settings, _BusyLLM(), "Default", period, now=NOW, client=offline_client)

    assert note.degraded is True and note.pending_titles == ["새 글"]
    assert note.path == path and path.read_text(encoding="utf-8") == before
    assert len(note.clusters) == 2 and note.bookmark_ids == [WEBRTC_ID, ICE_ID, RUST_ID]


def test_build_insight_without_previous_note_raises_when_model_is_down(
    settings: BookmarkInsightSettings, offline_client: httpx.Client
) -> None:
    """No note to fall back on → ClusterUnavailableError (the tool turns it into a friendly error)."""
    from bookmark_insight.insight import ClusterUnavailableError

    period = Period(since=date(2026, 10, 1), until=date(2026, 10, 7))
    _write_pending(settings, "시월 글", "deadbeef-0000-4000-8000-000000000002", date(2026, 10, 3))
    with pytest.raises(ClusterUnavailableError):
        build_insight(settings, _BusyLLM(), "Default", period, now=NOW, client=offline_client)
    assert not note_path(settings, "Default", period).exists()


# --- M7 backfill: chunked batch, partial notes, resume ------------------------------------------

ARTICLE = (
    "<html><head><meta charset='utf-8'><title>t</title></head><body><article>"
    + "".join(
        f"<p>WebRTC 연결이 끊겼을 때 ICE restart와 세션 재협상을 어떻게 설계하는지 다루는 문단 {i}이다. "
        "네트워크 전환과 앱 백그라운드 복귀를 구분해 처리하고 재접속 중 미디어 트랙을 유지한다.</p>"
        for i in range(6)
    )
    + "</article></body></html>"
)
OCTOBER = Period(since=date(2026, 10, 1), until=date(2026, 10, 31))


@pytest.fixture
def article_client() -> httpx.Client:
    """HTTP client that serves the same long article for every URL."""
    return build_client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=ARTICLE.encode())
        )
    )


def _pending_repo(tmp_path: Path, count: int, **overrides: Any) -> BookmarkInsightSettings:
    """Fresh repository with ``count`` pending bookmarks dated inside October 2026."""
    repo = tmp_path / "backfill_repo"
    repo.mkdir()
    settings = BookmarkInsightSettings(
        bookmark_repo=repo, chrome_profiles=("Default",), default_profile="Default", **overrides
    )
    for index in range(count):
        day = date(2026, 10, 1 + index % 28)
        guid = f"{index:08x}-0000-4000-8000-0000000000aa"
        _write_pending(settings, f"백필 글 {index:02d}", guid, day)
    return settings


def test_partial_note_renders_pending_section_and_round_trips(tmp_path: Path) -> None:
    """``## 미요약`` lists at most 10 titles before ``## 본문 미확보``; complete/pending survive the file."""
    from bookmark_insight.store import write_note

    settings = _pending_repo(tmp_path, 12)
    bookmarks = bookmarks_in_period(settings, "Default", OCTOBER)
    pending_ids = [bookmark.guid for bookmark in bookmarks]
    ghost = Bookmark("유령 글", "https://example.com/ghost", "ghost-0000", "Default", OCTOBER.since)
    note = InsightNote(
        profile="Default",
        since=OCTOBER.since,
        until=OCTOBER.until,
        generated_at="2026-11-06T12:00:00Z",
        unavailable=[ghost.guid],
        bookmark_ids=pending_ids + [ghost.guid],
        complete=False,
        pending=pending_ids,
    )
    body = render_note_body(note, bookmarks)
    assert "## 미요약\n아직 요약하지 않은 북마크 12건\n" in body
    assert body.count("\n- 백필 글 ") == 10 and "- … 외 2건\n" in body
    assert body.index("## 미요약") < body.index("## 본문 미확보")

    path = note_path(settings, "Default", OCTOBER)
    write_note(note, body, path)
    front_matter = path.read_text(encoding="utf-8").split("---")[1]
    assert "complete: false" in front_matter and "pending:" in front_matter
    reloaded, rendered = read_note(path)
    assert reloaded.complete is False and reloaded.pending == pending_ids
    assert reloaded.summarized_count == 1 and reloaded.pending_count == 12
    assert rendered + "\n" == body
    assert note_is_stale(reloaded, bookmarks + [ghost]) is False  # partial but current → not stale

    # A complete note writes no ``pending`` key at all (byte-compatible with M4 notes).
    note.complete, note.pending = True, []
    write_note(note, render_note_body(note, bookmarks), path)
    assert "pending" not in path.read_text(encoding="utf-8").split("---")[1]
    assert "## 미요약" not in read_note(path)[1]


def test_build_insight_partial_then_resume_then_complete(tmp_path: Path, article_client: httpx.Client) -> None:
    """25 pending > cap 20: the first call writes a partial note, the second continues and completes it."""
    settings = _pending_repo(tmp_path, 25, batch_max_summaries=20)
    llm = FakeLLM()

    first = build_insight(settings, llm, "Default", OCTOBER, now=NOW, client=article_client)
    assert first.complete is False and first.daily_cap_reached is False
    assert first.summarized_count == 20 and first.pending_count == 5
    assert len([call for call in llm.calls if call["op"] == "summarize"]) == 20
    assert llm.calls[-1]["op"] == "cluster"
    clustered = set(llm.calls[-1]["ids"])
    assert len(clustered) == 20 and clustered.isdisjoint(first.pending)  # pending ones never reach the model
    assert first.unavailable == []  # pending is not "본문 미확보"
    assert set(first.bookmark_ids) == clustered | set(first.pending)  # the note still covers the whole period
    stored, rendered = read_note(Path(first.path))
    assert stored.complete is False and stored.pending == first.pending
    assert "아직 요약하지 않은 북마크 5건" in rendered
    assert note_is_stale(stored, bookmarks_in_period(settings, "Default", OCTOBER)) is False

    # Resume: only the 5 pending ones are summarized; the note becomes complete.
    second = build_insight(settings, llm, "Default", OCTOBER, now=NOW, client=article_client)
    assert second.complete is True and second.pending == [] and second.pending_count == 0
    assert second.summarized_count == 25 and second.unavailable == []
    assert len([call for call in llm.calls if call["op"] == "summarize"]) == 25
    assert set(llm.calls[-1]["ids"]) == set(second.bookmark_ids) and len(second.bookmark_ids) == 25
    stored, rendered = read_note(Path(second.path))
    assert stored.complete is True and stored.pending == [] and "## 미요약" not in rendered
    assert all(b.summary_status == "ok" for b in bookmarks_in_period(settings, "Default", OCTOBER))


def test_build_insight_at_daily_cap_writes_partial_note_without_clusters(
    tmp_path: Path, article_client: httpx.Client
) -> None:
    """Counter already at the cap: nothing summarized, no cluster call, the partial note flags the daily cap."""
    from bookmark_insight.summarize.runner import save_daily_count

    settings = _pending_repo(tmp_path, 3, daily_max_summaries=10)
    save_daily_count(settings, NOW.astimezone().date(), 10)
    llm = FakeLLM()
    note = build_insight(settings, llm, "Default", OCTOBER, now=NOW, client=article_client)
    assert note.complete is False and note.daily_cap_reached is True
    assert note.clusters == [] and note.summarized_count == 0 and note.pending_count == 3
    assert llm.calls == []
    assert Path(note.path).is_file()

    # One slot left today: the cap stops the chunk after a single summary → still partial, cap flagged.
    save_daily_count(settings, NOW.astimezone().date(), 9)
    again = build_insight(settings, llm, "Default", OCTOBER, now=NOW, client=article_client)
    assert again.complete is False and again.daily_cap_reached is True
    assert again.summarized_count == 1 and again.pending_count == 2 and len(again.clusters) == 1
