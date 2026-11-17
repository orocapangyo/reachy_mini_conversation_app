"""Thread-safe log ring buffer with parsing and asyncio fan-out to dashboard clients."""

from __future__ import annotations
import re
import time
import asyncio
import threading
from typing import Any
from collections import deque
from dataclasses import asdict, dataclass

from jisu.dashboard.constants import LOG_BUFFER_MAX_LINES, LOG_SUBSCRIBER_QUEUE_MAX


# Matches the app's logging format (utils.setup_logger):
#   "%(asctime)s %(levelname)s %(name)s:%(lineno)d | %(message)s"
_APP_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<logger>[^\s|]+) \| (?P<message>.*)$"
)
# Loose fallback for other tools (uvicorn "INFO:     ...", daemon "WARNING ...").
_LEVEL_PREFIX_RE = re.compile(r"^\s*(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b[:\s]")
_TRACEBACK_HINT_RE = re.compile(r"^(Traceback \(most recent call last\)|\s+File \"|\w+(Error|Exception):)")


@dataclass(frozen=True)
class LogEntry:
    """One log line as shown in the dashboard."""

    seq: int
    ts: float
    source: str
    level: str
    logger: str
    message: str
    raw: str

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


def parse_log_line(line: str) -> tuple[str, str, str]:
    """Return ``(level, logger, message)`` for one raw output line."""
    match = _APP_LOG_RE.match(line)
    if match:
        return match.group("level"), match.group("logger"), match.group("message")
    prefix = _LEVEL_PREFIX_RE.match(line)
    if prefix:
        level = prefix.group("level")
        return ("WARNING" if level == "WARN" else level), "", line
    if _TRACEBACK_HINT_RE.match(line):
        return "ERROR", "", line
    return "INFO", "", line


class LogBuffer:
    """Keeps the last N lines and pushes new ones to subscribed asyncio queues."""

    def __init__(self, max_lines: int = LOG_BUFFER_MAX_LINES) -> None:
        """Create an empty buffer holding at most ``max_lines`` entries."""
        self._entries: deque[LogEntry] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._seq = 0
        self._subscribers: dict[asyncio.Queue[LogEntry], asyncio.AbstractEventLoop] = {}

    def append(self, source: str, line: str) -> LogEntry:
        """Record one line (callable from any thread)."""
        raw = line.rstrip("\r\n")
        level, logger_name, message = parse_log_line(raw)
        with self._lock:
            self._seq += 1
            entry = LogEntry(
                seq=self._seq,
                ts=time.time(),
                source=source,
                level=level,
                logger=logger_name,
                message=message,
                raw=raw,
            )
            self._entries.append(entry)
            subscribers = list(self._subscribers.items())
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, queue, entry)
            except RuntimeError:
                # Loop closed: the subscriber is gone.
                self.unsubscribe(queue)
        return entry

    def snapshot(self, after_seq: int = 0) -> list[LogEntry]:
        """Return buffered entries newer than ``after_seq``."""
        with self._lock:
            return [entry for entry in self._entries if entry.seq > after_seq]

    def clear(self) -> None:
        """Drop buffered entries (sequence numbers keep increasing)."""
        with self._lock:
            self._entries.clear()

    def subscribe(self) -> asyncio.Queue[LogEntry]:
        """Register a queue on the running loop that receives every new entry."""
        queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=LOG_SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subscribers[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LogEntry]) -> None:
        """Stop pushing entries to ``queue``."""
        with self._lock:
            self._subscribers.pop(queue, None)


def _offer(queue: asyncio.Queue[LogEntry], entry: LogEntry) -> None:
    """Enqueue without blocking; drop the oldest item when a slow client falls behind."""
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(entry)
