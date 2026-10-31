# Gemini Live (gemini-3.1-flash-live-preview) 변경 내역 정리

OpenAI `gpt-realtime-mini` 기반으로 동작하던 VAD, STT, TTS, 툴 콜링(Tool Calling) 파이프라인을 Google Gemini Multimodal Live API (`gemini-3.1-flash-live-preview`)로 확장 및 전환하였습니다.

---

## 1. 변경 및 생성된 파일 목록

| 파일 경로 | 작업 유형 | 주요 내용 |
| :--- | :--- | :--- |
| `src/reachy_mini_conversation_app/gemini_realtime.py` | **신규 생성** | Gemini Live WebSocket (`BidiGenerateContent`) 핸들러 구현 (VAD, 16kHz 마이크 스트리밍, 24kHz 오디오 재생 및 리샘플링, 실시간 STT/TTS 전사 처리, 툴 콜링 및 카메라 이미지 전송, 슬립/웨이크 스탠바이 모드) |
| `src/reachy_mini_conversation_app/openai_realtime.py` | **수정** | `resample_pcm`에서 3샘플 미만(예: 1~2샘플)의 초소형 오디오 패킷 수신 시 `np.convolve` 크기 불일치로 인한 `ValueError: fp and xp are not of the same length` 에러 수정 |
| `src/reachy_mini_conversation_app/config.py` | **수정** | `GEMINI_BACKEND`, `GEMINI_LIVE_URL`, `DEFAULT_GEMINI_LIVE_MODEL`, `DEFAULT_GEMINI_VOICE` 상수 추가, `_normalize_conversation_backend` 및 `Config` 클래스 / 런타임 환경변수 동기화에 Gemini 항목 추가 |
| `src/reachy_mini_conversation_app/main.py` | **수정** | `CONVERSATION_BACKEND == GEMINI_BACKEND` 시 `GEMINI_API_KEY` 유효성 검사 및 `GeminiRealtimeHandler` 생성 연결 |
| `.env` | **수정** | `CONVERSATION_BACKEND=gemini`, `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL="gemini-3.1-flash-live-preview"`, `GEMINI_VOICE="Kore"` 기본 적용 |
| `.env.example` | **수정** | Gemini Live 백엔드 설정 가이드 및 파라미터 예시 추가 (보안을 위해 시크릿 키는 제외) |
| `tests/test_gemini_realtime.py` | **신규 생성** | Gemini 백엔드 단위 테스트 21개 구현 (환경변수 정규화, 음성 카탈로그, 마이크 16kHz 다운/업샘플링, 모델 24kHz 재생 리샘플링, VAD 중단 처리, 툴 콜링 및 결과 전달) |
| `tests/test_openai_realtime.py` | **수정** | `resample_pcm`의 초소형 프레임(1~4샘플) 리샘플링 회귀 방지 단위 테스트 추가 |
| `README.md` | **수정** | Gemini Live 백엔드 지원 내용 및 .env 설정 테이블 업데이트 |

---

## 2. 파일별 상세 변경 내용

### (1) `src/reachy_mini_conversation_app/gemini_realtime.py` (신규)
- **엔드포인트 연동**: `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}`
- **모델 설정**: 기본값 `"gemini-3.1-flash-live-preview"` (`models/gemini-3.1-flash-live-preview`)
- **오디오 포맷**:
  - 마이크 입력: 16 kHz mono PCM16 (`audio/pcm;rate=16000`)
  - 모델 출력 음성: 24 kHz mono PCM16 (`audio/pcm;rate=24000`)
  - 출력 디바이스 주파수(예: 16 kHz)에 맞춰 `resample_pcm`으로 자동 리샘플링
- **양방향 VAD 및 인터럽트(Barge-in)**:
  - Gemini 서버 내장 VAD를 통해 사용자가 발화를 시작하면 서버로부터 `interrupted: True` 수신
  - 재생 큐의 오디오 버퍼를 즉시 비워 말 끊김 및 즉각적인 청취 상태 전환 보장
- **실시간 전사(STT / TTS)**:
  - `serverContent.outputTranscription`: 어시스턴트 모델 발화 실시간 텍스트 수신
  - `serverContent.inputTranscription`: 사용자 발화 실시간 전사 수신
- **툴 콜링 (Function Calling)**:
  - 세션 초기화(`setup`) 시 Reachy Mini의 17개 툴 스펙을 `functionDeclarations`로 자동 등록
  - 모델이 `toolCall` 메시지를 보내면 `BackgroundToolManager`를 통해 비동기 실행
  - 카메라 툴(`camera`) 호출 시 촬영된 JPEG 이미지를 `realtimeInput.video`로 전송하여 시각 인식 지원
  - 툴 실행 결과를 `toolResponse.functionResponses`로 전달하여 모델이 자연스럽게 음성 응답을 이어가도록 처리
- **음성 카탈로그**:
  - `Aoede`, `Charon`, `Fenrir`, `Kore`, `Puck`, `Leda`, `Orus`, `Zephyr` (기본값: `Kore`)
  - 런타임 음성 변경(`change_voice`) 지원
- **스탠바이 / 웨이크 루프**:
  - "종료해 줘" → `go_to_sleep` 취침 포즈 및 스탠바이 모드 진입
  - "깨어나", "리치미니", "일어나" 웨이크 워드 수신 시 기상 모션 + DoA(음원 방향 감지) 시선 추적 + 인사말 재생

### (2) `src/reachy_mini_conversation_app/config.py`
- `GEMINI_BACKEND = "gemini"` 추가
- `GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"` 추가
- `DEFAULT_GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"` 추가
- `DEFAULT_GEMINI_VOICE = "Kore"` 추가
- `_normalize_conversation_backend()`: `"gemini"`, `"google"`을 인식하여 `GEMINI_BACKEND` 반환하도록 확장
- `Config` 클래스 및 `refresh_runtime_config_from_env()`에 `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL`, `GEMINI_VOICE` 바인딩

### (3) `src/reachy_mini_conversation_app/main.py`
- `GEMINI_BACKEND` import 및 startup 시 API 키 유효성 검사 분기 추가
- `build_handler()`에서 `config.CONVERSATION_BACKEND == GEMINI_BACKEND`일 때 `GeminiRealtimeHandler`를 인스턴스화하여 반환

### (4) `.env` 및 `.env.example`
- `.env`:
  ```env
  CONVERSATION_BACKEND=gemini
  GEMINI_API_KEY="your_gemini_api_key_here"
  GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
  GEMINI_VOICE=Kore
  ```
- `.env.example`: Gemini 백엔드 환경변수 항목 및 설명 추가

### (5) `tests/test_gemini_realtime.py`
- 환경변수 및 백엔드 식별자 정규화 테스트
- 보이스 카탈로그 검증 및 대소문자 정규화 테스트
- 마이크 입력 48kHz → 16kHz 모노 리샘플링 검증
- 모델 출력 24kHz → 디바이스 재생 주파수(16kHz) 리샘플링 검증
- `toolCall` 수신 및 비동기 실행 검증
- 사용자 끼어들기(`interrupted`) 시 오디오 버퍼 드레인 검증
- 툴 실행 완료 후 `toolResponse` 전송 검증
- `change_voice` 및 `say` 기능 검증

---

## 3. 검증 결과

- **Linter & Formatter**: `ruff check .` 및 `ruff format .` 통과 (0 errors)
- **Type Checker**: `mypy --strict src/reachy_mini_conversation_app/gemini_realtime.py` 통과 (0 errors)
- **Unit Tests**: `pytest tests/test_gemini_realtime.py tests/test_openai_realtime.py` 실행 결과 51개 테스트 전체 통과 (100% Pass)
- **Live Connection Test**: 실제 Google Gemini Live WebSocket 엔드포인트에 `gemini-3.1-flash-live-preview` 모델로 연결하여 `setupComplete`, 실시간 16kHz 오디오 입력, 24kHz 오디오 출력, `toolCall` 및 `toolResponse` 왕복 테스트 성공
