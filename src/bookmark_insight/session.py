"""Insight-conversation session state machine (§3.3, M6).

The tools open a session in ``get_insight`` / ``build_insight`` (``ready``) and
close it in ``finish_insight``. The app feeds heartbeats from its activity
observer and closes the session itself on silence / sleep / shutdown through
``auto_finish`` (``reason="auto"``), which writes a ``## 대화 기록`` section whose
body is only the ``자동 종료`` marker: no LLM is involved in that path.
"""

from __future__ import annotations
import time
import logging
import threading
from typing import Literal
from pathlib import Path
from datetime import datetime
from dataclasses import field, dataclass

from bookmark_insight.store import append_conversation_section


logger = logging.getLogger(__name__)

FinishReason = Literal["explicit", "confirmed", "auto"]

# --- tuning constants / note wording ---------------------------------------------
AUTO_CLOSE_MARKER = "자동 종료"
TRIGGER_IDLE = "무응답"  # formatted as "무응답 10분" by the caller
TRIGGER_SLEEP = "슬립 전환"
TRIGGER_SHUTDOWN = "앱 종료"


def now_local_label() -> str:
    """Local ``YYYY-MM-DD HH:MM`` used in ``## 대화 기록`` headings."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def auto_close_body(trigger: str) -> str:
    """Body of the conversation section written by the app-side close (conclusion left blank)."""
    return f"_{AUTO_CLOSE_MARKER}: {trigger}_"


@dataclass
class InsightSession:
    """One open insight conversation."""

    profile: str
    note_path: Path
    started_at: float = field(default_factory=time.monotonic)
    started_wall: float = field(default_factory=time.time)  # wall clock, to pick transcript lines (M8)
    last_activity: float = field(default_factory=time.monotonic)
    asked_to_finish: bool = False  # "마칠까요?" asked once already


class InsightSessionManager:
    """Process-wide holder of the single open insight session (thread-safe)."""

    def __init__(self) -> None:
        """Start with no open session."""
        self._lock = threading.Lock()
        self._session: InsightSession | None = None
        self._last_closed: InsightSession | None = None

    def open(self, profile: str, note_path: Path) -> InsightSession:
        """Start a session, implicitly closing any previous one."""
        with self._lock:
            self._session = InsightSession(profile=profile, note_path=note_path)
            return self._session

    def current(self) -> InsightSession | None:
        """Return the open session, if any."""
        with self._lock:
            return self._session

    def heartbeat(self) -> None:
        """Record user/assistant activity (resets the idle timer)."""
        with self._lock:
            if self._session is not None:
                self._session.last_activity = time.monotonic()

    def close(self) -> InsightSession | None:
        """Close and return the session (caller writes the note section).

        The closed session is kept as ``last_closed`` whatever closed it, so a
        later ``finish_insight`` without an open session (premature explicit
        finish, app-side idle/sleep close) can still record its conclusion
        there once (``take_last_closed``).
        """
        with self._lock:
            session, self._session = self._session, None
            if session is not None:
                self._last_closed = session
            return session

    def reset(self) -> None:
        """Drop the open session and the last-closed memory (tests, app restart)."""
        with self._lock:
            self._session = None
            self._last_closed = None

    def last_closed(self) -> InsightSession | None:
        """Return the most recently closed session (a late ``finish_insight`` can still find its note)."""
        with self._lock:
            return self._last_closed

    def take_last_closed(self) -> InsightSession | None:
        """Return and forget the last closed session (the safety net is used once)."""
        with self._lock:
            session, self._last_closed = self._last_closed, None
            return session

    def idle_seconds(self) -> float | None:
        """Seconds since the last heartbeat, or ``None`` when no session is open."""
        with self._lock:
            if self._session is None:
                return None
            return time.monotonic() - self._session.last_activity

    def close_if_idle(self, timeout_s: float) -> InsightSession | None:
        """Atomically close the session when it has been idle for at least ``timeout_s`` seconds."""
        with self._lock:
            session = self._session
            if session is None or time.monotonic() - session.last_activity < timeout_s:
                return None
            self._session = None
            self._last_closed = session
            return session

    def auto_finish(self, trigger: str, *, idle_timeout_s: float | None = None) -> InsightSession | None:
        """App-side close (§3.3 path 3): mark the note ``자동 종료: <trigger>`` and return the closed session.

        With ``idle_timeout_s`` the session is only closed when idle for that long.
        Never raises: this runs from shutdown and watchdog paths.
        """
        session = self.close_if_idle(idle_timeout_s) if idle_timeout_s is not None else self.close()
        if session is None:
            return None
        try:
            append_conversation_section(session.note_path, now_local_label(), auto_close_body(trigger))
        except Exception:
            logger.exception("auto_finish: could not mark %s in %s", AUTO_CLOSE_MARKER, session.note_path)
        else:
            logger.info("Insight session auto-closed (%s): %s", trigger, session.note_path)
        return session


SESSION_MANAGER = InsightSessionManager()
