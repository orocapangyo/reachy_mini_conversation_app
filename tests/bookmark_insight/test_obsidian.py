"""Obsidian vault discovery used by the setup dashboard."""

import json
from pathlib import Path

import pytest

from bookmark_insight.obsidian import Vault, find_vault, config_path, list_vaults, suggest_repo
from bookmark_insight.settings import ENV_OBSIDIAN_CONFIG


def _write_config(path: Path, vaults: dict[str, object]) -> Path:
    path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")
    return path


def _make_vault(root: Path) -> Path:
    (root / ".obsidian").mkdir(parents=True)
    return root


def test_find_vault_walks_up_through_missing_folders(tmp_path: Path) -> None:
    """A repo path below a vault resolves to the vault even when the repo does not exist yet."""
    vault = _make_vault(tmp_path / "notes")
    assert find_vault(vault) == vault
    assert find_vault(vault / "bookmarks") == vault
    assert find_vault(vault / "deep" / "er" / "bookmarks") == vault
    assert find_vault(tmp_path / "elsewhere" / "bookmarks") is None
    nested = _make_vault(vault / "inner")
    assert find_vault(nested / "bookmarks") == nested


def test_config_path_per_os_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The OS default locations are used unless BOOKMARK_INSIGHT_OBSIDIAN_CONFIG points elsewhere; missing files give None."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(ENV_OBSIDIAN_CONFIG, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    expected = {
        "darwin": home / "Library" / "Application Support" / "obsidian" / "obsidian.json",
        "win32": tmp_path / "appdata" / "obsidian" / "obsidian.json",
        "linux": tmp_path / "xdg" / "obsidian" / "obsidian.json",
    }
    for platform, path in expected.items():
        assert config_path(platform) is None, platform
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        assert config_path(platform) == path, platform

    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config_path("linux") is None
    override = tmp_path / "custom.json"
    monkeypatch.setenv(ENV_OBSIDIAN_CONFIG, str(override))
    assert config_path() is None
    override.write_text("{}", encoding="utf-8")
    assert config_path() == override


def test_list_vaults_orders_open_first_then_newest_and_skips_missing(tmp_path: Path) -> None:
    """Only existing vault folders are listed: open ones first, then by ``ts`` descending; junk entries are ignored."""
    old = _make_vault(tmp_path / "old")
    newer = _make_vault(tmp_path / "newer")
    opened = _make_vault(tmp_path / "opened")
    config = _write_config(
        tmp_path / "obsidian.json",
        {
            "a": {"path": str(old), "ts": 100},
            "b": {"path": str(newer), "ts": 200},
            "c": {"path": str(opened), "ts": 50, "open": True},
            "d": {"path": str(tmp_path / "deleted"), "ts": 900},
            "e": {"ts": 1},
            "f": "nonsense",
            "g": {"path": 42},
        },
    )
    assert list_vaults(config) == [
        Vault(opened, "opened", True),
        Vault(newer, "newer", False),
        Vault(old, "old", False),
    ]


def test_list_vaults_missing_or_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No config, unreadable JSON or an unexpected shape all give an empty list (never an exception)."""
    monkeypatch.setenv(ENV_OBSIDIAN_CONFIG, str(tmp_path / "missing.json"))
    assert list_vaults() == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert list_vaults(broken) == []
    for name, text in (("array.json", "[]"), ("list.json", '{"vaults": []}')):
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert list_vaults(tmp_path / name) == [], name


def test_suggest_repo(tmp_path: Path) -> None:
    """The first vault wins; without a vault the default is returned unchanged."""
    default = tmp_path / "Documents" / "bookmarks"
    vaults = [Vault(tmp_path / "notes", "notes", True), Vault(tmp_path / "other", "other", False)]
    assert suggest_repo(vaults, default) == tmp_path / "notes" / "bookmarks"
    assert suggest_repo(vaults, default, "links") == tmp_path / "notes" / "links"
    assert suggest_repo([], default) == default
