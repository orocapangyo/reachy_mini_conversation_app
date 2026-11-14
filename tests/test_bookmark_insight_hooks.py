"""M6/M8: Bookmark Insight app hooks — activity/transcript fan-out, auto-close and conversation log on sleep / shutdown."""

import time
import shutil
import asyncio
import logging
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import reachy_mini_conversation_app.bookmark_insight_transcript_log as transcript_log_mod
from bookmark_insight.session import InsightSessionManager
from reachy_mini_conversation_app.console import LocalStream
from reachy_mini_conversation_app.bookmark_insight_hooks import (
    BookmarkInsightSessionHooks,
    idle_timeout_label,
    install_bookmark_insight_hooks,
)
from reachy_mini_conversation_app.bookmark_insight_transcript_log import TranscriptLogger


FIXTURE_NOTE = Path(__file__).parent / "bookmark_insight" / "fixtures" / "repo_sample" / "insights"


@pytest.fixture
def note(tmp_path: Path) -> Path:
    """Writable copy of the fixture insight note."""
    source = next(FIXTURE_NOTE.rglob("*.md"))
    target = tmp_path / source.name
    shutil.copy(source, target)
    return target


def _stream() -> LocalStream:
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    robot = SimpleNamespace(media=SimpleNamespace(audio=SimpleNamespace(clear_player=MagicMock())))
    return LocalStream(handler, robot)


def test_on_activity_is_a_heartbeat(note: Path) -> None:
    """Any activity reason resets the session idle timer."""
    manager = InsightSessionManager()
    session = manager.open("Default", note)
    session.last_activity -= 100
    BookmarkInsightSessionHooks(manager, idle_timeout_min=10).on_activity("user_speech_started")
    assert (manager.idle_seconds() or 0) < 1


def test_on_sleep_and_on_shutdown_auto_close(note: Path) -> None:
    """Sleep and shutdown both close the session and mark the note with their trigger."""
    manager = InsightSessionManager()
    hooks = BookmarkInsightSessionHooks(manager, idle_timeout_min=10)
    manager.open("Default", note)
    hooks.on_sleep()
    assert manager.current() is None and "자동 종료: 슬립 전환" in note.read_text(encoding="utf-8")
    manager.open("Default", note)
    hooks.on_shutdown()
    assert manager.current() is None and "자동 종료: 앱 종료" in note.read_text(encoding="utf-8")


def test_check_idle_disabled_with_zero_timeout(note: Path) -> None:
    """A 0-minute timeout disables the idle close and the watchdog thread."""
    manager = InsightSessionManager()
    hooks = BookmarkInsightSessionHooks(manager, idle_timeout_min=0)
    session = manager.open("Default", note)
    session.last_activity -= 10_000
    assert hooks.check_idle() is False and manager.current() is session
    hooks.start()
    assert hooks._thread is None


def test_watchdog_closes_idle_session(note: Path) -> None:
    """The watchdog thread closes a session idle past the timeout without any LLM involvement."""
    manager = InsightSessionManager()
    hooks = BookmarkInsightSessionHooks(manager, idle_timeout_min=1 / 60, poll_interval_s=0.01)
    session = manager.open("Default", note)
    session.last_activity -= 5
    hooks.start()
    try:
        deadline = time.monotonic() + 2.0
        while manager.current() is not None and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        hooks.stop()
    assert manager.current() is None and hooks._thread is None
    assert "자동 종료: 무응답 1초" in note.read_text(encoding="utf-8")


def test_local_stream_fans_activity_out_to_extra_observers() -> None:
    """Registered observers all receive the reason; one raising does not stop the others."""
    stream = _stream()
    seen: list[str] = []

    def bad(_reason: str) -> None:
        raise RuntimeError("boom")

    stream.add_activity_observer(bad)
    stream.add_activity_observer(seen.append)
    stream.add_activity_observer(lambda reason: seen.append(reason.upper()))
    stream._dispatch_activity("say")
    assert seen == ["say", "SAY"]


def test_local_stream_fans_transcripts_out_to_extra_observers() -> None:
    """The RPC push and every registered transcript observer get (role, text, final); one raising is ignored."""
    stream = _stream()
    stream._rpc = MagicMock()
    seen: list[tuple[str, str, bool]] = []

    def bad(_role: str, _text: str, _final: bool) -> None:
        raise RuntimeError("boom")

    stream.add_transcript_observer(bad)
    stream.add_transcript_observer(lambda role, text, final: seen.append((role, text, final)))
    stream._dispatch_transcript("user", "안녕", False)
    stream._dispatch_transcript("assistant", "네", True)
    assert seen == [("user", "안녕", False), ("assistant", "네", True)]
    assert stream._rpc.broadcast_threadsafe.call_count == 2
    # The fan-out survives a handler rebuild: the new handler's slot points back at the dispatcher.
    stream._install_handler(MagicMock())
    stream.handler.set_transcript_observer.assert_called_once_with(stream._dispatch_transcript)


def test_install_registers_observer_without_thread_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing wires both observers into the stream; a 0 timeout starts no watchdog; no repo → log disabled."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_INSIGHT_IDLE_TIMEOUT_MIN", "0")
    monkeypatch.delenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", raising=False)
    monkeypatch.setattr(transcript_log_mod, "TRANSCRIPT_LOG", None)
    stream = _stream()
    hooks = install_bookmark_insight_hooks(stream, logging.getLogger("test"))
    assert hooks.idle_timeout_s == 0 and hooks._thread is None
    assert hooks.on_activity in stream._extra_activity_observers
    assert hooks.transcript_log is not None and not hooks.transcript_log.enabled
    assert hooks.transcript_log.on_transcript in stream._extra_transcript_observers
    assert transcript_log_mod.get_transcript_log() is hooks.transcript_log


def test_install_enables_log_in_configured_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With a repo configured the logger writes under the resolved conversation log dir via the stream fan-out."""
    monkeypatch.setenv("BOOKMARK_INSIGHT_INSIGHT_IDLE_TIMEOUT_MIN", "0")
    monkeypatch.setenv("BOOKMARK_INSIGHT_BOOKMARK_REPO", str(tmp_path))
    monkeypatch.delenv("BOOKMARK_INSIGHT_CONVERSATION_LOG_DIR", raising=False)
    monkeypatch.setattr(transcript_log_mod, "TRANSCRIPT_LOG", None)
    stream = _stream()
    hooks = install_bookmark_insight_hooks(stream, logging.getLogger("test"))
    assert (
        hooks.transcript_log is not None
        and hooks.transcript_log.log_dir == tmp_path / ".bookmark_insight" / "conversations"
    )
    stream._dispatch_transcript("user", "안녕", True)
    files = list((tmp_path / ".bookmark_insight" / "conversations").glob("*.jsonl"))
    assert len(files) == 1 and '"text": "안녕"' in files[0].read_text(encoding="utf-8")


def test_sleep_and_shutdown_close_the_log_file(note: Path, tmp_path: Path) -> None:
    """on_sleep / on_shutdown close the transcript file so the next line after wake starts a new one."""
    log = TranscriptLogger(tmp_path / "conv")
    hooks = BookmarkInsightSessionHooks(InsightSessionManager(), idle_timeout_min=10, transcript_log=log)
    log.on_transcript("user", "하나", final=True)
    first = log.current_file
    hooks.on_sleep()
    assert first is not None and log.current_file is None
    log.on_transcript("assistant", "둘", final=True)
    second = log.current_file
    hooks.on_shutdown()
    assert second is not None and second != first and log.current_file is None
    assert len(list((tmp_path / "conv").glob("*.jsonl"))) == 2


def test_hooks_without_transcript_log_still_close_sessions(note: Path) -> None:
    """The transcript log is optional for the hooks (older wiring, tests)."""
    manager = InsightSessionManager()
    hooks = BookmarkInsightSessionHooks(manager, idle_timeout_min=10)
    manager.open("Default", note)
    hooks.on_sleep()
    assert manager.current() is None and hooks.transcript_log is None


@pytest.mark.parametrize(("seconds", "label"), [(600, "10분"), (60, "1분"), (90, "90초"), (1, "1초")])
def test_idle_timeout_label(seconds: float, label: str) -> None:
    """Whole minutes read as 분, anything else as 초."""
    assert idle_timeout_label(seconds) == label
