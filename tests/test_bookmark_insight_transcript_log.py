"""M8: Bookmark Insight conversation transcript log — JSONL per session, final lines only, ring buffer, rendering."""

import json
import time
from pathlib import Path

import pytest

from bookmark_insight.settings import BookmarkInsightSettings
from reachy_mini_conversation_app.bookmark_insight_transcript_log import (
    TRANSCRIPT_CUT_NOTE,
    TranscriptLine,
    TranscriptLogger,
    local_iso,
    render_transcript_lines,
)


def _read(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_logs_final_user_and_assistant_lines_only(tmp_path: Path) -> None:
    """Deltas, empty text and other roles are dropped; the file is created on the first kept line."""
    log = TranscriptLogger(tmp_path / "conversations")
    log.start_session()
    assert log.current_file is None and not (tmp_path / "conversations").exists()

    log.on_transcript("user", "안녕", final=False)  # delta
    log.on_transcript("user", "   ", final=True)  # empty
    log.on_transcript("tool", "x", final=True)  # other role
    assert log.current_file is None

    log.on_transcript("user", "안녕, 리치미니 ", final=True)
    log.on_transcript("assistant", "안녕하세요!", final=True)

    path = log.current_file
    assert path is not None and path.parent == tmp_path / "conversations"
    assert path.suffix == ".jsonl" and len(path.stem) == len("2026-11-07_100321")
    rows = _read(path)
    assert [(row["role"], row["text"]) for row in rows] == [("user", "안녕, 리치미니"), ("assistant", "안녕하세요!")]
    assert all(set(row) == {"ts", "role", "text"} for row in rows)
    assert rows[0]["ts"][:4].isdigit() and ("+" in rows[0]["ts"][10:] or "-" in rows[0]["ts"][10:])  # offset


def test_new_file_per_session(tmp_path: Path) -> None:
    """end_session closes the file; the next line (after wake) opens a new one; start_session rotates too."""
    log = TranscriptLogger(tmp_path)
    log.on_transcript("assistant", "첫 세션", final=True)
    first = log.current_file
    assert first is not None
    assert log.end_session() == first and log.current_file is None

    log.on_transcript("user", "깨어났다", final=True)  # implicit new session after sleep/wake
    second = log.current_file
    assert second is not None and second != first

    log.start_session()
    log.on_transcript("user", "세 번째", final=True)
    third = log.current_file
    assert third is not None and third not in (first, second)

    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == sorted(p.name for p in (first, second, third))
    assert _read(first)[0]["text"] == "첫 세션" and _read(second)[0]["text"] == "깨어났다"
    assert log.end_session() == third and log.end_session() is None


def test_noop_when_not_configured(tmp_path: Path) -> None:
    """Without a bookmark repo the logger is disabled: nothing written, nothing buffered."""
    log = TranscriptLogger.from_settings(BookmarkInsightSettings(bookmark_repo=None))
    assert not log.enabled and log.log_dir is None
    log.start_session()
    log.on_transcript("user", "안녕", final=True)
    assert log.current_file is None and log.lines_since(0) == [] and log.end_session() is None
    assert list(tmp_path.iterdir()) == []


def test_from_settings_uses_resolved_dir(tmp_path: Path) -> None:
    """The log folder is the resolved conversation log dir (env override or <repo>/.bookmark_insight/conversations)."""
    assert TranscriptLogger.from_settings(BookmarkInsightSettings(bookmark_repo=tmp_path)).log_dir == (
        tmp_path / ".bookmark_insight" / "conversations"
    )
    custom = tmp_path / "logs"
    assert (
        TranscriptLogger.from_settings(
            BookmarkInsightSettings(bookmark_repo=tmp_path, conversation_log_dir=custom)
        ).log_dir
        == custom
    )


def test_write_failure_never_raises(tmp_path: Path) -> None:
    """A directory that cannot be created is logged and ignored; the buffer still fills."""
    blocker = tmp_path / "file"
    blocker.write_text("not a dir", encoding="utf-8")
    log = TranscriptLogger(blocker / "conversations")
    log.on_transcript("user", "안녕", final=True)
    assert [line.text for line in log.lines_since(0)] == ["안녕"]


def test_buffer_is_bounded_and_filtered_by_wall_time(tmp_path: Path) -> None:
    """lines_since returns only lines at/after the given wall time; the ring buffer keeps the newest."""
    log = TranscriptLogger(tmp_path, buffer_max=3)
    log.on_transcript("user", "old", final=True)
    cut = time.time()
    time.sleep(0.002)
    for text in ("a", "b", "c"):
        log.on_transcript("assistant", text, final=True)
    assert [line.text for line in log.lines_since(0)] == ["a", "b", "c"]
    assert [line.text for line in log.lines_since(cut)] == ["a", "b", "c"]
    assert log.lines_since(time.time() + 60) == []


def test_render_lines_and_cap() -> None:
    """Bullets read `- HH:MM 사용자/리치미니: …`; over the cap the last lines are kept and the cut is noted."""
    base = time.mktime((2026, 11, 7, 10, 3, 21, 0, 0, -1))
    lines = [
        TranscriptLine(wall=base, ts=local_iso(base), role="user", text="이번 주 북마크\n뭐 있어?"),
        TranscriptLine(wall=base + 60, ts=local_iso(base + 60), role="assistant", text="세 묶음이에요."),
    ]
    assert (
        render_transcript_lines(lines) == "- 10:03 사용자: 이번 주 북마크 뭐 있어?\n- 10:04 리치미니: 세 묶음이에요."
    )
    assert render_transcript_lines([]) == ""

    many = [TranscriptLine(wall=base + i, ts=local_iso(base + i), role="user", text=str(i)) for i in range(5)]
    capped = render_transcript_lines(many, max_lines=2).splitlines()
    assert capped == [TRANSCRIPT_CUT_NOTE.format(omitted=3), "- 10:03 사용자: 3", "- 10:03 사용자: 4"]


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_roles_are_labelled(role: str) -> None:
    """Both logged roles get a Korean label."""
    line = TranscriptLine(wall=0.0, ts=local_iso(0.0), role=role, text="x")
    assert ("사용자" if role == "user" else "리치미니") in render_transcript_lines([line])
