# 시뮬레이터 테스트 대시보드 — 설계 문서

- **작성일**: 2026-11-17
- **상태**: 구현 완료(게이트 통과), GBrain 실제 왕복 검증 완료.
  시뮬+음성 전체 E2E와 브라우저 UI 확인은 아직 남음. 아래 "알려진 이슈 & 다음 개선" 참조.
- **브랜치**: `feat/simulator-dashboard` (`dev/gemini` 기준)
- **사용법**: [`src/jisu/dashboard/README.md`](../src/jisu/dashboard/README.md)

## 배경과 핵심 제약

리치미니를 테스트할 때 대화, 로그, 장기 기억이 흩어져 있다.

- 대화는 스피커로만 들린다.
- 로그는 콘솔에만 남는다.
- 장기 기억은 `memory.py`의 JSON 파일 하나로 고정되어 있다.

이 셋을 **한 화면에서 보고 조작하는 독립 대시보드**를 만든다. 장기 기억은 **GBrain**을 붙일 수 있게 하고, 이후 SQLite·온톨로지 같은 다른 저장소도 쉽게 바꿔 끼울 수 있게 한다.

**제약 (사용자 요구)**

- 기존 코드(`src/reachy_mini_conversation_app/**`, `memory.py` 포함)는 **수정하지 않는다**.
- 신규 코드는 모두 `src/jisu/dashboard/`에 모은다.
- `reachy-mini-daemon` 기반의 기존 실행 방식(시뮬레이터 + Desk Companion Ko 창)과 별개로, UI를 추가로 띄운다.
- 시뮬레이터 기준으로 개발하고, Windows에서도 동작해야 한다(하드코딩 경로나 OS 전용 API 금지).

## 재사용하는 기존 부품

| 부품 | 출처 | 용도 |
| :--- | :--- | :--- |
| JSON-RPC WebSocket `/rpc` | `console.py` (`JsonRpcServer`, `reachy_mini/apps/jsonrpc_server.py`) | 대화창의 유일한 통로 |
| `conversation.transcript {role, text, final}` 알림 | `console.py:_dispatch_transcript` ← `ConversationHandler._emit_transcript` | 음성 대화 실시간 자막. `final=false`는 누적 문장이라 말풍선을 덮어쓴다 |
| `conversation.turn` / `conversation.phase` 알림 | `console.py` | 상태 표시(듣는 중/생각 중/말하는 중) |
| `conversation.say {text}` | `console.py` → 각 핸들러 `say()` (사용자 턴으로 주입) | 텍스트 채팅 입력 |
| `conversation.interrupt`, `conversation.mic` | `console.py` | 말 끊기, 마이크 음소거 |
| 로그 포맷 `%(asctime)s %(levelname)s %(name)s:%(lineno)d \| %(message)s` | `utils.setup_logger` | 로그 라인을 레벨·logger·메시지로 파싱 |
| `MemoryFact`, `ForgetMemoryResult`, `normalize_memory_text`, `memory_path_for_instance`, `MAX_FACTS` | `memory.py` | 백엔드 공통 데이터형·정규화·저장 위치 |
| 데몬 기동 절차 (health `:8000/api/state/full`, macOS `mjpython` + `DYLD_FALLBACK_LIBRARY_PATH`) | `run_sim_mujoco.sh` | 런처가 Python으로 옮겨 Windows에서도 동작 |
| `fastapi`, `uvicorn`, `httpx` | 기존 의존성 | 신규 pip 의존성 없음 |

**재사용하지 않는 것**

- 기존 웹 UI(7860). 대시보드는 별도 포트(7870)에서 띄우고, 7860 UI는 링크로만 연결한다.

## 아키텍처

```
python -m jisu.dashboard   (FastAPI :7870, 브라우저 자동 오픈)
 ├─ launcher.py   데몬(필요 시) + 대화 앱을 subprocess로 기동/재시작/정지
 │                stdout/stderr → LogBuffer (ring buffer 3000줄) → /ws/logs
 ├─ server.py     /api/app/{status,start,stop,restart}
 │                /api/memory{,/forget,/search,/clear,/backends,/gbrain/init}
 │                /ws/logs, /static
 └─ static/       index.html · app.js · style.css
                  브라우저가 ws://localhost:7860/rpc 에 직접 접속 (대화창)

대화 앱 프로세스 = python -m jisu.dashboard.app_entry --ui --profile desk_companion_ko
 └─ memory/hook.py: 기억 호출 지점 3곳을 JISU_MEMORY_BACKEND 백엔드로 연결
    → reachy_mini_conversation_app.main.main() (기존 앱 그대로)
```

**화면 구성**

- 왼쪽 위: 대화창
- 왼쪽 아래: 로그
- 오른쪽: 장기 기억 패널
- 상단: 앱·데몬·대화 연결·턴 상태 표시와 시작/재시작/정지 버튼

**대화창**

- 사용자 음성(`🎤`)과 리치미니 응답(`🤖`)이 실시간 자막으로 뜬다. 진행 중인 자막은 흐리게 표시하다가 `final`이 오면 확정한다.
- 채팅은 `conversation.say`로 보낸다. `say`로 넣은 텍스트는 user 자막으로 되돌아오지 않으므로, 내가 입력한 말풍선(`⌨️`)은 UI가 직접 그린다.
- 앱이 재시작되면 2초 간격으로 자동 재연결한다.

**로그**

- 출처(app/daemon/dashboard), 레벨(DEBUG+ ~ ERROR+), 검색어로 거르고, 자동 스크롤을 지원한다.
- 앱 로그 포맷이 아닌 줄(uvicorn, 데몬, traceback)은 접두어로 레벨을 추정한다.

## 핵심 결정

### 핵심 결정 1 — 런처형 (대시보드가 앱을 직접 띄운다)

콘솔 로그를 가져오려면 대시보드가 앱 프로세스의 stdout을 직접 잡아야 한다. 이미 떠 있는 앱의 `/rpc`에 붙기만 하는 방식(부착형)은 로그를 파일로 남기도록 기존 코드를 고쳐야 해서 제약에 어긋난다.

- 대시보드가 데몬과 대화 앱을 subprocess로 띄운다.
- 8000 포트에 데몬이 이미 떠 있으면 그 데몬을 재사용하고, 종료할 때도 남겨 둔다.
- 7860을 다른 앱이 쓰고 있으면 시작을 거부한다(409 + 로그).
- 자식 프로세스는 별도 세션/프로세스 그룹으로 띄운다(POSIX `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`). 그래서 터미널 Ctrl+C가 자식에게 직접 가지 않고, 대시보드가 정리할 때 한 번만 interrupt를 보낸다(POSIX SIGINT / Windows CTRL_BREAK). 앱의 sleep-on-exit 경로도 그대로 동작한다.

### 핵심 결정 2 — memory hook: 호출 지점의 모듈 속성을 교체한다

`memory.py`를 고치지 않고 백엔드를 바꾸기 위해 hook을 쓴다. 기억을 쓰는 곳은 3곳이고, 모두 `from ...memory import fn`으로 이름을 바인딩한다.

| 호출 지점 | 함수 |
| :--- | :--- |
| `prompts.py` | `format_memory_for_prompt` |
| `tools/remember.py` | `add_memory_fact` |
| `tools/forget.py` | `forget_memory_fact` |

- 내장 도구는 `importlib.import_module`로 로드되어 `sys.modules`에 캐시된다(`core_tools.py:291`). 그래서 모듈 속성을 한 번 교체하면 프로세스가 끝날 때까지 유지된다.
- `memory.py` 자체는 교체하지 않는다. JSON 백엔드가 원본 함수를 그대로 쓰게 하기 위해서다.
- `HOOK_TARGETS`에 정의된 대상이 사라지면(업스트림이 이름을 바꾸면) `RuntimeError`를 낸다. 조용히 우회되는 일을 막는다.
- 선택한 백엔드를 만들 수 없으면(예: gbrain 미설치) warning을 남기고 **json으로 폴백**한다. 대화 루프를 죽이지 않는다.
- `json`을 고르면 아무것도 교체하지 않는다. 기존 동작과 바이트 단위로 같다.
- 백엔드 인스턴스는 `instance_path`마다 하나씩 캐시한다.

### 핵심 결정 3 — 백엔드는 Protocol + registry

```python
class MemoryBackend(Protocol):
    name: str
    def list_facts(self) -> list[MemoryFact]: ...
    def add_fact(self, text: str) -> MemoryFact | None: ...
    def forget_fact(self, query: str | None) -> ForgetMemoryResult: ...
    def clear(self) -> None: ...
    def search(self, query: str, limit: int) -> list[MemoryFact]: ...
    def format_for_prompt(self) -> str: ...
```

- `registry.register_backend(name, label, factory)`로 등록한다. 등록하면 대시보드 드롭다운, `--backend`, `JISU_MEMORY_BACKEND`에 바로 나타난다.
- SQLite·온톨로지는 `memory/<name>_backend.py` 하나를 만들고 `_register_builtin_backends()`에 한 줄을 추가하면 된다.
- 대화 경로의 메서드는 실패해도 raise하지 않는다(로그 + 빈 결과). `clear`는 대시보드 전용이라 실패를 그대로 올린다.
- 프롬프트 문구는 `format_facts_for_prompt`로 공유한다. upstream 문구와 바이트 단위로 같다는 것을 테스트로 고정했다.

### 핵심 결정 4 — GBrain 소스는 저장소 루트 `vendor/gbrain` (git submodule)

- 처음에는 `src/jisu/dashboard/vendor/gbrain`에 submodule로 넣었다. 그런데 GBrain(약 260MB) 안의 `.py` 스크립트 때문에 필수 게이트가 깨졌다(**ruff 52건, mypy 74건**, 전부 vendor 파일).
- 사용자 결정으로 **`vendor/gbrain`**(저장소 루트)으로 옮겼다.
  - mypy는 `files=["src/"]`라서 자동으로 제외된다.
  - ruff는 `vendor/ruff.toml`(`exclude = ["*"]`)로 제외한다. `pyproject.toml`은 수정하지 않았다.
- submodule 출처: `https://github.com/robertchoi/gbrain` (garrytan/gbrain fork). `git lfs pull`은 해 두었지만 현재 LFS 객체는 없다.

### 핵심 결정 5 — GBrain 연동은 memory verb CLI (`gbrain call`)

- 처음 계획은 "Markdown 파일을 쓰고 `gbrain import`"였다. 소스를 확인해 보니 GBrain에 이 용도에 정확히 맞는 memory verb가 있어서 그쪽으로 바꿨다(`vendor/gbrain/docs/protocol/MEMORY_VERBS_v1.md`).

| 백엔드 메서드 | GBrain 호출 |
| :--- | :--- |
| `add_fact` | `gbrain call remember '{"fact", "provenance", "kind": "fact"}'` → `{id, status}` (`duplicate`이면 기존 id) |
| `list_facts` / `format_for_prompt` | `gbrain call recall '{"limit": 60}'` → `facts[]` 최신순 |
| `search` | `gbrain call recall '{"question", "limit"}'` (+ 로컬 대체 검색, 아래 실측 참조) |
| `forget_fact` | 부분 문자열로 후보를 찾은 뒤(JSON 백엔드와 같은 규칙) `gbrain call forget '{"id", "reason"}'` |
| `clear` | 모든 fact에 `forget` |

- **전용 브레인**: `GBRAIN_HOME=<앱 데이터 폴더>/gbrain`(GBrain이 `.gbrain`을 덧붙인다). 개인 `~/.gbrain`과 분리된다.
- **외부 DB 차단**: `DATABASE_URL`, `GBRAIN_DATABASE_URL`, `GBRAIN_BRAIN_ID`, `GBRAIN_SOURCE`는 GBrain에 넘기지 않는다.
- **초기화**: `gbrain init --pglite --no-embedding --search-mode conservative --non-interactive --json` (대시보드의 "GBrain 초기화" 버튼).
- **응답 파싱**: CLI 출력 앞뒤에 notice 줄이 섞이므로, 첫 JSON 객체만 골라 읽는다. 에러 응답 `{error, code, message}`은 `code: message`로 남긴다.
- **테스트 용이성**: CLI 실행은 주입 가능한 `CommandRunner`를 거친다. 테스트는 Fake를 쓴다.

## 파일 배치

| 경로 | 역할 |
| :--- | :--- |
| `src/jisu/dashboard/__main__.py` | CLI (`--backend`, `--headless`, `--no-start`, `--open-app-ui`, `--conversation-backend`, `--debug` …) |
| `src/jisu/dashboard/constants.py` | 튜닝 상수 (포트, 타임아웃, 버퍼 크기, GBrain 설정) |
| `src/jisu/dashboard/launcher.py` | `LaunchSettings`, 명령 생성 순수 함수, `ManagedProcess`, `SimulationLauncher` |
| `src/jisu/dashboard/log_buffer.py` | `LogBuffer`(스레드 안전 ring buffer + asyncio 구독), `parse_log_line` |
| `src/jisu/dashboard/server.py` | FastAPI 앱 조립 (`create_app`, `AppController` Protocol) |
| `src/jisu/dashboard/app_entry.py` | 대화 앱 진입점 (hook 설치 후 `main.main()`) |
| `src/jisu/dashboard/memory/{base,registry,json_backend,gbrain_backend,hook}.py` | 기억 백엔드 |
| `src/jisu/dashboard/static/{index.html,app.js,style.css}` | UI (바닐라 JS, 라이트/다크 대응, 좁은 화면 1열) |
| `src/jisu/dashboard/README.md` | 실행법, GBrain 설치, 환경 변수, 백엔드 추가 방법 |
| `tests/jisu/test_dashboard_*.py` | 테스트 4개 파일 |
| `vendor/gbrain` | GBrain submodule |
| `vendor/ruff.toml` | vendor 디렉터리를 ruff에서 제외 |

`pyproject.toml`의 `packages.find where=["src"]`가 `jisu` 패키지를 자동으로 포함하고, editable 설치도 `src/` 전체를 잡는다. 따라서 설정 파일 변경도, `uv sync`도 필요 없었다.

## 설정

| 환경 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `JISU_MEMORY_BACKEND` | `json` | 대화 앱이 쓸 기억 백엔드 |
| `JISU_GBRAIN_BIN` | `gbrain` (PATH → `$BUN_INSTALL/bin` 순으로 탐색) | gbrain 실행 파일 |
| `JISU_GBRAIN_HOME` | `<앱 데이터 폴더>/gbrain` | `GBRAIN_HOME` |
| `JISU_GBRAIN_TIMEOUT_S` | `30` | CLI 호출 타임아웃(초) |

- 포트: 대시보드 7870, 대화 앱 7860, 데몬 8000.
- `CONVERSATION_BACKEND`는 기본적으로 `.env` 설정을 따르고, `--conversation-backend`로 덮어쓸 수 있다.
- 사용자 지시에 따라 기존 `config.py`/`.env.example`에는 추가하지 않고, 상수는 `constants.py`, 설명은 README에 둔다.

## GBrain 실측 결과 (2026-11-17)

**환경**: Bun 1.4.4, gbrain 0.60.687.0 (`bun link` → `~/.bun/bin/gbrain` → `vendor/gbrain`). scratchpad의 임시 브레인 3개로 검증했고, 실제 앱용 브레인은 건드리지 않았다.

**왕복 결과 — 전부 정상**

| 경로 | 확인 내용 |
| :--- | :--- |
| 백엔드 직접 호출 | 저장 → 중복 저장 시 같은 id(`duplicate`) → 최신순 조회 → 검색 → 프롬프트 문구 → 부분 문자열 삭제 → 전체 삭제 |
| 대화 앱 도구 경로 | hook 설치 후 진짜 `Remember`/`Forget` 도구 클래스 호출 → `prompts.format_memory_for_prompt`에 반영 / 삭제 후 제외 / 없는 기억 삭제 시 기존 에러 메시지 |
| 대시보드 HTTP API | 백엔드 목록(초기화 전 "not initialized") → `/api/memory/gbrain/init` → 추가 → 검색 → 조회 → 삭제 |

- 지연: 호출당 **0.5~1.8초**(대부분 Bun 기동 시간). 초기화는 약 4.7초.
- 대화 중 `remember`/`forget` 도구 응답이 약 1초 늦어지고, 세션 시작 시 프롬프트 생성에 약 1초가 더 든다.

**실측하면서 발견하고 수정한 것**

1. **검색 모드 자동 선택**: `--search-mode`를 빼고 초기화하면 환경 변수의 LLM 키를 감지해 `tokenmax`(외부 reranker `voyage:rerank-3-lite`, 결과 50개)를 고른다. → `conservative`(reranker 없음, 결과 10개)로 고정했다. `gbrain config get search.mode`로 확인했다.
2. **PATH 문제**: Bun의 PATH 설정은 `~/.zshrc`(대화형 셸)에만 있고, `gbrain`은 `#!/usr/bin/env bun` 스크립트다. → `resolve_gbrain_binary()`가 `$BUN_INSTALL/bin`(기본 `~/.bun/bin`)도 찾고, 실행 env의 PATH 앞에 그 경로를 넣는다. `PATH=/usr/bin:/bin`에서도 동작하는 것을 확인했다.
3. **에러 메시지**: 에러 응답에서 `error` 코드만 남기던 것을 `code: message`로 보강했다. 예: `invalid_params: fact_withdrawn: …`

**한국어 검색 제약 (GBrain 측, 우회 적용)**

- 임베딩이 없을 때 fact 랭킹은 **3글자 미만 단어를 버리고**(`vendor/gbrain/src/core/search/fact-relevance.ts:49`, `t.length >= 3`) 단어 전체가 일치해야 한다.
- 실측: "매운 음식", "매운", "음식" → `no_query_terms`로 빈 결과. "음식을", "매운 음식을", "고양이" → 찾음.
- 우회: `GBrainMemoryBackend.search`는 GBrain 결과가 비면 `keyword_search`(로컬 부분 문자열 매칭)로 다시 찾는다.
- 로봇 대화는 기억 전체를 프롬프트에 넣고, 삭제도 부분 문자열로 찾으므로 **이 제약의 영향을 받지 않는다**. 영향은 대시보드 검색창뿐이다.

**철회 동작 (GBrain 사양)**

- GBrain의 `forget`은 삭제가 아니라 **철회**다. 같은 문장을 다시 `remember`하면 `fact_withdrawn`으로 거부된다.
- 이때 `add_fact`는 None을 반환하고, remember 도구는 기존 "nothing was saved" 에러를 돌려준다.

## 알려진 이슈 & 다음 개선

| # | 항목 | 성격 | 다음 개선 방향 |
| :-- | :--- | :--- | :--- |
| 1 | 한국어 검색 품질 (위 실측) | GBrain 키워드 랭킹 | GBrain 임베딩 활성화(로컬 Ollama / llama-server) → cosine 경로로 검색 |
| 2 | 대시보드 기억 목록의 "삭제" 버튼이 해당 문장을 부분 문자열 질의로 보냄 | 대시보드 | 그 문장을 포함하는 더 최신 기억이 있으면 그쪽이 지워질 수 있음 → id 기준 삭제 API 추가 |
| 3 | JSON 백엔드를 대시보드와 앱 두 프로세스가 같은 파일에 씀 | 동시성 | 원자적 교체(atomic replace)라 파일이 깨지지는 않지만, 거의 동시에 쓰면 한쪽 변경이 유실될 수 있음 → 파일 락 또는 앱 쪽 RPC 경유 |
| 4 | 사용자 제보: "기존 reachy-mini-daemon이 구동 안 됨" | 미확인 | 조사 시점에는 대시보드가 띄운 데몬이 `mjpython` + `headless=False`로 정상 응답(`/api/state/full` 200)했고, MuJoCo 창은 왼쪽 보조 모니터(x=-2461)에 떠 있었다. 증상 확인 후 재현 필요 |
| 5 | `uv build` wheel에는 `jisu` static 파일이 포함되지 않음 | 패키징 | 저장소에서 실행하는 개발용 도구라 현재는 범위 외. 배포가 필요해지면 `package-data` 추가 |

## 오류 처리

- **대화 경로**(`add_fact`/`forget_fact`/`list_facts`/`search`/`format_for_prompt`)는 GBrain 실패(비정상 종료, 타임아웃, JSON 없음, 에러 응답)를 로그로 남기고 None 또는 빈 결과를 돌려준다. raise하지 않는다.
- **백엔드 생성 실패**(gbrain 없음 / 미초기화) → `BackendUnavailableError`
  - hook은 json으로 폴백한다.
  - 대시보드 API는 409로 응답하고, 원인과 설치 안내를 표시한다.
- **알 수 없는 백엔드 이름** → 404
- **앱 시작 실패**(7860 사용 중, 데몬 조기 종료, 60초 대기 초과) → 409 + 로그 패널 ERROR
- **프로세스 정지**: interrupt를 보낸 뒤 15초 안에 끝나지 않으면 kill한다(추가 5초 대기).
- **UI 확인 절차**: 브라우저 `confirm()` 대화상자 대신 "같은 버튼 두 번 누르기"로 확인한다(전체 삭제, 백엔드 적용).

## 테스트

`tests/jisu/` — 4개 파일, 41개 테스트. 네트워크와 프로세스 기동 없이 실행된다.

| 파일 | 내용 |
| :--- | :--- |
| `test_dashboard_memory_backends.py` | JSON 백엔드와 upstream 저장소 일치, 프롬프트 문구 일치, GBrain 계약(Fake CLI: 왕복·중복·실패 시 무예외·forget 실패·초기화·바이너리 없음·Bun 경로 탐색·에러 메시지·한국어 검색 대체), JSON 파싱, wire 변환, registry |
| `test_dashboard_memory_hook.py` | hook 대상 존재, json이면 교체 없음, 도구·프롬프트가 선택한 백엔드를 거침 + uninstall 복원, 쓸 수 없는 백엔드는 json 폴백, 대상이 사라지면 RuntimeError |
| `test_dashboard_logs_launcher.py` | 로그 파싱(앱 포맷과 기타), ring buffer, 스레드 → asyncio 구독, 데몬 명령(headless/macOS/Linux/Windows), 앱 명령·env |
| `test_dashboard_server.py` | 페이지·static·config, lifecycle 라우트와 409, JSON 기억 CRUD, 백엔드 목록·404·409, 로그 WebSocket backlog와 스트림 |

**전체 게이트**: ruff·format·mypy(77 files) 통과, `pytest tests/` **602 passed**, 2 skipped, 2 xfailed. 기존 테스트 중 깨진 것은 없다.

**수동 검증**: 위 "GBrain 실측 결과"의 3개 경로, 대시보드 서버 스모크(임시 `XDG_DATA_HOME`), `app_entry --help`, `node --check app.js`.

## 범위 외 (후속)

- GBrain 임베딩과 dream cycle(enrichment, 중복 병합)
- GBrain MCP 연동(`gbrain serve`)
- 로봇이 대화 중 기억을 검색하는 recall 도구(현재는 프롬프트에 전체 주입)
- 대시보드 인증과 원격 접속(현재 `127.0.0.1` 바인딩)
- SQLite·온톨로지 백엔드 구현(확장 경로만 준비)
- wheel 패키징
