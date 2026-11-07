"""대화 시나리오 offline 실행. 파일을 tests/scenarios/에 넣으면 자동으로 포함된다.

여기서의 통과는 "도구가 호출된 뒤의 처리가 정상"이라는 뜻이다. 모델이 도구를
제대로 고르는지는 scripts/run_scenarios.py --target live로 확인한다.
"""

from __future__ import annotations
import sys
from typing import Any
from pathlib import Path
from collections.abc import Iterator

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scenario_kit.checks import Observation, evaluate  # noqa: E402
from scenario_kit.runner import FAIL, run_scenario  # noqa: E402
from scenario_kit.schema import Step, ScenarioError, load_scenarios, parse_scenario  # noqa: E402
from scenario_kit.targets import OfflineTarget  # noqa: E402


_APP_MODULES = ("reachy_mini_conversation_app", "scenario_kit.harness")


@pytest.fixture(scope="module", autouse=True)
def fresh_app_modules() -> Iterator[None]:
    """깨끗한 앱 모듈로 실행하고 끝나면 되돌린다.

    일부 테스트가 core_tools를 sys.modules에서 빼고 다시 import하면 ToolDependencies 클래스가
    둘로 갈라져 ToolCallRoutine의 pydantic 검증이 실패한다.
    """
    saved = {name: module for name, module in sys.modules.items() if name.startswith(_APP_MODULES)}
    for name in saved:
        del sys.modules[name]
    yield
    for name in [name for name in sys.modules if name.startswith(_APP_MODULES)]:
        del sys.modules[name]
    sys.modules.update(saved)


SCENARIOS = load_scenarios()
OFFLINE = [s for s in SCENARIOS if "offline" in s.targets]


def _case(scenario: Any) -> Any:
    marks = []
    if scenario.pending:
        marks.append(pytest.mark.skip(reason=f"미구현: {scenario.pending}"))
    if scenario.known_bug:
        # strict: 버그가 고쳐지면 테스트가 알려 주므로 known_bug 줄을 지우게 된다.
        marks.append(pytest.mark.xfail(reason=scenario.known_bug, strict=True))
    return pytest.param(scenario, id=scenario.id, marks=marks)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [_case(s) for s in OFFLINE])
async def test_scenario_offline(scenario: Any) -> None:
    """실제 핸들러와 도구를 가짜 모델로 구동한다."""
    result = await run_scenario(scenario, OfflineTarget())
    assert not result.failures, "\n".join(result.failures)


def _inline(**step: Any) -> Any:
    return parse_scenario({"id": "inline", "title": "t", "group": "tool", "steps": [step]})


@pytest.mark.asyncio
async def test_runner_fails_when_expected_tool_differs_from_called_tool() -> None:
    """가짜 모델이 다른 도구를 부르면 판정이 실패해야 한다 (판정기 자체 검증)."""
    scenario = parse_scenario(
        {
            "id": "inline",
            "title": "t",
            "group": "tool",
            "steps": [{"say": "오늘 일정", "tools": ["get_schedule"], "mock": {"calls": [{"name": "move_head"}]}}],
        }
    )
    result = await run_scenario(scenario, OfflineTarget())
    assert result.status == FAIL
    assert any("get_schedule" in failure for failure in result.failures)


@pytest.mark.asyncio
async def test_tool_name_is_published_as_activity_for_sim() -> None:
    """sim은 conversation.activity의 tool_started 이유로 도구 이름을 받는다."""
    target = OfflineTarget()
    await target.start()
    try:
        reasons: list[str] = []
        target.handler.set_activity_observer(reasons.append)
        await target.perform(Step(say="왼쪽 봐줘", tools=("move_head",)))
    finally:
        await target.stop()
    assert "tool_started:move_head" in reasons


@pytest.mark.asyncio
async def test_isolation_restores_global_state() -> None:
    """시나리오가 전역 레지스트리, 프로필, 캘린더, genai.Client를 오염시키지 않는다."""
    from reachy_mini_conversation_app import gemini_realtime
    from reachy_mini_conversation_app.tools import core_tools
    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.calendar_service import GoogleCalendarService

    before = (
        core_tools.ALL_TOOLS,
        config.REACHY_MINI_CUSTOM_PROFILE,
        config.GEMINI_API_KEY,
        GoogleCalendarService._instance,
        gemini_realtime.genai.Client,
    )
    target = OfflineTarget()
    await target.start()
    await target.stop()
    assert before == (
        core_tools.ALL_TOOLS,
        config.REACHY_MINI_CUSTOM_PROFILE,
        config.GEMINI_API_KEY,
        GoogleCalendarService._instance,
        gemini_realtime.genai.Client,
    )


def test_unknown_field_is_rejected() -> None:
    """오타 필드는 조용히 무시되지 않고 로드 시 실패한다."""
    with pytest.raises(ScenarioError, match="tool"):
        _inline(say="안녕", tool=["get_schedule"])


def test_silent_step_fails_when_robot_reacts() -> None:
    """수면 중 반응이 있으면 실패한다."""
    obs = Observation(replies=["네"], state="asleep")
    assert evaluate(Step(say="안녕", silent=True), obs)


def test_interrupt_visibility_skips_check_for_sim() -> None:
    """Sim처럼 신호를 볼 수 없으면 interrupted 판정을 하지 않는다."""
    step = Step(say="그만", interrupted=True)
    assert evaluate(step, Observation())
    assert not evaluate(step, Observation(interrupt_visible=False))
