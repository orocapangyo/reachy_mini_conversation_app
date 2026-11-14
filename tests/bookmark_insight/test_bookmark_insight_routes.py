"""Tests for the ``bookmark_insight.*`` settings-UI methods (M10 setup dashboard)."""

import os
import sys
import json
import time
import inspect
import threading
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_mini.apps.jsonrpc_server import JsonRpcServer
import reachy_mini_conversation_app.bookmark_insight_routes as routes_mod
from bookmark_insight import demo as demo_mod
from bookmark_insight.store import LOCK_FILENAME, repo_lock
from bookmark_insight.settings import (
    ENV_BOOKMARK_REPO,
    ENV_CHROME_PROFILES,
    ENV_OBSIDIAN_CONFIG,
    ENV_CHROME_USER_DATA_DIR,
    BookmarkInsightSettings,
    load_settings,
)
from bookmark_insight.sync.__main__ import discover_profiles
from reachy_mini_conversation_app.console import LocalStream
from reachy_mini_conversation_app.bookmark_insight_routes import (
    LAST_SYNC_FILENAME,
    BookmarkInsightOps,
    BookmarkInsightRouteError,
    validate_repo_path,
    register_bookmark_insight_methods,
)


METHODS = (
    "bookmark_insight.status",
    "bookmark_insight.discover",
    "bookmark_insight.sync",
    "bookmark_insight.repo.set",
    "bookmark_insight.env.apply",
    "bookmark_insight.demo_setup",
)


class FakeEnvWriter:
    """Stand-in for ``LocalStream._persist_env_values``: records calls and updates ``os.environ`` like the real one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record calls and push values into the environment through ``monkeypatch``."""
        self._monkeypatch = monkeypatch
        self.calls: list[dict[str, str]] = []
        self.values: dict[str, str] = {}

    def __call__(self, updates: dict[str, str]) -> None:
        """Persist non-empty values (same contract as the console writer)."""
        self.calls.append(dict(updates))
        for key, value in updates.items():
            if value:
                self.values[key] = value
                self._monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def clean_bookmark_insight_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start every test without Bookmark Insight keys in the environment and without the developer's real Obsidian config."""
    for key in list(os.environ):
        if key.startswith("BOOKMARK_INSIGHT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(ENV_OBSIDIAN_CONFIG, str(tmp_path / "no-obsidian.json"))


def _obsidian_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake vault and point BOOKMARK_INSIGHT_OBSIDIAN_CONFIG at a config that lists it."""
    vault = tmp_path / "notes"
    (vault / ".obsidian").mkdir(parents=True)
    config = tmp_path / "obsidian.json"
    config.write_text(json.dumps({"vaults": {"abc": {"path": str(vault), "ts": 1, "open": True}}}), encoding="utf-8")
    monkeypatch.setenv(ENV_OBSIDIAN_CONFIG, str(config))
    return vault


@pytest.fixture
def env_writer(monkeypatch: pytest.MonkeyPatch) -> FakeEnvWriter:
    """Fake ``.env`` writer wired to ``monkeypatch.setenv``."""
    return FakeEnvWriter(monkeypatch)


def _mount(ops: BookmarkInsightOps) -> TestClient:
    app = FastAPI()
    rpc = JsonRpcServer()
    register_bookmark_insight_methods(rpc, ops)
    rpc.mount(app)
    return TestClient(app)


def _rpc_call(client: TestClient, method: str, params: dict[str, object] | None = None) -> dict[str, Any]:
    with client.websocket_connect("/rpc") as websocket:
        websocket.send_json({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}})
        response: dict[str, Any] = websocket.receive_json()
        return response


def _result(client: TestClient, method: str, params: dict[str, object] | None = None) -> dict[str, Any]:
    response = _rpc_call(client, method, params)
    assert "error" not in response, response
    result: dict[str, Any] = response["result"]
    return result


def _reason(client: TestClient, method: str, params: dict[str, object] | None = None) -> str:
    response = _rpc_call(client, method, params)
    assert "error" in response, response
    reason: str = response["error"]["data"]["reason"]
    return reason


def _ops_for(settings: BookmarkInsightSettings, env_writer: FakeEnvWriter) -> BookmarkInsightOps:
    return BookmarkInsightOps(persist_env=env_writer, load_settings_fn=lambda: settings)


# --- registration / hygiene -----------------------------------------------------


def test_registers_all_six_methods(env_writer: FakeEnvWriter) -> None:
    """Every method of the route table is registered on the server."""
    rpc = JsonRpcServer()
    register_bookmark_insight_methods(rpc, BookmarkInsightOps(persist_env=env_writer))
    assert set(METHODS) <= set(rpc._methods)


def test_console_registers_bookmark_insight_methods() -> None:
    """``_init_settings_ui_if_needed`` registers bookmark_insight.* next to the other route families."""
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()
    assert stream._rpc is not None
    assert set(METHODS) <= set(stream._rpc._methods)


def test_no_shell_in_setup_modules() -> None:
    """R4: the setup path never builds a command string (no subprocess / os.system / shlex)."""
    for module in (routes_mod, demo_mod):
        source = inspect.getsource(module)
        assert "subprocess" not in source
        assert "os.system" not in source
        assert "shlex" not in source


# --- status ------------------------------------------------------------------------


def test_status_unconfigured_is_not_an_error(env_writer: FakeEnvWriter) -> None:
    """Without BOOKMARK_INSIGHT_BOOKMARK_REPO the status says so and lists nothing."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    status = _result(client, "bookmark_insight.status")
    assert status["configured"] is False
    assert status["repo"] is None and status["insights_dir"] is None
    assert status["profiles"] == [] and status["last_sync"] is None
    assert status["sync_running"] is False
    assert status["chrome_dir_is_default"] is True
    assert status["suggested_repo"] == str(Path.home() / "Documents" / "bookmarks")
    assert status["suggested_demo_target"] == str(Path.home() / routes_mod.DEMO_DEFAULT_DIRNAME)
    assert status["obsidian"] == {"installed": False, "vaults": [], "suggested_repo_reason": "default"}


def test_status_suggests_a_folder_inside_the_obsidian_vault(
    tmp_path: Path, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Obsidian installed the suggested repo moves into the first vault so ``[[wikilinks]]`` resolve."""
    vault = _obsidian_vault(tmp_path, monkeypatch)
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    status = _result(client, "bookmark_insight.status")
    assert status["suggested_repo"] == str(vault / "bookmarks")
    assert status["obsidian"] == {
        "installed": True,
        "vaults": [{"name": "notes", "path": str(vault), "open": True}],
        "suggested_repo_reason": "obsidian_vault",
    }


def test_status_counts_sample_repo(settings: BookmarkInsightSettings, repo: Path, env_writer: FakeEnvWriter) -> None:
    """The sample repository shows one profile with 3 bookmarks and 1 insight note."""
    client = _mount(_ops_for(settings, env_writer))
    status = _result(client, "bookmark_insight.status")
    assert status["configured"] is True
    assert status["repo"] == str(repo)
    assert status["insights_dir"] == str(repo / "insights")
    assert status["allow_list"] == ["Default"] and status["default_profile"] == "Default"
    assert status["chrome_dir_is_default"] is False and status["chrome_dir_exists"] is True
    assert status["profiles"] == [
        {"folder": "Default", "bookmark_count": 3, "archived_count": 0, "insight_note_count": 1}
    ]
    assert status["last_sync"] is None


# --- discover ----------------------------------------------------------------------


def test_discover_profiles_dict(chrome_user_data: Path) -> None:
    """``discover_profiles`` (shared with the CLI) returns usable folders and per-file counts."""
    info = discover_profiles(chrome_user_data)
    assert info["root"] == str(chrome_user_data)
    assert info["last_used"] == "Default"
    assert info["usable"] == ["Default", "Profile 1"]
    with pytest.raises(FileNotFoundError):
        discover_profiles(chrome_user_data / "missing")


def test_discover_lists_profiles_with_counts(chrome_user_data: Path, env_writer: FakeEnvWriter) -> None:
    """Discovery lists every profile, marks the last used one and proposes the .env values."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    result = _result(client, "bookmark_insight.discover", {"chrome_dir": str(chrome_user_data)})
    assert result["root"] == str(chrome_user_data) and result["root_is_default"] is False
    assert result["last_used"] == "Default"
    by_folder = {profile["folder"]: profile for profile in result["profiles"]}
    assert set(by_folder) == {"Default", "Profile 1", "Profile 2"}
    default = by_folder["Default"]
    assert default["name"] == "개인" and default["is_last_used"] is True and default["usable"] is True
    assert default["bookmark_files"] == [{"name": "Bookmarks", "count": 7, "error": None}]
    assert by_folder["Profile 1"]["bookmark_files"] == [{"name": "Bookmarks", "count": 2, "error": None}]
    assert by_folder["Profile 2"]["usable"] is False and by_folder["Profile 2"]["bookmark_files"] == []
    assert result["suggested_env"] == {
        ENV_CHROME_PROFILES: "Default",
        ENV_CHROME_USER_DATA_DIR: str(chrome_user_data),
    }


def test_discover_uses_settings_dir_when_param_omitted(
    settings: BookmarkInsightSettings, chrome_user_data: Path, env_writer: FakeEnvWriter
) -> None:
    """Without ``chrome_dir`` the configured directory is scanned."""
    client = _mount(_ops_for(settings, env_writer))
    result = _result(client, "bookmark_insight.discover")
    assert result["root"] == str(chrome_user_data)


def test_discover_errors(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """A missing directory and a directory without profiles map to their reason codes."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    assert (
        _reason(client, "bookmark_insight.discover", {"chrome_dir": str(tmp_path / "nope")})
        == "bookmark_insight_chrome_dir_not_found"
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _reason(client, "bookmark_insight.discover", {"chrome_dir": str(empty)}) == "bookmark_insight_no_profiles"
    assert _reason(client, "bookmark_insight.discover", {"chrome_dir": 3}) == "bookmark_insight_params_invalid"


# --- sync --------------------------------------------------------------------------


def test_sync_reports_counts_and_records_last_sync(
    settings: BookmarkInsightSettings, repo: Path, env_writer: FakeEnvWriter
) -> None:
    """A sync returns the SyncReport as JSON, is idempotent and leaves last_sync.json for the status card."""
    client = _mount(_ops_for(settings, env_writer))
    first = _result(client, "bookmark_insight.sync")
    assert first["profiles"] == ["Default"] and first["skipped_profiles"] == ["Profile 1"]
    assert set(first["counts"]["Default"]) == {"created", "updated", "archived", "excluded"}
    assert first["counts"]["Default"]["created"] >= 0 and first["warnings"] == []
    assert first["mirror_commits"] == {}  # mirror is off by default (M9)
    assert isinstance(first["duration_ms"], int)
    assert first["started_at"].endswith("Z") and first["finished_at"].endswith("Z")

    second = _result(client, "bookmark_insight.sync")
    assert second["counts"]["Default"]["created"] == 0

    recorded = json.loads((repo / ".bookmark_insight" / LAST_SYNC_FILENAME).read_text(encoding="utf-8"))
    assert recorded["profiles"] == ["Default"] and recorded["at"] == second["finished_at"]
    status = _result(client, "bookmark_insight.status")
    assert status["last_sync"]["profiles"] == ["Default"]
    assert status["last_sync"]["at"].endswith("Z")
    assert status["profiles"][0]["bookmark_count"] >= 1


def test_sync_profiles_param_overrides_allow_list(
    settings: BookmarkInsightSettings, env_writer: FakeEnvWriter
) -> None:
    """``profiles`` behaves like the CLI ``--profile`` (accepts a list or a comma string)."""
    client = _mount(_ops_for(settings, env_writer))
    result = _result(client, "bookmark_insight.sync", {"profiles": "Profile 1"})
    assert result["profiles"] == ["Profile 1"] and result["skipped_profiles"] == ["Default"]
    assert _reason(client, "bookmark_insight.sync", {"profiles": [1]}) == "bookmark_insight_params_invalid"


def test_sync_not_configured(env_writer: FakeEnvWriter) -> None:
    """Without a repository the sync is refused."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    assert _reason(client, "bookmark_insight.sync") == "bookmark_insight_not_configured"


def test_sync_running_guard(settings: BookmarkInsightSettings, env_writer: FakeEnvWriter) -> None:
    """A second sync while one is running fails immediately with bookmark_insight_sync_running."""
    ops = _ops_for(settings, env_writer)
    client = _mount(ops)
    assert ops._sync_guard.acquire(blocking=False)
    try:
        started = time.monotonic()
        assert _reason(client, "bookmark_insight.sync") == "bookmark_insight_sync_running"
        assert time.monotonic() - started < 1.0
        assert _result(client, "bookmark_insight.status")["sync_running"] is True
    finally:
        ops._sync_guard.release()


def test_sync_repo_locked(
    settings: BookmarkInsightSettings, repo: Path, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock file held by another process turns into bookmark_insight_repo_locked after SYNC_LOCK_TIMEOUT_S."""
    monkeypatch.setattr(routes_mod, "SYNC_LOCK_TIMEOUT_S", 0.2)
    lock_path = repo / ".bookmark_insight" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999 now\n", encoding="utf-8")
    client = _mount(_ops_for(settings, env_writer))
    assert _reason(client, "bookmark_insight.sync") == "bookmark_insight_repo_locked"
    assert lock_path.exists()


def test_sync_repo_held_by_another_thread(
    settings: BookmarkInsightSettings, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The voice batch holding repo_lock in another thread yields bookmark_insight_repo_locked quickly, not a hang."""
    monkeypatch.setattr(routes_mod, "SYNC_RLOCK_WAIT_S", 0.2)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with repo_lock(settings):
            held.set()
            release.wait(5.0)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5.0)
    try:
        client = _mount(_ops_for(settings, env_writer))
        started = time.monotonic()
        assert _reason(client, "bookmark_insight.sync") == "bookmark_insight_repo_locked"
        assert time.monotonic() - started < 1.5
        assert _result(client, "bookmark_insight.status")["sync_running"] is False
    finally:
        release.set()
        holder.join(5.0)


def test_sync_ignores_path_overrides(
    settings: BookmarkInsightSettings, chrome_user_data: Path, env_writer: FakeEnvWriter
) -> None:
    """``repo`` / ``chrome_dir`` params are not accepted: paths always come from validated settings."""
    client = _mount(_ops_for(settings, env_writer))
    result = _result(client, "bookmark_insight.sync", {"repo": str(chrome_user_data / "Default")})
    assert result["profiles"] == ["Default"]
    assert not (chrome_user_data / "Default" / ".bookmark_insight").exists()


# --- repo.set ----------------------------------------------------------------------


def test_repo_set_creates_and_is_idempotent(tmp_path: Path, chrome_user_data: Path, env_writer: FakeEnvWriter) -> None:
    """Creating a nested repo folder writes BOOKMARK_INSIGHT_BOOKMARK_REPO once; a second call changes nothing."""
    ops = BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings)
    client = _mount(ops)
    target = tmp_path / "vault" / "bookmarks"
    first = _result(client, "bookmark_insight.repo.set", {"path": str(target), "create": True})
    assert target.is_dir()
    assert first == {
        "path": str(target),
        "created": True,
        "exists": True,
        "writable": True,
        "bookmark_count": 0,
        "insight_count": 0,
        "in_obsidian_vault": False,
        "vault_path": None,
        "vault_name": None,
        "applied": True,
    }
    assert env_writer.calls == [{ENV_BOOKMARK_REPO: str(target)}]

    second = _result(client, "bookmark_insight.repo.set", {"path": str(target), "create": True})
    assert second["created"] is False and second["exists"] is True and second["applied"] is True
    assert env_writer.values == {ENV_BOOKMARK_REPO: str(target)}
    assert _result(client, "bookmark_insight.status")["repo"] == str(target)


def test_repo_set_preview_does_not_create(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """``create=false`` only reports the state (for the input preview line)."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    target = tmp_path / "later"
    result = _result(client, "bookmark_insight.repo.set", {"path": str(target), "create": False})
    assert result["exists"] is False and result["writable"] is False and result["applied"] is False
    assert result["created"] is False
    assert not target.exists() and env_writer.calls == []


def test_repo_set_reports_obsidian_vault(
    tmp_path: Path, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview says whether the path sits inside a vault (a folder with ``.obsidian/``), existing or not."""
    vault = _obsidian_vault(tmp_path, monkeypatch)
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    inside = _result(client, "bookmark_insight.repo.set", {"path": str(vault / "sub" / "bookmarks"), "create": False})
    assert inside["in_obsidian_vault"] is True
    assert inside["vault_path"] == str(vault) and inside["vault_name"] == "notes"
    outside = _result(client, "bookmark_insight.repo.set", {"path": str(tmp_path / "bookmarks"), "create": False})
    assert outside["in_obsidian_vault"] is False
    assert outside["vault_path"] is None and outside["vault_name"] is None
    assert env_writer.calls == []


def test_repo_set_preview_counts_sample_repo(repo: Path, env_writer: FakeEnvWriter) -> None:
    """The preview counts the markdown already in bookmarks/ and insights/."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    result = _result(client, "bookmark_insight.repo.set", {"path": str(repo)})
    assert result["exists"] is True and result["writable"] is True
    assert result["bookmark_count"] == 3 and result["insight_count"] == 1


def test_repo_set_rejections(
    tmp_path: Path, chrome_user_data: Path, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative, inside-Chrome, reserved and malformed paths are refused before anything is created."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert Path.home() == home
    chrome_settings = BookmarkInsightSettings(bookmark_repo=None, chrome_user_data_dir=chrome_user_data)
    chrome_entries_before = sorted(path.name for path in (chrome_user_data / "Default").iterdir())
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=lambda: chrome_settings))
    cases = {
        "bookmarks": "bookmark_insight_repo_path_relative",
        str(chrome_user_data / "Default"): "bookmark_insight_repo_inside_chrome",
        str(chrome_user_data): "bookmark_insight_repo_inside_chrome",
        str(home): "bookmark_insight_repo_path_reserved",
        str(Path(tmp_path.anchor)): "bookmark_insight_repo_path_reserved",
        f"{tmp_path / 'a'}\n{tmp_path / 'b'}": "bookmark_insight_repo_path_invalid",
        "   ": "bookmark_insight_repo_path_invalid",
    }
    for raw, expected in cases.items():
        assert _reason(client, "bookmark_insight.repo.set", {"path": raw, "create": True}) == expected, raw
    assert _reason(client, "bookmark_insight.repo.set", {"create": True}) == "bookmark_insight_repo_path_invalid"
    assert env_writer.calls == []
    assert sorted(path.name for path in (chrome_user_data / "Default").iterdir()) == chrome_entries_before
    assert not (tmp_path / "a").exists() and not (tmp_path / "bookmarks").exists()


def test_repo_set_rejects_existing_file(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """A path that is an existing file is refused as invalid (not reported as "not writable" or "missing")."""
    file_path = tmp_path / "notes.md"
    file_path.write_text("x", encoding="utf-8")
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    assert (
        _reason(client, "bookmark_insight.repo.set", {"path": str(file_path), "create": False})
        == "bookmark_insight_repo_path_invalid"
    )
    assert (
        _reason(client, "bookmark_insight.repo.set", {"path": str(file_path), "create": True})
        == "bookmark_insight_repo_path_invalid"
    )
    assert env_writer.calls == []


@pytest.mark.skipif(sys.platform != "darwin", reason="case-insensitive default filesystem (APFS)")
def test_repo_path_inside_chrome_case_insensitive(tmp_path: Path, chrome_user_data: Path) -> None:
    """A case variant of the Chrome directory is still "inside Chrome" on a case-insensitive filesystem."""
    variant = chrome_user_data.parent / chrome_user_data.name.swapcase() / "Default"
    with pytest.raises(BookmarkInsightRouteError) as excinfo:
        validate_repo_path(str(variant), chrome_root=chrome_user_data)
    assert excinfo.value.reason == "bookmark_insight_repo_inside_chrome"
    # Fallback (Chrome dir does not exist yet): string comparison is case-folded too.
    missing_root = tmp_path / "Chrome"
    with pytest.raises(BookmarkInsightRouteError) as excinfo:
        validate_repo_path(str(tmp_path / "chrome" / "Default"), chrome_root=missing_root)
    assert excinfo.value.reason == "bookmark_insight_repo_inside_chrome"
    assert validate_repo_path(str(tmp_path / "chrome-vault"), chrome_root=missing_root) == tmp_path / "chrome-vault"


def test_validate_repo_path_normalizes(tmp_path: Path, chrome_user_data: Path) -> None:
    """The returned path is absolute and normalized (``..`` collapsed)."""
    raw = str(tmp_path / "vault" / ".." / "repo")
    assert validate_repo_path(raw, chrome_root=chrome_user_data) == tmp_path / "repo"
    with pytest.raises(BookmarkInsightRouteError) as excinfo:
        validate_repo_path("relative/path", chrome_root=chrome_user_data)
    assert excinfo.value.reason == "bookmark_insight_repo_path_relative"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permission bits (and not root)")
def test_repo_set_not_writable(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """A folder that cannot be created maps to bookmark_insight_repo_not_writable."""
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
        reason = _reason(client, "bookmark_insight.repo.set", {"path": str(read_only / "vault"), "create": True})
        assert reason == "bookmark_insight_repo_not_writable"
        assert env_writer.calls == []
        preview = _result(client, "bookmark_insight.repo.set", {"path": str(read_only), "create": False})
        assert preview["exists"] is True and preview["writable"] is False
    finally:
        read_only.chmod(0o700)


# --- env.apply -------------------------------------------------------------------


def test_apply_env_writes_allowed_keys_once(tmp_path: Path, chrome_user_data: Path, env_writer: FakeEnvWriter) -> None:
    """All three keys go through a single persist call, the repo is created and the new status is returned."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    target = tmp_path / "vault"
    result = _result(
        client,
        "bookmark_insight.env.apply",
        {
            ENV_CHROME_PROFILES: " Default ",
            ENV_BOOKMARK_REPO: str(target),
            ENV_CHROME_USER_DATA_DIR: str(chrome_user_data),
        },
    )
    assert sorted(result["applied"]) == sorted([ENV_CHROME_PROFILES, ENV_BOOKMARK_REPO, ENV_CHROME_USER_DATA_DIR])
    assert result["ignored"] == [] and result["warnings"] == []
    assert target.is_dir()
    assert env_writer.calls == [
        {
            ENV_CHROME_PROFILES: "Default",
            ENV_BOOKMARK_REPO: str(target),
            ENV_CHROME_USER_DATA_DIR: str(chrome_user_data),
        }
    ]
    status = result["status"]
    assert status["configured"] is True and status["repo"] == str(target)
    assert status["allow_list"] == ["Default"] and status["chrome_dir_is_default"] is False
    assert _result(client, "bookmark_insight.status")["configured"] is True


def test_apply_env_ignores_empty_and_rejects_bad_input(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """Empty values are skipped (never deleted); foreign keys and multi-line values are refused."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    result = _result(client, "bookmark_insight.env.apply", {ENV_CHROME_PROFILES: "Default", ENV_BOOKMARK_REPO: ""})
    assert result["applied"] == [ENV_CHROME_PROFILES] and result["ignored"] == [ENV_BOOKMARK_REPO]
    assert env_writer.calls == [{ENV_CHROME_PROFILES: "Default"}]

    assert (
        _reason(client, "bookmark_insight.env.apply", {"OPENAI_API_KEY": "sk-x"})
        == "bookmark_insight_env_key_not_allowed"
    )
    assert (
        _reason(client, "bookmark_insight.env.apply", {ENV_CHROME_PROFILES: "a\nb"})
        == "bookmark_insight_env_value_invalid"
    )
    assert (
        _reason(client, "bookmark_insight.env.apply", {ENV_CHROME_PROFILES: 7}) == "bookmark_insight_env_value_invalid"
    )
    assert len(env_writer.calls) == 1

    nothing = _result(client, "bookmark_insight.env.apply", {ENV_BOOKMARK_REPO: None})
    assert nothing["applied"] == [] and nothing["ignored"] == [ENV_BOOKMARK_REPO]
    assert len(env_writer.calls) == 1


def test_apply_env_expands_home(tmp_path: Path, env_writer: FakeEnvWriter, monkeypatch: pytest.MonkeyPatch) -> None:
    """``~`` is expanded with ``Path.home()`` before validation and persisting (OS independent)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    result = _result(client, "bookmark_insight.env.apply", {ENV_BOOKMARK_REPO: "~/vault"})
    assert result["applied"] == [ENV_BOOKMARK_REPO]
    assert env_writer.values == {ENV_BOOKMARK_REPO: str(Path.home() / "vault")}
    assert (home / "vault").is_dir()


def test_apply_env_is_all_or_nothing(chrome_user_data: Path, tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """A repo inside the Chrome directory (as given in the same call) is refused and no key is written."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings))
    reason = _reason(
        client,
        "bookmark_insight.env.apply",
        {
            ENV_CHROME_PROFILES: "Default",
            ENV_CHROME_USER_DATA_DIR: str(chrome_user_data),
            ENV_BOOKMARK_REPO: str(chrome_user_data / "Default"),
        },
    )
    assert reason == "bookmark_insight_repo_inside_chrome"
    assert env_writer.calls == []
    assert (
        _reason(client, "bookmark_insight.env.apply", {ENV_BOOKMARK_REPO: "relative"})
        == "bookmark_insight_repo_path_relative"
    )
    missing_chrome = _result(
        client, "bookmark_insight.env.apply", {ENV_CHROME_USER_DATA_DIR: str(tmp_path / "no-chrome")}
    )
    assert missing_chrome["warnings"] and "not found" in missing_chrome["warnings"][0]


# --- demo_setup ------------------------------------------------------------------


def test_demo_setup_success_then_target_exists(tmp_path: Path, fixtures_dir: Path, env_writer: FakeEnvWriter) -> None:
    """The demo button copies the fixtures and returns the env values; a second run refuses to overwrite."""
    client = _mount(
        BookmarkInsightOps(persist_env=env_writer, load_settings_fn=load_settings, fixtures_dir=fixtures_dir)
    )
    target = tmp_path / "demo"
    result = _result(client, "bookmark_insight.demo_setup", {"target": str(target)})
    assert result["target"] == str(target)
    assert result["repo"] == str(target / "repo")
    assert result["chrome_user_data_dir"] == str(target / "User Data")
    assert result["env"] == {
        ENV_BOOKMARK_REPO: str(target / "repo"),
        ENV_CHROME_USER_DATA_DIR: str(target / "User Data"),
        ENV_CHROME_PROFILES: "Default",
    }
    assert result["personality"] == "deskmate_ko"
    assert result["note_period"] == {"since": "2026-10-31", "until": "2026-11-06"}
    assert len(list((target / "repo" / "bookmarks" / "Default").rglob("*.md"))) == 3
    assert env_writer.calls == []  # .env is applied by the UI through bookmark_insight.env.apply

    assert (
        _reason(client, "bookmark_insight.demo_setup", {"target": str(target)})
        == "bookmark_insight_demo_target_exists"
    )
    assert (
        _reason(client, "bookmark_insight.demo_setup", {"target": "relative"}) == "bookmark_insight_repo_path_relative"
    )
    assert _reason(client, "bookmark_insight.demo_setup", {"target": ""}) == "bookmark_insight_repo_path_invalid"


def test_demo_setup_without_fixtures(tmp_path: Path, env_writer: FakeEnvWriter) -> None:
    """Outside a checkout the fixtures are missing and the button says so."""
    client = _mount(BookmarkInsightOps(persist_env=env_writer, fixtures_dir=tmp_path / "no-fixtures"))
    assert (
        _reason(client, "bookmark_insight.demo_setup", {"target": str(tmp_path / "demo")})
        == "bookmark_insight_fixtures_missing"
    )
    assert not (tmp_path / "demo").exists()
