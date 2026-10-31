# OpenAI Realtime vs Gemini Live — 데스크 동반자 백엔드 비교 분석

> 상태: **측정 대기**. 비교 환경(토글·벤치마크 도구)과 구조 비교(§3)는 완료.
> 실측 수치(§4)는 두 API 키로 `scripts/realtime_benchmark.py`를 실행한 뒤 채운다.
> 이 문서의 수치는 전부 벤치마크 결과 파일에서 옮겨 적는다. 추정치는 넣지 않는다.

## 1. 목적

DeskMate의 실시간 음성 대화 백엔드로 **OpenAI Realtime**(`openai_realtime.py`)과
**Gemini Live**(PR #3, `gemini_realtime.py`) 중 어느 쪽이 시연 시나리오에 적합한지를
같은 조건에서 비교한다. 판단 기준은 다음 네 가지다.

1. **응답 지연**: 사용자가 말을 마친 뒤 로봇이 말하기 시작할 때까지 걸리는 시간
2. **도구 호출 정확도**: 시연 씬마다 기대하는 도구(`get_schedule`, `pomodoro_timer` 등)를 호출하는지
3. **한국어 품질**: 음성 인식(STT) 정확도, 응답 문장의 자연스러움, 페르소나 규칙(1~2문장, 해요체) 준수
4. **운영성**: 안정성(재연결), 기능 지원 범위(standby·웨이크 워드), 비용

## 2. 비교 환경

### 2.1 런타임 토글 (시뮬레이터에서 바로 전환)

Settings UI(`--ui`, http://127.0.0.1:7860/ → Settings)에 **Realtime backend** 섹션을 추가했다.

- 드롭다운에서 OpenAI Realtime / Gemini Live를 고르고 **Switch backend**를 누르면
  앱 재시작 없이 핸들러를 다시 만들고 재연결한다. 이때 페르소나와 도구는 그대로 유지된다.
- API 키를 한 번 입력하면 instance `.env`에 저장되므로, 이후 전환할 때는 키를 다시 넣지 않아도 된다.
- 보이스 이름은 백엔드마다 다르다. 그래서 전환할 때 보이스 override를 비우고,
  각 백엔드의 기본 보이스(`OPENAI_VOICE` / `GEMINI_VOICE`)를 쓴다.
- 서버 쪽 구현은 JSON-RPC `backend.select` (`{"backend": "openai"|"gemini", "api_key"?: str}`)다.
  상태 응답(`conversation.status`)에 `has_openai_key` / `has_gemini_key` 필드를 추가했다.

### 2.2 벤치마크 하네스 (`scripts/realtime_benchmark.py`)

- 앱의 **실제 핸들러 클래스**를 그대로 구동한다. 같은 프로필 프롬프트(`desk_companion_ko`),
  같은 도구 레지스트리, 같은 `receive()` 오디오 경로를 쓴다. 로봇과 movement manager는 mock이라 데몬 없이 돌아간다.
- 입력은 `scripts/benchmark_scenarios.json`의 한국어 발화 8개로, 시연 씬 1~5와 날씨·시간·자유 대화로 구성했다.
  macOS `say -v Yuna`로 WAV를 한 번 합성해 두고, 20ms 청크 단위로 **실시간 속도**로 흘려보낸 뒤
  무음을 계속 넣는다. 실제 콘솔 루프에서 마이크가 계속 열려 있는 상황과 같다.
- 반복할 때마다 백엔드 실행 순서를 바꿔서, 한쪽이 항상 먼저 실행되어 생기는 편향(네트워크 상태 등)을 줄인다.
- 결과물: `benchmarks/results/benchmark-<시각>.json`(원시 데이터)과 `.md`(요약표). `benchmarks/`는 gitignore 대상이다.

| 지표 | 정의 |
| :--- | :--- |
| 연결 시간 | `start_up()` 호출 → 실시간 세션 연결 |
| 기동→인사 첫 음성 | `start_up()` 호출 → 시작 인사의 첫 오디오 청크. 사용자가 체감하는 "켜고 나서 첫 마디까지" |
| **TTFA** (time to first audio) | 사용자 발화 끝 → 응답 첫 오디오 청크. **VAD 무음 대기 시간이 포함된다** |
| 턴 완료 | 사용자 발화 끝 → 마지막 이벤트(도구 후속 발화 포함) |
| 도구 선택 정확도 | 호출된 도구 집합이 시나리오의 `expected_tools`와 정확히 같으면 일치 |
| 무응답·타임아웃 | 35초 안에 응답 오디오가 없거나 턴이 끝나지 않은 경우 |

**하네스의 한계**

- 오디오는 TTS로 합성한 깨끗한 음성이다. 실제 마이크 환경(잡음, 스피커 소리 재유입)의 STT 품질은 따로 봐야 한다 → §4.3 수동 검증.
- 도구는 실제로 실행된다. `get_schedule`처럼 OAuth가 필요한 도구는 인증이 안 된 상태면 `{"error": ...}`를 돌려준다.
  조건은 두 백엔드에 똑같고, 오류를 받았을 때 모델이 어떻게 복구하는지도 비교 대상에 들어간다.
- 끼어들기(barge-in, 씬 4)는 자동화하지 않았다. 실기에서 수동으로 확인한다.

## 3. 구조 비교 (코드 기준, 확인 완료)

| 항목 | OpenAI Realtime | Gemini Live | 시연 영향 |
| :--- | :--- | :--- | :--- |
| 모델 (기본값) | `gpt-realtime-2.1` (`.env.example`) | `gemini-3.8-live` | — |
| 입력 오디오 | 24 kHz PCM16 | 16 kHz PCM16 | 둘 다 핸들러가 리샘플링 |
| 출력 오디오 | 24 kHz | 24 kHz | 같음 |
| 턴 감지 (VAD) | `server_vad`, threshold 0.4, prefix 300ms, **silence 600ms** (앱에서 명시) | Gemini 자동 활동 감지 (SDK 기본값, 앱에서 별도 설정 안 함) | TTFA 차이의 상당 부분이 VAD 설정 차이일 수 있다. §5 참고 |
| 입력 전사 (STT) | `whisper-1`, `language=ko` 고정 | Live 내장 입력 전사 (언어 힌트 없음) | 한국어 인식률 비교 필요 |
| 보이스 | 10종 (marin, cedar, …) | 8종 (Kore, Puck, …) | 한국어 음색은 청취 평가 필요 |
| 도구 호출 | 호출마다 개별 응답 | 병렬 호출은 **전체 FunctionResponse를 모아서 한 번에** 보내야 발화를 재개 (PR #3 실측) | Gemini는 여러 도구를 동시에 부를 때 가장 느린 도구를 기다린다 |
| 끼어들기 | VAD `interrupt_response=False`, 앱이 `speech_started`에서 처리 | 서버 `interrupted` 이벤트 → 큐와 하드웨어 버퍼 플러시 | 둘 다 지원 (PR #3 씬 4 실증) |
| 컨텍스트 유지 | 세션 내 유지 | 슬라이딩 윈도우 압축. **재연결하면 컨텍스트 초기화** | 장시간 시연에서 Gemini 세션이 끊기면 대화 맥락이 사라진다 |
| 재연결 | 콘솔 루프 재시도 (5초) | 핸들러 내부 지수 백오프 5회 + 콘솔 루프 | — |
| standby / 웨이크 워드 | **지원** (`enter_standby`, 전사 전용 VAD) | **미지원** | 씬 5(수면 → 웨이크 워드 재기동)는 OpenAI만 완전 동작 |
| 페르소나 변경 | `session.update`로 즉시 반영 | 세션 재시작 | Gemini는 전환할 때 잠깐 끊긴다 |

## 4. 실측 결과

> 아래 표는 `benchmarks/results/benchmark-*.md`에서 옮겨 적는다. 실행 조건(날짜, 네트워크, 모델 id, repeats)을 함께 남긴다.

**실행 조건**: 날짜 `____` · 네트워크 `____` · OpenAI 모델 `____` · Gemini 모델 `____` · repeats `__`

### 4.1 지연·안정성

| 지표 | OpenAI | Gemini |
| :--- | ---: | ---: |
| 연결 시간 중앙값 | | |
| 기동→인사 첫 음성 중앙값 | | |
| TTFA p50 | | |
| TTFA p90 | | |
| 턴 완료 p50 | | |
| 무응답·타임아웃 | | |
| 세션 실패 | | |

### 4.2 시나리오별 도구 호출

| 시나리오 | 기대 도구 | OpenAI (일치/반복) | Gemini (일치/반복) | 비고 |
| :--- | :--- | :---: | :---: | :--- |
| s1_welcome | play_emotion | | | |
| s2_schedule | get_schedule | | | |
| s3_pomodoro | pomodoro_timer | | | |
| s4_celebrate | play_emotion | | | |
| x1_weather | get_weather | | | |
| x2_time | get_time | | | |
| x3_chitchat | (없음) | | | |
| s5_sleep | go_to_sleep | | | |

### 4.3 한국어 품질 (수동 평가, 1~5점)

시뮬레이터에서 Settings 토글로 백엔드를 바꿔 가며, 같은 사람이 같은 마이크로 시연 씬 1~5를 진행한다.

| 항목 | OpenAI | Gemini | 메모 |
| :--- | :---: | :---: | :--- |
| STT 정확도 (실 마이크) | | | |
| 응답 자연스러움 | | | |
| 페르소나 준수 (1~2문장, 해요체) | | | |
| 숫자·시간 낭독 ("오후 세 시 반") | | | |
| 음색 / 발음 | | | |
| 끼어들기 반응 (씬 4) | | | |

### 4.4 비용

벤치마크 결과의 `audio_out_s`(응답 음성 길이)와 시나리오 입력 길이로 분당 비용을 계산한다.
단가는 측정 시점의 각 사 공식 가격표를 인용하고 출처 링크와 조회 날짜를 남긴다.

## 5. 해석할 때 주의할 점

- **TTFA에는 VAD 무음 대기 시간이 포함된다.** OpenAI는 600ms 무음을 기다리도록 명시되어 있고,
  Gemini는 SDK 기본값을 쓴다. 지연 차이가 크게 나오면 모델 속도 차이인지 턴 감지 설정 차이인지 구분해서 써야 한다.
  공정하게 보려면 Gemini의 `realtime_input_config.automatic_activity_detection`
  (`silence_duration_ms`)을 600ms로 맞춘 추가 실행을 권장한다.
- `config.py`에 OpenAI 기본 모델이 두 곳에서 다르게 정의되어 있다
  (`DEFAULT_OPENAI_REALTIME_MODEL="gpt-realtime-mini"`와 클래스 기본값 `"gpt-realtime-2.1"`).
  측정할 때는 `.env`에 `OPENAI_REALTIME_MODEL`과 `GEMINI_LIVE_MODEL`을 **명시**한다.
- 반복 횟수가 적으면 p90은 의미가 약하다. repeats는 최소 3회, 가능하면 5회로 한다.

## 6. 결론 및 권장 (측정 후 작성)

- 시연 기본 백엔드: `____`
- 근거: `____`
- 상대 백엔드에서 보완할 점: `____`

## 부록: 재현 방법

```bash
# 1) .env에 두 키와 모델을 명시
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
GEMINI_LIVE_MODEL=gemini-3.8-live
REACHY_MINI_CUSTOM_PROFILE=desk_companion_ko

# 2) 자동 벤치마크 (데몬 불필요, 약 10~15분)
python scripts/realtime_benchmark.py --backends openai gemini --repeats 3

# 3) 시뮬레이터 수동 비교
reachy-mini-daemon --sim                 # 터미널 1 (macOS는 SIM-ONBOARDING.md의 mjpython 방법 사용)
reachy-mini-conversation-app --ui        # 터미널 2 → Settings → Realtime backend 토글
```
