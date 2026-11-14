"""Track B (M3): fetch guards, caching, back-off, caps and bookmark writes of the summarizer."""

import json
from typing import Any, Iterator
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

import httpx
import pytest

from bookmark_insight.llm import FakeLLM
from bookmark_insight.store import read_bookmark, write_bookmark
from bookmark_insight.models import FETCH_FAILED_SUMMARY_TEXT, Bookmark
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.summarize import (
    CALL_INTERVAL_S,
    FETCH_MAX_BYTES,
    RETRY_BACKOFF_DAYS,
    DAILY_MAX_SUMMARIES,
    summarize_bookmarks,
)
from bookmark_insight.summarize.fetch import FetchResult, fetch_url, build_client
from bookmark_insight.summarize.runner import counter_path
from bookmark_insight.summarize.sections import summary_text, replace_summary_section


NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
PARAGRAPH = (
    "WebRTC 연결이 끊겼을 때 ICE restart와 세션 재협상을 어떻게 설계하는지 다룬다. "
    "네트워크 전환과 앱 백그라운드 복귀를 구분해 처리하고, 재접속 중 미디어 트랙을 유지하는 방법을 설명한다. "
)


def article_html(title: str = "WebRTC 재접속 처리 패턴", paragraphs: int = 6) -> str:
    """Return a realistic article page with enough body text for extraction."""
    body = "".join(f"<p>{PARAGRAPH}{index}</p>" for index in range(paragraphs))
    return (
        f'<html><head><meta charset="utf-8"><title>{title}</title></head><body><nav>메뉴 홈</nav>'
        f"<article><h1>{title}</h1>{body}</article><footer>저작권</footer></body></html>"
    )


class _Spy(httpx.SyncByteStream):
    """Response stream that records whether anyone read it."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False

    def __iter__(self) -> Iterator[bytes]:
        self.iterated = True
        yield from self.chunks


class Server:
    """Routing table for ``httpx.MockTransport``; records every request path."""

    def __init__(self) -> None:
        """Start with no routes and an empty request log."""
        self.routes: dict[str, httpx.Response] = {}
        self.requests: list[str] = []
        self.big_stream = _Spy([b"x" * (1024 * 1024)] * 3)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Route one request."""
        path = request.url.path
        self.requests.append(path)
        if path.startswith("/redirect/"):
            hops = int(path.rsplit("/", 1)[1])
            if hops == 0:
                return httpx.Response(200, headers={"content-type": "text/html"}, content=article_html().encode())
            return httpx.Response(302, headers={"location": f"/redirect/{hops - 1}"})
        if path == "/content-length-3mb":
            return httpx.Response(
                200, headers={"content-type": "text/html", "content-length": "3000000"}, stream=self.big_stream
            )
        if path == "/stream-3mb":
            return httpx.Response(200, headers={"content-type": "text/html"}, stream=_Spy([b"x" * (1024 * 1024)] * 3))
        return self.routes.get(path, httpx.Response(404, headers={"content-type": "text/html"}))


@pytest.fixture
def server() -> Server:
    """Fake HTTP server with the standard page set."""
    srv = Server()
    srv.routes["/ok"] = httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=article_html().encode()
    )
    srv.routes["/plain"] = httpx.Response(
        200, headers={"content-type": "text/plain"}, content=(PARAGRAPH * 4).encode()
    )
    srv.routes["/pdf"] = httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4 " + b"x" * 500
    )
    srv.routes["/js-empty"] = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=b"<html><body><div id=app></div><script>boot()</script></body></html>",
    )
    srv.routes["/euc-kr"] = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=article_html("한글 인코딩 페이지").replace('charset="utf-8"', 'charset="euc-kr"').encode("euc-kr"),
    )
    srv.routes["/rate-limited"] = httpx.Response(429, headers={"retry-after": "120"}, content=b"slow down")
    return srv


@pytest.fixture
def client(server: Server) -> httpx.Client:
    """HTTP client bound to the fake server with the production limits."""
    return build_client(transport=httpx.MockTransport(server))


@pytest.fixture
def repo_settings(tmp_path: Path) -> BookmarkInsightSettings:
    """Return settings for an empty writable repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return BookmarkInsightSettings(bookmark_repo=repo, chrome_profiles=("Default",), default_profile="Default")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the call-interval sleep and record requested durations."""
    sleeps: list[float] = []
    monkeypatch.setattr("bookmark_insight.summarize.runner._sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def make_bookmark(settings: BookmarkInsightSettings, slug: str, url: str, **overrides: Any) -> Bookmark:
    """Write a pending bookmark into the repo and return it as loaded from disk."""
    guid = overrides.pop("guid", f"{slug:0<8}-0000-4000-8000-000000000000"[:36])
    bookmark = Bookmark(
        title=f"페이지 {slug}",
        url=url,
        guid=guid,
        profile="Default",
        date=date(2026, 11, 5),
        tags=["개발"],
        profile_name="개인",
    )
    for key, value in overrides.items():
        setattr(bookmark, key, value)
    path = settings.profile_bookmarks_dir("Default") / "2026" / f"2026-11-05 페이지 {slug} {guid[:6]}.md"
    write_bookmark(bookmark, path)
    return read_bookmark(path)


def usage_lines(settings: BookmarkInsightSettings) -> list[dict[str, Any]]:
    """Parse the usage JSONL log."""
    path = settings.resolved_usage_log
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- fetch layer ----------------------------------------------------------------------


def test_fetch_success_and_http_error(client: httpx.Client) -> None:
    """A normal HTML page yields its article text; 404 fails with the status."""
    result = fetch_url("https://example.com/ok", client=client)
    assert result.ok and "ICE restart" in result.text and result.content_type == "text/html"
    missing = fetch_url("https://example.com/missing", client=client)
    assert not missing.ok and missing.status_code == 404 and "404" in (missing.reason or "")


def test_fetch_rejects_content_length_without_reading_body(client: httpx.Client, server: Server) -> None:
    """``Content-Length`` above the cap fails before any byte of the body is read."""
    result = fetch_url("https://example.com/content-length-3mb", client=client)
    assert not result.ok and "content-length" in (result.reason or "")
    assert server.big_stream.iterated is False


def test_fetch_stops_streaming_past_cap(client: httpx.Client) -> None:
    """A chunked body without ``Content-Length`` is cut once it passes the cap."""
    result = fetch_url("https://example.com/stream-3mb", client=client)
    assert not result.ok and str(FETCH_MAX_BYTES) in (result.reason or "")


def test_fetch_rejects_binary_and_empty_pages(client: httpx.Client) -> None:
    """PDF fails on content type; a JS-only shell fails on minimum body length."""
    pdf = fetch_url("https://example.com/pdf", client=client)
    assert not pdf.ok and "application/pdf" in (pdf.reason or "")
    empty = fetch_url("https://example.com/js-empty", client=client)
    assert not empty.ok and "too short" in (empty.reason or "")


def test_fetch_decodes_euc_kr_via_meta_charset(client: httpx.Client) -> None:
    """A EUC-KR page declared only by ``<meta charset>`` decodes to readable Korean."""
    result = fetch_url("https://example.com/euc-kr", client=client)
    assert result.ok and "한글 인코딩 페이지" in result.text and "ICE restart" in result.text
    assert "�" not in result.text


def test_fetch_redirect_limit(client: httpx.Client) -> None:
    """Two hops are followed; six exceed the limit of five."""
    assert fetch_url("https://example.com/redirect/2", client=client).ok
    too_many = fetch_url("https://example.com/redirect/6", client=client)
    assert not too_many.ok and "redirects" in (too_many.reason or "")


def test_fetch_plain_text_and_rate_limit(client: httpx.Client) -> None:
    """``text/plain`` bodies are used raw; 429 records ``Retry-After``."""
    plain = fetch_url("https://example.com/plain", client=client)
    assert plain.ok and plain.text.startswith("WebRTC 연결이")
    limited = fetch_url("https://example.com/rate-limited", client=client)
    assert not limited.ok and limited.status_code == 429 and limited.retry_after_s == 120.0


# --- section editing ----------------------------------------------------------------


def test_replace_summary_section_keeps_user_sections() -> None:
    """Only ``## 요약`` is replaced; headings and notes below survive."""
    body = "## 요약\n옛 요약\n\n## 내 메모\n- 다시 읽기\n"
    updated = replace_summary_section(body, "새 요약")
    assert updated == "## 요약\n새 요약\n\n## 내 메모\n- 다시 읽기\n"
    assert summary_text(updated) == "새 요약"
    assert replace_summary_section("", "첫 요약") == "## 요약\n첫 요약\n"
    assert replace_summary_section("## 내 메모\n메모\n", "요약") == "## 요약\n요약\n\n## 내 메모\n메모\n"
    assert summary_text("## 요약\n\n## 기타\nx") is None


# --- summarize run ----------------------------------------------------------------------


def test_summarize_success_writes_summary_status_and_usage(
    repo_settings: BookmarkInsightSettings, client: httpx.Client, no_sleep: list[float]
) -> None:
    """A fetched page is summarized, written under ``## 요약`` and logged."""
    llm = FakeLLM()
    bookmark = make_bookmark(repo_settings, "ok", "https://example.com/ok")
    report = summarize_bookmarks(repo_settings, llm, [bookmark], now=NOW, client=client)
    assert report.summarized == [bookmark.guid] and not report.failed and not report.warnings
    saved = read_bookmark(Path(bookmark.path))
    assert saved.summary_status == "ok" and saved.summarized_at == NOW.astimezone().date().isoformat()
    assert saved.fetch_attempts == 1 and saved.last_fetch_at == "2026-11-06T12:00:00Z"
    assert saved.body.startswith("## 요약\n페이지 ok: ")
    assert summary_text(saved.body) is not None and "ICE restart" in saved.body
    lines = usage_lines(repo_settings)
    assert len(lines) == 1 and lines[0]["op"] == "summarize" and lines[0]["bookmark_id"] == bookmark.guid
    assert lines[0]["profile"] == "Default" and lines[0]["model"] == "fake" and lines[0]["input_tokens"] > 0
    assert set(lines[0]) >= {"ts", "cached_tokens", "thinking_tokens", "output_tokens"}
    assert no_sleep == []  # single call: no interval wait
    assert json.loads(counter_path(repo_settings).read_text(encoding="utf-8")) == {
        "date": NOW.astimezone().date().isoformat(),
        "count": 1,
    }


def test_summarize_failures_mark_fetch_failed(repo_settings: BookmarkInsightSettings, client: httpx.Client) -> None:
    """404, PDF and an empty page become ``fetch_failed`` with the placeholder body."""
    llm = FakeLLM()
    items = [
        make_bookmark(repo_settings, "missing", "https://example.com/missing"),
        make_bookmark(repo_settings, "pdf", "https://example.com/pdf"),
        make_bookmark(repo_settings, "empty", "https://example.com/js-empty"),
    ]
    report = summarize_bookmarks(repo_settings, llm, items, now=NOW, client=client)
    assert sorted(report.failed) == sorted(item.guid for item in items) and not report.summarized
    assert llm.calls == []
    for item in items:
        saved = read_bookmark(Path(item.path))
        assert saved.summary_status == "fetch_failed" and saved.fetch_attempts == 1
        assert saved.body == f"## 요약\n{FETCH_FAILED_SUMMARY_TEXT}\n"
        assert saved.last_fetch_at == "2026-11-06T12:00:00Z"


def test_excluded_urls_are_never_requested(
    repo_settings: BookmarkInsightSettings, client: httpx.Client, server: Server
) -> None:
    """Private ranges and localhost fail without a request; a custom pattern applies too."""
    custom = BookmarkInsightSettings(
        bookmark_repo=repo_settings.bookmark_repo,
        exclude_url_patterns=repo_settings.exclude_url_patterns + ("wiki.corp.example",),
    )
    items = [
        make_bookmark(custom, "private", "http://192.168.0.10/ok"),
        make_bookmark(custom, "local", "http://localhost:8080/ok"),
        make_bookmark(custom, "corp", "https://wiki.corp.example/ok"),
    ]
    report = summarize_bookmarks(custom, FakeLLM(), items, now=NOW, client=client)
    assert sorted(report.failed) == sorted(item.guid for item in items)
    assert server.requests == []
    assert all(read_bookmark(Path(item.path)).summary_status == "fetch_failed" for item in items)


def test_ok_bookmarks_are_cached(repo_settings: BookmarkInsightSettings, client: httpx.Client, server: Server) -> None:
    """Already summarized bookmarks are neither fetched nor sent to the model."""
    llm = FakeLLM()
    done = make_bookmark(
        repo_settings, "done", "https://example.com/ok", summary_status="ok", body="## 요약\n이미 있음\n"
    )
    report = summarize_bookmarks(repo_settings, llm, [done], now=NOW, client=client)
    assert report.skipped == [done.guid] and llm.calls == [] and server.requests == []
    assert read_bookmark(Path(done.path)).body == "## 요약\n이미 있음\n"


def test_fetch_failed_backoff(repo_settings: BookmarkInsightSettings, client: httpx.Client, server: Server) -> None:
    """One attempt a day ago is retried; one attempt an hour ago waits; three attempts never retry."""
    yesterday = (NOW - timedelta(days=1, minutes=1)).isoformat().replace("+00:00", "Z")
    recent = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    retry = make_bookmark(
        repo_settings,
        "retry",
        "https://example.com/ok",
        summary_status="fetch_failed",
        fetch_attempts=1,
        last_fetch_at=yesterday,
    )
    wait = make_bookmark(
        repo_settings,
        "wait",
        "https://example.com/ok",
        summary_status="fetch_failed",
        fetch_attempts=1,
        last_fetch_at=recent,
    )
    gave_up = make_bookmark(
        repo_settings,
        "giveup",
        "https://example.com/ok",
        summary_status="fetch_failed",
        fetch_attempts=len(RETRY_BACKOFF_DAYS),
        last_fetch_at="2020-01-01T00:00:00Z",
    )
    report = summarize_bookmarks(repo_settings, FakeLLM(), [retry, wait, gave_up], now=NOW, client=client)
    assert report.summarized == [retry.guid]
    assert sorted(report.skipped) == sorted([wait.guid, gave_up.guid])
    assert server.requests == ["/ok"]
    assert read_bookmark(Path(retry.path)).fetch_attempts == 2


def test_body_is_truncated_before_llm(repo_settings: BookmarkInsightSettings, client: httpx.Client) -> None:
    """The model never receives more than ``body_max_chars``."""
    small = BookmarkInsightSettings(bookmark_repo=repo_settings.bookmark_repo, body_max_chars=300)

    class RecordingLLM(FakeLLM):
        """FakeLLM that keeps the text lengths it was given."""

        def __init__(self) -> None:
            super().__init__()
            self.lengths: list[int] = []

        def summarize(self, *, title: str, url: str, text: str) -> Any:
            self.lengths.append(len(text))
            return super().summarize(title=title, url=url, text=text)

    llm = RecordingLLM()
    bookmark = make_bookmark(small, "long", "https://example.com/ok")
    summarize_bookmarks(small, llm, [bookmark], now=NOW, client=client)
    assert llm.lengths == [300]


def test_batch_cap_and_call_interval(
    repo_settings: BookmarkInsightSettings, client: httpx.Client, no_sleep: list[float]
) -> None:
    """``batch_max_summaries`` bounds one run; the interval sleep runs between model calls."""
    capped = BookmarkInsightSettings(bookmark_repo=repo_settings.bookmark_repo, batch_max_summaries=2)
    items = [make_bookmark(capped, f"b{index}", "https://example.com/ok") for index in range(3)]
    report = summarize_bookmarks(capped, FakeLLM(), items, now=NOW, client=client)
    assert report.summarized == [items[0].guid, items[1].guid]
    assert report.skipped == [items[2].guid]
    assert any("batch cap" in warning for warning in report.warnings)
    assert no_sleep == [CALL_INTERVAL_S]


def test_daily_cap_is_persisted(repo_settings: BookmarkInsightSettings, client: httpx.Client, server: Server) -> None:
    """The per-day counter file stops the run and survives between runs."""
    today = NOW.astimezone().date().isoformat()
    path = counter_path(repo_settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": today, "count": DAILY_MAX_SUMMARIES - 1}), encoding="utf-8")
    llm = FakeLLM()
    items = [make_bookmark(repo_settings, f"d{index}", "https://example.com/ok") for index in range(2)]
    report = summarize_bookmarks(repo_settings, llm, items, now=NOW, client=client)
    assert report.summarized == [items[0].guid] and report.skipped == [items[1].guid]
    assert any("daily cap" in warning for warning in report.warnings)
    assert len(llm.calls) == 1 and server.requests == ["/ok"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"date": today, "count": DAILY_MAX_SUMMARIES}

    again = summarize_bookmarks(repo_settings, llm, [items[1]], now=NOW, client=client)
    assert again.summarized == [] and len(llm.calls) == 1

    path.write_text(json.dumps({"date": "2026-01-01", "count": DAILY_MAX_SUMMARIES}), encoding="utf-8")
    fresh_day = summarize_bookmarks(repo_settings, llm, [items[1]], now=NOW, client=client)
    assert fresh_day.summarized == [items[1].guid]


def test_runner_reads_caps_and_interval_from_settings(
    repo_settings: BookmarkInsightSettings, client: httpx.Client, no_sleep: list[float]
) -> None:
    """M7: ``call_interval_s`` and ``daily_max_summaries`` come from settings, and the report flags the caps."""
    tuned = BookmarkInsightSettings(
        bookmark_repo=repo_settings.bookmark_repo, batch_max_summaries=3, daily_max_summaries=2, call_interval_s=1.5
    )
    items = [make_bookmark(tuned, f"s{index}", "https://example.com/ok") for index in range(4)]
    report = summarize_bookmarks(tuned, FakeLLM(), items, now=NOW, client=client)
    assert report.summarized == [items[0].guid, items[1].guid]
    assert no_sleep == [1.5]  # one interval between the two model calls, from settings not the constant
    assert report.batch_cap_reached is True and report.daily_cap_reached is True
    assert sorted(report.skipped) == sorted([items[2].guid, items[3].guid])

    # Below both caps nothing is flagged.
    roomy = BookmarkInsightSettings(
        bookmark_repo=repo_settings.bookmark_repo, daily_max_summaries=50, call_interval_s=0.0
    )
    report = summarize_bookmarks(roomy, FakeLLM(), [items[2]], now=NOW, client=client)
    assert report.summarized == [items[2].guid]
    assert report.batch_cap_reached is False and report.daily_cap_reached is False


def test_daily_cap_and_interval_settings_from_env(tmp_path: Path) -> None:
    """``BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES`` / ``BOOKMARK_INSIGHT_CALL_INTERVAL_S`` are read; defaults match the constants."""
    from bookmark_insight.settings import load_settings

    defaults = load_settings({"BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path)})
    assert defaults.daily_max_summaries == DAILY_MAX_SUMMARIES == 100
    assert defaults.call_interval_s == CALL_INTERVAL_S == 6.0
    tuned = load_settings(
        {
            "BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path),
            "BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES": "30",
            "BOOKMARK_INSIGHT_CALL_INTERVAL_S": "2.5",
        }
    )
    assert tuned.daily_max_summaries == 30 and tuned.call_interval_s == 2.5
    broken = load_settings(
        {
            "BOOKMARK_INSIGHT_BOOKMARK_REPO": str(tmp_path),
            "BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES": "many",
            "BOOKMARK_INSIGHT_CALL_INTERVAL_S": "x",
        }
    )
    assert broken.daily_max_summaries == 100 and broken.call_interval_s == 6.0


def test_user_sections_survive_resummarize(repo_settings: BookmarkInsightSettings, client: httpx.Client) -> None:
    """Re-summarizing a failed bookmark replaces only ``## 요약``; the user's notes stay."""
    body = f"## 요약\n{FETCH_FAILED_SUMMARY_TEXT}\n\n## 내 메모\n- 월요일에 다시 보기\n"
    bookmark = make_bookmark(
        repo_settings, "notes", "https://example.com/ok", summary_status="fetch_failed", fetch_attempts=1, body=body
    )
    summarize_bookmarks(repo_settings, FakeLLM(), [bookmark], now=NOW, client=client)
    saved = read_bookmark(Path(bookmark.path))
    assert saved.summary_status == "ok"
    assert FETCH_FAILED_SUMMARY_TEXT not in saved.body
    assert saved.body.endswith("\n\n## 내 메모\n- 월요일에 다시 보기\n")


def test_rate_limit_warning_and_llm_error_isolation(
    repo_settings: BookmarkInsightSettings, client: httpx.Client
) -> None:
    """429 is reported with its Retry-After; a model exception abandons the batch without raising."""
    limited = make_bookmark(repo_settings, "limited", "https://example.com/rate-limited")
    report = summarize_bookmarks(repo_settings, FakeLLM(), [limited], now=NOW, client=client)
    assert report.failed == [limited.guid]
    assert any("429" in warning and "120" in warning for warning in report.warnings)

    class BrokenLLM(FakeLLM):
        """Model that always fails."""

        def summarize(self, *, title: str, url: str, text: str) -> Any:
            raise RuntimeError("quota exhausted")

    first = make_bookmark(repo_settings, "first", "https://example.com/ok")
    second = make_bookmark(repo_settings, "second", "https://example.com/ok")
    report = summarize_bookmarks(repo_settings, BrokenLLM(), [first, second], now=NOW, client=client)
    assert report.summarized == [] and report.failed == [] and report.skipped == []
    assert report.llm_failed == [first.guid, second.guid]  # the run went on after the first failure
    assert any("quota exhausted" in warning for warning in report.warnings)
    stored = read_bookmark(Path(first.path))
    assert stored.summary_status == "pending" and stored.summary_error is not None
    assert "quota exhausted" in stored.summary_error and stored.fetch_attempts == 0

    # A later successful run clears the error.
    report = summarize_bookmarks(repo_settings, FakeLLM(), [first], now=NOW, client=client)
    assert report.summarized == [first.guid]
    assert read_bookmark(Path(first.path)).summary_error is None


def test_consecutive_llm_failures_stop_the_run(repo_settings: BookmarkInsightSettings, client: httpx.Client) -> None:
    """After MAX_CONSECUTIVE_LLM_FAILURES failures in a row the rest of the queue is skipped, not attempted."""
    from bookmark_insight.summarize import MAX_CONSECUTIVE_LLM_FAILURES

    calls: list[str] = []

    class DownLLM(FakeLLM):
        """Model that is down for good."""

        def summarize(self, *, title: str, url: str, text: str) -> Any:
            calls.append(title)
            raise RuntimeError("503 UNAVAILABLE")

    bookmarks = [
        make_bookmark(repo_settings, f"b{index}", "https://example.com/ok")
        for index in range(MAX_CONSECUTIVE_LLM_FAILURES + 2)
    ]
    report = summarize_bookmarks(repo_settings, DownLLM(), bookmarks, now=NOW, client=client)
    assert len(calls) == MAX_CONSECUTIVE_LLM_FAILURES
    assert len(report.llm_failed) == MAX_CONSECUTIVE_LLM_FAILURES and len(report.skipped) == 2
    assert any("in a row" in warning for warning in report.warnings)


def test_fetch_result_failure_shape() -> None:
    """The failure constructor carries the reason and leaves the text empty."""
    result = FetchResult.failure("https://example.com/x", "boom", status_code=500)
    assert result.ok is False and result.text == "" and result.status_code == 500 and result.reason == "boom"
