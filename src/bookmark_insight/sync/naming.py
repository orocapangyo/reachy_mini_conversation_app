"""Bookmark file naming and URL keys (§2.4 파일명 규칙).

``bookmarks/<profile>/YYYY/YYYY-MM-DD <title> <guid[:6]>.md`` — the title
part is NFC-normalized, stripped of Windows-forbidden and Obsidian
link-breaking characters, kept clear of reserved device names and trimmed so
the name stays under ``FILENAME_MAX_CHARS`` and the full path under
``PATH_MAX_CHARS``.

The stem is what insight notes put inside ``[[...]]`` (front matter
``related``), so ``[``, ``]``, ``#`` (heading anchor), ``^`` (block anchor)
and ``|`` (alias separator) can never appear in it. Files created before this
rule keep their names: the guid in the name is what sync matches on, and a
rename would break links already written.
"""

import unicodedata
from pathlib import Path
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from bookmark_insight.settings import BookmarkInsightSettings


FILENAME_MAX_CHARS = 120
PATH_MAX_CHARS = 240
GUID_PREFIX_CHARS = 6
UNTITLED = "untitled"
WINDOWS_FORBIDDEN_CHARS = frozenset(':*?"<>|')
OBSIDIAN_LINK_CHARS = frozenset("[]#^|")  # would end or split a [[wikilink]] to the file
FORBIDDEN_CHARS = WINDOWS_FORBIDDEN_CHARS | OBSIDIAN_LINK_CHARS
SEPARATOR_CHARS = frozenset("/\\")  # become a space so "async/await" stays readable
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)


def sanitize_title(title: str) -> str:
    """Return ``title`` as a safe filename fragment: NFC, no forbidden/control chars, single spaces, no trailing dots."""
    kept: list[str] = []
    for char in unicodedata.normalize("NFC", title):
        if char in SEPARATOR_CHARS or char.isspace():
            kept.append(" ")
        elif char in FORBIDDEN_CHARS or ord(char) < 32 or ord(char) == 127:
            continue
        else:
            kept.append(char)
    return _finish(" ".join("".join(kept).split()))


def build_filename(day: date, title: str, guid: str, *, max_chars: int = FILENAME_MAX_CHARS) -> str:
    """Return ``YYYY-MM-DD <title> <guid[:6]>.md``, trimming the title so the name has at most ``max_chars`` characters."""
    prefix = day.isoformat()
    suffix = f"{guid[:GUID_PREFIX_CHARS]}.md"
    budget = max_chars - len(prefix) - len(suffix) - 2  # two separating spaces
    clean = sanitize_title(title) or UNTITLED
    if len(clean) > budget:
        clean = _finish(clean[: max(budget, 0)])
        if len(clean) > budget:  # reserved-name suffix pushed it over
            clean = _finish(clean[: budget - 1]) if budget > 1 else ""
    return " ".join(part for part in (prefix, clean, suffix) if part)


def build_bookmark_path(settings: BookmarkInsightSettings, profile: str, day: date, title: str, guid: str) -> Path:
    """Return the repo path of a bookmark, trimming the title further so the whole path stays under ``PATH_MAX_CHARS``."""
    directory = settings.profile_bookmarks_dir(profile) / str(day.year)
    max_chars = min(FILENAME_MAX_CHARS, PATH_MAX_CHARS - len(str(directory)) - 1)
    return directory / build_filename(day, title, guid, max_chars=max_chars)


def normalize_url(url: str) -> str:
    """Return the duplicate-detection key: lowercase scheme and host, no fragment, no trailing slash, query kept."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def _finish(text: str) -> str:
    """Strip trailing dots/spaces and dodge Windows reserved device names."""
    stripped = text.strip().rstrip(". ")
    if stripped.upper() in RESERVED_NAMES:
        return stripped + "_"
    return stripped
