# 시뮬레이터 테스트 대시보드 구현 계획

> **For agentic workers:** 태스크 단위로 구현하고 체크박스로 추적한다. 각 태스크 끝에서
> 게이트를 통과시킨다. 커밋은 사용자 승인 후에 한다.

**Goal:** `python -m jisu.dashboard` 한 번으로 시뮬 데몬과 대화 앱을 띄우고, 브라우저 탭 하나에서 대화(음성 자막 + 텍스트 채팅), 실시간 로그, 장기 기억(JSON/GBrain 선택)을 테스트한다. 기존 앱 코드는 수정하지 않는다.

**Architecture:** 런처형 FastAPI 대시보드(:7870)가 데몬과 앱을 subprocess로 관리하며 stdout을 로그 버퍼로 받는다. 대화창은 브라우저가 앱의 `/rpc`(JSON-RPC)에 직접 붙는다. 장기 기억은 `MemoryBackend` Protocol + registry 구조이고, 앱 프로세스 안에서 `app_entry`가 hook을 설치해 기억 호출 지점 3곳을 선택한 백엔드로 연결한다. GBrain은 `vendor/gbrain` submodule이고 `gbrain call` memory verb로 연동한다.

**Spec:** [`oss_plan/2026-11-17-simulator-dashboard-design.md`](2026-11-17-simulator-dashboard-design.md)

## Global Constraints

- 기존 코드(`src/reachy_mini_conversation_app/**`, `memory.py` 포함)와 `pyproject.toml`·`config.py`·`.env.example`은 **수정 금지**. 신규 코드는 `src/jisu/dashboard/`, 테스트는 `tests/jisu/`.
- 신규 pip 의존성 금지. `fastapi`·`uvicorn`·`httpx`는 기존 의존성이다.
- 게이트: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v`
- 대화 경로의 기억 메서드는 실패 시 raise하지 않고 로그 + 빈 결과를 돌려준다(CLAUDE.md 도구 규칙).
- 테스트는 네트워크·프로세스 기동·GBrain 설치 없이 통과해야 한다(Fake runner / Fake controller).
- 하드코딩 경로나 OS 전용 API 금지(Windows 팀원). OS별 분기는 `platform.system()`과 `getattr`로 처리한다.
- 브랜치: `feat/simulator-dashboard` (`dev/gemini` 기준).

## 성공 기준 (Definition of Done)

테스트는 기존 패턴을 따른다. 손수 만든 `Fake*` 클래스(MagicMock 지양), 비동기는 `@pytest.mark.asyncio`, 경계값은 `@pytest.mark.parametrize`.

**전체 완료 조건:**

1. 시뮬에서 `python -m jisu.dashboard` 실행 시 다음이 전부 동작한다.
   - 데몬과 앱이 기동되고 7870 탭이 열린다.
   - 로그 패널에 앱·데몬 로그가 실시간으로 흐른다.
   - 마이크 발화와 로봇 응답이 대화창에 자막으로 뜬다.
   - 채팅 입력에 로봇이 음성으로 응답한다.
   - json ↔ gbrain 전환 후 "기억해줘" 대화가 선택한 저장소에 반영된다.
2. 전체 게이트 통과. 기존 테스트 **0개 깨짐**.
3. `JISU_MEMORY_BACKEND` 미설정 또는 `json`이면 기존 기억 동작이 그대로다(hook이 아무것도 교체하지 않음).
4. gbrain 미설치나 미초기화 상태에서 gbrain을 골라도 대화가 계속된다(json 폴백 + 경고 로그).

**태스크별 수용 기준:**

| 태스크 | 수용 기준 (테스트로 증명) |
| :--- | :--- |
| 1 submodule | `git submodule status`에 `vendor/gbrain` / ruff·mypy가 vendor를 스캔하지 않음 |
| 2 memory | JSON 백엔드가 upstream `memory.v1.json`과 같은 결과·프롬프트 문구를 냄 / GBrain Fake 왕복(중복 id, 최신순, 부분 문자열 삭제, 전체 삭제, env 격리) / CLI 실패 시 무예외 / 초기화·바이너리 없음 / hook이 3곳을 교체하고 uninstall로 복원 / 쓸 수 없는 백엔드는 json 폴백 / hook 대상이 사라지면 RuntimeError |
| 3 logs·launcher | 앱 로그 포맷 파싱과 기타 레벨 추정 / ring buffer 용량·seq / 다른 스레드 → asyncio 구독자 전달 / 데몬 명령(headless·macOS mjpython+DYLD·Linux·Windows .exe) / 앱 명령·env(`JISU_MEMORY_BACKEND`, 포트, 프로필, UTF-8) |
| 4 server·UI | `/`·static·`/api/config` / lifecycle 라우트와 실패 시 409 + ERROR 로그 / JSON 기억 CRUD·검색 / 백엔드 목록(사용 가능 여부와 원인)·404·409 / `/ws/logs` backlog 후 스트림 / `app.js` 문법 검사 |
| 6 GBrain 실측 | 실제 gbrain으로 백엔드·앱 도구 경로·HTTP API 왕복 / conservative 모드 확인 / PATH에 bun이 없어도 동작 / 한국어 2글자 검색어 대체 |

---

### Task 1: 브랜치 + GBrain submodule

**Files:** Create: `.gitmodules`, `vendor/gbrain`(submodule), `vendor/ruff.toml`

**Steps:**
- [x] `feat/simulator-dashboard` 브랜치 생성(작업트리의 `run_sim_mujoco.sh` 변경은 건드리지 않음)
- [x] `git submodule add https://github.com/robertchoi/gbrain` → `git lfs pull` (LFS 객체 없음 확인)
- [x] `src/jisu/dashboard/vendor/gbrain`에 두니 게이트 깨짐(ruff 52건, mypy 74건) → 사용자 결정으로 **`vendor/gbrain`**으로 이동. `.gitmodules` 섹션 이름과 `.git/modules` 경로 정리
- [x] `vendor/ruff.toml`(`exclude = ["*"]`)로 ruff 제외. mypy는 `files=["src/"]`라 자동 제외
- [x] GBrain CLI 확인: `gbrain call <op> '<json>'`, memory verb(remember/recall/forget) 응답 형식, `GBRAIN_HOME`(`.gbrain`을 덧붙임), `init --pglite --no-embedding --non-interactive --json`

### Task 2: 장기 기억 백엔드 + hook

**Files:** Create: `src/jisu/__init__.py`, `src/jisu/dashboard/{__init__,constants}.py`,
`src/jisu/dashboard/memory/{__init__,base,registry,json_backend,gbrain_backend,hook}.py`,
`tests/jisu/test_dashboard_memory_backends.py`, `tests/jisu/test_dashboard_memory_hook.py`

**Interfaces:**
- `MemoryBackend(Protocol)`: `name`, `list_facts()`, `add_fact(text)`, `forget_fact(query)`, `clear()`, `search(query, limit)`, `format_for_prompt()`
- `register_backend(name, label, factory, *, replace=False)`, `create_backend(name, instance_path)`, `backend_status(name, instance_path) -> (bool, str)`, `available_backends()`, `selected_backend_name()`
- `GBrainMemoryBackend(instance_path=None, *, runner=None, binary=None, home=None, timeout_s=None, require_ready=True)` + `initialize()`, `readiness_problem()`, `call(op, params)`
- `install_memory_hook(backend_name) -> InstalledHook` (`uninstall()`), `HOOK_TARGETS`
- 공통 헬퍼: `format_facts_for_prompt`, `substring_candidates`, `keyword_search`

**Steps:**
- [x] base: Protocol, `BackendUnavailableError`, 프롬프트 문구(upstream과 동일), 부분 문자열 후보, 키워드 검색
- [x] json_backend: upstream `memory.py` 함수에 위임(원본 함수를 import 시점에 바인딩)
- [x] gbrain_backend: `CommandRunner` 주입, JSON 파싱(notice 줄 허용), wire → `MemoryFact`, 전용 `GBRAIN_HOME`, 외부 DB env 제거
- [x] registry: 내장 백엔드는 처음 쓸 때 등록(lazy), 환경 변수로 선택
- [x] hook: 3곳 교체, 대상 존재 가드, instance_path별 캐시, json 폴백, json이면 교체 없음
- [x] 테스트 → 게이트

### Task 3: 로그 버퍼 + 런처 + 앱 진입점

**Files:** Create: `src/jisu/dashboard/{log_buffer,launcher,app_entry}.py`, `tests/jisu/test_dashboard_logs_launcher.py`

**Interfaces:**
- `LogBuffer(max_lines)`: `append(source, line)`, `snapshot(after_seq)`, `clear()`, `subscribe()`, `unsubscribe(queue)`. `LogEntry(seq, ts, source, level, logger, message, raw)`
- `LaunchSettings`, `build_daemon_command(bin_dir, *, headless, system, libdir)`, `build_app_command(python, settings)`, `build_app_env(base_env, settings)`, `daemon_ready(port)`, `port_in_use(port)`
- `ManagedProcess(name, logs)`: `start`, `stop(graceful)`, `is_running`, `pid`, `returncode`
- `SimulationLauncher(settings, logs, cwd)`: `ensure_daemon`, `start_app(backend)`, `stop_app`, `restart_app(backend)`, `shutdown`, `status`
- 상수: `LOG_BUFFER_MAX_LINES=3000`, `DAEMON_WAIT_MAX_S=60`, `PROCESS_STOP_TIMEOUT_S=15`, `PROCESS_KILL_TIMEOUT_S=5`

**Steps:**
- [x] 로그 파서(앱 포맷 / `INFO:` 접두어 / traceback), 스레드 안전 ring buffer, 느린 구독자는 오래된 항목부터 버림
- [x] `run_sim_mujoco.sh` 절차를 Python으로 이식: 데몬 재사용·기동·health 대기, macOS mjpython + DYLD
- [x] 자식 프로세스를 별도 세션/그룹으로 띄움, 정지 시 interrupt → 대기 → kill, 7860 포트 충돌 감지
- [x] `app_entry`: `install_memory_hook(selected_backend_name())` → `reachy_mini_conversation_app.main.main()`
- [x] 테스트 → 게이트

### Task 4: 서버 + CLI + UI

**Files:** Create: `src/jisu/dashboard/{server,__main__}.py`, `src/jisu/dashboard/static/{index.html,app.js,style.css}`, `tests/jisu/test_dashboard_server.py`

**Interfaces:**
- `create_app(controller: AppController, logs: LogBuffer, *, app_port, instance_path=None) -> FastAPI`
- REST: `GET /api/config`, `GET /api/app/status`, `POST /api/app/{start,stop,restart}`, `GET|POST /api/memory`, `POST /api/memory/{forget,search,clear,gbrain/init}`, `GET /api/memory/backends`, `POST /api/logs/clear`
- WS: `/ws/logs` → `{"type":"backlog","entries":[…]}` 후 `{"type":"entry","entry":{…}}`
- CLI: `--host --port --profile --backend --conversation-backend --headless --no-start --no-browser --open-app-ui --debug --daemon-port --app-port`

**Steps:**
- [x] server: lifecycle 실패 → 409 + 로그, 백엔드 404/409 매핑, GBrain 초기화 엔드포인트
- [x] `__main__`: 서버를 먼저 띄우고 부팅은 백그라운드 스레드에서 수행, 브라우저 자동 오픈, 종료 시 `launcher.shutdown()`
- [x] UI
  - 대화창: `/rpc` 연결·자동 재연결, 진행 중 자막 덮어쓰기와 final 확정, `say`/`interrupt`/`mic`
  - 로그: 출처·레벨·검색어 필터, 자동 스크롤
  - 기억 패널: 백엔드 선택·적용, GBrain 초기화, 추가·검색·삭제·전체 삭제
  - 상태 표시 2초 폴링, 확인은 버튼 두 번 누르기(브라우저 대화상자 없음)
- [x] 테스트 → 게이트, `node --check app.js`

### Task 5: README + 전체 게이트

**Files:** Create: `src/jisu/dashboard/README.md`

**Steps:**
- [x] 실행법, 옵션, 동작 구조, GBrain 설치(Bun → `bun install && bun link`), 환경 변수, 새 백엔드 추가 방법
- [x] 전체 게이트 통과
- [x] 스모크: 대시보드 서버만 띄워(`--no-start`, 임시 `XDG_DATA_HOME`) 상태·백엔드·기억 API 확인, `app_entry --help`

### Task 6: GBrain 실제 왕복 검증 + 후속 수정

**Files:** Modify: `src/jisu/dashboard/constants.py`, `src/jisu/dashboard/memory/gbrain_backend.py`, `src/jisu/dashboard/README.md`, `tests/jisu/test_dashboard_memory_backends.py`

**Steps:**
- [x] Bun 1.4.4 / gbrain 0.60.687.0 설치 확인(`~/.bun/bin`, 링크가 `vendor/gbrain`을 가리킴)
- [x] scratchpad 임시 브레인으로 3개 경로 왕복: 백엔드 직접 / hook + `Remember`·`Forget` 도구 + 프롬프트 / 대시보드 HTTP API
- [x] 수정 ① 초기화 시 `--search-mode conservative` 고정(`tokenmax` 자동 선택 방지, `GBRAIN_SEARCH_MODE`)
- [x] 수정 ② `resolve_gbrain_binary()`로 `$BUN_INSTALL/bin` 탐색 + 실행 env PATH 보강(`#!/usr/bin/env bun` 대응)
- [x] 수정 ③ 에러 응답을 `code: message`로 기록(`_error_detail`)
- [x] 수정 ④ 한국어 2글자 검색어를 GBrain이 버리는 문제(`fact-relevance.ts:49`) → `search` 결과가 비면 `keyword_search`로 대체
- [x] 테스트 추가(Bun 경로 탐색, 에러 메시지, 검색 대체) → 전체 게이트(602 passed)

### Task 7: 남은 검증 + 마무리

**Steps:**
- [ ] 시뮬 + 음성 E2E (전체 완료 조건 1)
  - [ ] 마이크 발화와 응답 자막
  - [ ] 채팅 → 음성 응답
  - [ ] json → gbrain 전환, "기억해줘" 대화가 `gbrain call recall`에 반영
  - [ ] gbrain을 쓸 수 없을 때 json 폴백 경고와 대화 지속
- [ ] 브라우저 UI 렌더링 확인(라이트/다크, 좁은 화면)
- [ ] 사용자 제보 "데몬 구동 안 됨" 재현 확인(설계 문서 알려진 이슈 #4)
- [ ] 실제 앱용 브레인 초기화(대시보드 "GBrain 초기화" 버튼)
- [ ] 커밋·PR (사용자 승인 후, `feat/simulator-dashboard` → `dev/gemini`)
