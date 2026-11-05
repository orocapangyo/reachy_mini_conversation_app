"""시나리오 TOML 스키마와 로더."""

from __future__ import annotations
import tomllib
from typing import Any
from pathlib import Path
from dataclasses import field, fields, dataclass


SCENARIO_DIR = Path(__file__).resolve().parents[2] / "tests" / "scenarios"
TARGETS = ("offline", "live", "sim")
GROUPS = ("tool", "barge", "calendar", "sleep")
ACTIONS = ("say", "wake", "wait")
STATES = ("asleep", "awake")


class ScenarioError(ValueError):
    """시나리오 파일이 스키마에 맞지 않을 때 발생한다."""


@dataclass(frozen=True)
class MockCall:
    """Offline 가짜 모델이 호출할 도구 1건."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Mock:
    """Offline 가짜 모델의 응답을 직접 지정한다. 없으면 기대값에서 유도한다."""

    calls: tuple[MockCall, ...] | None = None
    reply: str | None = None


@dataclass(frozen=True)
class Step:
    """대화 한 단계: 행동 1개와 그 결과에 대한 기대값."""

    # 행동
    action: str = "say"
    say: str = ""
    wait_s: float = 0.0
    interrupt_after_s: float | None = None  # 답변 시작 후 이 시간이 지나면 다음 step으로 끼어든다
    wait_reply: bool = True  # False면 도구 호출만 확인하고 답변을 기다리지 않는다
    fail_tools: tuple[str, ...] = ()  # 이 도구는 예외를 던진다 (오류 주입)
    slow_tools: tuple[str, ...] = ()  # 이 도구는 끝나지 않는다 (실행 중 끼어들기용)
    # 기대값
    tools: tuple[str, ...] = ()
    no_tools: tuple[str, ...] = ()
    args: dict[str, dict[str, Any]] = field(default_factory=dict)
    reply_any: tuple[str, ...] = ()
    reply_none: tuple[str, ...] = ()
    reply_later_any: tuple[str, ...] = ()
    silent: bool = False
    state: str | None = None
    interrupted: bool = False  # interrupted 신호 수신과 재생 큐 비움을 확인한다
    # sim 전용: 모션처럼 사람이 봐야 하는 항목을 y/n으로 묻는다
    observe: str = ""
    mock: Mock = field(default_factory=Mock)

    @property
    def label(self) -> str:
        """리포트에 표시할 짧은 이름."""
        return self.say or self.action


@dataclass(frozen=True)
class Scenario:
    """시나리오 파일 1개."""

    id: str
    title: str
    group: str
    steps: tuple[Step, ...]
    targets: tuple[str, ...] = TARGETS
    pending: str = ""  # 기능 미구현 사유. 구현되면 이 줄을 지운다
    known_bug: str = ""  # 현재 실패가 예상되는 버그. 고치면 이 줄을 지운다
    path: Path | None = None


def _reject_unknown(raw: dict[str, Any], cls: type, where: str) -> None:
    unknown = set(raw) - {f.name for f in fields(cls)}
    if unknown:
        raise ScenarioError(f"{where}: 알 수 없는 필드 {sorted(unknown)}")


def _parse_mock(raw: dict[str, Any], where: str) -> Mock:
    _reject_unknown(raw, Mock, where)
    calls = raw.get("calls")
    return Mock(
        calls=None if calls is None else tuple(MockCall(c["name"], c.get("args", {})) for c in calls),
        reply=raw.get("reply"),
    )


def _parse_step(raw: dict[str, Any], where: str) -> Step:
    _reject_unknown(raw, Step, where)
    data = dict(raw)
    if "mock" in data:
        data["mock"] = _parse_mock(data["mock"], f"{where}.mock")
    for f in fields(Step):
        if isinstance(data.get(f.name), list):
            data[f.name] = tuple(data[f.name])
    if "action" not in data and "say" not in data and "wait_s" in data:
        data["action"] = "wait"
    step = Step(**data)
    if step.action not in ACTIONS:
        raise ScenarioError(f"{where}: action은 {ACTIONS} 중 하나여야 합니다")
    if step.action == "say" and not step.say:
        raise ScenarioError(f"{where}: say 문장이 없습니다")
    if step.state is not None and step.state not in STATES:
        raise ScenarioError(f"{where}: state는 {STATES} 중 하나여야 합니다")
    return step


def parse_scenario(raw: dict[str, Any], *, where: str = "<inline>", path: Path | None = None) -> Scenario:
    """Dict를 검증해 Scenario로 변환한다."""
    _reject_unknown(raw, Scenario, where)
    for key in ("id", "title", "group", "steps"):
        if key not in raw:
            raise ScenarioError(f"{where}: 필수 필드 {key!r} 없음")
    if raw["group"] not in GROUPS:
        raise ScenarioError(f"{where}: group은 {GROUPS} 중 하나여야 합니다")
    targets = tuple(raw.get("targets", TARGETS))
    if set(targets) - set(TARGETS):
        raise ScenarioError(f"{where}: targets는 {TARGETS} 중에서 골라야 합니다")
    steps = tuple(_parse_step(s, f"{where} steps[{i}]") for i, s in enumerate(raw["steps"]))
    return Scenario(**{**raw, "steps": steps, "targets": targets, "path": path})


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    """디렉터리의 시나리오 파일을 모두 읽는다. 파일을 넣기만 하면 추가된다."""
    scenarios = []
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as fh:
            scenario = parse_scenario(tomllib.load(fh), where=path.name, path=path)
        if scenario.id != path.stem:
            raise ScenarioError(f"{path.name}: id({scenario.id})는 파일명과 같아야 합니다")
        scenarios.append(scenario)
    return scenarios
