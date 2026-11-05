"""Offline용 가짜 Gemini Live. 실제 SDK 메시지 타입으로 정해진 대본을 재생한다.

모델의 판단은 흉내 내지 않는다. 시나리오가 기대한 도구를 그대로 호출하므로,
여기서의 통과는 "호출된 뒤의 처리가 정상"이라는 뜻이다.
"""

from __future__ import annotations
import asyncio
from typing import Any
from dataclasses import field, dataclass

from google.genai import types

from scenario_kit.schema import Step, MockCall


_AUDIO_CHUNK = b"\x00\x01" * 240
_PARTIAL_TEXT = "(끊기기 전 답변 조각) "


@dataclass
class TurnPlan:
    """다음 사용자 턴에 재생할 대본."""

    calls: tuple[MockCall, ...] = ()
    reply: str = "네."
    hold: bool = False  # True면 턴을 끝내지 않고 말하는 중으로 남는다
    speak_after_tools: bool = True
    pending_ids: set[str] = field(default_factory=set)


def plan_from_step(step: Step) -> TurnPlan:
    """Step 기대값에서 대본을 유도한다. [steps.mock]이 있으면 그 값을 쓴다."""
    calls = step.mock.calls
    if calls is None:
        calls = tuple(MockCall(name, _concrete_args(step.args.get(name, {}))) for name in step.tools)
    words = step.reply_any or step.reply_later_any
    reply = step.mock.reply if step.mock.reply is not None else (words[0] if words else "네.")
    return TurnPlan(
        calls=calls,
        reply=reply,
        hold=step.interrupt_after_s is not None,
        speak_after_tools=step.wait_reply,
    )


def _concrete_args(expected: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in expected.items()}


def _content(**kwargs: Any) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(**kwargs))


def _audio() -> types.LiveServerMessage:
    blob = types.Blob(data=_AUDIO_CHUNK, mime_type="audio/pcm;rate=24000")
    return _content(model_turn=types.Content(role="model", parts=[types.Part(inline_data=blob)]))


def _spoken(text: str) -> types.LiveServerMessage:
    return _content(output_transcription=types.Transcription(text=text))


class FakeLiveSession:
    """client.aio.live.connect()가 돌려주는 세션 대역."""

    def __init__(self, owner: "FakeGeminiClient") -> None:
        """대본을 공유하는 클라이언트를 기억한다."""
        self._owner = owner
        self._queue: asyncio.Queue[types.LiveServerMessage] = asyncio.Queue()
        self._waiting: list[TurnPlan] = []
        self._speaking = False

    async def __aenter__(self) -> "FakeLiveSession":
        """세션을 연다."""
        self._owner.session = self
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        """세션을 닫는다."""

    async def receive(self) -> Any:
        """실제 SDK처럼 턴 경계마다 제너레이터를 끝낸다."""
        while True:
            message = await self._queue.get()
            yield message
            if message.server_content and message.server_content.turn_complete:
                return

    async def send_client_content(self, **_kwargs: Any) -> None:
        """사용자 턴 수신. 실제 API에서 측정한 순서를 따른다.

        말하는 중이면 interrupted 뒤 빈 turn_complete가 온다. 도구 응답을 기다리는 중이면
        빈 turn_complete만 오고, 텍스트 입력 경로에서는 tool_call_cancellation이 오지 않았다.
        """
        if self._speaking:
            self._queue.put_nowait(_content(interrupted=True))
            self._speaking = False
            self._queue.put_nowait(_content(turn_complete=True))
        elif self._waiting:
            self._queue.put_nowait(_content(turn_complete=True))
        self._begin(self._owner.take_plan())

    async def send_tool_response(self, *, function_responses: list[types.FunctionResponse]) -> None:
        """모든 도구 응답이 모이면 답변을 시작한다."""
        self._owner.tool_responses.extend(function_responses)
        done = {response.id for response in function_responses}
        for plan in list(self._waiting):
            plan.pending_ids -= done
            if not plan.pending_ids:
                self._waiting.remove(plan)
                if plan.speak_after_tools:
                    self._speak(plan)

    async def send_realtime_input(self, **_kwargs: Any) -> None:
        """마이크 프레임 수를 기록한다."""
        self._owner.frames += 1

    async def close(self) -> None:
        """닫기 호출은 수신 루프 취소로 처리되므로 할 일이 없다."""

    def _begin(self, plan: TurnPlan) -> None:
        if not plan.calls:
            self._speak(plan)
            return
        function_calls = []
        for call in plan.calls:
            call_id = self._owner.next_call_id()
            plan.pending_ids.add(call_id)
            function_calls.append(types.FunctionCall(id=call_id, name=call.name, args=call.args))
        self._waiting.append(plan)
        self._queue.put_nowait(
            types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=function_calls))
        )

    def _speak(self, plan: TurnPlan) -> None:
        self._queue.put_nowait(_audio())
        if plan.hold:
            self._queue.put_nowait(_spoken(_PARTIAL_TEXT))
            self._speaking = True
            return
        self._queue.put_nowait(_spoken(plan.reply))
        self._queue.put_nowait(_content(turn_complete=True))


class _FakeLive:
    def __init__(self, owner: "FakeGeminiClient") -> None:
        self._owner = owner

    def connect(self, *, model: str, config: Any) -> FakeLiveSession:
        return FakeLiveSession(self._owner)


class _FakeAio:
    def __init__(self, owner: "FakeGeminiClient") -> None:
        self.live = _FakeLive(owner)


class FakeGeminiClient:
    """genai.Client 대역. 재연결해도 대본과 전송 기록을 유지한다."""

    def __init__(self) -> None:
        """비어 있는 대본으로 시작한다."""
        self.aio = _FakeAio(self)
        self.session: FakeLiveSession | None = None
        self.tool_responses: list[types.FunctionResponse] = []
        self.frames = 0
        self._plan: TurnPlan | None = None
        self._call_counter = 0

    def set_plan(self, plan: TurnPlan) -> None:
        """다음 사용자 턴의 대본을 지정한다."""
        self._plan = plan

    def take_plan(self) -> TurnPlan:
        """대본을 꺼낸다. 없으면 인사말 같은 기본 답변을 쓴다."""
        plan, self._plan = self._plan, None
        return plan or TurnPlan(reply="안녕하세요.")

    def next_call_id(self) -> str:
        """SDK처럼 call_<번호> 형태의 id를 만든다."""
        self._call_counter += 1
        return f"call_{self._call_counter}"
