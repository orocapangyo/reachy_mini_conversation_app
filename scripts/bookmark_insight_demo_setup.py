"""Create a Bookmark Insight demo bookmark repository from the test fixtures.

Usage (from the project root, venv active)::

    python scripts/bookmark_insight_demo_setup.py ~/bookmark-insight-demo

It copies ``tests/bookmark_insight/fixtures/repo_sample`` (3 summarized bookmarks +
one ready insight note for 2026-10-31..2026-11-06) and the fake Chrome
``User Data`` directory, then prints the ``.env`` lines to paste. With these,
``get_insight(since="2026-10-31", until="2026-11-06")`` answers ``ready`` in the
emulator without any network or LLM key (M1 대화 품질 선검증).

The same thing is available as the "데모 데이터 준비" button in the settings
UI (``reachy-mini-conversation-app --ui`` → Settings → Bookmark Insight).
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path

from bookmark_insight.demo import DEMO_PERSONALITY, create_demo_repo


def main(argv: list[str] | None = None) -> int:
    """Copy the fixtures and print the environment lines."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="directory to create (must not exist)")
    args = parser.parse_args(argv)
    target: Path = args.target.expanduser().resolve()
    try:
        demo = create_demo_repo(target)
    except (FileExistsError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("Demo repository created. Add these lines to your .env:\n")
    for key, value in demo.env_lines().items():
        print(f"{key}={value}")
    print(f"REACHY_MINI_CUSTOM_PROFILE={DEMO_PERSONALITY}")
    print("\nThen say: '10월 31일부터 11월 6일까지 북마크 얘기하자' (ready note) or")
    print("'이번 주 북마크 얘기하자' (generating → build_insight with FakeLLM unless GEMINI_API_KEY is set).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
