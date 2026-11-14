"""Track A: Chrome bookmarks → ``bookmarks/<profile>/YYYY/*.md`` mirror (§2.4).

Public entry point (contract for tools and the CLI):

    sync_profiles(settings, *, profiles=None, now=None) -> SyncReport

``chrome`` reads Chrome's files, ``naming`` owns the filename rule and URL
key, ``mirror`` applies the §2.4 update/archive policy through ``store``,
``git_mirror`` (M9, optional) turns the files into back-dated git commits.
``python -m bookmark_insight.sync`` runs one sync by hand (``--git-rebuild`` rebuilds
the git history).
"""

from bookmark_insight.sync.mirror import sync_profiles
from bookmark_insight.sync.naming import normalize_url, build_filename, build_bookmark_path
from bookmark_insight.sync.report import SyncReport
from bookmark_insight.sync.git_mirror import MirrorReport, GitMirrorError, rebuild_history


__all__ = [
    "GitMirrorError",
    "MirrorReport",
    "SyncReport",
    "build_bookmark_path",
    "build_filename",
    "normalize_url",
    "rebuild_history",
    "sync_profiles",
]
