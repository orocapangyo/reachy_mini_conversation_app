"""시나리오 1개를 target에서 실행하고 결과를 만든다."""

from __future__ import annotations
from dataclasses import field, dataclass

from scenario_kit.checks import evaluate
from scenario_kit.schema import Scenario
from scenario_kit.targets import Target


PASS, FAIL, KNOWN_BUG, FIXED, PENDING, ERROR = "PASS", "FAIL", "KNOWN_BUG", "FIXED?", "PENDING", "ERROR"


@dataclass
class StepResult:
    """Step 하나의 판정."""

    label: str
    failures: list[str]
    tools: list[str] = field(default_factory=list)
    reply: str = ""
    trace: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """시나리오 실행 1회의 판정."""

    scenario: Scenario
    status: str
    steps: list[StepResult] = field(default_factory=list)
    error: str = ""

    @property
    def failures(self) -> list[str]:
        """실패 사유를 step 이름과 함께 모은다."""
        found = [f"[{step.label}] {failure}" for step in self.steps for failure in step.failures]
        return [*found, *([self.error] if self.error else [])]


async def run_scenario(scenario: Scenario, target: Target) -> ScenarioResult:
    """모든 step을 실행한다. 앞 step이 실패해도 끝까지 돌려 전체 동작을 본다."""
    steps: list[StepResult] = []
    error = ""
    try:
        await target.start()
        try:
            for step in scenario.steps:
                obs = await target.perform(step)
                steps.append(StepResult(step.label, evaluate(step, obs), obs.tool_names, obs.reply_text, obs.trace))
        finally:
            await target.stop()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    failed = bool(error) or any(step.failures for step in steps)
    if error:
        status = ERROR
    elif scenario.known_bug:
        status = KNOWN_BUG if failed else FIXED
    else:
        status = FAIL if failed else PASS
    return ScenarioResult(scenario, status, steps, error)
