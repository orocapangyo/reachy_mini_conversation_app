"""실제 GeminiRealtimeHandler를 격리된 환경에서 구동하고 관측한다."""

from __future__ import annotations
import os
import asyncio
import datetime
import tempfile
import contextlib
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock
from collections.abc import Iterator

from scenario_kit.checks import Observation
from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config, set_custom_profile
from reachy_mini_conversation_app.gemini_realtime import GeminiRealtimeHandler
from reachy_mini_conversation_app.calendar_service import GoogleCalendarService
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


PROFILE = "desk_companion_ko"
HANG_SECONDS = 3600.0


class FakeCalendar(GoogleCalendarService):
    """실제 Google 계정 대신 쓰는 인메모리 캘린더."""

    def __init__(self) -> None:
        """시나리오용 고정 일정으로 시작한다."""
        super().__init__()
        # 실행 시각과 무관하게 항상 "다음 회의"가 되도록 현재 시각 기준으로 만든다.
        start = datetime.datetime.now().astimezone() + datetime.timedelta(hours=2)
        end = start + datetime.timedelta(hours=1)
        self.events: list[dict[str, str]] = [
            {"summary": "팀 회의", "start": start.isoformat(), "end": end.isoformat(), "location": "회의실 A"},
        ]

    def get_events(
        self, target_date: str = "today", calendar_id: str = "primary", max_results: int = 10
    ) -> dict[str, Any]:
        """저장된 일정을 돌려준다."""
        events = self.events[:max_results]
        summaries = ", ".join(event["summary"] for event in events)
        return {
            "authenticated": True,
            "date": target_date,
            "event_count": len(events),
            "events": events,
            "message": summaries,
        }


class StubTool(Tool):
    """네트워크, 카메라가 필요한 도구를 고정 응답으로 대신한다."""

    _auto_register = False

    def __init__(self, name: str, description: str, properties: dict[str, Any], result: dict[str, Any]) -> None:
        """도구 명세와 고정 응답을 저장한다."""
        self.name = name
        self.description = description
        self.parameters_schema = {"type": "object", "properties": properties, "required": []}
        self._result = result

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """고정 응답을 돌려준다."""
        return dict(self._result)


class FaultTool(Tool):
    """원본 도구의 명세를 유지한 채 실패하거나 멈춘다."""

    _auto_register = False

    def __init__(self, original: Tool, *, hang: bool) -> None:
        """원본 명세를 복사한다."""
        self.name = original.name
        self.description = original.description
        self.parameters_schema = original.parameters_schema
        self._hang = hang

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """멈추거나 예외를 던진다."""
        if self._hang:
            await asyncio.sleep(HANG_SECONDS)
        raise RuntimeError("시나리오에서 주입한 도구 오류")


def _stub_tools(profile_tools: dict[str, Tool]) -> list[Tool]:
    """외부 자원에 의존하는 도구의 대역. 명세는 가능한 한 원본을 유지한다."""
    motion = {"status": "queued"}
    now = datetime.datetime.now().astimezone()
    canned = {
        "camera": {"status": "ok", "description": "책상 위에 노트북과 컵이 보입니다."},
        "detect_face": {"status": "ok", "faces": 1},
        "detect_objects": {"status": "ok", "objects": ["cup", "laptop"]},
        "play_emotion": motion,
        "dance": motion,
    }
    stubs: list[Tool] = [
        StubTool(
            name,
            profile_tools[name].description,
            profile_tools[name].parameters_schema["properties"],
            result,
        )
        for name, result in canned.items()
        if name in profile_tools
    ]
    stubs.append(
        StubTool(
            "pollen_robotics_reachy_mini_weather_tool__get_weather",
            "Get the current weather for a location.",
            {"location": {"type": "string", "description": "City name"}},
            {"location": "서울", "condition": "맑음", "temperature_c": 12},
        )
    )
    stubs.append(
        StubTool(
            "pollen_robotics_reachy_mini_time_tool__get_time",
            "Get the current local date and time.",
            {"timezone": {"type": "string", "description": "IANA timezone"}},
            {"time": now.strftime("%H:%M"), "date": now.date().isoformat(), "weekday": now.strftime("%A")},
        )
    )
    return stubs


@contextlib.contextmanager
def isolated_app(*, api_key: str | None = None) -> Iterator[tuple[Path, FakeCalendar]]:
    """프로필, 도구 레지스트리, 캘린더를 격리하고 끝나면 전부 되돌린다."""
    saved_env = os.environ.get("REACHY_MINI_CUSTOM_PROFILE")
    saved_profile = config.REACHY_MINI_CUSTOM_PROFILE
    saved_key = config.GEMINI_API_KEY
    saved_calendar = GoogleCalendarService._instance
    saved_registry = (core_tools.ALL_TOOLS, core_tools._TOOLS_SIGNATURE, core_tools._TOOLS_INSTANCE_PATH)

    with tempfile.TemporaryDirectory(prefix="deskmate-scenario-") as tmp:
        instance_path = Path(tmp)
        calendar = FakeCalendar()
        GoogleCalendarService._instance = calendar
        set_custom_profile(PROFILE)
        if api_key is not None:
            config.GEMINI_API_KEY = api_key
        try:
            core_tools.initialize_tools(instance_path=instance_path, force=True)
            core_tools.ALL_TOOLS.update({tool.name: tool for tool in _stub_tools(core_tools.ALL_TOOLS)})
            yield instance_path, calendar
        finally:
            core_tools.ALL_TOOLS, core_tools._TOOLS_SIGNATURE, core_tools._TOOLS_INSTANCE_PATH = saved_registry
            GoogleCalendarService._instance = saved_calendar
            config.GEMINI_API_KEY = saved_key
            set_custom_profile(saved_profile)
            if saved_env is None:
                os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)


class HandlerSpy:
    """핸들러 내부를 바꾸지 않고 수신 메시지와 최종 자막을 기록한다."""

    def __init__(self, handler: GeminiRealtimeHandler, *, trace: bool = False) -> None:
        """핸들러에 관측 지점을 연결한다."""
        self.handler = handler
        self.obs = Observation()
        self._trace = trace
        self._audio_since_turn = 0
        self._flushing_cut_turn = False
        self._process_original = handler._process_message
        handler._process_message = self._process  # type: ignore[method-assign]
        handler._clear_queue = lambda: None
        handler.set_transcript_observer(self._on_transcript)

    def begin_step(self) -> Observation:
        """새 step의 관측을 시작한다."""
        self.obs = Observation()
        self._audio_since_turn = 0
        return self.obs

    def _on_transcript(self, role: str, text: str, final: bool) -> None:
        if role == "assistant" and final:
            (self.obs.interrupted_replies if self._flushing_cut_turn else self.obs.replies).append(text)

    def _note(self, text: str) -> None:
        if self._trace:
            self.obs.trace.append(text)

    async def _process(self, message: Any) -> None:
        obs = self.obs
        if message.data:
            obs.audio_bytes += len(message.data)
            self._audio_since_turn += len(message.data)
        if message.tool_call:
            for call in message.tool_call.function_calls or []:
                obs.tool_calls.append((call.name or "", dict(call.args or {})))
                self._note(f"tool_call {call.name} {dict(call.args or {})}")
        if message.tool_call_cancellation:
            self._note(f"tool_call_cancellation {message.tool_call_cancellation.ids}")
        content = message.server_content
        # 이번 턴에 오디오 없이 turn_complete가 오면, 쌓여 있던 자막은 끊긴 이전 답변이다.
        self._flushing_cut_turn = bool(content and content.turn_complete and not self._audio_since_turn)
        await self._process_original(message)
        self._flushing_cut_turn = False
        if content is None:
            return
        if content.interrupted:
            obs.interrupted = True
            obs.audio_left_after_interrupt = _queue_has_audio(self.handler)
            self._audio_since_turn = 0
            self._note("interrupted")
        if content.turn_complete:
            self._note(f"turn_complete audio={self._audio_since_turn} cut={obs.interrupted_replies}")
            if self._audio_since_turn:
                obs.turns_done += 1
            self._audio_since_turn = 0


def _queue_has_audio(handler: GeminiRealtimeHandler) -> bool:
    queued = list(handler.output_queue._queue)  # type: ignore[attr-defined]
    return any(isinstance(item, tuple) for item in queued)


def make_handler(instance_path: Path) -> tuple[GeminiRealtimeHandler, ToolDependencies]:
    """Mock 로봇을 연결한 실제 핸들러를 만든다."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), instance_path=instance_path)
    handler = GeminiRealtimeHandler(deps, instance_path=str(instance_path))
    # main.py go_to_sleep_action의 로컬 standby 경로와 같은 호출.
    deps.go_to_sleep = handler.request_local_standby
    return handler, deps


def inject_faults(fail: tuple[str, ...], slow: tuple[str, ...]) -> None:
    """지정한 도구를 실패하거나 멈추는 대역으로 바꾼다."""
    for name, hang in [*((n, False) for n in fail), *((n, True) for n in slow)]:
        core_tools.ALL_TOOLS[name] = FaultTool(core_tools.ALL_TOOLS[name], hang=hang)
