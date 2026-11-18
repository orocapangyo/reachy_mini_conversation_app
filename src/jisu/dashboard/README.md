# Simulator Dashboard (`jisu.dashboard`)

리치미니 테스트용 독립 대시보드다. 브라우저 탭 하나에서 아래 세 가지를 한다.

- **대화창**: 스피커/마이크로 오간 대화를 실시간 자막으로 보고, 텍스트로 채팅한다.
  답변 위에는 **생각 과정** 블록이 붙어 그 턴에 리치미니가 내부적으로 한 일(도구 호출·인자·결과·소요 시간)을 보여 준다.
- **로그**: 대화 앱, 시뮬 데몬, 대시보드의 콘솔 로그를 실시간으로 보고 레벨·출처·검색어로 거른다.
- **장기 기억**: 백엔드(JSON / GBrain / …)를 고르고, 기억을 조회·추가·검색·삭제한다.

기존 앱 코드(`src/reachy_mini_conversation_app/**`)는 한 줄도 수정하지 않는다.

## 실행

```bash
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m jisu.dashboard             # 시뮬 데몬 + 대화 앱 기동, http://localhost:7870 자동 오픈
```

| 옵션 | 설명 |
| :--- | :--- |
| `--backend json\|gbrain` | 대화 앱이 쓸 장기 기억 백엔드 (기본: `JISU_MEMORY_BACKEND` 또는 `gbrain`) |
| `--headless` | MuJoCo 3D 창 없이 데몬 실행 |
| `--conversation-backend openai\|gemini\|hf` | `CONVERSATION_BACKEND`를 덮어쓴다 (기본은 `.env` 설정을 따름) |
| `--profile NAME` | 페르소나 (기본 `desk_companion_ko`) |
| `--no-start` | 대화 앱을 자동으로 시작하지 않는다 (UI의 "시작" 버튼으로 시작) |
| `--open-app-ui` | 기존 앱 UI(7860)도 함께 연다 |
| `--debug` | 대화 앱을 `--debug`로 실행한다 (DEBUG 로그) |

- 8000 포트에 데몬이 이미 떠 있으면 그 데몬을 그대로 쓰고, 종료할 때도 남겨 둔다.
- 7860을 다른 대화 앱이 쓰고 있으면 시작하지 않는다(예: `run_sim_mujoco.sh`). 그 앱을 먼저 끈다.
- 대시보드에서 Ctrl+C를 누르면 대화 앱에 Ctrl+C를 전달하고(sleep-on-exit 동작 유지), 대시보드가 띄운 데몬을 정지한다.

## 동작 구조

```
python -m jisu.dashboard  (FastAPI :7870)
 ├─ launcher.py   데몬 + 대화 앱 subprocess, stdout → log_buffer → /ws/logs
 ├─ server.py     /api/app/*, /api/memory/*, /ws/logs, static UI
 └─ static/       브라우저가 ws://localhost:7860/rpc 에 직접 접속 (대화창)

대화 앱 = python -m jisu.dashboard.app_entry --ui --profile ...
 ├─ memory/hook.py 가 기억 호출 지점을 선택한 백엔드로 연결
 ├─ trace/hook.py  가 도구 실행을 감싸 [jisu-trace] JSON 로그를 남김
 └─ 기존 main() 실행
```

- **대화창**은 앱이 원래 제공하는 JSON-RPC(`/rpc`)를 쓴다.
  - 받는 알림: `conversation.transcript`(자막), `conversation.turn`(상태)
  - 보내는 요청: `conversation.say`(채팅 입력), `conversation.interrupt`(말 끊기), `conversation.mic`(음소거)
  - 앱은 매번 마이크가 켜진 채 뜬다. 그래서 대시보드가 앱 프로세스마다 한 번 음소거를 건다(기본값 `DEFAULT_MIC_MUTED`). 그 뒤 사용자가 바꾼 상태는 RPC가 다시 연결되어도 유지된다.
  - `say`로 보낸 문장은 자막으로 되돌아오지 않는다. 그래서 내가 입력한 말풍선은 UI가 직접 그린다.
- **장기 기억 hook**은 `prompts.format_memory_for_prompt`, `tools.remember.add_memory_fact`, `tools.forget.forget_memory_fact`의 모듈 속성만 바꾼다. `memory.py`는 그대로 둔다.
  - 기억은 세션이 시작될 때 시스템 프롬프트에 들어간다. 그래서 백엔드를 바꾸면 앱을 재시작해야 한다(UI의 "적용 (앱 재시작)").
  - 선택한 백엔드를 쓸 수 없으면(예: gbrain 미설치) 경고 로그를 남기고 JSON으로 돌아간다.
- **생각 과정(thinking) 패널**은 두 출처를 합쳐 턴마다 접히는 블록으로 그린다.
  - `/rpc`의 `conversation.activity` 사유 → 단계 타임라인 (텍스트 입력 전달 → 응답 생성 → 도구 호출 요청 → 결과 전달 → 말하기 시작 → 응답 완료, 턴 시작 기준 `+ms`)
  - `trace/hook.py`가 `BackgroundToolManager._run_tool`을 감싸 남기는 `[jisu-trace] {...}` 로그 → `log_buffer.parse_trace`가 `entry.trace`로 파싱 → 도구별 줄(✅/❌/⏹, 인자 요약, 소요 시간). 줄을 펼치면 인자·결과 JSON이 보인다.
  - 모든 백엔드(openai/hf/gemini)와 idle 도구가 같은 경로를 지나므로 전부 잡힌다. idle 도구는 `🤖 자율 행동` 블록으로 따로 표시된다.
  - 긴 문자열(카메라 base64 등)은 `TRACE_MAX_FIELD_CHARS`(500자)로 잘린다. 추적 실패는 도구 실행에 영향을 주지 않는다.
  - 블록은 처음에 접힌 채로 나오고, 언제든 클릭해 펼치거나 접을 수 있다. 접혀 있어도 요약 줄에 호출한 도구가 `🛠 고개 움직이기(move_head) ✅, …` 형태로 보인다(한국어 설명은 `app.js`의 `TOOL_LABEL`).
  - 모델 내부 추론 텍스트는 앱이 받지 않으므로 표시하지 않는다. 헤더의 `생각 과정` 체크박스로 숨길 수 있다(브라우저에 저장).
  - 로그 WS 재연결 시 backlog의 trace는 대화창에 다시 그리지 않는다.

## GBrain 백엔드 설정

GBrain 소스는 git submodule로 `vendor/gbrain`에 있다(`robertchoi/gbrain`, Bun/TypeScript).

```bash
git submodule update --init vendor/gbrain     # 처음 clone 했을 때
git -C vendor/gbrain lfs pull                 # LFS 객체가 있으면 받는다 (없으면 아무 일도 안 함)

# Bun 설치 (1.4.4+)
curl -fsSL https://bun.sh/install | bash                  # macOS / Linux
powershell -c "irm bun.sh/install.ps1 | iex"              # Windows

cd vendor/gbrain && bun install && bun link   # `gbrain` 명령이 PATH에 생긴다
gbrain --version
```

그다음 대시보드 기억 패널에서 백엔드를 `GBrain`으로 고른다. **GBrain 초기화** 버튼을 누르고, **적용 (앱 재시작)**을 누른다.

- 브레인 위치: `<앱 데이터 폴더>/gbrain/.gbrain`. 기본값은 `~/.local/share/reachy_mini_conversation_app/gbrain`이다. 개인 `~/.gbrain`과 분리되어 있다.
- 초기화 명령은 `gbrain init --pglite --no-embedding --search-mode conservative --non-interactive --json`이다. API 키 없이 키워드 검색으로 동작한다.
  - `--search-mode`를 빼면 환경 변수의 LLM 키를 감지해 `tokenmax`(외부 reranker 사용)가 자동으로 선택된다. 그래서 `conservative`로 고정한다.
- `gbrain`은 `#!/usr/bin/env bun` 스크립트다. PATH에 없으면 `$BUN_INSTALL/bin`(기본 `~/.bun/bin`)에서 찾고, 실행할 때 그 경로를 PATH에 넣는다.
- 임베딩 없이 동작하는 GBrain의 기억 검색은 **3글자 미만 단어를 무시**하고 단어 전체가 일치해야 한다(`vendor/gbrain/src/core/search/fact-relevance.ts`). 그래서 "매운 음식" 같은 한국어 검색은 대부분 빈 결과가 나온다. 결과가 비면 저장된 기억을 대상으로 로컬 부분 문자열 검색을 대신 돌린다. 로봇 대화는 기억 전체를 프롬프트에 넣으므로 이 제약의 영향을 받지 않는다.
- 연동에 쓰는 명령은 `gbrain call remember|recall|forget '<json>'`이다(`vendor/gbrain/docs/protocol/MEMORY_VERBS_v1.md`).
- GBrain의 `forget`은 해당 기억을 **철회**한다. 같은 문장을 다시 `remember`하면 거부된다(`fact_withdrawn`).

| 환경 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `JISU_MEMORY_BACKEND` | `gbrain` | 대화 앱이 쓸 기억 백엔드 |
| `JISU_GBRAIN_BIN` | `gbrain` | gbrain 실행 파일 경로 |
| `JISU_GBRAIN_HOME` | `<앱 데이터 폴더>/gbrain` | `GBRAIN_HOME` 경로 |
| `JISU_GBRAIN_TIMEOUT_S` | `30` | CLI 호출 타임아웃(초) |

## 기억 카테고리와 페르소나 (`jisu.persona`)

페르소나는 두 층으로 나눈다.

| 층 | 위치 | 내용 |
|---|---|---|
| 고정 정체성 | `profiles/<이름>/profile.md` (그대로) | 이름·정체성, 응답 규칙, 도구 목록, 목소리, 인사 |
| 성장하는 페르소나·기억 | GBrain (`entity` + `kind`로 분류) | 대화하며 쌓이는 성격·말투, 약속, 사용자 취향·행동 |

카테고리 표는 `src/jisu/persona/categories.py` 한 곳에 있다.

| 카테고리 | entity | kind | 대시보드 칩 |
|---|---|---|---|
| `persona_trait` | `reachy-mini` | `belief` | 페르소나 |
| `persona_style` | `reachy-mini` | `preference` | 페르소나 |
| `promise` | `reachy-mini` | `commitment` | 약속 |
| `user_preference` / `user_behavior` / `user_fact` | `user` | `preference` / `event` / `fact` | 사용자 |

- 기존 `remember` 도구는 `user_fact`로 저장한다. entity 없이 저장된 예전 기억도 `user_fact`로 보인다.
- 세션 프롬프트는 `[나(Reachy)…] → [약속] → [사용자…]` 섹션으로 profile 지시문 앞에 붙는다. 충돌하면 profile이 우선이다.
- LLM은 `update_persona` 도구(`src/jisu/tools/`)로 성격·말투·약속을 직접 남긴다. 런처가 `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`와 `AUTOLOAD_EXTERNAL_TOOLS=1`을 넣어 준다. `.env`에 같은 변수가 있으면 그 값이 이긴다.
- json 백엔드는 카테고리를 저장하지 않는다. 칩이 숨겨지고 `update_persona`는 오류를 돌려준다.

### 페르소나 적용 가이드

1. **어디에 쓸지:** "새 사용자를 만나도 똑같아야 하나?" → 예면 `profile.md`, 아니면 GBrain.
2. **문장 쓰는 법:** GBrain에는 "나는 ~한다" / "사용자는 ~한다" 형태로, 한 문장에 사실 하나만 쓴다. 명령문("~해라")은 `profile.md`에만 쓴다.
   한국어 키워드 검색은 3글자 미만 단어를 버리므로 핵심 단어는 3글자 이상으로 쓴다.
3. **초기 페르소나:** `src/jisu/persona/seeds/<프로필 이름>.toml`에 3~7문장을 적는다 (예: `desk_companion_ko.toml`).
   GBrain을 처음 초기화할 때 자동으로 들어가고, 대시보드의 **페르소나 시드 넣기**로 다시 넣을 수 있다. 이미 있는 문장은 "이미 있음"으로 건너뛴다.
4. **확인:** 기억 패널의 **프롬프트 미리보기**로 실제로 들어가는 섹션을 본다.
5. **지우기:** 기억 패널에서 id로 지운다. GBrain에서 지운 문장은 철회 상태가 되어 같은 문장으로 다시 저장되지 않는다 (시드도 다시 넣지 않음).

## 새 기억 백엔드 추가 (SQLite, 온톨로지 등)

1. `memory/<name>_backend.py`에 `MemoryBackend` 프로토콜(`memory/base.py`)을 구현한다.
   - 대화 경로의 메서드(`add_fact`, `forget_fact`, `list_facts`, `search`, `format_for_prompt`)는 실패해도 raise하지 않고 로그를 남긴 뒤 빈 결과를 돌려준다.
2. `memory/registry.py`의 `_register_builtin_backends()`에 `register_backend("<name>", "<표시 이름>", Factory)`를 추가한다.
3. `tests/jisu/test_dashboard_memory_backends.py`에 계약 테스트를 추가한다.

이렇게 하면 대시보드 드롭다운, `--backend`, `JISU_MEMORY_BACKEND`에 바로 나타난다.

## 테스트

```bash
pytest tests/jisu -v
```
