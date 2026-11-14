"""Data contracts shared by all Bookmark Insight tracks (design doc §2.2, §2.4, §2.5).

These are the shapes written to disk (bookmark front matter, insight notes)
and returned to the app's tools. Changing a field here is a contract change:
update the design doc and every track.
"""

from __future__ import annotations
from typing import Any, Literal
from datetime import date
from dataclasses import field, asdict, dataclass


SummaryStatus = Literal["pending", "ok", "fetch_failed"]
DateSource = Literal["chrome", "sync"]

# Front matter keys in the order they are written to disk (§2.4 example file).
BOOKMARK_FRONTMATTER_KEYS: tuple[str, ...] = (
    "title",
    "url",
    "tags",
    "date",
    "date_added_utc",
    "date_source",
    "guid",
    "profile",
    "profile_name",
    "last_used",
    "archived",
    "summary_status",
    "summarized_at",
    "fetch_attempts",
    "last_fetch_at",
    "summary_error",
)

SUMMARY_HEADING = "## 요약"
CONVERSATION_HEADING_PREFIX = "## 대화 기록"
FETCH_FAILED_SUMMARY_TEXT = "본문을 가져오지 못했다. 제목 기준으로만 다룸."


@dataclass
class Bookmark:
    """One bookmark = one markdown file (§2.4)."""

    title: str
    url: str
    guid: str
    profile: str
    date: date  # local-date of date_added; period filtering uses this
    tags: list[str] = field(default_factory=list)
    date_added_utc: str | None = None  # ISO 8601 with Z
    date_source: DateSource = "chrome"
    profile_name: str | None = None
    last_used: str | None = None
    archived: bool = False
    summary_status: SummaryStatus = "pending"
    summarized_at: str | None = None
    fetch_attempts: int = 0
    last_fetch_at: str | None = None
    summary_error: str | None = None  # last batch-model failure (503 etc.); status stays pending
    body: str = ""  # everything after the front matter; owned by the summarizer/user
    path: Any = None  # pathlib.Path once loaded from disk; not serialized

    @property
    def id(self) -> str:
        """Stable id used by tools: the Chrome guid."""
        return self.guid

    def to_frontmatter(self) -> dict[str, Any]:
        """Return the front matter mapping in canonical key order."""
        data = asdict(self)
        data.pop("body", None)
        data.pop("path", None)
        data["date"] = self.date.isoformat()
        return {key: data[key] for key in BOOKMARK_FRONTMATTER_KEYS if data.get(key) is not None}

    def to_tool_summary(self) -> dict[str, Any]:
        """Compact shape for ``list_bookmarks`` (§2.2)."""
        return {
            "id": self.guid,
            "title": self.title,
            "url": self.url,
            "tags": list(self.tags),
            "date": self.date.isoformat(),
            "summary_status": self.summary_status,
        }


@dataclass
class InsightCluster:
    """One topic cluster inside an insight note (§2.5 step 3 JSON schema)."""

    topic: str
    bookmark_ids: list[str]
    summary: str
    correlation: str
    question: str

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for tool results."""
        return asdict(self)


@dataclass
class InsightNote:
    """Insight note = ``insights/<profile>/<since>_<until>.md`` (§2.5 step 4)."""

    profile: str
    since: date
    until: date
    generated_at: str  # ISO 8601
    clusters: list[InsightCluster] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)  # bookmark guids without body (fetch_failed)
    # Obsidian ``"[[<bookmark file stem>]]"`` links for the graph/backlinks; front matter only,
    # written when the note lives in an Obsidian vault (settings ``note_links``), else empty = key omitted.
    related: list[str] = field(default_factory=list)
    bookmark_ids: list[str] = field(default_factory=list)  # every bookmark of the period (seen or not)
    complete: bool = True  # False while a backfill is still running (M7)
    pending: list[str] = field(default_factory=list)  # guids not summarized yet (M7 partial note); empty = complete
    conversations: list[str] = field(default_factory=list)  # raw "## 대화 기록" sections, preserved verbatim
    path: Any = None
    # Runtime-only (never written to disk): the batch model was unavailable and this
    # is the previous note served as a fallback; ``pending_titles`` are the bookmarks
    # of the period it does not cover yet.
    degraded: bool = False
    pending_titles: list[str] = field(default_factory=list)
    # Runtime-only (M7): the daily summary cap stopped the batch, so the pending
    # bookmarks can only continue tomorrow.
    daily_cap_reached: bool = False

    @property
    def summarized_count(self) -> int:
        """Bookmarks of the period the note is built from (everything that is not pending)."""
        return max(0, len(self.bookmark_ids) - len(self.pending))

    @property
    def pending_count(self) -> int:
        """Bookmarks of the period the batch has not summarized yet."""
        return len(self.pending)

    @property
    def key(self) -> str:
        """File stem: ``<since>_<until>``."""
        return f"{self.since.isoformat()}_{self.until.isoformat()}"

    def to_tool_result(self) -> dict[str, Any]:
        """Fields that ``get_insight`` returns to the model (§2.2)."""
        return {
            "profile": self.profile,
            "period": {"since": self.since.isoformat(), "until": self.until.isoformat()},
            "note_path": str(self.path) if self.path is not None else "",
            "clusters": [cluster.to_dict() for cluster in self.clusters],
            "unavailable": list(self.unavailable),
        }
