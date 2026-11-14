"""Tests for ``bookmark_insight.demo`` and the ``scripts/bookmark_insight_demo_setup.py`` wrapper."""

import sys
import importlib.util
from types import ModuleType
from pathlib import Path

import pytest

from bookmark_insight.demo import DEFAULT_FIXTURES_DIR, DemoRepo, create_demo_repo
from bookmark_insight.settings import ENV_BOOKMARK_REPO, ENV_CHROME_PROFILES, ENV_CHROME_USER_DATA_DIR


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bookmark_insight_demo_setup.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bookmark_insight_demo_setup", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_create_demo_repo_copies_fixtures(tmp_path: Path, fixtures_dir: Path) -> None:
    """The demo target gets the sample repo (3 bookmarks + 1 note) and the fake Chrome directory."""
    demo = create_demo_repo(tmp_path / "demo", fixtures_dir=fixtures_dir)
    assert isinstance(demo, DemoRepo)
    assert demo.repo == tmp_path / "demo" / "repo"
    assert demo.chrome_user_data_dir == tmp_path / "demo" / "User Data"
    assert len(list((demo.repo / "bookmarks" / "Default").rglob("*.md"))) == 3
    assert len(list((demo.repo / "insights" / "Default").glob("*.md"))) == 1
    assert (demo.chrome_user_data_dir / "Local State").is_file()
    assert demo.env_lines() == {
        ENV_BOOKMARK_REPO: str(demo.repo),
        ENV_CHROME_USER_DATA_DIR: str(demo.chrome_user_data_dir),
        ENV_CHROME_PROFILES: "Default",
    }
    assert (demo.note_since, demo.note_until) == ("2026-10-31", "2026-11-06")


def test_create_demo_repo_refuses_existing_target(tmp_path: Path, fixtures_dir: Path) -> None:
    """An existing target is never overwritten."""
    target = tmp_path / "demo"
    target.mkdir()
    with pytest.raises(FileExistsError):
        create_demo_repo(target, fixtures_dir=fixtures_dir)
    assert list(target.iterdir()) == []


def test_create_demo_repo_without_fixtures(tmp_path: Path) -> None:
    """No fixtures (installed package) raises FileNotFoundError before touching the target."""
    with pytest.raises(FileNotFoundError):
        create_demo_repo(tmp_path / "demo", fixtures_dir=None)
    with pytest.raises(FileNotFoundError):
        create_demo_repo(tmp_path / "demo", fixtures_dir=tmp_path / "missing")
    assert not (tmp_path / "demo").exists()


def test_default_fixtures_dir_points_at_checkout() -> None:
    """In this checkout the default fixtures directory is found."""
    assert DEFAULT_FIXTURES_DIR is not None and DEFAULT_FIXTURES_DIR.is_dir()


def test_script_prints_env_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI wrapper keeps its output contract and exit codes."""
    script = _load_script()
    target = tmp_path / "demo"
    assert script.main([str(target)]) == 0
    out = capsys.readouterr().out
    assert f"{ENV_BOOKMARK_REPO}={target / 'repo'}" in out
    assert f"{ENV_CHROME_USER_DATA_DIR}={target / 'User Data'}" in out
    assert f"{ENV_CHROME_PROFILES}=Default" in out
    assert "REACHY_MINI_CUSTOM_PROFILE=deskmate_ko" in out

    assert script.main([str(target)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
