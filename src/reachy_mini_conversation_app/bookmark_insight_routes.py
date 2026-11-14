"""Bookmark Insight first-time setup over the settings JSON-RPC server (``bookmark_insight.*`` methods, M10).

``BookmarkInsightOps`` is transport independent and calls the ``bookmark_insight`` package
directly (no child process, no shell); ``register_bookmark_insight_methods`` binds it to
the ``/rpc`` server the same way ``tool_space_routes`` and
``personality_routes`` do. Long work runs in ``asyncio.to_thread`` so the
voice loop keeps going; a sync is guarded by a non-blocking in-process lock on
top of the package's ``repo_lock``. ``.env`` writes go through the callback the
console already uses for other settings.
"""

from __future__ import annotations
import os
import sys
import json
import time
import asyncio
import logging
import tempfile
import threading
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Callable, Awaitable

from reachy_mini.io.jsonrpc import JsonRpcError
from reachy_mini.apps.jsonrpc_server import JsonRpcServer
from bookmark_insight.demo import DEMO_PERSONALITY, DEFAULT_FIXTURES_DIR, create_demo_repo
from bookmark_insight.store import repo_lock, list_profiles, iter_bookmarks, atomic_write_text
from bookmark_insight.obsidian import find_vault, config_path, list_vaults, suggest_repo
from bookmark_insight.settings import (
    ENV_BOOKMARK_REPO,
    ENV_CHROME_PROFILES,
    ENV_CHROME_USER_DATA_DIR,
    BookmarkInsightSettings,
    load_settings,
)
from bookmark_insight.sync.chrome import default_user_data_dir
from bookmark_insight.sync.mirror import sync_profiles
from bookmark_insight.sync.__main__ import discover_profiles, suggested_profile


logger = logging.getLogger(__name__)

# --- tuning constants ---------------------------------------------------------
SYNC_LOCK_TIMEOUT_S = 60.0  # wait this long for repo_lock held by another process before bookmark_insight_repo_locked
SYNC_RLOCK_WAIT_S = 2.0  # wait this long for repo_lock held by another thread (voice batch) so the UI answers fast
DEMO_DEFAULT_DIRNAME = "bookmark-insight-demo"  # default demo target under the home directory
LAST_SYNC_FILENAME = "last_sync.json"  # written under <repo>/.bookmark_insight/ after a UI sync
SUGGESTED_REPO_PARTS: tuple[str, ...] = ("Documents", "bookmarks")  # default repo under the home directory
OBSIDIAN_VAULT_LIMIT = 10  # vaults listed in bookmark_insight.status
ALLOWED_ENV_KEYS: tuple[str, ...] = (ENV_CHROME_PROFILES, ENV_BOOKMARK_REPO, ENV_CHROME_USER_DATA_DIR)
PATH_ENV_KEYS: frozenset[str] = frozenset({ENV_BOOKMARK_REPO, ENV_CHROME_USER_DATA_DIR})
BOOKMARKS_DIRNAME = "bookmarks"
INSIGHTS_DIRNAME = "insights"

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, object]]]


class BookmarkInsightRouteError(Exception):
    """Operation failure with a stable ``reason`` the UI branches on and a human ``detail``."""

    def __init__(self, reason: str, detail: str) -> None:
        """Store the stable reason code and the user-facing detail."""
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# --- pure helpers -------------------------------------------------------------


def _clean_line(raw: object) -> str:
    """Return ``raw`` stripped when it is a single-line string, else an empty string."""
    if not isinstance(raw, str):
        return ""
    if "\n" in raw or "\r" in raw:
        return ""
    return raw.strip()


def validate_repo_path(raw: str, *, chrome_root: Path) -> Path:
    """Validate a bookmark repository path typed by the user and return it normalized and absolute.

    Rejects (in this order) empty or multi-line values, relative paths, the
    Chrome user data directory or anything inside it, and reserved locations
    (the home directory itself, a filesystem or drive root). Nothing is created.
    """
    text = _clean_line(raw)
    if not text:
        raise BookmarkInsightRouteError(
            "bookmark_insight_repo_path_invalid", "Enter a repository path (one line, not empty)."
        )
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise BookmarkInsightRouteError("bookmark_insight_repo_path_relative", f"Enter an absolute path: {text}")
    normalized = Path(os.path.normpath(str(path)))
    resolved = normalized.resolve()
    if _is_inside(resolved, chrome_root.expanduser().resolve()):
        raise BookmarkInsightRouteError(
            "bookmark_insight_repo_inside_chrome",
            f"The repository must not be inside the Chrome user data directory ({chrome_root}).",
        )
    home = Path.home().resolve()
    if resolved == home or resolved == Path(resolved.anchor) or normalized == Path(normalized.anchor):
        raise BookmarkInsightRouteError(
            "bookmark_insight_repo_path_reserved",
            f"Choose a folder of its own, not the home directory or a drive root: {normalized}",
        )
    if normalized.exists() and not normalized.is_dir():
        raise BookmarkInsightRouteError("bookmark_insight_repo_path_invalid", f"Not a folder: {normalized}")
    return normalized


def _is_inside(path: Path, root: Path) -> bool:
    """Return whether ``path`` is ``root`` or below it, tolerant of case-insensitive filesystems (APFS, NTFS).

    When ``root`` exists, ``path`` and its existing parents are compared by
    inode (``os.path.samestat``), which sees through case variants and
    symlinks. Otherwise the strings are compared, case-folded on macOS/Windows.
    """
    if path == root or path.is_relative_to(root):
        return True
    try:
        root_stat = root.stat()
    except OSError:
        root_stat = None
    if root_stat is not None:
        for candidate in (path, *path.parents):
            try:
                if os.path.samestat(candidate.stat(), root_stat):
                    return True
            except OSError:
                continue
        return False
    left, right = str(path), str(root)
    if sys.platform == "darwin" or os.name == "nt":
        left, right = os.path.normcase(left).lower(), os.path.normcase(right).lower()
    return left == right or left.startswith(right.rstrip(os.sep) + os.sep)


def _probe_writable(path: Path) -> None:
    """Raise ``OSError`` unless a file can be created and removed inside ``path``."""
    with tempfile.NamedTemporaryFile(dir=path, prefix=".bookmark-insight-write-test-", delete=True):
        pass


def _is_writable(path: Path) -> bool:
    try:
        _probe_writable(path)
    except OSError:
        return False
    return True


def _count_markdown(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*.md") if not path.name.startswith("."))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- operations ---------------------------------------------------------------


class BookmarkInsightOps:
    """Transport-independent Bookmark Insight setup operations (status, discover, sync, repo, env, demo)."""

    def __init__(
        self,
        *,
        persist_env: Callable[[dict[str, str]], None],
        load_settings_fn: Callable[[], BookmarkInsightSettings] = load_settings,
        fixtures_dir: Path | None = None,
    ) -> None:
        """Wire the ``.env`` writer, the settings loader and (for tests) the demo fixtures directory."""
        self._persist_env = persist_env
        self._load_settings = load_settings_fn
        self._fixtures_dir = fixtures_dir if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
        self._sync_guard = threading.Lock()

    # -- shared -------------------------------------------------------------

    @staticmethod
    def _chrome_root(settings: BookmarkInsightSettings) -> Path:
        return settings.chrome_user_data_dir or default_user_data_dir()

    @staticmethod
    def _repo_state(target: Path) -> dict[str, object]:
        exists = target.is_dir()
        vault = find_vault(target)
        return {
            "exists": exists,
            "writable": exists and _is_writable(target),
            "bookmark_count": _count_markdown(target / BOOKMARKS_DIRNAME),
            "insight_count": _count_markdown(target / INSIGHTS_DIRNAME),
            "in_obsidian_vault": vault is not None,
            "vault_path": str(vault) if vault is not None else None,
            "vault_name": vault.name if vault is not None else None,
        }

    @staticmethod
    def _prepare_repo(target: Path) -> bool:
        """Create ``target`` (parents included) and check it is writable; return whether it was created now."""
        created = not target.exists()
        try:
            target.mkdir(parents=True, exist_ok=True)
            _probe_writable(target)
        except OSError as exc:
            raise BookmarkInsightRouteError(
                "bookmark_insight_repo_not_writable", f"Cannot write to {target}: {exc}"
            ) from exc
        return created

    # -- status -------------------------------------------------------------

    @staticmethod
    def _profile_names(settings: BookmarkInsightSettings) -> list[str]:
        names = set(list_profiles(settings))
        insights_root = settings.resolved_insights_dir
        if insights_root.is_dir():
            names.update(
                path.name for path in insights_root.iterdir() if path.is_dir() and not path.name.startswith(".")
            )
        return sorted(names)

    @staticmethod
    def _profile_counts(settings: BookmarkInsightSettings, profile: str) -> dict[str, object]:
        active = archived = 0
        for bookmark in iter_bookmarks(settings, profile, include_archived=True):
            if bookmark.archived:
                archived += 1
            else:
                active += 1
        return {
            "folder": profile,
            "bookmark_count": active,
            "archived_count": archived,
            "insight_note_count": _count_markdown(settings.profile_insights_dir(profile)),
        }

    @staticmethod
    def _read_last_sync(settings: BookmarkInsightSettings) -> dict[str, object] | None:
        path = settings.state_dir / LAST_SYNC_FILENAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable %s: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def status(self) -> dict[str, object]:
        """Return the current Bookmark Insight configuration and repository contents (read-only, never an error)."""
        settings = self._load_settings()
        chrome_root = self._chrome_root(settings)
        profiles: list[dict[str, object]] = []
        last_sync: dict[str, object] | None = None
        if settings.configured:
            try:
                profiles = [self._profile_counts(settings, name) for name in self._profile_names(settings)]
                last_sync = self._read_last_sync(settings)
            except OSError as exc:
                logger.warning("Could not inspect the Bookmark Insight repository %s: %s", settings.bookmark_repo, exc)
        obsidian_config = config_path()
        vaults = list_vaults(obsidian_config)[:OBSIDIAN_VAULT_LIMIT] if obsidian_config is not None else []
        suggested_repo = suggest_repo(vaults, Path.home().joinpath(*SUGGESTED_REPO_PARTS), SUGGESTED_REPO_PARTS[-1])
        return {
            "configured": settings.configured,
            "repo": str(settings.bookmark_repo) if settings.bookmark_repo is not None else None,
            "suggested_repo": str(suggested_repo),
            "obsidian": {
                "installed": obsidian_config is not None,
                "vaults": [{"name": vault.name, "path": str(vault.path), "open": vault.open} for vault in vaults],
                "suggested_repo_reason": "obsidian_vault" if vaults else "default",
            },
            "suggested_demo_target": str(Path.home() / DEMO_DEFAULT_DIRNAME),
            "insights_dir": str(settings.resolved_insights_dir) if settings.configured else None,
            "chrome_user_data_dir": str(chrome_root),
            "chrome_dir_is_default": settings.chrome_user_data_dir is None,
            "chrome_dir_exists": chrome_root.is_dir(),
            "allow_list": list(settings.chrome_profiles),
            "default_profile": settings.default_profile,
            "profiles": profiles,
            "last_sync": last_sync,
            "sync_running": self._sync_guard.locked(),
        }

    # -- discover -----------------------------------------------------------

    def discover(self, chrome_dir: str | None = None) -> dict[str, object]:
        """List the Chrome profiles and their bookmark files, plus the ``.env`` values to use them."""
        settings = self._load_settings()
        root = Path(chrome_dir).expanduser() if chrome_dir else self._chrome_root(settings)
        root_is_default = root == default_user_data_dir()
        try:
            info = discover_profiles(root)
        except FileNotFoundError as exc:
            raise BookmarkInsightRouteError("bookmark_insight_chrome_dir_not_found", str(exc)) from exc
        profiles = info["profiles"]
        if not profiles:
            raise BookmarkInsightRouteError("bookmark_insight_no_profiles", f"No Chrome profiles found in {root}")
        suggested_env: dict[str, str] = {ENV_CHROME_PROFILES: suggested_profile(info) or ""}
        if not root_is_default:
            suggested_env[ENV_CHROME_USER_DATA_DIR] = str(root)
        return {
            "root": str(root),
            "root_is_default": root_is_default,
            "last_used": info["last_used"],
            "profiles": profiles,
            "suggested_env": suggested_env,
        }

    # -- sync ---------------------------------------------------------------

    def sync(self, profiles: list[str] | None = None) -> dict[str, object]:
        """Mirror the allowed Chrome profiles once and return the per-profile counts (``SyncReport`` as JSON).

        Repository and Chrome paths always come from the saved settings
        (``bookmark_insight.repo.set`` / ``bookmark_insight.env.apply`` validated them); a
        per-call override would bypass ``validate_repo_path``.
        """
        settings = self._load_settings()
        if not settings.configured:
            raise BookmarkInsightRouteError("bookmark_insight_not_configured", f"{ENV_BOOKMARK_REPO} is not set")
        if not self._sync_guard.acquire(blocking=False):
            raise BookmarkInsightRouteError(
                "bookmark_insight_sync_running", "A sync is already running; wait for it to finish."
            )
        try:
            started_at = _utc_now_iso()
            clock = time.monotonic()
            try:
                # Outer lock: bounded waits for both another thread (voice batch, SYNC_RLOCK_WAIT_S) and another
                # process (lock file, SYNC_LOCK_TIMEOUT_S); sync_profiles re-enters it per profile.
                with repo_lock(settings, timeout_s=SYNC_LOCK_TIMEOUT_S, process_timeout_s=SYNC_RLOCK_WAIT_S):
                    report = sync_profiles(settings, profiles=profiles)
            except TimeoutError as exc:
                raise BookmarkInsightRouteError("bookmark_insight_repo_locked", str(exc)) from exc
            except Exception as exc:
                logger.exception("Bookmark Insight sync failed")
                raise BookmarkInsightRouteError(
                    "bookmark_insight_sync_failed", str(exc) or type(exc).__name__
                ) from exc
            finished_at = _utc_now_iso()
            duration_ms = int((time.monotonic() - clock) * 1000)
        finally:
            self._sync_guard.release()

        counts = {
            folder: {
                "created": report.created.get(folder, 0),
                "updated": report.updated.get(folder, 0),
                "archived": report.archived.get(folder, 0),
                "excluded": report.excluded.get(folder, 0),
            }
            for folder in report.profiles
        }
        if report.profiles:
            summary: dict[str, object] = {
                "at": finished_at,
                "profiles": list(report.profiles),
                "created": sum(report.created.get(folder, 0) for folder in report.profiles),
                "updated": sum(report.updated.get(folder, 0) for folder in report.profiles),
                "archived": sum(report.archived.get(folder, 0) for folder in report.profiles),
            }
            try:
                atomic_write_text(settings.state_dir / LAST_SYNC_FILENAME, json.dumps(summary, ensure_ascii=False))
            except OSError as exc:
                logger.warning("Could not record the last sync: %s", exc)
        return {
            "profiles": list(report.profiles),
            "skipped_profiles": list(report.skipped_profiles),
            "counts": counts,
            "mirror_commits": dict(
                report.mirror_commits
            ),  # commits per profile; empty unless BOOKMARK_INSIGHT_GIT_MIRROR=1
            "warnings": list(report.warnings),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
        }

    # -- repository ---------------------------------------------------------

    def set_repo(self, path: str, *, create: bool) -> dict[str, object]:
        """Validate a repository path; with ``create`` make it, check it is writable and write it to ``.env``."""
        settings = self._load_settings()
        target = validate_repo_path(path, chrome_root=self._chrome_root(settings))
        created = False
        if create:
            created = self._prepare_repo(target)
            self._persist_env({ENV_BOOKMARK_REPO: str(target)})
        return {"path": str(target), "created": created, **self._repo_state(target), "applied": create}

    # -- .env -----------------------------------------------------------------

    def apply_env(self, values: dict[str, object]) -> dict[str, object]:
        """Write the allowed Bookmark Insight keys to ``.env`` in one call (all or nothing) and return the new status."""
        updates: dict[str, str] = {}
        ignored: list[str] = []
        for key, value in values.items():
            if key not in ALLOWED_ENV_KEYS:
                raise BookmarkInsightRouteError(
                    "bookmark_insight_env_key_not_allowed",
                    f"{key} cannot be set here (allowed: {', '.join(ALLOWED_ENV_KEYS)})",
                )
            if value is None or (isinstance(value, str) and not value.strip()):
                ignored.append(key)
                continue
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                raise BookmarkInsightRouteError(
                    "bookmark_insight_env_value_invalid", f"{key} must be a single-line string"
                )
            text = value.strip()
            if key in PATH_ENV_KEYS:
                text = str(Path(text).expanduser())
            updates[key] = text

        warnings: list[str] = []
        settings = self._load_settings()
        chrome_root = (
            Path(updates[ENV_CHROME_USER_DATA_DIR])
            if ENV_CHROME_USER_DATA_DIR in updates
            else self._chrome_root(settings)
        )
        if ENV_CHROME_USER_DATA_DIR in updates and not chrome_root.is_dir():
            warnings.append(f"Chrome user data directory not found: {chrome_root}")
        if ENV_BOOKMARK_REPO in updates:
            target = validate_repo_path(updates[ENV_BOOKMARK_REPO], chrome_root=chrome_root)
            self._prepare_repo(target)
            updates[ENV_BOOKMARK_REPO] = str(target)
        if updates:
            self._persist_env(updates)
        return {"applied": list(updates), "ignored": ignored, "warnings": warnings, "status": self.status()}

    # -- demo -----------------------------------------------------------------

    def demo_setup(self, target: str) -> dict[str, object]:
        """Copy the demo fixtures into ``target`` and return the ``.env`` values to apply (does not write ``.env``)."""
        text = _clean_line(target)
        if not text:
            raise BookmarkInsightRouteError(
                "bookmark_insight_repo_path_invalid", "Enter a target folder for the demo data."
            )
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise BookmarkInsightRouteError("bookmark_insight_repo_path_relative", f"Enter an absolute path: {text}")
        try:
            demo = create_demo_repo(path, fixtures_dir=self._fixtures_dir)
        except FileExistsError as exc:
            raise BookmarkInsightRouteError("bookmark_insight_demo_target_exists", str(exc)) from exc
        except FileNotFoundError as exc:
            raise BookmarkInsightRouteError("bookmark_insight_fixtures_missing", str(exc)) from exc
        except Exception as exc:
            logger.exception("Bookmark Insight demo setup failed")
            raise BookmarkInsightRouteError("bookmark_insight_demo_failed", str(exc) or type(exc).__name__) from exc
        return {
            "target": str(demo.target),
            "repo": str(demo.repo),
            "chrome_user_data_dir": str(demo.chrome_user_data_dir),
            "env": demo.env_lines(),
            "personality": DEMO_PERSONALITY,
            "note_period": {"since": demo.note_since, "until": demo.note_until},
        }


def build_bookmark_insight_ops(*, persist_env: Callable[[dict[str, str]], None]) -> BookmarkInsightOps:
    """Build the operations object the console registers (``.env`` writes go through ``persist_env``)."""
    return BookmarkInsightOps(persist_env=persist_env)


# --- JSON-RPC binding ---------------------------------------------------------


def _optional_str(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise JsonRpcError(f"{key} must be a string", reason="bookmark_insight_params_invalid", data={"detail": key})
    return value or None


def _optional_profiles(params: dict[str, Any]) -> list[str] | None:
    value = params.get("profiles")
    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise JsonRpcError(
            "profiles must be a list of strings", reason="bookmark_insight_params_invalid", data={"detail": "profiles"}
        )
    names = [item.strip() for item in value if item.strip()]
    return names or None


async def _run(
    fallback_reason: str, fn: Callable[..., dict[str, object]], *args: Any, **kwargs: Any
) -> dict[str, object]:
    """Run an ops method off the event loop and map failures to ``JsonRpcError``."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except BookmarkInsightRouteError as exc:
        raise JsonRpcError(exc.detail, reason=exc.reason, data={"detail": exc.detail}) from exc
    except JsonRpcError:
        raise
    except Exception as exc:
        logger.exception("Bookmark Insight operation %s failed", getattr(fn, "__name__", fn))
        detail = str(exc).strip() or type(exc).__name__
        raise JsonRpcError(detail, reason=fallback_reason, data={"detail": detail}) from exc


def register_bookmark_insight_methods(rpc: JsonRpcServer, ops: BookmarkInsightOps) -> None:
    """Register the ``bookmark_insight.*`` methods on the settings JSON-RPC server."""

    async def _status(_params: dict[str, Any]) -> dict[str, object]:
        return await _run("bookmark_insight_status_failed", ops.status)

    async def _discover(params: dict[str, Any]) -> dict[str, object]:
        return await _run("bookmark_insight_discover_failed", ops.discover, _optional_str(params, "chrome_dir"))

    async def _sync(params: dict[str, Any]) -> dict[str, object]:
        return await _run("bookmark_insight_sync_failed", ops.sync, profiles=_optional_profiles(params))

    async def _repo_set(params: dict[str, Any]) -> dict[str, object]:
        path = params.get("path")
        if not isinstance(path, str):
            raise JsonRpcError(
                "path must be a string", reason="bookmark_insight_repo_path_invalid", data={"detail": "path"}
            )
        return await _run(
            "bookmark_insight_repo_not_writable", ops.set_repo, path, create=bool(params.get("create", False))
        )

    async def _env_apply(params: dict[str, Any]) -> dict[str, object]:
        return await _run("bookmark_insight_env_value_invalid", ops.apply_env, dict(params))

    async def _demo_setup(params: dict[str, Any]) -> dict[str, object]:
        target = params.get("target")
        if not isinstance(target, str):
            raise JsonRpcError(
                "target must be a string", reason="bookmark_insight_repo_path_invalid", data={"detail": "target"}
            )
        return await _run("bookmark_insight_demo_failed", ops.demo_setup, target)

    handlers: dict[str, Handler] = {
        "bookmark_insight.status": _status,
        "bookmark_insight.discover": _discover,
        "bookmark_insight.sync": _sync,
        "bookmark_insight.repo.set": _repo_set,
        "bookmark_insight.env.apply": _env_apply,
        "bookmark_insight.demo_setup": _demo_setup,
    }
    for name, handler in handlers.items():
        rpc.register(name, handler)
