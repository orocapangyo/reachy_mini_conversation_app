"""Mirror Chrome profiles into ``bookmarks/<profile>/YYYY/*.md`` (§2.4, 부록 A 4·14·15·19).

Decisions where §2.4 leaves room, all applied per profile and never across:

- Duplicate URL inside one profile: the node met first in Chrome's traversal
  order (``bookmark_bar`` → ``other`` → ``synced``, depth first) owns guid,
  title and date; the other nodes only contribute tags (union, traversal
  order). This matches the fixture, whose "복사본" node has an *earlier*
  ``date_added`` than the original.
- A guid already in the repo folder is updated in place. A new guid whose
  normalized URL matches an existing file (bookmark deleted and re-added in
  Chrome) reuses that file and keeps the file's guid: the filename embeds the
  guid and renaming would break ``[[links]]``.
- Every file in the repo folder that this run did not match is archived.
  That includes bookmarks moved into an excluded (``private``) folder or out
  of the include list, because they must disappear from insights; the file
  and its body stay. Bookmarks cut off by ``sync_since`` are only kept from
  being *created*; existing files are still updated and never archived
  because the setting changed.
- ``now`` carries the local timezone: its ``tzinfo`` (system local when
  omitted or naive) turns ``date_added`` into the front matter ``date``.
- With ``settings.git_mirror`` on (M9, off by default) the files this run
  created / updated / archived are committed right after they are written,
  still inside the repository lock. A mirror failure is a warning: the
  markdown files are the product, the git log is a convenience.
"""

import logging
from pathlib import Path
from datetime import date, datetime
from dataclasses import field, dataclass

from bookmark_insight.store import (
    nfc,
    repo_lock,
    iter_bookmarks,
    write_bookmark,
    modify_bookmark,
)
from bookmark_insight.models import Bookmark, DateSource
from bookmark_insight.settings import BookmarkInsightSettings
from bookmark_insight.sync.chrome import (
    ChromeProfile,
    ChromeReadError,
    ChromeBookmarkNode,
    utc_iso,
    load_json,
    iter_url_nodes,
    discover_user_data,
    default_user_data_dir,
)
from bookmark_insight.sync.naming import normalize_url, build_bookmark_path
from bookmark_insight.sync.report import SyncReport
from bookmark_insight.sync.git_mirror import SyncChanges, commit_changes


logger = logging.getLogger(__name__)


@dataclass
class _UrlGroup:
    """Chrome nodes of one profile sharing a normalized URL; the first one met owns guid/title/date."""

    primary: ChromeBookmarkNode
    nodes: list[ChromeBookmarkNode] = field(default_factory=list)

    @property
    def guids(self) -> list[str]:
        return [node.guid for node in self.nodes]

    @property
    def tags(self) -> list[str]:
        merged: list[str] = []
        for node in self.nodes:
            for name in node.folders:
                if name not in merged:
                    merged.append(name)
        return merged

    def last_used(self, now: datetime) -> str | None:
        """Local date of the most recent ``date_last_used`` across the group, or None when Chrome has 0."""
        stamps = [node.date_last_used for node in self.nodes if node.date_last_used is not None]
        return max(stamps).astimezone(now.tzinfo).date().isoformat() if stamps else None


def sync_profiles(
    settings: BookmarkInsightSettings,
    *,
    profiles: list[str] | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """Mirror the allowed Chrome profiles into the bookmark repository.

    ``profiles`` overrides the allow list (tests); ``now`` fixes the sync
    timestamp and, through its ``tzinfo``, the local timezone used for ``date``.
    """
    report = SyncReport()
    settings.require_repo()
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.astimezone()

    root = settings.chrome_user_data_dir or default_user_data_dir()
    if not root.is_dir():
        report.warnings.append(f"Chrome user data directory not found: {root}")
        logger.warning(report.warnings[-1])
        return report
    chrome = discover_user_data(root)

    raw_allowed = profiles if profiles is not None else settings.chrome_profiles
    allowed: list[str] = []
    for name in raw_allowed:
        folder = nfc(name.strip())
        if folder and folder not in allowed:
            allowed.append(folder)
    if not allowed:
        if chrome.last_used is None:
            report.warnings.append(f"no profile allow list and no last-used profile in {root}")
            logger.warning(report.warnings[-1])
            return report
        allowed = [chrome.last_used]
    report.skipped_profiles = [folder for folder in chrome.on_disk() if folder not in allowed]

    since: date | None = None
    if settings.sync_since:
        try:
            since = date.fromisoformat(settings.sync_since.strip())
        except ValueError:
            report.warnings.append(f"ignoring invalid sync_since {settings.sync_since!r} (expected YYYY-MM-DD)")
            logger.warning(report.warnings[-1])

    for folder in allowed:
        profile = chrome.profiles.get(folder)
        if profile is None or not profile.bookmarks_paths:
            report.warnings.append(f"profile {folder!r} is allowed but has no bookmark file under {root}")
            logger.warning(report.warnings[-1])
            continue
        try:
            # Bookmarks + AccountBookmarks of one profile are merged (same profile, §2.4);
            # a broken file skips the whole profile so a partial read never archives bookmarks.
            bookmark_docs = [load_json(path) for path in profile.bookmarks_paths]
        except ChromeReadError as exc:
            report.warnings.append(f"profile {folder!r}: could not read bookmark file: {exc}")
            logger.warning(report.warnings[-1])
            continue
        with repo_lock(settings):
            changes = _sync_profile(settings, profile, bookmark_docs, report, now, since)
            if settings.git_mirror:
                _mirror_to_git(settings, folder, changes, report, now)
        report.profiles.append(folder)
        logger.info(
            "Synced Chrome profile %s (%s): created=%d updated=%d archived=%d excluded=%d",
            folder,
            profile.name or "no name",
            report.created[folder],
            report.updated[folder],
            report.archived[folder],
            report.excluded[folder],
        )
    return report


def _mirror_to_git(
    settings: BookmarkInsightSettings, folder: str, changes: SyncChanges, report: SyncReport, now: datetime
) -> None:
    """Commit this run's files (M9). Never raises: the markdown is already written."""
    try:
        mirror = commit_changes(settings, changes, now=now)
    except Exception as exc:
        report.warnings.append(f"profile {folder!r}: git mirror failed: {exc}")
        logger.warning(report.warnings[-1])
        return
    report.mirror_commits[folder] = mirror.total
    for warning in mirror.warnings:
        report.warnings.append(f"profile {folder!r}: git mirror: {warning}")
        logger.warning(report.warnings[-1])


def _sync_profile(
    settings: BookmarkInsightSettings,
    profile: ChromeProfile,
    bookmark_docs: list[dict[str, object]],
    report: SyncReport,
    now: datetime,
    since: date | None,
) -> SyncChanges:
    """Apply the §2.4 policy for one profile; return the files written (for the optional git mirror)."""
    folder = profile.folder
    excluded_names = {nfc(name).casefold() for name in settings.exclude_folders}
    include_keys = tuple(nfc(key).strip("/ ") for key in settings.include_folders if key.strip("/ "))

    excluded = 0
    groups: dict[str, _UrlGroup] = {}
    for node in (node for doc in bookmark_docs for node in iter_url_nodes(doc)):
        if any(name.casefold() in excluded_names for name in node.folders):
            excluded += 1
            continue
        if include_keys and not any(
            node.folder_key == key or node.folder_key.startswith(key + "/") for key in include_keys
        ):
            continue
        group = groups.setdefault(normalize_url(node.url), _UrlGroup(primary=node))
        group.nodes.append(node)

    existing = list(iter_bookmarks(settings, folder, include_archived=True))
    by_guid = {bookmark.guid: bookmark for bookmark in existing}
    by_url: dict[str, Bookmark] = {}
    for bookmark in sorted(existing, key=lambda item: item.archived):  # prefer a live file over an archived one
        by_url.setdefault(normalize_url(bookmark.url), bookmark)

    changes = SyncChanges()
    matched: set[str] = set()
    for key, group in groups.items():
        target = next((by_guid[guid] for guid in group.guids if guid in by_guid), None) or by_url.get(key)
        if target is not None:
            if target.guid in matched:
                continue  # two Chrome URL groups map to one legacy file; the first one wins
            matched.add(target.guid)
            if _update_existing(target, group, profile.name, now):
                changes.updated.append(Path(target.path))
            continue

        primary = group.primary
        date_source: DateSource = "chrome"
        if primary.date_added is None:
            day, date_added_utc, date_source = now.date(), utc_iso(now), "sync"
        else:
            day, date_added_utc = primary.date_added.astimezone(now.tzinfo).date(), utc_iso(primary.date_added)
        if since is not None and day < since:
            continue
        bookmark = Bookmark(
            title=primary.title,
            url=primary.url,
            guid=primary.guid,
            profile=folder,
            date=day,
            tags=group.tags,
            date_added_utc=date_added_utc,
            date_source=date_source,
            profile_name=profile.name,
            last_used=group.last_used(now),
        )
        changes.created.append(
            write_bookmark(bookmark, build_bookmark_path(settings, folder, day, primary.title, primary.guid))
        )

    for bookmark in existing:
        if bookmark.guid not in matched and not bookmark.archived:
            modify_bookmark(Path(bookmark.path), _archive)
            changes.archived.append(Path(bookmark.path))

    report.created[folder] = len(changes.created)
    report.updated[folder] = len(changes.updated)
    report.archived[folder] = len(changes.archived)
    report.excluded[folder] = excluded
    return changes


def _update_existing(target: Bookmark, group: _UrlGroup, profile_name: str | None, now: datetime) -> bool:
    """Refresh the sync-owned front matter fields of an existing file; return whether anything was written."""
    title = group.primary.title
    tags = group.tags
    last_used = group.last_used(now)
    changed = False

    def refresh(bookmark: Bookmark) -> Bookmark | None:
        nonlocal changed
        changed = (
            bookmark.title != title
            or bookmark.tags != tags
            or (last_used is not None and bookmark.last_used != last_used)
            or (profile_name is not None and bookmark.profile_name != profile_name)
            or bookmark.archived
        )
        if not changed:
            return None
        bookmark.title = title
        bookmark.tags = tags
        if last_used is not None:
            bookmark.last_used = last_used
        if profile_name is not None:
            bookmark.profile_name = profile_name
        bookmark.archived = False
        return bookmark

    modify_bookmark(Path(target.path), refresh)
    return changed


def _archive(bookmark: Bookmark) -> Bookmark:
    bookmark.archived = True
    return bookmark
