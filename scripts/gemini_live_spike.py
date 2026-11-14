"""Gemini 연동 스파이크 — 앱에 붙이기 전 단독 검증 스크립트.

주간 목표의 "기본 요청·응답 흐름 구현 및 테스트 / 대표 요청의 응답과 오류 처리 테스트"를
앱 코드 수정 없이 먼저 확인한다.

검증 항목:
  1. 텍스트 요청·응답 (generate_content) — API 키·네트워크·모델 접근 확인
  2. Live API 세션 연결 (실시간 대화의 기반) — 텍스트 왕복으로 세션 수립 확인
  3. 오류 처리 — 잘못된 모델명 요청 시 에러가 구조적으로 잡히는지 확인

사전 준비:
  .env 에 GEMINI_API_KEY=... 추가 (https://aistudio.google.com/apikey 에서 무료 발급)

실행:
  .venv/bin/python scripts/gemini_live_spike.py
"""

import os
import sys
import asyncio

from dotenv import load_dotenv
from google import genai
from google.genai import types, errors


load_dotenv()

# gemini-2.5 계열은 신규 발급 키에서 차단됨 (404: no longer available to new users)
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.8-flash")
# Live API(실시간 음성)용 모델 — bidiGenerateContent 지원 모델이어야 함
LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")


def check_text_roundtrip(client: genai.Client) -> None:
    """1. 기본 텍스트 요청·응답."""
    resp = client.models.generate_content(
        model=TEXT_MODEL,
        contents="한국어로 한 문장으로 자기소개 해줘.",
    )
    text = resp.text or ""
    print(f"[1] 텍스트 응답 OK: {text.strip()[:80]}")


async def check_live_session(client: genai.Client) -> None:
    """2. Live API 세션 수립 + 오디오 응답 왕복.

    gemini-3.8-live는 오디오 전용(TEXT 모달리티 미지원)이므로
    AUDIO 응답 + 출력 트랜스크립션으로 검증한다. 실제 앱도 음성 경로라 이쪽이 맞다.
    """
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": "안녕! 짧게 인사해줘."}]},
            turn_complete=True,
        )
        audio_bytes = 0
        transcript: list[str] = []
        async for message in session.receive():
            sc = message.server_content
            if message.data:
                audio_bytes += len(message.data)
            if sc and sc.output_transcription and sc.output_transcription.text:
                transcript.append(sc.output_transcription.text)
            if sc and sc.turn_complete:
                break
        print(f'[2] Live 세션 OK: 오디오 {audio_bytes:,} bytes, 전사="{("".join(transcript)).strip()[:60]}"')


def check_error_handling(client: genai.Client) -> None:
    """3. 오류가 예외로 구조적으로 잡히는지 확인."""
    try:
        client.models.generate_content(model="없는-모델", contents="hi")
    except errors.APIError as e:
        print(f"[3] 오류 처리 OK: code={e.code} ({type(e).__name__})")
        return
    print("[3] 경고: 잘못된 모델명인데 오류가 발생하지 않음")


def main() -> int:
    """Run all three spike checks; return a process exit code."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY가 없습니다. .env에 추가하세요 (https://aistudio.google.com/apikey)")
        return 1

    client = genai.Client(api_key=api_key)
    check_text_roundtrip(client)
    asyncio.run(check_live_session(client))
    check_error_handling(client)
    print("\n스파이크 전 항목 통과 — 앱 백엔드(gemini_realtime) 구현 진행 가능")
    return 0


if __name__ == "__main__":
    sys.exit(main())
