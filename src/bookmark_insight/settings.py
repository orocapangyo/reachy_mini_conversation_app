"""Environment-driven settings for the Bookmark Insight package (design doc §2.3).

The conversation app loads ``.env`` before importing tools, so reading
``os.environ`` here is enough. All values are plain data: no I/O, no
validation of paths beyond type conversion. Tracks A/B/C must take a
``BookmarkInsightSettings`` instance instead of reading ``os.environ`` themselves.
"""

from __future__ import annotations
import os
import logging
from pathlib import Path
from dataclasses import field, dataclass


logger = logging.getLogger(__name__)

ENV_PREFIX = "BOOKMARK_INSIGHT_"

# --- env variable names (single source of truth; .env.example mirrors these) ---
ENV_BOOKMARK_REPO = "BOOKMARK_INSIGHT_BOOKMARK_REPO"
ENV_CHROME_USER_DATA_DIR = "BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR"
ENV_CHROME_PROFILES = "BOOKMARK_INSIGHT_CHROME_PROFILES"
ENV_DEFAULT_PROFILE = "BOOKMARK_INSIGHT_DEFAULT_PROFILE"
ENV_INCLUDE_FOLDERS = "BOOKMARK_INSIGHT_INCLUDE_FOLDERS"
ENV_EXCLUDE_FOLDERS = "BOOKMARK_INSIGHT_EXCLUDE_FOLDERS"
ENV_SYNC_SINCE = "BOOKMARK_INSIGHT_SYNC_SINCE"
ENV_INSIGHTS_DIR = "BOOKMARK_INSIGHT_INSIGHTS_DIR"
ENV_EXCLUDE_URL_PATTERNS = "BOOKMARK_INSIGHT_EXCLUDE_URL_PATTERNS"
ENV_INSIGHT_IDLE_TIMEOUT_MIN = "BOOKMARK_INSIGHT_INSIGHT_IDLE_TIMEOUT_MIN"
ENV_CONVERSATION_LOG_DIR = "BOOKMARK_INSIGHT_CONVERSATION_LOG_DIR"
ENV_USAGE_LOG = "BOOKMARK_INSIGHT_USAGE_LOG"
ENV_GIT_MIRROR = "BOOKMARK_INSIGHT_GIT_MIRROR"
ENV_BATCH_MODEL = "BOOKMARK_INSIGHT_BATCH_MODEL"
ENV_BODY_MAX_CHARS = "BOOKMARK_INSIGHT_BODY_MAX_CHARS"
ENV_BATCH_MAX_SUMMARIES = "BOOKMARK_INSIGHT_BATCH_MAX_SUMMARIES"
ENV_DAILY_MAX_SUMMARIES = "BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES"
ENV_CALL_INTERVAL_S = "BOOKMARK_INSIGHT_CALL_INTERVAL_S"
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"  # shared with the Gemini Live backend
ENV_OBSIDIAN_CONFIG = (
    "BOOKMARK_INSIGHT_OBSIDIAN_CONFIG"  # overrides the OS default obsidian.json (setup dashboard only)
)
ENV_NOTE_LINKS = (
    "BOOKMARK_INSIGHT_NOTE_LINKS"  # auto | on | off: Obsidian ``related`` links in insight note front matter
)

# --- defaults (tuning constants; keep in sync with design doc §2.3) ---
DEFAULT_EXCLUDE_FOLDERS: tuple[str, ...] = ("private",)
DEFAULT_EXCLUDE_URL_PATTERNS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.2",  # 172.20.–172.29.
    "172.30.",
    "172.31.",
    "192.168.",
)
DEFAULT_INSIGHT_IDLE_TIMEOUT_MIN = 10
# measured 2026-11-07: gemini-3.8-flash took 230-270 s per generateContent call on a free key (even for a one-line prompt); gemini-3.5-flash took 5 s
DEFAULT_BATCH_MODEL = "gemini-3.5-flash"
DEFAULT_BODY_MAX_CHARS = 20_000
DEFAULT_BATCH_MAX_SUMMARIES = 20
DEFAULT_DAILY_MAX_SUMMARIES = 100  # free-tier protection; the rest waits for tomorrow (M7 backfill)
DEFAULT_CALL_INTERVAL_S = 6.0  # seconds between batch-model calls (requests-per-minute protection)
NOTE_LINKS_AUTO = "auto"  # write ``related`` [[links]] only when the note folder sits inside an Obsidian vault
NOTE_LINKS_ON = "on"
NOTE_LINKS_OFF = "off"
NOTE_LINKS_CHOICES: tuple[str, ...] = (NOTE_LINKS_AUTO, NOTE_LINKS_ON, NOTE_LINKS_OFF)
DEFAULT_NOTE_LINKS = NOTE_LINKS_AUTO
INSIGHTS_DIRNAME = "insights"
BOOKMARK_INSIGHT_STATE_DIRNAME = ".bookmark_insight"
LEGACY_STATE_DIRNAME = ".deskmate"  # name before the 2026-11-13 package rename; migrated once by ``state_dir``
USAGE_LOG_FILENAME = "usage.jsonl"
CONVERSATION_LOG_DIRNAME = "conversations"


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_path(name: str) -> Path | None:
    raw = (os.getenv(name) or "").strip()
    return Path(raw).expanduser() if raw else None


def _env_choice(name: str, choices: tuple[str, ...], default: str) -> str:
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in choices else default


@dataclass(frozen=True)
class BookmarkInsightSettings:
    """Resolved Bookmark Insight configuration (design doc §2.3)."""

    bookmark_repo: Path | None
    chrome_user_data_dir: Path | None = None
    chrome_profiles: tuple[str, ...] = ()  # allow list; empty = last-used profile only
    default_profile: str | None = None  # None = first allowed / last-used
    include_folders: tuple[str, ...] = ()  # empty = all roots
    exclude_folders: tuple[str, ...] = DEFAULT_EXCLUDE_FOLDERS
    sync_since: str | None = None  # ISO date; None = full history
    insights_dir: Path | None = None  # None = <repo>/insights
    exclude_url_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_URL_PATTERNS
    insight_idle_timeout_min: int = DEFAULT_INSIGHT_IDLE_TIMEOUT_MIN
    conversation_log_dir: Path | None = None  # None = <repo>/.bookmark_insight/conversations
    usage_log: Path | None = None  # None = <repo>/.bookmark_insight/usage.jsonl
    git_mirror: bool = False
    batch_model: str = DEFAULT_BATCH_MODEL
    body_max_chars: int = DEFAULT_BODY_MAX_CHARS
    batch_max_summaries: int = DEFAULT_BATCH_MAX_SUMMARIES
    daily_max_summaries: int = DEFAULT_DAILY_MAX_SUMMARIES
    call_interval_s: float = DEFAULT_CALL_INTERVAL_S
    note_links: str = DEFAULT_NOTE_LINKS  # auto | on | off (NOTE_LINKS_CHOICES)
    gemini_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject values outside the documented choices (``load_settings`` falls back to the default first)."""
        if self.note_links not in NOTE_LINKS_CHOICES:
            raise ValueError(f"{ENV_NOTE_LINKS} must be one of {', '.join(NOTE_LINKS_CHOICES)}: {self.note_links!r}")

    # --- derived paths -------------------------------------------------------
    @property
    def configured(self) -> bool:
        """Return whether the feature can run at all (a repo path is required)."""
        return self.bookmark_repo is not None

    def require_repo(self) -> Path:
        """Return the repo path or raise a clear error for tool wrappers to convert."""
        if self.bookmark_repo is None:
            raise RuntimeError(f"{ENV_BOOKMARK_REPO} is not set")
        return self.bookmark_repo

    @property
    def bookmarks_dir(self) -> Path:
        """Root of ``bookmarks/<profile>/YYYY/*.md``."""
        return self.require_repo() / "bookmarks"

    @property
    def resolved_insights_dir(self) -> Path:
        """Root of ``insights/<profile>/<since>_<until>.md``."""
        return self.insights_dir if self.insights_dir is not None else self.require_repo() / INSIGHTS_DIRNAME

    @property
    def note_links_probe_dir(self) -> Path | None:
        """Folder whose ancestors decide ``note_links: auto``: the insights dir when set, else the repo (None = unconfigured)."""
        return self.insights_dir if self.insights_dir is not None else self.bookmark_repo

    @property
    def state_dir(self) -> Path:
        """Package-private state (usage log, locks, counters); renames a pre-rename ``.deskmate/`` folder once."""
        repo = self.require_repo()
        state = repo / BOOKMARK_INSIGHT_STATE_DIRNAME
        legacy = repo / LEGACY_STATE_DIRNAME
        if not state.exists() and legacy.is_dir():
            legacy.rename(state)
            logger.info("Renamed legacy state folder %s to %s", legacy, state)
        return state

    @property
    def resolved_usage_log(self) -> Path:
        """Path of the token usage JSONL log."""
        return self.usage_log if self.usage_log is not None else self.state_dir / USAGE_LOG_FILENAME

    @property
    def resolved_conversation_log_dir(self) -> Path:
        """Directory of per-session conversation JSONL logs (M8)."""
        if self.conversation_log_dir is not None:
            return self.conversation_log_dir
        return self.state_dir / CONVERSATION_LOG_DIRNAME

    def profile_bookmarks_dir(self, profile: str) -> Path:
        """``bookmarks/<profile>/`` for one Chrome profile folder name."""
        return self.bookmarks_dir / profile

    def profile_insights_dir(self, profile: str) -> Path:
        """``insights/<profile>/`` for one Chrome profile folder name."""
        return self.resolved_insights_dir / profile


def load_settings(environ: dict[str, str] | None = None) -> BookmarkInsightSettings:
    """Build settings from ``os.environ`` (or an explicit mapping for tests)."""
    if environ is not None:
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(environ)
        try:
            return load_settings(None)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    exclude_folders = _split_csv(os.getenv(ENV_EXCLUDE_FOLDERS))
    exclude_urls = _split_csv(os.getenv(ENV_EXCLUDE_URL_PATTERNS))
    profiles = _split_csv(os.getenv(ENV_CHROME_PROFILES))
    default_profile = (os.getenv(ENV_DEFAULT_PROFILE) or "").strip() or None
    if default_profile is None and profiles:
        default_profile = profiles[0]

    return BookmarkInsightSettings(
        bookmark_repo=_env_path(ENV_BOOKMARK_REPO),
        chrome_user_data_dir=_env_path(ENV_CHROME_USER_DATA_DIR),
        chrome_profiles=profiles,
        default_profile=default_profile,
        include_folders=_split_csv(os.getenv(ENV_INCLUDE_FOLDERS)),
        exclude_folders=exclude_folders if os.getenv(ENV_EXCLUDE_FOLDERS) is not None else DEFAULT_EXCLUDE_FOLDERS,
        sync_since=(os.getenv(ENV_SYNC_SINCE) or "").strip() or None,
        insights_dir=_env_path(ENV_INSIGHTS_DIR),
        exclude_url_patterns=DEFAULT_EXCLUDE_URL_PATTERNS + exclude_urls,
        insight_idle_timeout_min=_env_int(ENV_INSIGHT_IDLE_TIMEOUT_MIN, DEFAULT_INSIGHT_IDLE_TIMEOUT_MIN),
        conversation_log_dir=_env_path(ENV_CONVERSATION_LOG_DIR),
        usage_log=_env_path(ENV_USAGE_LOG),
        git_mirror=_env_flag(ENV_GIT_MIRROR, default=False),
        batch_model=(os.getenv(ENV_BATCH_MODEL) or "").strip() or DEFAULT_BATCH_MODEL,
        body_max_chars=_env_int(ENV_BODY_MAX_CHARS, DEFAULT_BODY_MAX_CHARS),
        batch_max_summaries=_env_int(ENV_BATCH_MAX_SUMMARIES, DEFAULT_BATCH_MAX_SUMMARIES),
        daily_max_summaries=_env_int(ENV_DAILY_MAX_SUMMARIES, DEFAULT_DAILY_MAX_SUMMARIES),
        call_interval_s=_env_float(ENV_CALL_INTERVAL_S, DEFAULT_CALL_INTERVAL_S),
        note_links=_env_choice(ENV_NOTE_LINKS, NOTE_LINKS_CHOICES, DEFAULT_NOTE_LINKS),
        gemini_api_key=(os.getenv(ENV_GEMINI_API_KEY) or "").strip() or None,
    )
