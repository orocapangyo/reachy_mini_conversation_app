"""Chrome ``User Data`` discovery and ``Bookmarks`` JSON reading (§2.4).

Chrome's on-disk format is not a public API, so everything that touches it
is isolated here: if the format changes, only this module has to follow.

A profile can hold two bookmark files with the same JSON layout: ``Bookmarks``
(local / legacy sync storage) and ``AccountBookmarks`` (account storage used
when the user is signed in to Chrome, 2024+). A signed-in profile may have
only the latter, so both are read and merged per profile.
"""

import os
import sys
import json
import time
import logging
import unicodedata
from typing import Any, Iterator
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass


logger = logging.getLogger(__name__)

LOCAL_STATE_FILENAME = "Local State"
BOOKMARKS_FILENAME = "Bookmarks"
ACCOUNT_BOOKMARKS_FILENAME = "AccountBookmarks"
BOOKMARK_FILENAMES: tuple[str, ...] = (BOOKMARKS_FILENAME, ACCOUNT_BOOKMARKS_FILENAME)
DEFAULT_PROFILE_FOLDER = "Default"
ROOT_KEYS: tuple[str, ...] = ("bookmark_bar", "other", "synced")  # 부록 A 5: JSON keys, not display names
PARSE_ATTEMPTS = 2
PARSE_RETRY_DELAY_S = 0.5  # Chrome may be mid-write; §2.4 asks for exactly one retry
WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class ChromeReadError(RuntimeError):
    """A Chrome JSON file could not be read or parsed after the retry."""


@dataclass(frozen=True)
class ChromeProfile:
    """One profile folder: name from ``Local State`` plus its bookmark files (empty when absent on disk)."""

    folder: str
    name: str | None
    bookmarks_paths: tuple[Path, ...] = ()

    @property
    def bookmarks_path(self) -> Path | None:
        """First bookmark file (``Bookmarks`` before ``AccountBookmarks``), or ``None`` when the profile has none."""
        return self.bookmarks_paths[0] if self.bookmarks_paths else None


@dataclass(frozen=True)
class ChromeUserData:
    """Profiles found in one ``User Data`` directory, keyed by NFC folder name, and the last-used folder."""

    root: Path
    profiles: dict[str, ChromeProfile]
    last_used: str | None

    def on_disk(self) -> list[str]:
        """Return the folder names that actually hold a bookmark file."""
        return sorted(folder for folder, profile in self.profiles.items() if profile.bookmarks_paths)


@dataclass(frozen=True)
class ChromeBookmarkNode:
    """One ``type: url`` node with its folder path below the root it lives in."""

    guid: str
    url: str
    title: str
    root: str
    folders: tuple[str, ...]
    date_added: datetime | None  # UTC; None when Chrome stored 0
    date_last_used: datetime | None

    @property
    def folder_key(self) -> str:
        """``<root_key>/<folder>/<subfolder>`` form used by the include-folder setting."""
        return "/".join((self.root, *self.folders))


def default_user_data_dir(platform: str | None = None) -> Path:
    """Return Chrome's default ``User Data`` directory for ``platform`` (default: this OS)."""
    system = platform or sys.platform
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if system.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Google" / "Chrome" / "User Data"
    return Path.home() / ".config" / "google-chrome"


def load_json(path: Path) -> dict[str, Any]:
    """Parse a Chrome JSON file, retrying once after ``PARSE_RETRY_DELAY_S`` when it is unreadable or mid-write."""
    last_error: Exception | None = None
    for attempt in range(PARSE_ATTEMPTS):
        if attempt:
            time.sleep(PARSE_RETRY_DELAY_S)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Could not read %s (attempt %d/%d): %s", path, attempt + 1, PARSE_ATTEMPTS, exc)
            continue
        if isinstance(loaded, dict):
            return loaded
        last_error = ValueError("top-level JSON value is not an object")
        logger.warning("Unexpected content in %s (attempt %d/%d)", path, attempt + 1, PARSE_ATTEMPTS)
    raise ChromeReadError(f"{path}: {last_error}") from last_error


def bookmark_files(profile_dir: Path) -> tuple[Path, ...]:
    """Bookmark JSON files present in one profile folder, in ``BOOKMARK_FILENAMES`` order."""
    return tuple(profile_dir / name for name in BOOKMARK_FILENAMES if (profile_dir / name).is_file())


def discover_user_data(root: Path) -> ChromeUserData:
    """List profiles from ``Local State`` (folder + display name + last used) merged with folders holding a bookmark file."""
    names: dict[str, str | None] = {}
    last_used: str | None = None
    state_path = root / LOCAL_STATE_FILENAME
    if state_path.is_file():
        try:
            state = load_json(state_path)
        except ChromeReadError as exc:
            logger.warning("Ignoring unreadable %s: %s", state_path, exc)
        else:
            profile_section = state.get("profile")
            if isinstance(profile_section, dict):
                info_cache = profile_section.get("info_cache")
                if isinstance(info_cache, dict):
                    for folder, info in info_cache.items():
                        display = info.get("name") if isinstance(info, dict) else None
                        names[_nfc(str(folder))] = _nfc(str(display)) if display else None
                raw_last_used = profile_section.get("last_used")
                if isinstance(raw_last_used, str) and raw_last_used:
                    last_used = _nfc(raw_last_used)
    else:
        logger.warning("No %s in %s; listing folders that hold a bookmark file", LOCAL_STATE_FILENAME, root)

    folders_on_disk: dict[str, tuple[Path, ...]] = {}
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir():
                found = bookmark_files(child)
                if found:
                    folders_on_disk[_nfc(child.name)] = found
    for folder in folders_on_disk:
        names.setdefault(folder, None)

    profiles: dict[str, ChromeProfile] = {}
    for folder, display in names.items():
        profiles[folder] = ChromeProfile(folder=folder, name=display, bookmarks_paths=folders_on_disk.get(folder, ()))
    if last_used is None and DEFAULT_PROFILE_FOLDER in folders_on_disk:
        last_used = DEFAULT_PROFILE_FOLDER

    listing = ", ".join(
        f"{folder} ({profile.name or 'no name'}"
        f"{': ' + '+'.join(path.name for path in profile.bookmarks_paths) if profile.bookmarks_paths else ', no bookmark file'})"
        for folder, profile in profiles.items()
    )
    logger.info("Chrome profiles in %s: %s; last used: %s", root, listing or "none", last_used)
    return ChromeUserData(root=root, profiles=profiles, last_used=last_used)


def iter_url_nodes(bookmarks_json: dict[str, Any]) -> Iterator[ChromeBookmarkNode]:
    """Yield every url node under ``roots.bookmark_bar`` / ``other`` / ``synced`` in Chrome's traversal order."""
    roots = bookmarks_json.get("roots")
    if not isinstance(roots, dict):
        return
    for root_key in ROOT_KEYS:
        root_node = roots.get(root_key)
        if isinstance(root_node, dict):
            yield from _walk(root_node.get("children"), root_key, ())


def _walk(children: object, root_key: str, folders: tuple[str, ...]) -> Iterator[ChromeBookmarkNode]:
    if not isinstance(children, list):
        return
    for node in children:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        if node_type == "folder":
            yield from _walk(node.get("children"), root_key, (*folders, _nfc(str(node.get("name", "")))))
        elif node_type == "url":
            url = node.get("url")
            guid = node.get("guid")
            if not isinstance(url, str) or not url or not isinstance(guid, str) or not guid:
                continue
            yield ChromeBookmarkNode(
                guid=guid,
                url=url,
                title=_nfc(str(node.get("name", ""))),
                root=root_key,
                folders=folders,
                date_added=webkit_to_utc(node.get("date_added")),
                date_last_used=webkit_to_utc(node.get("date_last_used")),
            )


def webkit_to_utc(raw: object) -> datetime | None:
    """Convert a WebKit timestamp (µs since 1601-01-01 UTC, stored as a string) to an aware UTC datetime; 0 or invalid → None."""
    try:
        micros = int(str(raw))
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    return WEBKIT_EPOCH + timedelta(microseconds=micros)


def utc_iso(moment: datetime) -> str:
    """Render an aware datetime as ISO 8601 UTC with ``Z``, second precision (``date_added_utc`` format)."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)
