"""Conversation transcript logger for Bookmark Insight (design doc §2.6, M8).

One JSONL file per session under the configured conversation log folder
(``BOOKMARK_INSIGHT_CONVERSATION_LOG_DIR``, default ``<repo>/.bookmark_insight/conversations``),
one line per final transcript: ``{"ts", "role", "text"}``. Audio is never stored
and nothing leaves the machine.

Session boundary rule
---------------------
* A file is created lazily by the first final line of a session and named after
  that line's local time (``YYYY-MM-DD_HHMMSS.jsonl``).
* ``end_session()`` (called by the app hooks on sleep and on shutdown) closes the
  file; the next final line (after wake, when the realtime session reconnects
  and greets) opens a new file. ``start_session()`` forces the same rotation and
  the app calls it once at install time.
* Net effect: one file per app run, split per sleep/wake cycle.

The logger also keeps the last ``TRANSCRIPT_BUFFER_MAX`` lines in memory with
wall-clock timestamps so ``finish_insight`` can attach the lines of the current
insight session (``InsightSession.started_wall``) to the note as a collapsed
``대화 원문`` block.

The logger is attached to the handler through ``LocalStream.add_transcript_observer``
(fan-out: the JSON-RPC console keeps its own observer). The handler itself is
not modified. Writes never raise: a failure is logged at debug level and the
conversation goes on.
"""

from __future__ import annotations
import json
import time
import logging
import threading
from typing import Iterable
from pathlib import Path
from datetime import datetime
from collections import deque
from dataclasses import dataclass

from bookmark_insight.settings import BookmarkInsightSettings


logger = logging.getLogger(__name__)

# --- tuning constants -----------------------------------------------------------
TRANSCRIPT_BUFFER_MAX = 2000  # in-memory lines kept for finish_insight
TRANSCRIPT_SECTION_MAX_LINES = 200  # lines attached to a note (the last ones are kept)
SESSION_FILE_TIME_FORMAT = "%Y-%m-%d_%H%M%S"
LOGGED_ROLES = ("user", "assistant")
ROLE_LABELS = {"user": "사용자", "assistant": "리치미니"}
TRANSCRIPT_CUT_NOTE = "_(앞부분 {omitted}줄 생략)_"


@dataclass(frozen=True)
class TranscriptLine:
    """One final transcript line as logged (``wall`` is ``time.time()`` at logging time)."""

    wall: float
    ts: str
    role: str
    text: str

    def to_json(self) -> str:
        """Serialize the on-disk JSONL form (``wall`` is internal)."""
        return json.dumps({"ts": self.ts, "role": self.role, "text": self.text}, ensure_ascii=False)


def local_iso(wall: float) -> str:
    """ISO-8601 local time with UTC offset (``2026-11-07T10:03:21.123+09:00``)."""
    return datetime.fromtimestamp(wall).astimezone().isoformat(timespec="milliseconds")


class TranscriptLogger:
    """Append-only JSONL transcript writer plus an in-memory ring buffer (thread-safe)."""

    def __init__(self, log_dir: Path | None, *, buffer_max: int = TRANSCRIPT_BUFFER_MAX) -> None:
        """Log into ``log_dir``; ``None`` makes the logger a no-op (Bookmark Insight not configured)."""
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._buffer: deque[TranscriptLine] = deque(maxlen=buffer_max)
        self._current: Path | None = None  # session file, None until the first line is written

    @classmethod
    def from_settings(cls, settings: BookmarkInsightSettings) -> TranscriptLogger:
        """Build the logger from the resolved settings (disabled when no repo is configured)."""
        if not settings.configured:
            return cls(None)
        return cls(settings.resolved_conversation_log_dir)

    # --- state --------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """Whether lines are written at all."""
        return self._log_dir is not None

    @property
    def log_dir(self) -> Path | None:
        """Configured log folder (``None`` when disabled)."""
        return self._log_dir

    @property
    def current_file(self) -> Path | None:
        """File of the open session, or ``None`` before its first line / after ``end_session``."""
        with self._lock:
            return self._current

    def start_session(self) -> None:
        """Begin a new session: the next final line opens a new file."""
        with self._lock:
            self._current = None

    def end_session(self) -> Path | None:
        """Close the current session file (sleep / shutdown) and return its path, if any."""
        with self._lock:
            closed, self._current = self._current, None
        if closed is not None:
            logger.info("Conversation log closed: %s", closed)
        return closed

    # --- observer -----------------------------------------------------------------
    def on_transcript(self, role: str, text: str, final: bool) -> None:
        """Transcript observer: log final user/assistant lines; everything else is dropped."""
        if not final or role not in LOGGED_ROLES or not self.enabled:
            return
        text = text.strip()
        if not text:
            return
        wall = time.time()
        line = TranscriptLine(wall=wall, ts=local_iso(wall), role=role, text=text)
        with self._lock:
            self._buffer.append(line)
            try:
                self._write_locked(line)
            except Exception:
                logger.debug("conversation log write failed (ignored)", exc_info=True)

    def _write_locked(self, line: TranscriptLine) -> None:
        if self._log_dir is None:
            return
        if self._current is None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._current = self._new_session_path(line.wall)
            logger.info("Conversation log opened: %s", self._current)
        with self._current.open("a", encoding="utf-8") as handle:
            handle.write(line.to_json() + "\n")

    def _new_session_path(self, wall: float) -> Path:
        assert self._log_dir is not None
        stem = datetime.fromtimestamp(wall).strftime(SESSION_FILE_TIME_FORMAT)
        path = self._log_dir / f"{stem}.jsonl"
        suffix = 2
        while path.exists():  # two sessions within one second (tests, rapid sleep/wake)
            path = self._log_dir / f"{stem}_{suffix}.jsonl"
            suffix += 1
        return path

    # --- buffer -------------------------------------------------------------------
    def lines_since(self, started_wall: float) -> list[TranscriptLine]:
        """Buffered lines logged at or after ``started_wall`` (oldest first)."""
        with self._lock:
            return [line for line in self._buffer if line.wall >= started_wall]


def render_transcript_lines(lines: Iterable[TranscriptLine], *, max_lines: int = TRANSCRIPT_SECTION_MAX_LINES) -> str:
    """Render ``- HH:MM 사용자: …`` bullets for the note; keep the last ``max_lines`` and note the cut."""
    items = list(lines)
    omitted = max(len(items) - max_lines, 0)
    rendered = [TRANSCRIPT_CUT_NOTE.format(omitted=omitted)] if omitted else []
    for line in items[omitted:]:
        clock = datetime.fromtimestamp(line.wall).strftime("%H:%M")
        label = ROLE_LABELS.get(line.role, line.role)
        text = " ".join(line.text.split())  # one bullet per line, whatever the transcript contained
        rendered.append(f"- {clock} {label}: {text}")
    return "\n".join(rendered)


# --- process-wide instance (set by the app hooks, read by finish_insight) --------------
TRANSCRIPT_LOG: TranscriptLogger | None = None


def set_transcript_log(transcript_log: TranscriptLogger | None) -> None:
    """Register (or clear) the process-wide logger."""
    global TRANSCRIPT_LOG
    TRANSCRIPT_LOG = transcript_log


def get_transcript_log() -> TranscriptLogger | None:
    """Return the process-wide logger, or ``None`` when the app has not installed one."""
    return TRANSCRIPT_LOG
