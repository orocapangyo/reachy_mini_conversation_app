# Simulator Dashboard (`jisu.dashboard`)

리치미니 테스트용 독립 대시보드다. 브라우저 탭 하나에서 아래 세 가지를 한다.

- **대화창**: 스피커/마이크로 오간 대화를 실시간 자막으로 보고, 텍스트로 채팅한다.
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
| `--backend json\|gbrain` | 대화 앱이 쓸 장기 기억 백엔드 (기본: `JISU_MEMORY_BACKEND` 또는 `json`) |
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
 └─ memory/hook.py 가 기억 호출 지점을 선택한 백엔드로 연결한 뒤 기존 main() 실행
```

- **대화창**은 앱이 원래 제공하는 JSON-RPC(`/rpc`)를 쓴다.
  - 받는 알림: `conversation.transcript`(자막), `conversation.turn`(상태)
  - 보내는 요청: `conversation.say`(채팅 입력), `conversation.interrupt`(말 끊기), `conversation.mic`(음소거)
  - `say`로 보낸 문장은 자막으로 되돌아오지 않는다. 그래서 내가 입력한 말풍선은 UI가 직접 그린다.
- **장기 기억 hook**은 `prompts.format_memory_for_prompt`, `tools.remember.add_memory_fact`, `tools.forget.forget_memory_fact`의 모듈 속성만 바꾼다. `memory.py`는 그대로 둔다.
  - 기억은 세션이 시작될 때 시스템 프롬프트에 들어간다. 그래서 백엔드를 바꾸면 앱을 재시작해야 한다(UI의 "적용 (앱 재시작)").
  - 선택한 백엔드를 쓸 수 없으면(예: gbrain 미설치) 경고 로그를 남기고 JSON으로 돌아간다.

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
| `JISU_MEMORY_BACKEND` | `json` | 대화 앱이 쓸 기억 백엔드 |
| `JISU_GBRAIN_BIN` | `gbrain` | gbrain 실행 파일 경로 |
| `JISU_GBRAIN_HOME` | `<앱 데이터 폴더>/gbrain` | `GBRAIN_HOME` 경로 |
| `JISU_GBRAIN_TIMEOUT_S` | `30` | CLI 호출 타임아웃(초) |

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
