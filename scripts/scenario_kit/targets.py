"""시나리오 실행 환경: offline(가짜 모델), live(실제 Gemini), sim(실행 중인 앱)."""

from __future__ import annotations
import json
import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Callable, Awaitable

import numpy as np

from scenario_kit.checks import Observation
from scenario_kit.schema import Step


# --- tuning constants ---------------------------------------------------------
POLL_S = 0.02
SPEECH_FRAME_SAMPLES = 1600
SPEECH_FRAME_COUNT = 5
SIM_RPC_URL = "ws://localhost:7860/rpc"
SIM_STATE_TIMEOUT_S = 90.0
SIM_GREETING_S = 5.0
SIM_TOOL_REASON_PREFIX = "tool_started:"


async def _wait_until(condition: Callable[[], bool], timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(POLL_S)
    return True


class Target(ABC):
    """모든 환경의 공통 인터페이스."""

    name: str

    @abstractmethod
    async def start(self) -> None:
        """대화 세션을 준비한다."""

    @abstractmethod
    async def perform(self, step: Step) -> Observation:
        """Step 하나를 실행하고 관측 결과를 돌려준다."""

    @abstractmethod
    async def stop(self) -> None:
        """세션을 정리한다."""


class InProcessTarget(Target):
    """실제 핸들러를 이 프로세스에서 구동한다. offline과 live의 공통 부분."""

    turn_timeout_s: float
    silence_s: float
    later_timeout_s: float
    time_scale: float = 1.0

    def __init__(self, *, trace: bool = False) -> None:
        """관측 옵션을 저장한다."""
        self._trace = trace
        self._session_task: asyncio.Task[None] | None = None
        self._isolation: Any = None

    def _api_key(self) -> str | None:
        return None

    def _prepare_turn(self, step: Step) -> None:
        """사용자 턴 직전 처리. offline이 대본을 지정한다."""

    def _frames_sent(self) -> int:
        return 0

    async def start(self) -> None:
        """격리 환경을 만들고 세션을 연다."""
        from scenario_kit.harness import HandlerSpy, isolated_app, make_handler

        self._isolation = isolated_app(api_key=self._api_key())
        instance_path, self.calendar = self._isolation.__enter__()
        self.handler, self.deps = make_handler(instance_path)
        self.spy = HandlerSpy(self.handler, trace=self._trace)
        await self._open_session()

    async def _open_session(self) -> None:
        self.spy.begin_step()
        self._session_task = asyncio.create_task(self.handler.start_up(), name="scenario-session")
        connected = await _wait_until(
            lambda: self._session_failed() or self.handler._is_connected(), self.turn_timeout_s
        )
        if self._session_failed():
            assert self._session_task is not None
            self._session_task.result()
        if not connected:
            raise TimeoutError("Gemini 세션 연결 시간 초과")
        # 인사말 턴이 끝나야 다음 발화가 끼어들기로 처리되지 않는다.
        await _wait_until(lambda: self.spy.obs.turns_done > 0, self.turn_timeout_s)

    def _session_failed(self) -> bool:
        task = self._session_task
        return task is not None and task.done() and not task.cancelled() and task.exception() is not None

    def _state(self) -> str:
        return "asleep" if self.handler._local_standby else "awake"

    async def perform(self, step: Step) -> Observation:
        """Step을 실행한다."""
        from scenario_kit.harness import inject_faults

        inject_faults(step.fail_tools, step.slow_tools)
        obs = self.spy.begin_step()
        frames_before = self._frames_sent()
        if step.action == "wait":
            await asyncio.sleep(step.wait_s * self.time_scale)
        elif step.action == "wake":
            await self.handler.exit_local_standby()
            if self._session_task is not None:
                await self._session_task
            await self._open_session()
            obs = self.spy.obs
        elif self.handler._is_connected():
            self._prepare_turn(step)
            await self.handler.say(step.say)
            await self._await_outcome(step, obs)
        else:
            await self._speak_into_mic()
            await asyncio.sleep(self.silence_s)
        obs.frames_sent_to_model = self._frames_sent() - frames_before
        obs.state = self._state()
        return obs

    async def _speak_into_mic(self) -> None:
        """수면 중 발화: 텍스트 경로는 세션이 없으므로 마이크 경로로 프레임을 넣는다."""
        frame = (np.random.default_rng(0).integers(-3000, 3000, SPEECH_FRAME_SAMPLES)).astype(np.int16)
        for _ in range(SPEECH_FRAME_COUNT):
            await self.handler.receive((16000, frame))

    async def _await_outcome(self, step: Step, obs: Observation) -> None:
        if step.silent:
            await asyncio.sleep(self.silence_s)
            return
        if step.interrupt_after_s is not None:
            obs.timed_out = not await _wait_until(lambda: bool(obs.audio_bytes), self.turn_timeout_s)
            await asyncio.sleep(step.interrupt_after_s * self.time_scale)
            return
        if not step.wait_reply:
            expected = set(step.tools)
            obs.timed_out = not await _wait_until(lambda: expected <= set(obs.tool_names), self.turn_timeout_s)
            return
        finished = await _wait_until(lambda: obs.turns_done > 0 or self.handler._local_standby, self.turn_timeout_s)
        obs.timed_out = not finished
        if step.state == "asleep":
            # request_local_standby는 도구 스레드에서 비동기로 예약된다.
            await _wait_until(lambda: not self.handler._is_connected(), self.turn_timeout_s)
        if step.reply_later_any and finished:
            await _wait_until(lambda: any(w in obs.reply_text for w in step.reply_later_any), self.later_timeout_s)

    async def stop(self) -> None:
        """세션과 격리 환경을 정리한다."""
        with contextlib.suppress(Exception):
            await self.handler.shutdown()
        task = self._session_task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._isolation.__exit__(None, None, None)


class OfflineTarget(InProcessTarget):
    """가짜 모델. 호출 이후의 처리만 검증한다."""

    name = "offline"
    turn_timeout_s = 3.0
    silence_s = 0.1
    later_timeout_s = 0.1
    time_scale = 0.0

    async def start(self) -> None:
        """genai.Client를 가짜로 바꾼 뒤 세션을 연다."""
        from scenario_kit.fake_gemini import FakeGeminiClient
        from reachy_mini_conversation_app import gemini_realtime

        self.client = FakeGeminiClient()
        self._real_client_cls = gemini_realtime.genai.Client
        gemini_realtime.genai.Client = lambda **_kw: self.client  # type: ignore[assignment,misc]
        await super().start()

    def _api_key(self) -> str:
        return "offline-fake-key"

    def _prepare_turn(self, step: Step) -> None:
        from scenario_kit.fake_gemini import plan_from_step

        self.client.set_plan(plan_from_step(step))

    def _frames_sent(self) -> int:
        return self.client.frames

    async def stop(self) -> None:
        """가짜 클라이언트를 되돌린다."""
        from reachy_mini_conversation_app import gemini_realtime

        await super().stop()
        gemini_realtime.genai.Client = self._real_client_cls  # type: ignore[misc]


class LiveTarget(InProcessTarget):
    """실제 Gemini Live API. 로봇은 Mock. 모델의 도구 선택을 검증한다."""

    name = "live"
    turn_timeout_s = 45.0
    silence_s = 6.0
    later_timeout_s = 30.0


Prompt = Callable[[str], Awaitable[str]]


async def _console_prompt(message: str) -> str:
    return await asyncio.to_thread(input, message)


class SimTarget(Target):
    """실행 중인 앱에 /rpc WebSocket으로 접속한다. 3D 모션과 음성은 사람이 확인한다."""

    name = "sim"
    turn_timeout_s = 60.0
    silence_s = 8.0
    later_timeout_s = 45.0

    def __init__(self, *, voice: bool = False, url: str = SIM_RPC_URL, prompt: Prompt = _console_prompt) -> None:
        """접속 정보와 입력 방식을 저장한다."""
        self._voice = voice
        self._url = url
        self._prompt = prompt
        self._ws: Any = None
        self._listener: asyncio.Task[None] | None = None
        self._next_id = 0
        self._responses: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.obs = Observation(args_visible=False, interrupt_visible=False)

    async def start(self) -> None:
        """앱에 접속하고 Gemini 백엔드로 연결돼 있는지 확인한다."""
        import websockets

        try:
            self._ws = await websockets.connect(self._url)
        except OSError as exc:
            raise RuntimeError(
                f"앱에 접속할 수 없습니다({self._url}). 터미널 1에서 ./run_sim_mujoco.sh를 먼저 실행하세요."
            ) from exc
        self._listener = asyncio.create_task(self._listen(), name="scenario-rpc")
        status = await self._call("conversation.status")
        if status.get("backend") != "gemini":
            raise RuntimeError(
                f"Gemini 백엔드가 아닙니다: {status.get('backend')}. .env의 CONVERSATION_BACKEND를 확인하세요."
            )
        if not await self._connected():
            print("    🎤 앱이 잠들어 있습니다. 호출어를 말하세요: 리치미니", flush=True)
            if not await self._wait_connected(True, SIM_STATE_TIMEOUT_S):
                raise RuntimeError("앱의 Gemini 세션이 연결되지 않았습니다. 앱 로그의 wake 경고를 확인하세요.")
            await asyncio.sleep(SIM_GREETING_S)

    async def _listen(self) -> None:
        async for raw in self._ws:
            message = json.loads(raw)
            if "id" in message and message["id"] in self._responses:
                self._responses.pop(message["id"]).set_result(message)
                continue
            params = message.get("params") or {}
            if message.get("method") == "conversation.transcript":
                if params.get("role") == "assistant" and params.get("final"):
                    if self.obs.audio_bytes:
                        self.obs.replies.append(params.get("text", ""))
                        self.obs.turns_done += 1
                    else:  # 이번 step에서 말한 적 없는 자막 = 끊긴 이전 답변
                        self.obs.interrupted_replies.append(params.get("text", ""))
                    self.obs.audio_bytes = 0
            elif message.get("method") == "conversation.activity":
                reason = params.get("reason", "")
                if reason.startswith(SIM_TOOL_REASON_PREFIX):
                    self.obs.tool_calls.append((reason.removeprefix(SIM_TOOL_REASON_PREFIX), {}))
                elif reason == "assistant_audio_delta":
                    self.obs.audio_bytes += 1

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
        except (asyncio.TimeoutError, ConnectionError) as exc:
            raise RuntimeError(f"RPC {method} 응답 없음: 앱이 종료됐는지 확인하세요.") from exc
        return dict(response.get("result") or {"error": response.get("error")})

    async def _connected(self) -> bool:
        status = await self._call("conversation.status")
        return bool(status.get("backend_connected"))

    async def _wait_connected(self, expected: bool, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while await self._connected() != expected:
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)
        return True

    async def _say(self, step: Step) -> None:
        if self._voice:
            print(f"    🎤 지금 말하세요: {step.say}", flush=True)
            return
        await self._call("conversation.say", text=step.say)  # 수면 중이면 not_running 오류 = 전달 안 됨

    async def perform(self, step: Step) -> Observation:
        """Step을 실행한다."""
        obs = self.obs = Observation(args_visible=False, interrupt_visible=False)
        if step.fail_tools or step.slow_tools:
            raise RuntimeError("오류 주입은 sim에서 지원하지 않습니다. targets에서 sim을 빼 주세요.")
        if step.action == "wait":
            await asyncio.sleep(step.wait_s)
        elif step.action == "wake":
            print("    🎤 호출어를 말하세요: 리치미니", flush=True)
            await self._wait_connected(True, SIM_STATE_TIMEOUT_S)
            await _wait_until(lambda: obs.turns_done > 0, self.turn_timeout_s)
            obs = self.obs = Observation(args_visible=False, interrupt_visible=False)
        else:
            await self._say(step)
            await self._await_outcome(step, obs)
        obs.state = "awake" if await self._connected() else "asleep"
        if step.observe:
            try:
                answer = await self._prompt(f"    👀 {step.observe} [y/n] ")
            except EOFError:  # 터미널 입력이 없는 실행에서는 사람 확인을 건너뛴다
                print(f"    👀 확인 건너뜀: {step.observe}", flush=True)
            else:
                if answer.strip().lower() not in ("y", "yes", "ㅛ"):
                    obs.observe_failed = step.observe
        return obs

    async def _await_outcome(self, step: Step, obs: Observation) -> None:
        if step.silent:
            await asyncio.sleep(self.silence_s)
            return
        if step.interrupt_after_s is not None:
            obs.timed_out = not await _wait_until(lambda: bool(obs.audio_bytes), self.turn_timeout_s)
            await asyncio.sleep(step.interrupt_after_s)
            return
        if not step.wait_reply:
            expected = set(step.tools)
            obs.timed_out = not await _wait_until(lambda: expected <= set(obs.tool_names), self.turn_timeout_s)
            return
        if step.state == "asleep":
            obs.timed_out = not await self._wait_connected(False, self.turn_timeout_s)
            return
        obs.timed_out = not await _wait_until(lambda: obs.turns_done > 0, self.turn_timeout_s)
        if step.reply_later_any:
            await _wait_until(lambda: any(w in obs.reply_text for w in step.reply_later_any), self.later_timeout_s)

    async def stop(self) -> None:
        """접속을 닫는다."""
        if self._listener is not None:
            self._listener.cancel()
        if self._ws is not None:
            await self._ws.close()
