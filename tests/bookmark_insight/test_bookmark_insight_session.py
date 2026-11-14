"""M6: insight session state machine — app-side auto close (design doc §3.3 path 3)."""

from pathlib import Path

from bookmark_insight.models import CONVERSATION_HEADING_PREFIX
from bookmark_insight.session import (
    TRIGGER_SLEEP,
    AUTO_CLOSE_MARKER,
    InsightSessionManager,
    auto_close_body,
)


def _note(repo: Path) -> Path:
    return next((repo / "insights").rglob("*.md"))


def test_auto_finish_without_session_is_noop(repo: Path) -> None:
    """No open session → nothing closed, note untouched."""
    manager = InsightSessionManager()
    note = _note(repo)
    before = note.read_text(encoding="utf-8")
    assert manager.auto_finish(TRIGGER_SLEEP) is None
    assert note.read_text(encoding="utf-8") == before


def test_auto_finish_marks_note_and_closes(repo: Path) -> None:
    """Closing on sleep appends a 대화 기록 section whose body is only the 자동 종료 marker."""
    manager = InsightSessionManager()
    note = _note(repo)
    opened = manager.open("Default", note)
    closed = manager.auto_finish(TRIGGER_SLEEP)
    assert closed is opened and manager.current() is None
    text = note.read_text(encoding="utf-8")
    section = text[text.rfind(CONVERSATION_HEADING_PREFIX) :]
    assert section.startswith(f"{CONVERSATION_HEADING_PREFIX} (")
    assert auto_close_body(TRIGGER_SLEEP) in section and f"{AUTO_CLOSE_MARKER}: 슬립 전환" in section


def test_auto_finish_with_idle_timeout_only_closes_idle_sessions(repo: Path) -> None:
    """An active session survives the idle check; an idle one is closed."""
    manager = InsightSessionManager()
    session = manager.open("Default", _note(repo))
    assert manager.auto_finish("무응답 1분", idle_timeout_s=60) is None
    assert manager.current() is session
    session.last_activity -= 100
    assert manager.auto_finish("무응답 1분", idle_timeout_s=60) is session
    assert manager.current() is None
    assert "자동 종료: 무응답 1분" in _note(repo).read_text(encoding="utf-8")


def test_heartbeat_resets_last_activity(repo: Path) -> None:
    """A heartbeat moves ``last_activity`` forward so the idle timer restarts."""
    manager = InsightSessionManager()
    session = manager.open("Default", _note(repo))
    session.last_activity -= 100
    assert (manager.idle_seconds() or 0) >= 100
    manager.heartbeat()
    assert (manager.idle_seconds() or 0) < 1
    assert manager.close_if_idle(60) is None


def test_auto_finish_survives_missing_note(tmp_path: Path) -> None:
    """A vanished note must not raise from shutdown paths; the session is still closed."""
    manager = InsightSessionManager()
    manager.open("Default", tmp_path / "missing.md")
    assert manager.auto_finish(TRIGGER_SLEEP) is not None
    assert manager.current() is None
    assert not (tmp_path / "missing.md").exists()


def test_last_closed_remembers_every_close_and_is_consumed_once(repo: Path) -> None:
    """auto_finish and explicit close both leave the session in ``last_closed``; take_last_closed uses it once."""
    manager = InsightSessionManager()
    note = _note(repo)
    assert manager.last_closed() is None
    first = manager.open("Default", note)
    assert manager.auto_finish(TRIGGER_SLEEP) is first
    assert manager.last_closed() is first and manager.current() is None
    second = manager.open("Default", note)
    second.last_activity -= 100
    assert manager.auto_finish(TRIGGER_SLEEP, idle_timeout_s=60) is second
    assert manager.last_closed() is second
    third = manager.open("Default", note)
    assert manager.close() is third and manager.last_closed() is third  # explicit finish remembered too
    assert manager.close() is None and manager.last_closed() is third  # no session open: memory kept
    assert manager.take_last_closed() is third and manager.last_closed() is None  # used once
    manager.open("Default", note)
    manager.reset()
    assert manager.current() is None and manager.last_closed() is None
