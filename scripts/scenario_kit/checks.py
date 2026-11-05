"""Step 관측 결과를 기대값과 비교한다. 모든 target이 같은 판정을 쓴다."""

from __future__ import annotations
import datetime
from typing import Any
from dataclasses import field, dataclass

from scenario_kit.schema import Step


@dataclass
class Observation:
    """Step 하나를 실행하는 동안 관측한 내용."""

    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    interrupted_replies: list[str] = field(default_factory=list)  # 끊긴 이전 답변의 자막 (판정 제외)
    audio_bytes: int = 0
    turns_done: int = 0
    interrupted: bool = False
    audio_left_after_interrupt: bool = False
    frames_sent_to_model: int = 0
    state: str | None = None
    timed_out: bool = False
    args_visible: bool = True  # sim은 RPC로 도구 인자를 받을 수 없다
    interrupt_visible: bool = True  # sim은 RPC로 interrupted 신호를 받을 수 없다
    observe_failed: str = ""
    trace: list[str] = field(default_factory=list)

    @property
    def reply_text(self) -> str:
        """이 step에서 받은 답변 전체."""
        return " ".join(self.replies)

    @property
    def tool_names(self) -> list[str]:
        """호출된 도구 이름 목록."""
        return [name for name, _ in self.tool_calls]


def _expand(value: str) -> str:
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    return value.replace("{today}", today.isoformat()).replace("{tomorrow}", tomorrow.isoformat())


def _arg_matches(actual: Any, expected: Any) -> bool:
    """문자열은 부분 일치, 리스트는 하나라도 일치, 나머지는 값 일치."""
    if isinstance(expected, (list, tuple)):
        return any(_arg_matches(actual, item) for item in expected)
    if isinstance(expected, str):
        return _expand(expected).lower() in str(actual).lower()
    return bool(actual == expected)


def _check_args(step: Step, obs: Observation) -> list[str]:
    failures = []
    for tool, expected_args in step.args.items():
        calls = [args for name, args in obs.tool_calls if name == tool]
        if not calls:
            continue  # 호출 누락은 tools 검사가 보고한다
        for key, expected in expected_args.items():
            if not any(key in args and _arg_matches(args[key], expected) for args in calls):
                failures.append(f"{tool}.{key} 기대={expected!r} 실제={[args.get(key) for args in calls]}")
    return failures


def _check_reply(step: Step, obs: Observation) -> list[str]:
    failures = []
    text = obs.reply_text
    if step.reply_any and not any(word in text for word in step.reply_any):
        failures.append(f"답변에 {list(step.reply_any)} 중 하나도 없음: {text[:120]!r}")
    for word in step.reply_none:
        if word in text:
            failures.append(f"답변에 나오면 안 되는 {word!r} 포함: {text[:120]!r}")
    if step.reply_later_any and not any(word in _text_after_reply(step, text) for word in step.reply_later_any):
        failures.append(f"답변 뒤에 {list(step.reply_later_any)} 재개 없음: {text[:120]!r}")
    return failures


def _text_after_reply(step: Step, text: str) -> str:
    """reply_any 단어가 마지막으로 나온 뒤의 텍스트. 재개는 새 답변 뒤에 와야 한다."""
    cut = max((text.rfind(word) + len(word) for word in step.reply_any if word in text), default=0)
    return text[cut:]


def evaluate(step: Step, obs: Observation) -> list[str]:
    """실패 사유 목록을 돌려준다. 비어 있으면 통과."""
    failures = []
    for tool in step.tools:
        if tool not in obs.tool_names:
            failures.append(f"도구 {tool} 호출 안 됨 (실제: {obs.tool_names})")
    for tool in step.no_tools:
        if tool in obs.tool_names:
            failures.append(f"호출되면 안 되는 도구 {tool} 호출됨")
    if obs.args_visible:
        failures.extend(_check_args(step, obs))
    failures.extend(_check_reply(step, obs))
    if step.silent:
        if obs.tool_calls or obs.replies or obs.audio_bytes:
            failures.append(f"침묵해야 하는데 반응함: 도구={obs.tool_names} 답변={obs.reply_text[:80]!r}")
        if obs.frames_sent_to_model:
            failures.append(f"수면 중 음성 프레임 {obs.frames_sent_to_model}건이 모델로 전송됨")
    if step.state is not None and obs.state != step.state:
        failures.append(f"상태 기대={step.state} 실제={obs.state}")
    if step.interrupted and obs.interrupt_visible:
        if not obs.interrupted:
            failures.append("끼어들기(interrupted) 신호를 받지 못함")
        if obs.audio_left_after_interrupt:
            failures.append("끼어들기 후 이전 답변 오디오가 큐에 남음")
    if obs.timed_out and step.wait_reply and not step.silent and step.interrupt_after_s is None:
        failures.append("시간 초과: 턴이 끝나지 않음")
    if obs.observe_failed:
        failures.append(f"사람 확인 실패: {obs.observe_failed}")
    return failures
