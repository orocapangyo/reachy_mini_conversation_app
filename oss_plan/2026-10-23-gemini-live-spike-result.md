# Gemini Live API 스파이크 결과 (2026-10-23)

주간 목표 "개발 환경·기본 연동 구성 / 대표 요청의 응답·오류 처리 테스트 / 결과 문서화"에 대한
검증 결과. 검증 스크립트는 `scripts/gemini_live_spike.py`
(실행: `.venv/bin/python scripts/gemini_live_spike.py`).

## 검증 결과 — 3항목 전부 통과

| 항목 | 방법 | 결과 |
| :--- | :--- | :--- |
| 1. 텍스트 요청·응답 | `generate_content` (gemini-3.8-flash) | OK — 한국어 응답 수신 |
| 2. Live API 세션 | `aio.live.connect` (gemini-3.8-live), AUDIO 모달리티 | OK — 오디오 98KB + 전사 "안녕하세요! 반갑습니다." 수신 |
| 3. 오류 처리 | 존재하지 않는 모델명 요청 | OK — `errors.APIError`(ClientError, code=400)로 구조적으로 잡힘 |

## 진행 중 발견한 사실 (백엔드 구현 시 반드시 반영)

### 1. gemini-2.5 계열은 신규 발급 API 키에서 차단됨

- `gemini-2.5-flash` 요청 시 404: *"no longer available to new users. Please update
  your code to use models/gemini-3.8-flash"*.
- `models.list()` 목록에는 2.5 계열이 나오지만 실제 호출은 거부된다. **목록에 있다고
  쓸 수 있는 게 아니므로** 모델명은 반드시 실호출로 검증할 것.
- 현행 기본값: 텍스트 `gemini-3.8-flash`, Live `gemini-3.8-live` (`.env`의
  `GEMINI_TEXT_MODEL` / `GEMINI_LIVE_MODEL`로 오버라이드 가능).

### 2. gemini-3.8-live는 오디오 전용

- `response_modalities: ["TEXT"]`로 연결하면 WebSocket 1007로 세션이 끊긴다
  (*"The requested combination of response modalities (TEXT) is not supported"*).
- `["AUDIO"]` + `output_audio_transcription: {}` 조합으로 오디오와 텍스트 전사를
  동시에 받을 수 있다 → 앱의 대화 로그·자막 표시에 그대로 쓸 수 있음.
- 출력 오디오는 24kHz PCM (Live API 표준). 입력은 16kHz PCM.

### 3. 일시적 503 (high demand)이 실제로 발생함

- 스파이크 중 `gemini-3.8-flash`에서 503 UNAVAILABLE을 1회 수신, 20초 후 재시도로
  성공. `gemini_realtime` 백엔드는 **세션 수립·재연결 경로에 지수 백오프 재시도**를
  넣어야 한다 (google-genai SDK 자체 재시도(tenacity)로는 부족했음).

### 4. Live API 오류는 두 경로로 나뉨

- REST(`generate_content`) 오류: `google.genai.errors.APIError` 예외.
- Live 세션 중 오류: WebSocket close(`ConnectionClosedError`)로 전파된 뒤 SDK가
  `APIError`로 재포장. 세션 루프에서 두 경우 모두 잡아야 대화 루프가 죽지 않는다.

## 다음 단계

1. ~~스파이크 3종 검증~~ — 완료 (이 문서).
2. scipy 의존성 누락 수정 PR (`huggingface_realtime.py`가 import하는데
   `pyproject.toml`에 없음 → pip 설치 시 기동 불가). 한 줄 수정.
3. `gemini_realtime.py` 백엔드 구현 — `openai_realtime.py` 패턴 참고,
   `config.py`의 `CONVERSATION_BACKEND`로 선택. 착수 전 동훈님과 분담 범위 확인.
