"""The deskmate_ko profile wires the insight tools and the conversation rules (§3)."""

from reachy_mini_conversation_app.config import DEFAULT_PROFILES_DIRECTORY
from reachy_mini_conversation_app.profile_store import read_profile_from_directory


INSIGHT_TOOLS = ("get_insight", "build_insight", "get_bookmark", "list_bookmarks", "finish_insight")


def test_deskmate_ko_profile_lists_insight_tools_and_rules() -> None:
    """Profile parses, exposes the five tools and states the five principles + end rules."""
    profile_dir = DEFAULT_PROFILES_DIRECTORY / "deskmate_ko"
    profile = read_profile_from_directory("deskmate_ko", profile_dir)

    for tool in INSIGHT_TOOLS:
        assert tool in profile.default_tools
    assert "pomodoro_timer" in profile.default_tools  # desk companion features kept

    text = profile.instructions
    for phrase in (
        "노트 우선",
        "한 묶음, 한 질문",
        "평가 금지",
        "근거 표시",
        "제목만 보고 하는 얘긴데",
        "build_insight",
        "마칠까?",
        "finish_insight",
        'reason="auto"',
        "지난 일주일 동안은 새로 저장한 게 없네",
    ):
        assert phrase in text, phrase
    assert "왜 저장했는지" in text and "묻지 않는다" in text
    assert profile.greeting and "반말" in profile.greeting
