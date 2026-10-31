# Gemini Live 대화 백엔드 구현 계획

> **For agentic workers:** 태스크 단위로 구현하고 체크박스로 추적한다. 각 태스크 끝에서
> 게이트 통과 후 커밋 (커밋은 사용자 승인 후).

**Goal:** `CONVERSATION_BACKEND=gemini`로 Gemini Live API 기반 실시간 음성 대화
(오디오 왕복·전사·도구 호출·끼어들기·재연결)가 기존 도구/모션/유휴 정책 위에서 동작한다.

**Architecture:** `gemini_realtime.py`의 `GeminiRealtimeHandler(ConversationHandler)`
단독 구현 (HF/OpenAI 상속 체인 미사용 — 프로토콜이 다름). 도구 실행은 기존
`BackgroundToolManager` 재사용, 오디오 리샘플은 `openai_realtime.resample_pcm` 재사용.

**Spec:** [`oss_plan/2026-10-23-gemini-realtime-design.md`](2026-10-23-gemini-realtime-design.md)

## Global Constraints

- upstream 공유 파일 수정 최소화: `conversation_handler.py`·`core_tools.py`·
  `background_tool_manager.py`는 수정 금지. `config.py`/`main.py`는 분기 추가만,
  `console.py`는 2곳만 — 기동 루프 가드(`:946` 1곳, `:1006`은 이미 가드됨) +
  `_status_payload()` gemini 분기 (critic 1·2차 리뷰 지적).
- 게이트: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v`
- 테스트는 네트워크 금지 (mock 세션). 실 API 검증은 수동 단계로 분리.
- 도구 실패는 `{"error": ...}` 반환, 수신 루프는 예외로 죽지 않는다 (CLAUDE.md 규칙).
- 브랜치: `feat/gemini-realtime-backend` (upstream `dev/gemini` 기준).

## 성공 기준 (Definition of Done)

테스트는 기존 패턴을 따른다: `monkeypatch` + 손수 만든 `Fake*` 클래스
(`tests/test_openai_realtime.py`의 `FakeClient`/`FakeConnection`,
`tests/test_huggingface_realtime.py`의 `FakeSession` 스타일 — MagicMock 지양),
비동기는 `async def test_`, 경계값은 `@pytest.mark.parametrize`
(`tests/test_openai_realtime.py:41` backend 정규화 테스트 참조).

**전체 완료 조건:**

1. `CONVERSATION_BACKEND=gemini` + MuJoCo 시뮬에서: 음성 질문 → 한국어 음성 응답 +
   UI 전사 표시, 도구 호출(`get_current_time`) 왕복, 응답 중 끼어들기 시 즉시 중단,
   15분+ 세션에서 재연결 후 대화 계속 — 4개 시나리오 전부 통과
2. 전체 게이트 통과: `ruff check . --fix && ruff format . && mypy --pretty && pytest tests/ -v`
   — 기존 테스트 **0개 깨짐** (특히 `test_config.py`·`test_main.py`·`test_console.py`의
   backend 관련 테스트)
3. 신규 테스트 전부 네트워크·API 키 없이 통과 (CI 환경 기준)
4. `CONVERSATION_BACKEND` 미설정/`hf`/`openai` 기존 경로 동작 불변 (회귀 없음)

**태스크별 수용 기준:**

| 태스크 | 수용 기준 (테스트로 증명) |
| :--- | :--- |
| 1 설정 배선 | `_normalize_conversation_backend("gemini") == "gemini"`, 대소문자·공백 정규화, 오타→`hf` fallback + 경고 로그 (기존 `test_normalize_conversation_backend` parametrize에 케이스 추가 형태). gemini 선택 + 키 미설정 → 기동 시 안내 메시지 포함 RuntimeError (`test_main.py` 패턴) |
| 2a 골격·오디오 | FakeSession 주입 시: 오디오 메시지 → `output_queue`에 `(24000, int16 ndarray)` / stereo 마이크 프레임 → mono·16kHz로 `send_realtime_input` 도달 (`test_receive_monoizes_stereo_frames_before_upload` 상당) / `emit()` 장치 레이트 리샘플 4케이스 (`test_emit_*` 4종 상당) / Fake가 APIError 1회 던지면 백오프 후 재수립, `RECONNECT_MAX_ATTEMPTS` 초과 시 raise / shutdown이 루프 취소→세션 close→매니저 shutdown 순서로 수행 |
| 2b 전사·끼어들기 | 전사 → `_emit_transcript` observer 호출 / `interrupted` → 출력 큐 비움 **+ `_clear_queue` 호출** 양쪽 검증 / `turn_complete` → speaking 해제 / 시작 인사 핸들러당 1회 |
| 3 도구 브리지 | `tool_specs_to_gemini(get_tool_specs())`가 등록 도구 **전수** 변환 성공 + 미지원 스키마 키 부재 검증 / tool_call 수신 → `tool_manager.start_tool` 호출 인자(call_id·tool_name·args) 일치 / 병렬 2건 → 전체 완료까지 보류 후 일괄 `send_tool_response` / id 부재 시 synthetic id 매핑 / 결과 → FunctionResponse(id·name·response dict) 검증 / 도구 에러 → `{"error": ...}`가 모델로 전달 / `is_idle_tool_call=True` → 모델 미전송 (`test_parallel_tool_calls_trigger_single_response` 참조) |
| 4 보이스·say | 보이스 대소문자 정규화·미지원→기본값 fallback (`test_voice_normalization_against_openai_catalog` 상당) / `change_voice`·`apply_personality`가 연결 존재 시 재시작 경로 호출 (`test_change_voice_restarts_live_session` 상당), 연결 없으면 다음 연결로 유예 / `say("")` → ValueError, 세션 없음 → RuntimeError |
| 5 실기 검증 | 위 "전체 완료 조건 1" 시나리오 체크리스트를 설계 문서 검증 절에 결과와 함께 기록 (보이스 실호출 검증 결과 포함) |

---

### Task 1: 설정·의존성 배선

**Files:** Modify: `pyproject.toml`(+`uv lock`), `config.py`, `.env.example`, `main.py`,
`console.py`(가드 2곳)

**Steps:**
- [x] `pyproject.toml`에 `google-genai` 추가 → `uv lock`
- [x] `config.py`: `GEMINI_BACKEND = "gemini"` 상수, `_normalize_conversation_backend`에
      gemini 허용, `GEMINI_API_KEY` / `GEMINI_LIVE_MODEL`(기본 `gemini-3.8-live`) /
      `GEMINI_VOICE`(기본 `Kore`) 로드. **`CONVERSATION_BACKEND` 대입 지점 4곳 전부 확인**
      (Config 클래스 내 393·406 라인 2곳 + `refresh_runtime_config_from_env` 내 527·541
      라인 2곳 — 중복 대입이라 한 곳만 고치면 나중 대입이 덮어씀)
- [x] `console.py` 기동 루프 가드: `:946`의 `has_hf_realtime_target()` 검사를
      `config.CONVERSATION_BACKEND == HF_BACKEND`일 때만 타도록 수정 — 없으면 gemini는
      "waiting_for_config"에 갇혀 기동 불가. (`:1006`은 이미 가드됨 — 확인만)
- [x] `console.py` `_status_payload()`(`:561-565`): gemini 분기 추가 —
      `can_proceed`/`has_key`가 `GEMINI_API_KEY` 유무를 반영하도록 (UI 상태 API 정합)
- [x] `.env.example`에 3개 변수 + 주석 추가
- [x] `main.py`: 기동 로그·API 키 검증 분기, `build_handler()`에 gemini 분기 (import는 분기 안에서)
- [x] 테스트: backend 정규화("gemini"/"GEMINI"/오타→hf fallback), 키 미설정 시 에러 메시지,
      gemini 백엔드일 때 기동 루프가 HF 타깃 검사를 건너뛰는지 (`test_console.py` 패턴)
- [x] 게이트 통과

### Task 2: 핸들러 골격 + 세션 수립 + 오디오 왕복

**Files:** Create: `src/reachy_mini_conversation_app/gemini_realtime.py`,
`tests/test_gemini_realtime.py`

**Interfaces:**
- `GeminiRealtimeHandler(ConversationHandler)`: `__init__(deps, instance_path=None, startup_voice=None)`
  — HF 핸들러와 동일 시그니처 (build_handler 호환)
- 모듈 상단 튜닝 상수: `GEMINI_INPUT_SAMPLE_RATE=16000`, `GEMINI_OUTPUT_SAMPLE_RATE=24000`,
  `RECONNECT_MAX_ATTEMPTS=5`, `RECONNECT_BASE_DELAY_S=1.0`

**Steps (2a — 골격 + 세션 + 오디오 왕복):**
- [x] 골격: `__init__`(output_queue·tool_manager·`_connected_event`·
      `self.connection = None`·`_startup_greeting_sent = False`), `_is_connected`,
      `shutdown`(수신 루프 태스크 취소 → 재연결 시도 취소 → 세션 close
      → `tool_manager.shutdown()` → 큐 드레인).
      **connection 덕타이핑**: 세션 수립 시 `self.connection = session`, 종료 시 `None`
      — console.py `_backend_connected()`가 이 속성을 보므로 필수
- [x] `start_up()`: 클라이언트 생성 → 재연결 루프(지수 백오프+지터, APIError·WS close 캐치)
      → `_run_live_session()`
- [x] `_run_live_session()`: `LiveConnectConfig`(AUDIO, 전사 2종, system_instruction,
      speech_config, context_window_compression — sliding_window 파라미터는 공식 문서로
      확정) + `tool_manager.start_up(tool_callbacks=...)`/finally `shutdown()` →
      수신 루프: 오디오→큐에 **(24000, ndarray) 그대로** (리샘플 금지)
- [x] `receive()`: mono화→16kHz 리샘플→`send_realtime_input` (연결 없으면 드롭)
- [x] `emit()` 오버라이드: 24kHz→장치 레이트 리샘플 (리샘플은 여기서만 — 이중 리샘플 방지)
- [x] 테스트(mock 세션): 오디오→큐 적재 레이트, receive mono화·리샘플, emit 4케이스,
      재연결 백오프, shutdown 순서
- [x] 게이트 통과

**Steps (2b — 전사·끼어들기·인사·UI 신호):**
- [x] 전사: input/output transcription → `_emit_transcript` + `AdditionalOutputs`
      (델타로 오면 HF식 0.5s 디바운스 추가, 턴 단위면 생략 — 실측으로 결정하고 설계에 기록)
- [x] `interrupted` → 출력 큐 비움 + **`self._clear_queue()` 호출**(하드웨어 플레이어
      버퍼 플러시 — 없으면 끼어들기 후 수백 ms 잔여 음성 재생) + `set_speaking(False)`
- [x] `turn_complete` → speaking 해제, 응답 종료 처리
- [x] `_mark_activity` 호출 지점을 HF와 동일 reason 문자열로 배치 (콘솔 UI 상태 머신 소비)
- [x] movement_manager 신호: 첫 오디오 청크→`set_speaking(True)`, 입력 전사 수신→listening 프록시
- [x] 시작 인사: `get_session_greeting_prompt()`를 `send_client_content`로 핸들러당 1회 주입
- [x] 테스트(mock 세션): 전사→observer, interrupted 시 큐+_clear_queue 양쪽 호출,
      turn_complete→speaking 해제, 인사 1회 플래그
- [x] 게이트 통과

### Task 3: 도구 호출 브리지

**Files:** Modify: `gemini_realtime.py`, `tests/test_gemini_realtime.py`

**Interfaces:**
- `tool_specs_to_gemini(tool_specs: list[ToolSpec]) -> list[types.Tool]` — 모듈 함수
  (단독 테스트 가능), JSON Schema 허용 목록 변환
- `_handle_tool_result(completed_tool: ToolNotification)` — 매니저 콜백,
  `send_tool_response(FunctionResponse(id, name, response=dict))`

**Steps:**
- [x] **미니 스파이크 완료 (2026-10-23, `scripts/gemini_tool_spike.py`)**: 병렬 호출은
      한 메시지에 묶여 오고, **전체 FunctionResponse가 갖춰져야 발화 재개** (부분 전송
      시 무반응 실측) → 배칭 필수 확정. `FunctionCall.id`는 `call_<숫자>`로 채워짐.
      `ContextWindowCompressionConfig(sliding_window=SlidingWindow())`가 유효 최소 설정.
- [x] 변환기 + 실제 등록 도구 전수 변환 테스트 (미지원 키 제거 검증)
- [x] `tool_call` 수신 → `tool_manager.start_tool`. `FunctionCall.id` 사용(실측: 채워짐),
      부재 시 synthetic id fallback + 호출↔응답 매핑 유지
- [x] **병렬 배칭 (필수)**: `_in_flight_tool_calls` 상당으로 배치 전체 완료까지 결과 보류
      → `send_tool_response`에 모아 한 번에 전송 (실측상 부분 전송은 모델 무반응)
- [x] `_handle_tool_result`: 에러→`{"error":...}`, 유휴 도구는 모델 미전송,
      UI용 `AdditionalOutputs` 큐잉, camera `b64_im` 등 부피 필드 제거(`_sanitize` 상당)
- [x] 테스트: tool_call→매니저 기동, 병렬 2건 보류→일괄 전송, synthetic id 매핑,
      결과→send_tool_response 호출 인자, 에러 경로
- [x] 게이트 통과

### Task 4: 보이스·페르소나·say

**Files:** Modify: `gemini_realtime.py`, `tests/test_gemini_realtime.py`

**Steps:**
- [x] 보이스 카탈로그 상수 + `get_available_voices`/`get_current_voice`/`change_voice`
      (정규화·fallback은 HF `_resolve_backend_voice` 의미론)
- [x] `change_voice`/`apply_personality` → `_restart_session()` (연결 종료 후 백그라운드 재수립)
- [x] `say(text)`: 빈 문자열 ValueError, 세션 없음 RuntimeError, `send_client_content` 주입
- [x] 테스트: 보이스 정규화/fallback, say 가드, apply_personality가 재시작 경로 타는지
- [x] 게이트 통과

### Task 5: 실기 검증 + 문서

**Steps:**
- [x] 보이스 카탈로그 실호출 검증 (2026-10-23): 8개 보이스 전수 OK — Puck/Charon/Kore/Fenrir/Aoede/Leda/Orus/Zephyr 모두 오디오 바이트 수신 확인, 불가 보이스 없음 → 카탈로그 수정 불필요
- [x] 앱 기동 스모크 (2026-10-23): `CONVERSATION_BACKEND=gemini` + mockup-sim 데몬 환경에서 "Gemini Live session established (voice=Kore, model=gemini-3.8-live)" + "Sent startup greeting prompt" 로그 확인 — waiting_for_config 갇힘 없음. 오디오 장치 경고(--no-media)는 정상.
- [x] 실 음성 검증 (2026-10-24, MuJoCo 시뮬 + 실제 마이크·스피커, 공식 시연리스트
      5개 씬 기준): 결과는 설계 문서 "실증 검증 기록" 절 참조. 씬 1~4 전부 통과,
      씬 5는 수면 모션·앱 종료까지 정상 (재깨우기는 standby 미지원 제한 그대로)
- [x] `.env.example`에 gemini 백엔드 사용법 주석 (Task 1에서 완료)
- [x] 전체 게이트 + PR 제출: orocapangyo#3, base `dev/gemini`, 알려진 제한 명시

#### 범위 외

아래 항목은 사용자 직접 개입 또는 완전한 하드웨어 환경이 필요:

- 실제 음성 대화 왕복 (마이크·스피커 필요)
- 도구 호출 실 왕복 검증 (대화 왕복 선행 필요)
- 끼어들기 시나리오 (음성 입력 필요)
- 15분+ 재연결 동작 확인 (장시간 세션 유지 필요)

#### 검증 기록 (2026-10-23 무인 자동화 범위)

**A. 보이스 카탈로그 실호출 검증**

| 보이스 | 결과 | 오디오 수신 |
| :----- | :--- | :---------- |
| Puck   | OK   | 142,080 bytes |
| Charon | OK   | 153,602 bytes |
| Kore   | OK   | 161,282 bytes |
| Fenrir | OK   | 140,160 bytes |
| Aoede  | OK   | 105,600 bytes |
| Leda   | OK   | 153,602 bytes |
| Orus   | OK   | 121,440 bytes |
| Zephyr | OK   | 143,520 bytes |

결론: `GEMINI_AVAILABLE_VOICES` 8개 전수 사용 가능 확인. 카탈로그 수정 없음.

**B. 앱 기동 스모크**

환경: `reachy-mini-daemon --mockup-sim --headless --no-media` + `CONVERSATION_BACKEND=gemini`

도달 지점: 앱 기동 → 데몬 연결 성공 → 20개 도구 로드 → Gemini 세션 수립 → 인사 프롬프트 발송

```
2026-10-23 22:14:49,754 INFO | Using Gemini Live realtime handler (model=gemini-3.8-live)
2026-10-23 22:14:50,899 INFO | Gemini Live session established (voice=Kore, model=gemini-3.8-live)
2026-10-23 22:14:50,901 INFO | Sent startup greeting prompt
2026-10-23 22:15:18,889 INFO | Gemini Live session established (voice=Kore, model=gemini-3.8-live)
```

오디오 장치 경고(`Audio system is not initialized`)는 `--no-media` 플래그 때문으로 정상.
두 번째 세션 수립 로그는 재연결 루프가 정상 동작함을 시사.

**남은 사용자 필요 검증:**
- 실제 음성 대화 왕복 (`pip install reachy_mini[mujoco]` 후 MuJoCo 시뮬 + 마이크/스피커)
- 도구 호출(get_current_time 등) 실 왕복
- 응답 중 끼어들기 즉시 중단
- 15분+ 세션 재연결 후 대화 계속
