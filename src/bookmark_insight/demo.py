"""Create a Bookmark Insight demo repository from the test fixtures (shared by the CLI script and the settings UI).

The fixtures hold 3 summarized bookmarks, one ``ready`` insight note for
2026-10-31..2026-11-06 and a fake Chrome ``User Data`` directory, so
``get_insight`` answers without any network or LLM key. This module must not
import the conversation app (design doc, foundation B).
"""

from __future__ import annotations
import shutil
from pathlib import Path
from dataclasses import dataclass

from bookmark_insight.settings import ENV_BOOKMARK_REPO, ENV_CHROME_PROFILES, ENV_CHROME_USER_DATA_DIR


DEMO_PERSONALITY = "deskmate_ko"
DEMO_PROFILE = "Default"
DEMO_NOTE_SINCE = "2026-10-31"
DEMO_NOTE_UNTIL = "2026-11-06"
REPO_DIRNAME = "repo"
USER_DATA_DIRNAME = "User Data"
REPO_FIXTURE = "repo_sample"
CHROME_FIXTURE = "chrome_user_data"

_CHECKOUT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "bookmark_insight" / "fixtures"
DEFAULT_FIXTURES_DIR: Path | None = _CHECKOUT_FIXTURES if _CHECKOUT_FIXTURES.is_dir() else None


@dataclass(frozen=True)
class DemoRepo:
    """Where the demo data landed and the ``.env`` values that point at it."""

    target: Path
    repo: Path
    chrome_user_data_dir: Path
    profile: str = DEMO_PROFILE
    note_since: str = DEMO_NOTE_SINCE
    note_until: str = DEMO_NOTE_UNTIL

    def env_lines(self) -> dict[str, str]:
        """Return the Bookmark Insight ``.env`` values for this demo (same dict for the script and the RPC)."""
        return {
            ENV_BOOKMARK_REPO: str(self.repo),
            ENV_CHROME_USER_DATA_DIR: str(self.chrome_user_data_dir),
            ENV_CHROME_PROFILES: self.profile,
        }


def create_demo_repo(target: Path, *, fixtures_dir: Path | None = DEFAULT_FIXTURES_DIR) -> DemoRepo:
    """Copy the fixtures into ``target`` (must not exist) and return where everything is.

    Raises ``FileExistsError`` when ``target`` exists (never overwrites) and
    ``FileNotFoundError`` when the fixtures are missing (not a repository checkout).
    """
    target = Path(target).expanduser()
    if fixtures_dir is None or not fixtures_dir.is_dir():
        raise FileNotFoundError(
            f"Bookmark Insight demo fixtures not found ({fixtures_dir or 'no tests/bookmark_insight/fixtures'}); "
            "run from a repository checkout"
        )
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing directory: {target}")
    repo = target / REPO_DIRNAME
    user_data = target / USER_DATA_DIRNAME
    shutil.copytree(fixtures_dir / REPO_FIXTURE, repo)
    shutil.copytree(fixtures_dir / CHROME_FIXTURE, user_data)
    return DemoRepo(target=target, repo=repo, chrome_user_data_dir=user_data)
