# 대화 시나리오 테스트

시나리오 파일 하나를 세 환경에서 실행합니다.

| target | 모델 | 로봇 | 답해 주는 질문 | 답하지 못하는 질문 | 비용 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `offline` | 가짜 | Mock | 도구가 호출된 뒤 처리가 정상인가 | 모델이 맞는 도구를 고르는가 | 무료, CI 포함 |
| `live` | 실제 Gemini | Mock | 모델이 맞는 도구를 고르는가, 끼어들면 어떻게 되는가 | 음성 인식, VAD, 모션 | API 과금 |
| `sim` | 실제 Gemini | MuJoCo | 실행 중인 앱 전체, 모션, `--voice`면 마이크와 VAD | 도구 인자, `interrupted` 신호 | API 과금 |

## 시뮬레이터 없이 실행

```bash
source .venv/bin/activate

# API 키 없이
pytest tests/test_scenarios.py -v
python scripts/run_scenarios.py --target offline

# 실제 Gemini (.env의 GEMINI_API_KEY 사용)
python scripts/run_scenarios.py --target live --group tool --repeat 3   # 통과율 측정
python scripts/run_scenarios.py --target live --only barge_switch -v    # 도구, 답변, 수신 이벤트까지 출력
python scripts/run_scenarios.py --target live --only barge_resume --include-pending  # 미구현 기능의 현재 동작 측정
python scripts/run_scenarios.py --list
```

Windows PowerShell에서는 `.venv\Scripts\Activate.ps1`로 활성화하고, 한글 출력이 깨지면 `$env:PYTHONUTF8 = "1"`을 먼저 설정합니다.

`offline`과 `live`는 임시 폴더와 인메모리 캘린더를 쓰므로 실제 기억 파일과 Google 계정을 건드리지 않습니다.

## 시뮬레이터로 실행

**최초 1회 준비.** 호출어 기상에 `openwakeword`가 필요합니다. 없으면 잠든 뒤 경고만 남기고 깨어나지 못합니다.
`uv sync`는 MuJoCo를 제거하므로 다시 설치합니다.

```bash
uv sync --group dev && uv pip install mujoco
```

`.env`에 `CONVERSATION_BACKEND=gemini`와 `GEMINI_API_KEY`가 있어야 합니다. `.env`가 환경변수보다 우선하므로
스크립트 안의 `CONVERSATION_BACKEND=openai`는 `.env`에 값이 있으면 적용되지 않습니다.

```bash
# 터미널 1
REACHY_MINI_WAKE_WORD_ENABLED=1 ./run_sim_mujoco.sh

# 터미널 2: 문장을 자동 주입하고, 모션은 화면을 보고 y/n으로 답합니다
python scripts/run_scenarios.py --target sim --group tool
python scripts/run_scenarios.py --target sim --group sleep

# 문장을 화면에 보여 주면 직접 말합니다. 마이크와 VAD까지 검증됩니다
python scripts/run_scenarios.py --target sim --group barge --voice
```

PowerShell 터미널 1은 `$env:REACHY_MINI_WAKE_WORD_ENABLED = "1"; .\run_sim_mujoco.ps1`입니다.

주의할 점:
- 호출어 기상은 자동화되지 않습니다. 안내가 나오면 "리치미니"라고 말합니다.
- 수면 시나리오가 끝나면 로봇이 잠든 상태입니다. 다음 시나리오 시작 시 깨우라는 안내가 나옵니다.
- `sim`은 실제 앱 환경을 씁니다. 캘린더 미인증 상태면 가짜 기본 일정이 나오고, `tool_memory`는 실제 기억 파일에 쓴 뒤 지웁니다.
- 텍스트 주입 끼어들기는 VAD를 거치지 않습니다. 말로 끊는 동작은 `--voice`로 확인합니다.

## 시나리오 추가

`tests/scenarios/<id>.toml` 파일 하나를 넣으면 끝입니다. 코드는 수정하지 않습니다. `id`는 파일명과 같아야 합니다.

```toml
id = "tool_weather"
title = "날씨를 물으면 날씨 도구를 부른다"
group = "tool"                  # tool | barge | calendar | sleep
# targets = ["live", "sim"]     # 생략하면 전체
# pending = "미구현 사유"        # 있으면 미구현으로 보고. 구현되면 이 줄을 지웁니다
# known_bug = "버그 설명"        # 현재 실패가 예상됨. 고쳐지면 pytest가 알려 줍니다

[[steps]]
say = "서울 날씨 어때?"
tools = ["pollen_robotics_reachy_mini_weather_tool__get_weather"]
reply_any = ["맑", "도"]
```

**행동 필드**

| 필드 | 의미 |
| :--- | :--- |
| `say` | 사용자 발화. 수면 중이면 마이크 경로로 입력합니다 |
| `action` | `"wake"`는 호출어 기상, `"wait"`는 `wait_s`초 대기 |
| `interrupt_after_s` | 답변이 시작되고 이 시간 뒤 다음 step으로 끼어듭니다 |
| `wait_reply = false` | 도구 호출만 확인하고 답변을 기다리지 않습니다 |
| `fail_tools`, `slow_tools` | 도구 오류, 끝나지 않는 도구 주입 (`sim` 제외) |
| `[steps.mock]` | `offline`의 도구 호출(`calls`)과 답변(`reply`)을 직접 지정 |

**기대값 필드**

| 필드 | 의미 |
| :--- | :--- |
| `tools`, `no_tools` | 호출되어야 할 도구, 호출되면 안 될 도구 |
| `args` | 인자 확인. 문자열은 부분 일치, 리스트는 하나라도 일치. `{today}`, `{tomorrow}` 사용 가능 |
| `reply_any`, `reply_none` | 답변에 하나라도 있어야 할 단어, 있으면 안 될 단어 |
| `reply_later_any` | `reply_any` 단어 **뒤에** 나와야 할 단어 (답변 재개 확인) |
| `silent` | 도구 호출, 답변, 모델 전송 모두 없어야 합니다 |
| `state` | step이 끝난 뒤 상태. `"asleep"` 또는 `"awake"` |
| `interrupted` | 끼어들기 신호 수신과 재생 큐 비움 확인 |
| `observe` | `sim`에서 사람에게 y/n으로 묻는 항목 |

`offline`에서는 가짜 모델이 `tools`를 그대로 호출하고 `reply_any`의 첫 단어로 답합니다. 대본은 실제 API에서 측정한 메시지 순서를 따릅니다.

## 결과 상태

| 상태 | 의미 |
| :--- | :--- |
| `PASS` / `FAIL` | 기대값 충족 / 미충족 |
| `KNOWN_BUG` | `known_bug`로 표시한 실패 |
| `FIXED?` | `known_bug`인데 통과. 고쳐졌다면 해당 줄을 지웁니다 |
| `PENDING` | 기능 미구현으로 실행하지 않음 |
| `ERROR` | 실행 자체가 실패 (연결 오류 등) |

`live`와 `sim`은 모델 응답이 매번 달라지므로 한 번의 결과로 판단하지 말고 `--repeat`로 통과율을 봅니다.
