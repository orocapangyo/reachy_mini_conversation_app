"""Gemini Live API 도구(함수) 호출 동작 실측 스파이크.

검증 항목:
  1. 단일 도구 호출: get_time 등록 → "지금 몇 시야?" → tool_call 수신 여부,
     FunctionCall.id 채움 여부, FunctionResponse 전송 후 오디오 재개 여부
  2. 병렬 도구 호출: get_time + get_weather 등록 → "지금 시간이랑 서울 날씨 알려줘"
     (a) function_calls 묶임 여부, (b) 첫 응답만 보내면 모델 대기 여부,
     (c) 두 응답 한 번에 보내면 정상 동작 여부
  3. context_window_compression 빈 설정 연결 성립 여부

사전 준비:
  .env 에 GEMINI_API_KEY=... 추가

실행:
  .venv/bin/python scripts/gemini_tool_spike.py
"""

import os
import sys
import asyncio
import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types, errors


load_dotenv()

LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
MAX_RECEIVE_SECONDS = 15
RETRY_WAIT_SECONDS = 20
MAX_PROMPT_RETRIES = 3

# ---------------------------------------------------------------------------
# 가상 도구 구현
# ---------------------------------------------------------------------------

GET_TIME_DECL = types.FunctionDeclaration(
    name="get_time",
    description="현재 로컬 시각을 HH:MM 형식으로 반환한다.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

GET_WEATHER_DECL = types.FunctionDeclaration(
    name="get_weather",
    description="주어진 도시의 현재 날씨 요약을 반환한다.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "city": types.Schema(type=types.Type.STRING, description="도시 이름 (예: 서울)"),
        },
        required=["city"],
    ),
)


def _call_get_time() -> dict[str, str]:
    now = datetime.datetime.now().strftime("%H:%M")
    return {"time": now}


def _call_get_weather(city: str) -> dict[str, str]:
    # 실제 날씨 API 없이 더미 데이터 반환
    return {"city": city, "condition": "맑음", "temperature_c": "18"}


def _dispatch(name: str, args: dict) -> dict:  # type: ignore[type-arg]
    if name == "get_time":
        return _call_get_time()
    if name == "get_weather":
        return _call_get_weather(args.get("city", "서울"))
    return {"error": f"알 수 없는 도구: {name}"}


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _base_config(*decls: types.FunctionDeclaration) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tools=[types.Tool(function_declarations=list(decls))],
    )


async def _receive_until_done(
    session: genai.live.AsyncSession,
    timeout: float = MAX_RECEIVE_SECONDS,
) -> list[types.LiveServerMessage]:
    """turn_complete 또는 타임아웃까지 메시지를 수집한다."""
    collected: list[types.LiveServerMessage] = []
    try:
        async with asyncio.timeout(timeout):
            async for msg in session.receive():
                collected.append(msg)
                sc = msg.server_content
                if sc and sc.turn_complete:
                    break
    except TimeoutError:
        pass
    return collected


def _extract_transcripts(messages: list[types.LiveServerMessage]) -> str:
    parts: list[str] = []
    for msg in messages:
        sc = msg.server_content
        if sc and sc.output_transcription and sc.output_transcription.text:
            parts.append(sc.output_transcription.text)
    return "".join(parts).strip()


def _count_audio_bytes(messages: list[types.LiveServerMessage]) -> int:
    return sum(len(msg.data) for msg in messages if msg.data)


# ---------------------------------------------------------------------------
# 검증 1: 단일 도구 호출
# ---------------------------------------------------------------------------


async def check_single_tool(client: genai.Client) -> None:
    """단일 도구(get_time) 호출 흐름을 실측한다."""
    print("\n[1] 단일 도구 호출 검증 시작")
    config = _base_config(GET_TIME_DECL)

    prompts = [
        "get_time 도구를 사용해서 현재 시각을 알려줘.",
        "현재 로컬 시각이 몇 시인지 get_time 함수를 호출해서 답해줘.",
        "지금 몇 시야? 반드시 get_time 함수를 써서 대답해.",
    ]

    for attempt, prompt in enumerate(prompts, 1):
        print(f"  시도 {attempt}: 프롬프트='{prompt}'")
        try:
            async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                await session.send_client_content(
                    turns={"role": "user", "parts": [{"text": prompt}]},
                    turn_complete=True,
                )

                tool_call_msg: types.LiveServerMessage | None = None
                pre_tool_msgs: list[types.LiveServerMessage] = []

                # 도구 호출 메시지 탐색
                try:
                    async with asyncio.timeout(MAX_RECEIVE_SECONDS):
                        async for msg in session.receive():
                            if msg.tool_call:
                                tool_call_msg = msg
                                break
                            pre_tool_msgs.append(msg)
                            sc = msg.server_content
                            if sc and sc.turn_complete:
                                break
                except TimeoutError:
                    pass

                if tool_call_msg is None:
                    # 도구 안 불렀거나 turn_complete 먼저 옴
                    transcript = _extract_transcripts(pre_tool_msgs)
                    print(f"    -> 도구 호출 없음. 전사='{transcript[:60]}'")
                    if attempt < len(prompts):
                        continue
                    print("    [1] 결론: 모델이 도구를 호출하지 않음 (프롬프트 재시도 소진)")
                    return

                # 도구 호출 구조 기록
                fc_list = tool_call_msg.tool_call.function_calls  # type: ignore[union-attr]
                print(f"    -> tool_call 수신: function_calls 수={len(fc_list)}")
                for fc in fc_list:
                    print(f"       name={fc.name!r}, id={fc.id!r}, args={dict(fc.args)}")

                # FunctionResponse 전송
                responses = [
                    types.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response=_dispatch(fc.name, dict(fc.args)),
                    )
                    for fc in fc_list
                ]
                await session.send_tool_response(function_responses=responses)

                # 이후 오디오/전사 수집
                post_msgs = await _receive_until_done(session)
                audio_bytes = _count_audio_bytes(post_msgs)
                transcript = _extract_transcripts(post_msgs)

                id_filled = all(fc.id for fc in fc_list)
                print(f"    -> FunctionCall.id 채움 여부: {id_filled}")
                print(f"    -> id 값: {[fc.id for fc in fc_list]}")
                print(f"    -> FunctionResponse 후 오디오: {audio_bytes:,} bytes")
                print(f"    -> 전사: '{transcript[:80]}'")
                print(
                    f"  [1] 결론: tool_call={'있음' if tool_call_msg else '없음'}, "
                    f"id={'채움' if id_filled else '미채움'}, "
                    f"응답 오디오={'있음' if audio_bytes > 0 else '없음'}"
                )
                return

        except errors.ServerError as e:
            if "503" in str(e.code) or "UNAVAILABLE" in str(e):
                print(f"    503 UNAVAILABLE — {RETRY_WAIT_SECONDS}초 후 재시도")
                await asyncio.sleep(RETRY_WAIT_SECONDS)
                continue
            raise


# ---------------------------------------------------------------------------
# 검증 2: 병렬 도구 호출
# ---------------------------------------------------------------------------


async def check_parallel_tools(client: genai.Client) -> None:
    """병렬 도구(get_time + get_weather) 호출 흐름을 실측한다."""
    print("\n[2] 병렬 도구 호출 검증 시작")
    config = _base_config(GET_TIME_DECL, GET_WEATHER_DECL)

    prompts = [
        "get_time과 get_weather 두 도구를 모두 써서, 현재 시각과 서울 날씨를 함께 알려줘.",
        "지금 시간이랑 서울 날씨 알려줘. get_time, get_weather 함수 둘 다 호출해서 답해.",
        "현재 시각(get_time)과 서울 날씨(get_weather) 두 가지를 반드시 함께 조회해서 알려줘.",
    ]

    for attempt, prompt in enumerate(prompts, 1):
        print(f"  시도 {attempt}: 프롬프트='{prompt}'")
        try:
            async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                await session.send_client_content(
                    turns={"role": "user", "parts": [{"text": prompt}]},
                    turn_complete=True,
                )

                # (a) 묶임 여부: tool_call 메시지 수집
                tool_call_msgs: list[types.LiveServerMessage] = []
                try:
                    async with asyncio.timeout(MAX_RECEIVE_SECONDS):
                        async for msg in session.receive():
                            if msg.tool_call:
                                tool_call_msgs.append(msg)
                                # 잠깐 더 기다려 추가 tool_call이 오는지 확인
                                try:
                                    async with asyncio.timeout(2.0):
                                        async for msg2 in session.receive():
                                            if msg2.tool_call:
                                                tool_call_msgs.append(msg2)
                                            else:
                                                # tool_call 아닌 메시지면 중단
                                                break
                                except TimeoutError:
                                    pass
                                break
                            sc = msg.server_content
                            if sc and sc.turn_complete:
                                break
                except TimeoutError:
                    pass

                if not tool_call_msgs:
                    print("    -> 도구 호출 없음")
                    if attempt < len(prompts):
                        continue
                    print("  [2] 결론: 모델이 도구를 호출하지 않음 (재시도 소진)")
                    return

                # 구조 기록
                all_fcs: list[tuple[str, types.FunctionCall]] = []  # (msg_idx, fc)
                for i, tm in enumerate(tool_call_msgs):
                    fc_list = tm.tool_call.function_calls  # type: ignore[union-attr]
                    print(f"    -> tool_call 메시지[{i}]: function_calls 수={len(fc_list)}")
                    for fc in fc_list:
                        print(f"       name={fc.name!r}, id={fc.id!r}")
                        all_fcs.append((f"msg{i}", fc))

                bundled = len(tool_call_msgs) == 1 and len(tool_call_msgs[0].tool_call.function_calls) > 1  # type: ignore[union-attr]
                print(f"    (a) 한 메시지에 묶여 옴: {bundled}")

                # (b) 첫 번째 FunctionResponse만 전송 후 2초 대기
                first_fc = all_fcs[0][1]
                await session.send_tool_response(
                    function_responses=[
                        types.FunctionResponse(
                            id=first_fc.id,
                            name=first_fc.name,
                            response=_dispatch(first_fc.name, dict(first_fc.args)),
                        )
                    ]
                )
                print(f"    -> 첫 번째 FunctionResponse ({first_fc.name}) 전송 완료")

                partial_msgs: list[types.LiveServerMessage] = []
                resumed_after_first = False
                try:
                    async with asyncio.timeout(2.0):
                        async for msg in session.receive():
                            partial_msgs.append(msg)
                            if msg.data and len(msg.data) > 0:
                                resumed_after_first = True
                            sc = msg.server_content
                            if sc and sc.turn_complete:
                                break
                except TimeoutError:
                    pass

                audio_after_first = _count_audio_bytes(partial_msgs)
                print(
                    f"    (b) 첫 응답만 보낸 후 2초: 오디오={audio_after_first:,} bytes, "
                    f"모델 재개={'예' if resumed_after_first else '아니오 (두 번째 응답 대기 중으로 보임)'}"
                )

                # (c) 나머지 응답을 한 번에 전송
                remaining_fcs = [fc for _, fc in all_fcs[1:]]
                if remaining_fcs:
                    remaining_responses = [
                        types.FunctionResponse(
                            id=fc.id,
                            name=fc.name,
                            response=_dispatch(fc.name, dict(fc.args)),
                        )
                        for fc in remaining_fcs
                    ]
                    await session.send_tool_response(function_responses=remaining_responses)
                    print(f"    -> 나머지 FunctionResponse ({[fc.name for fc in remaining_fcs]}) 전송 완료")

                post_msgs = await _receive_until_done(session)
                audio_after_all = _count_audio_bytes(post_msgs)
                transcript = _extract_transcripts(post_msgs)
                print(f"    (c) 모든 응답 후 오디오: {audio_after_all:,} bytes")
                print(f"    -> 전사: '{transcript[:80]}'")
                print(
                    f"  [2] 결론: 묶임={'예' if bundled else '아니오(분리)'}, "
                    f"첫 응답 후 재개={'예' if resumed_after_first else '아니오'}, "
                    f"전체 응답 후 정상={'예' if audio_after_all > 0 else '아니오'}"
                )
                return

        except errors.ServerError as e:
            if "503" in str(e.code) or "UNAVAILABLE" in str(e):
                print(f"    503 UNAVAILABLE — {RETRY_WAIT_SECONDS}초 후 재시도")
                await asyncio.sleep(RETRY_WAIT_SECONDS)
                continue
            raise


# ---------------------------------------------------------------------------
# 검증 3: context_window_compression
# ---------------------------------------------------------------------------


async def check_context_window_compression(client: genai.Client) -> None:
    """context_window_compression 설정 별 연결 성립 여부를 실측한다."""
    print("\n[3] context_window_compression 연결 성립 여부 검증")

    # 3a: 빈 ContextWindowCompressionConfig
    try:
        config_a = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(),
        )
        async with client.aio.live.connect(model=LIVE_MODEL, config=config_a) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": "안녕."}]},
                turn_complete=True,
            )
            msgs = await _receive_until_done(session, timeout=10.0)
            audio = _count_audio_bytes(msgs)
            print(f"  (3a) 빈 ContextWindowCompressionConfig: 연결 성립=예, 오디오={audio:,} bytes")
    except Exception as e:
        print(f"  (3a) 빈 ContextWindowCompressionConfig: 연결 실패 — {type(e).__name__}: {e}")

    # 3b: sliding_window 명시
    try:
        config_b = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
        )
        async with client.aio.live.connect(model=LIVE_MODEL, config=config_b) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": "안녕."}]},
                turn_complete=True,
            )
            msgs = await _receive_until_done(session, timeout=10.0)
            audio = _count_audio_bytes(msgs)
            print(f"  (3b) SlidingWindow 명시: 연결 성립=예, 오디오={audio:,} bytes")
    except AttributeError as e:
        print(f"  (3b) SlidingWindow: SDK에 해당 타입 없음 — {e}")
    except Exception as e:
        print(f"  (3b) SlidingWindow: 연결 실패 — {type(e).__name__}: {e}")

    # 3c: compression 없는 기본 설정 대조군
    try:
        config_c = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        async with client.aio.live.connect(model=LIVE_MODEL, config=config_c) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": "안녕."}]},
                turn_complete=True,
            )
            msgs = await _receive_until_done(session, timeout=10.0)
            audio = _count_audio_bytes(msgs)
            print(f"  (3c) 기본(compression 없음) 대조군: 연결 성립=예, 오디오={audio:,} bytes")
    except Exception as e:
        print(f"  (3c) 기본 대조군: 실패 — {type(e).__name__}: {e}")

    print("  [3] context_window_compression 검증 완료")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def async_main() -> int:
    """모든 검증 항목을 순서대로 실행한다."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY 없음. .env에 추가하세요.")
        return 1

    client = genai.Client(api_key=api_key)

    print(f"=== Gemini Live API 도구 호출 스파이크 (모델: {LIVE_MODEL}) ===")
    await check_single_tool(client)
    await check_parallel_tools(client)
    await check_context_window_compression(client)
    print("\n=== 스파이크 완료 ===")
    return 0


def main() -> int:
    """스크립트 진입점."""
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
