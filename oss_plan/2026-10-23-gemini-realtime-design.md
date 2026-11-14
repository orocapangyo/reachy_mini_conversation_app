# Gemini Live 대화 백엔드 — 설계 문서

- **작성일**: 2026-10-23
- **상태**: critic 리뷰(REVISE) 지적 반영됨 — 구현 착수 가능, 미검증 가정 2건은 구현 중 확인
  (병렬 도구 호출 재개 동작, context_window_compression 파라미터)
- **선행 검증**: [`2026-10-23-gemini-live-spike-result.md`](2026-10-23-gemini-live-spike-result.md)

## 배경과 핵심 제약

기존 백엔드 2종(hf / openai)은 **둘 다 OpenAI Realtime 프로토콜**이다.
`OpenAIRealtimeHandler`가 `HuggingFaceRealtimeHandler`의 얇은 서브클래스일 수 있는 이유가
그것이고, Gemini Live는 프로토콜(google-genai SDK, 이벤트 구조, 도구 호출 방식)이 전부
다르므로 **이 상속 체인을 타지 못한다**. 따라서 `conversation_handler.py`의
`ConversationHandler` ABC를 직접 구현한다. HF 핸들러가 가진 세션 루프·도구 배관을
Gemini 프로토콜로 새로 작성하되, 프로토콜 비의존 부품은 최대한 재사용한다.

### 재사용하는 기존 부품

| 부품 | 출처 | 용도 |
| :--- | :--- | :--- |
| `emit()` 큐·유휴 행동 | `ConversationHandler` 베이스 | `super().emit()` 호출 위에 장치 레이트 리샘플만 추가 (OpenAI 핸들러 `emit()` 패턴) |
| `BackgroundToolManager` / `ToolCallRoutine` | `tools/background_tool_manager.py` | 도구 비동기 실행 |
| `get_tool_specs()` / `get_tools()` | `tools/core_tools.py` | 도구 카탈로그 |
| `resample_pcm()` | `openai_realtime.py` | 16k↔24k↔장치 레이트 변환 |
| `get_session_instructions/greeting_prompt/voice` | `prompts.py` | 페르소나·인사 |
| `audio_to_int16` / `normalize_audio_frame` / `AdditionalOutputs` | `streaming.py` | 프레임 정규화·UI 출력 |

**재사용하지 않는 것**: `is_hallucinated_transcript` — Whisper 전용 환청 패턴(한국어 뉴스
클로징 등)이라 Gemini 자체 STT에는 부적합. 오탐 시 정상 발화를 버릴 위험이 있어 미적용.
Gemini 쪽 환청 패턴은 실사용에서 발견되면 별도 목록으로 추가.

## 아키텍처

### 신규 파일: `gemini_realtime.py`

`GeminiRealtimeHandler(ConversationHandler)` — 추상 메서드 9개 전부 구현.

```
마이크 → receive(): mono화 → 16kHz 리샘플 → session.send_realtime_input(audio=Blob)
Gemini → 수신 루프(_run_live_session):
  message.data(24kHz PCM)            → output_queue에 (24000, ndarray) **그대로** 적재
                                        (리샘플은 emit() 오버라이드에서만 — 이중 리샘플 금지)
  server_content.input_transcription  → 사용자 전사 → _emit_transcript("user", ...)
  server_content.output_transcription → 어시스턴트 전사 (턴 단위 누적 → turn_complete에 확정)
  server_content.interrupted         → 출력 큐 비우기 + self._clear_queue() + set_speaking(False)  [끼어들기]
  server_content.turn_complete       → set_speaking(False), 응답 종료 처리
  message.tool_call.function_calls   → BackgroundToolManager.start_tool(...)
```

### 세션 설정 (`LiveConnectConfig`)

스파이크에서 검증된 조합 그대로:

- `response_modalities=["AUDIO"]` — **3.8-live는 TEXT 미지원** (TEXT 지정 시 WS 1007)
- `output_audio_transcription={}` + `input_audio_transcription={}` — 대화 로그·자막
- `system_instruction` = `get_session_instructions(instance_path)` (페르소나)
- `speech_config` = 프리빌트 보이스 (아래 보이스 절)
- `tools` = ToolSpec → `FunctionDeclaration` 변환 결과
- VAD는 Gemini Live 기본 자동 감지(automatic activity detection) 사용 — OpenAI처럼
  server_vad 파라미터 튜닝 없음. 1차에서는 기본값으로 가고 체감 후 조정.

### 도구 호출 브리지

- **스펙 변환**: `ToolSpec`(OpenAI function 포맷: name/description/parameters JSON Schema)
  → `types.FunctionDeclaration`. JSON Schema 중 Gemini 미지원 키는 변환기에서 제거
  (허용 목록 방식: type/description/enum/properties/required/items 만 통과).
- **실행**: `tool_call` 수신 → call_id별 `tool_manager.start_tool(...)` (HF 핸들러와 동일
  패턴 — 병렬 호출·취소·상태는 기존 매니저가 담당, 신규 인프라 없음).
- **결과 반환**: `_handle_tool_result` 콜백 → `session.send_tool_response(
  function_responses=[FunctionResponse(id=call_id, name=..., response=result_dict)])`.
  Gemini는 response가 dict이므로 HF처럼 json.dumps 하지 않는다.
- 유휴 도구(`is_idle_tool_call=True`)는 모델로 결과를 보내지 않음 (베이스 정책 유지).
- **병렬 호출 배칭 (실측 확정, 2026-10-23 도구 스파이크)**: 병렬 호출은 **한
  `LiveServerToolCall` 메시지의 `function_calls` 리스트로 묶여 온다** (실측: 2개 묶임).
  부분 전송으로는 모델이 발화를 재개하지 않고 **모든 FunctionResponse가 갖춰져야
  재개**한다 (첫 응답만 보내고 2초 대기 → 오디오 0 bytes 실측). 따라서 배치 내 전체
  완료까지 결과를 보류했다가 `send_tool_response`에 모아 한 번에 전송한다 — 선택이
  아니라 필수. 검증 스크립트: `scripts/gemini_tool_spike.py`.
- **call id (실측 확정)**: `FunctionCall.id`는 `call_<숫자>` 형식으로 채워져 온다.
  `FunctionResponse(id=fc.id, ...)` 필수. 방어적으로 id 부재 시 synthetic id fallback은
  유지한다.
- **실측 주의 1건**: 단일 도구 케이스에서 응답 전송 후 오디오가 생성되지 않은 단발
  사례 관찰 (턴이 오디오 없이 닫힘 — 재현성 미확인). 구현 시 tool response 후 일정
  시간 내 오디오/턴 신호가 없으면 로그를 남기고, 재현되면 후속 대응.
- OpenAI식 `response.create` 큐잉(`_response_sender_loop` 상당물)은 두지 않는다 —
  Gemini Live에는 대응 개념이 없음 (설계 단순화 포인트).

### 연결 수명·재연결 (스파이크 이슈 반영)

- `start_up()`: 연결 루프. `_run_live_session()` 정상 종료 시 리턴, 예외 시
  **지수 백오프 + 지터**(1s→2s→4s..., 최대 5회)로 재연결. 503 UNAVAILABLE이 실측으로
  발생했고 SDK 자체 재시도로는 부족했음.
- 오류 2경로 모두 캐치: `google.genai.errors.APIError` + WebSocket close
  (`websockets.ConnectionClosed` 계열; SDK가 재포장하는 경우 포함).
- **Gemini Live 세션은 시간 제한이 있다**(오디오 세션 수 분~15분 수준). 끊김을 예외가
  아닌 정상 흐름으로 보고 재연결한다. 1차에서는 재연결 시 대화 컨텍스트가 초기화되는
  것을 알려진 제한으로 수용하고, `context_window_compression`(슬라이딩 윈도우)을 설정해
  세션 내 수명을 최대화한다. 세션 resumption handle은 범위 외(후속 PR).
- `_restart_session()`: 보이스/페르소나 변경용 강제 재연결 (HF의 동명 메서드와 같은
  의미론 — 연결 종료 → 새 세션 백그라운드 기동).

### 음성 (voices)

- 카탈로그: Gemini Live 프리빌트 보이스 상수 목록 (Puck, Charon, Kore, Fenrir, Aoede,
  Leda, Orus, Zephyr — 구현 시 공식 문서 기준으로 확정, 실호출 검증 필수. 스파이크
  교훈: "목록에 있다고 쓸 수 있는 게 아니다").
- `GEMINI_VOICE` 기본값 → `get_session_voice()` 세션 오버라이드 → `change_voice()`.
- Live 세션은 연결 시 설정 고정이므로 `change_voice`/`apply_personality`는
  `_restart_session()` 방식 (OpenAI 핸들러와 동일 접근).

### say() / 시작 인사

- `say(text)`: `send_client_content(turns={"role":"user","parts":[{"text":...}]},
  turn_complete=True)` — 스파이크 검증 경로. 모델이 음성으로 발화.
- 시작 인사: 세션 수립 직후 `get_session_greeting_prompt()`를 같은 경로로 1회 주입
  (HF `_send_startup_greeting_prompt` 의미론, 세션당이 아닌 핸들러당 1회 플래그).

### 모션 연동 (movement_manager)

- OpenAI의 `speech_started/stopped` 같은 명시 이벤트가 없으므로:
  `set_speaking(True)` = 첫 오디오 청크 수신 시 / `set_speaking(False)` =
  `turn_complete`·`interrupted` 시. `set_listening`은 입력 전사 델타 수신을 프록시로
  사용 (1차 근사 — 체감상 부족하면 후속 조정).

### 세션 수명 주기 세부

- **기동 경로 차단 해제 (필수)**: `console.py:946`의 기동 루프가 백엔드와 무관하게
  `has_hf_realtime_target()`을 검사해 gemini는 영원히 "waiting_for_config"에 갇힌다.
  이 1곳에 `config.CONVERSATION_BACKEND == HF_BACKEND` 가드를 추가한다.
  (`:1006`은 이미 동일 가드가 있음 — 확인만, 수정 금지.)
- **상태 API 정합 (필수)**: `console.py:561-565` `_status_payload()`의 `can_proceed`가
  openai가 아니면 HF 연결 유무를 보므로 gemini에서 항상 false가 된다. gemini 분기
  (`GEMINI_API_KEY` 유무)를 추가한다 — UI가 매 로드마다 호출하는 경로라 방치 불가.
- **connection 덕타이핑 규약 (필수)**: `LocalStream._backend_connected()`
  (`console.py:268-274`)는 `handler.connection is not None`으로 연결 상태를 본다.
  Gemini 핸들러도 세션 수립 시 `self.connection = session`, 종료 시 `None`으로
  설정해 이 규약을 따른다 (console.py 무수정으로 상태 표시 호환).
- **shutdown()**: 수신 루프 태스크 취소 → 진행 중 재연결 시도 취소 → 세션 close →
  `tool_manager.shutdown()` → 출력 큐 드레인. (HF `shutdown` 의미론 준수)
- **tool_manager 수명**: 세션 루프 진입 시 `tool_manager.start_up(
  tool_callbacks=[self._handle_tool_result])`, finally에서 `shutdown()` (HF와 동일).
- **_connected_event**: 세션 수립 시 set, 종료 시 clear — `_restart_session()` 호출자가
  재수립 완료를 대기하는 신호 (HF 패턴).
- **_mark_activity**: 콘솔 UI 상태 머신이 activity reason을 소비하므로 HF와 같은
  지점에서 호출한다 — 사용자 전사 수신, 응답 시작(첫 오디오), 오디오 델타,
  tool_call 수신, tool 결과 준비, say. reason 문자열은 HF와 동일하게 유지.
- **서버 오류 메시지**: 수신 루프에서 `LiveServerMessage`의 오류/`GoAway` 류 신호를
  로그 + 사용자 노출용 `AdditionalOutputs`로 처리 (내부 상태성 오류는 UI 미노출 —
  HF `error` 이벤트 처리 의미론).
- **전사 델타 vs 완결**: Gemini `input_transcription`이 델타로 오는지 턴 단위로 오는지
  구현 시 실측으로 확인. 델타라면 HF의 0.5s 디바운스 상당을 추가, 턴 단위면 생략.
- `context_window_compression` (실측 확정): 빈 `ContextWindowCompressionConfig()`는
  서버가 거부한다 (1007 invalid argument). **`ContextWindowCompressionConfig(
  sliding_window=types.SlidingWindow())` 명시가 유효한 최소 파라미터** (연결·오디오
  정상 실측). 세션 지속시간 연장 효과는 Task 5 장시간 검증에서 확인.

### 설정 (`config.py` + `.env.example`)

- `_normalize_conversation_backend`: `"gemini"` 추가 (`GEMINI_BACKEND` 상수).
- 신규 env: `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL`(기본 `gemini-3.8-live`),
  `GEMINI_VOICE`(기본 `Kore`). 스파이크의 `GEMINI_TEXT_MODEL`은 백엔드에선 불필요.
- `main.py`: 기동 시 gemini 백엔드면 `GEMINI_API_KEY` 검증 (openai 분기와 동일 패턴),
  `build_handler()`에 분기 추가.
- `pyproject.toml`: `google-genai` 의존성 추가 + `uv lock`.

## 오류 처리

- 도구 실행 실패 → `{"error": ...}` dict로 모델에 반환 (raise 금지, CLAUDE.md 규칙).
- 수신 루프 내 예외 → 로그 + 재연결 루프로 복귀, 대화 루프를 죽이지 않는다.
- `send_realtime_input` 실패(연결 유실) → 프레임 드롭 + debug 로그 (HF `receive`와 동일).
- API 키 미설정 → 기동 시 RuntimeError (명확한 안내 메시지).

## 테스트

`tests/test_gemini_realtime.py` — 네트워크 없이:
- ToolSpec→FunctionDeclaration 변환: 실제 등록 도구 전수 변환 통과, 미지원 스키마 키 제거
- 보이스 해석: 대소문자 정규화·미지원 보이스 fallback
- 수신 이벤트 매핑: mock 세션으로 오디오→큐 / 전사→observer / interrupted→큐 비움 / tool_call→매니저 호출
- 재연결: mock이 APIError 1회 던진 뒤 성공 → 백오프 후 재수립 확인

라이브 검증: MuJoCo 시뮬 데몬 + `CONVERSATION_BACKEND=gemini`로 실제 대화 왕복,
도구 호출(`get_current_time` 등 단순 도구), 끼어들기, 15분 경과 재연결.

## 실증 검증 기록 (2026-10-24, 실제 마이크·스피커 + MuJoCo 시뮬)

공식 시연 스크립트(`docs/oss_report/시연리스트.md`) 5개 씬 기준, 사용자 실증:

| 씬 | 판정 | 근거 |
| :--- | :--- | :--- |
| 1. 착석 인사 | ✅ | `play_emotion(welcoming)` 호출 + 환영 발화 |
| 2. 일정 브리핑 | ✅ | `get_schedule` → 하루 일정 4건 한국어 브리핑 (Calendar OAuth 경유) |
| 3. 뽀모도로 | ✅ 풀체인 | 집중 완료 → 휴식 자동 시작 → 휴식 완료 알림. 진행 중 자유 대화와 병행됨 |
| 4. 끼어들기 | ✅ | `flushing player queue` 로그 — 하드웨어 버퍼 플러시 동작, 즉시 새 응답. 끊긴 발화를 "계속해 줘"로 이어받는 맥락 유지 확인 |
| 5. 수면 | ⚠️ 부분 | 수면 모션 + 앱 종료 정상 (sleep-on-exit 설계). 웨이크 워드 재기동은 standby 미지원 제한 — 범위 외 명시대로 |

추가 관찰: 다중 턴(날씨 지역 되묻기 4턴) 유지, 도구 에러 시 모델이 스스로 재질의
(`{"error": ...}` 설계 의도대로). 종료 시 `stop-current-app` HTTP 400 1건은 앱 매니저
없는 단독 실행 환경의 무해 로그.

## 범위 외 (후속 PR)

- standby/웨이크 워드 (현재 OpenAI 핸들러 전용 기능)
- console.py 설정 UI의 backend 토글에 gemini 노출 (1차는 `.env`로만 전환 — 단,
  기동 루프 가드 `:946`과 `_status_payload` gemini 분기는 **범위 내**, 없으면 기동·상태
  표시가 깨짐). 알려진 풋건: `backend.config` RPC(`console.py:848`)는 gemini 상태에서
  호출되면 HF로 되돌린다 — UI 토글 미노출이므로 1차에선 문서화만.
- `config.py`의 `get_available_voices()`/`get_default_voice()` fallback(`:557-568`)의
  gemini 분기 — 주 경로(핸들러 메서드)가 동작하므로 오류 fallback만 영향, 후속 처리
- 세션 resumption handle (재연결 시 컨텍스트 보존)
- 카메라 이미지 전달 (Gemini 멀티모달 입력) — 도구 자체는 돌지만 이미지는 모델에 미전달
- VAD 파라미터 튜닝
