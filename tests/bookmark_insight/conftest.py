"""Shared fixtures for Bookmark Insight tests (all tracks)."""

import json
import shutil
from typing import Any
from pathlib import Path

import pytest

from bookmark_insight.settings import BookmarkInsightSettings


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Root of the static fixtures."""
    return FIXTURES_DIR


@pytest.fixture
def chrome_user_data(fixtures_dir: Path, tmp_path: Path) -> Path:
    """Return a writable copy of the fake Chrome ``User Data`` directory (two profiles + Local State)."""
    target = tmp_path / "User Data"
    shutil.copytree(fixtures_dir / "chrome_user_data", target)
    return target


@pytest.fixture
def repo(fixtures_dir: Path, tmp_path: Path) -> Path:
    """Return a writable copy of the sample bookmark repository (3 bookmarks + 1 insight note)."""
    target = tmp_path / "repo"
    shutil.copytree(fixtures_dir / "repo_sample", target)
    return target


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    """Return an empty bookmark repository directory."""
    target = tmp_path / "empty_repo"
    target.mkdir()
    return target


@pytest.fixture
def settings(repo: Path, chrome_user_data: Path) -> BookmarkInsightSettings:
    """Return settings pointing at the sample repo and fake Chrome data, allow list = Default only."""
    return BookmarkInsightSettings(
        bookmark_repo=repo,
        chrome_user_data_dir=chrome_user_data,
        chrome_profiles=("Default",),
        default_profile="Default",
    )


@pytest.fixture
def fake_llm_responses(fixtures_dir: Path) -> dict[str, Any]:
    """Canned FakeLLM responses keyed by URL / cluster payload."""
    data: dict[str, Any] = json.loads((fixtures_dir / "llm" / "fake_responses.json").read_text(encoding="utf-8"))
    return data
