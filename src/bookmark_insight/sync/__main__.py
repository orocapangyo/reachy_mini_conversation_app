"""Manual sync run.

``python -m bookmark_insight.sync [--discover] [--profile NAME ...] [--repo PATH] [--chrome-dir PATH]
[--git-mirror] [--git-rebuild] [-v]``
"""

import sys
import logging
import argparse
from typing import cast
from pathlib import Path
from dataclasses import replace

from dotenv import find_dotenv, load_dotenv

from bookmark_insight.settings import (
    ENV_BOOKMARK_REPO,
    ENV_CHROME_PROFILES,
    ENV_CHROME_USER_DATA_DIR,
    BookmarkInsightSettings,
    load_settings,
)
from bookmark_insight.sync.chrome import (
    ChromeReadError,
    load_json,
    iter_url_nodes,
    discover_user_data,
    default_user_data_dir,
)
from bookmark_insight.sync.mirror import sync_profiles
from bookmark_insight.sync.git_mirror import GitMirrorError, rebuild_history


def discover_profiles(root: Path) -> dict[str, object]:
    """Collect the Chrome profiles under ``root`` with the bookmark count of each file (no sync, no printing).

    Returns ``{root, last_used, profiles, usable}`` where every profile is
    ``{folder, name, is_last_used, usable, bookmark_files: [{name, count, error}]}``
    and ``usable`` lists the folders holding at least one bookmark file. Raises
    ``FileNotFoundError`` when ``root`` is not a directory; zero profiles is not
    an error (``profiles == []``).
    """
    if not root.is_dir():
        raise FileNotFoundError(f"Chrome user data directory not found: {root}")
    chrome = discover_user_data(root)
    profiles: list[dict[str, object]] = []
    usable: list[str] = []
    for folder, profile in chrome.profiles.items():
        files: list[dict[str, object]] = []
        for path in profile.bookmarks_paths:
            try:
                count: int | None = sum(1 for _ in iter_url_nodes(load_json(path)))
                error: str | None = None
            except ChromeReadError as exc:
                count, error = None, str(exc)
            files.append({"name": path.name, "count": count, "error": error})
        is_usable = bool(profile.bookmarks_paths)
        if is_usable:
            usable.append(folder)
        profiles.append(
            {
                "folder": folder,
                "name": profile.name,
                "is_last_used": folder == chrome.last_used,
                "usable": is_usable,
                "bookmark_files": files,
            }
        )
    return {"root": str(root), "last_used": chrome.last_used, "profiles": profiles, "usable": usable}


def suggested_profile(info: dict[str, object]) -> str | None:
    """Return the profile folder to propose for ``BOOKMARK_INSIGHT_CHROME_PROFILES`` (last used if usable, else the first usable)."""
    usable = cast(list[str], info["usable"])
    if not usable:
        return None
    last_used = info["last_used"]
    return last_used if isinstance(last_used, str) and last_used in usable else usable[0]


def discover(chrome_dir: Path | None) -> int:
    """Print the Chrome profiles found on this machine and the ``.env`` lines to use them (no sync)."""
    root = chrome_dir or default_user_data_dir()
    print(f"Chrome User Data: {root}" + ("" if chrome_dir else " (OS default; override with --chrome-dir)"))
    try:
        info = discover_profiles(root)
    except FileNotFoundError:
        print("  not found. Open chrome://version in Chrome and copy the parent of 'Profile Path'.", file=sys.stderr)
        return 1
    profiles = cast(list[dict[str, object]], info["profiles"])
    if not profiles:
        print("  no profiles found. Open chrome://version in Chrome and check 'Profile Path'.", file=sys.stderr)
        return 1
    print("Profiles (folder name is what BOOKMARK_INSIGHT_CHROME_PROFILES expects):")
    for profile in profiles:
        label = f"  {str(profile['folder']):<12} name={profile['name'] or '-'}"
        files = cast(list[dict[str, object]], profile["bookmark_files"])
        if not files:
            print(f"{label}  (no bookmark file: never bookmarked, or profile not on this machine)")
            continue
        counts = [
            f"{entry['name']}: unreadable ({entry['error']})"
            if entry["error"] is not None
            else f"{entry['name']}: {entry['count']} bookmarks"
            for entry in files
        ]
        suffix = "  <- last used" if profile["is_last_used"] else ""
        print(f"{label}  {', '.join(counts)}{suffix}")
    chosen = suggested_profile(info)
    if chosen is None:
        print("No profile holds a bookmark file yet. Press Cmd+D / Ctrl+D in Chrome once, then run again.")
        return 1
    print("\nAdd these lines to .env (profile list: comma separated, only the ones you want read):")
    if chrome_dir is not None:
        print(f"{ENV_CHROME_USER_DATA_DIR}={root}")
    print(f"{ENV_CHROME_PROFILES}={chosen}")
    print(f"{ENV_BOOKMARK_REPO}=<markdown repository path, e.g. ~/Documents/bookmarks>")
    return 0


def git_rebuild(settings: BookmarkInsightSettings, profiles: list[str] | None) -> int:
    """Rebuild the git history of the bookmark repository (M9) and print the commit count."""
    total = 0
    targets: list[str | None] = list(profiles) if profiles else [None]
    try:
        for profile in targets:
            report = rebuild_history(settings, profile=profile)
            total += report.total
            label = f"{profile}: " if profile else ""
            print(
                f"{label}git mirror: {report.total} commits (bookmarks={report.bookmarks} updates={report.updates} "
                f"archives={report.archives} skipped={report.skipped}"
                f"{', repository initialized' if report.initialized else ''})"
            )
            for warning in report.warnings:
                print(f"warning: {warning}", file=sys.stderr)
    except GitMirrorError as exc:
        print(f"git mirror failed: {exc}", file=sys.stderr)
        return 1
    print(f"total commits: {total}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one sync with settings from ``.env``/environment and print a per-profile summary."""
    parser = argparse.ArgumentParser(
        prog="python -m bookmark_insight.sync", description="Mirror Chrome bookmarks to markdown."
    )
    parser.add_argument(
        "--discover", action="store_true", help="only list Chrome profiles and bookmark files, print .env lines"
    )
    parser.add_argument(
        "--profile", action="append", help="Chrome profile folder to sync (repeatable; overrides the allow list)"
    )
    parser.add_argument("--repo", type=Path, help="bookmark repository (overrides BOOKMARK_INSIGHT_BOOKMARK_REPO)")
    parser.add_argument(
        "--chrome-dir", type=Path, help="Chrome User Data directory (overrides BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR)"
    )
    parser.add_argument(
        "--git-mirror",
        action="store_true",
        help="commit this run's files to git (forces BOOKMARK_INSIGHT_GIT_MIRROR on)",
    )
    parser.add_argument(
        "--git-rebuild",
        action="store_true",
        help="no sync: (re)build the git history from the bookmark files, one back-dated commit per bookmark",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    settings = load_settings()
    if args.repo is not None:
        settings = replace(settings, bookmark_repo=args.repo)
    if args.chrome_dir is not None:
        settings = replace(settings, chrome_user_data_dir=args.chrome_dir)
    if args.discover:
        return discover(settings.chrome_user_data_dir)
    if not settings.configured:
        print("BOOKMARK_INSIGHT_BOOKMARK_REPO is not set (or pass --repo)", file=sys.stderr)
        return 2
    if args.git_rebuild:
        return git_rebuild(settings, args.profile)
    if args.git_mirror:
        settings = replace(settings, git_mirror=True)

    report = sync_profiles(settings, profiles=args.profile)
    for profile in report.profiles:
        line = (
            f"{profile}: created={report.created[profile]} updated={report.updated[profile]} "
            f"archived={report.archived[profile]} excluded={report.excluded[profile]}"
        )
        if profile in report.mirror_commits:
            line += f" git_commits={report.mirror_commits[profile]}"
        print(line)
    for profile in report.skipped_profiles:
        print(f"{profile}: skipped (not in allow list)")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0 if report.profiles else 1


if __name__ == "__main__":
    raise SystemExit(main())
