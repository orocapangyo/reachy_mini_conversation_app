# M10 셋업 대시보드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 처음 쓰는 사람이 터미널 없이 설정 웹 UI(7860)의 버튼만으로 Bookmark Insight 첫 세팅(Chrome 프로필 탐색 → `.env` 적용 → 동기화 → 결과 확인, 선택으로 데모 데이터 준비)을 끝내고, 결과를 화면에서 본다.

**Architecture:** 앱의 기존 FastAPI 설정 UI에 얹는다. 백엔드는 새 모듈 `src/reachy_mini_conversation_app/bookmark_insight_routes.py` 하나: 전송 독립적인 `BookmarkInsightOps`(상태·탐색·저장소 폴더 지정·생성·동기화·.env 적용·데모 준비)와 이를 기존 JSON-RPC 서버(`/rpc`)에 `bookmark_insight.*` 메서드로 붙이는 `register_bookmark_insight_methods()`로 구성하며, `tool_space_routes.py`·`personality_routes.py`와 같은 틀이다. 버튼은 전부 `bookmark_insight` 패키지의 파이썬 함수(`bookmark_insight.sync.chrome.discover_user_data`, `bookmark_insight.sync.mirror.sync_profiles`, 새로 분리할 `bookmark_insight.demo.create_demo_repo`)를 직접 호출하고, 서브프로세스·셸은 쓰지 않는다. 긴 작업은 `asyncio.to_thread`로 이벤트 루프 밖에서 돌리고, 동기화는 프로세스 안 가드 + 기존 `bookmark_insight.store.repo_lock`으로 겹침을 막는다. `.env` 쓰기는 `console.py`의 기존 `LocalStream._persist_env_values()`를 콜백으로 넘겨 재사용한다(새 .env 파서 없음). 프런트는 `static/js/components/bookmark-insight.js` 섹션 하나를 Settings 뷰에 추가하고 `api.js`의 `rpcCall` 래퍼로 호출한다. `console.py` 수정은 등록 호출 1곳뿐이다.

**Spec:** [`oss_plan/2026-11-06-weekly-insight-design.md`](2026-11-06-weekly-insight-design.md) §2.3 설정(.env), §6.3 마일스톤 표 M10 행

## 배경과 요구사항

지금 첫 세팅은 `python -m bookmark_insight.sync --discover` 출력에서 `.env` 줄을 손으로 복사하고, `python -m bookmark_insight.sync`로 동기화하고, 시뮬 데모라면 `python scripts/bookmark_insight_demo_setup.py <dir>`를 따로 돌려야 한다. 이 세 단계를 버튼으로 바꾼다.

| 요구사항 | 이유 | 수용 기준 |
| :--- | :--- | :--- |
| R1. 첫 세팅에 터미널이 필요 없다 | 사용자가 복사·붙여넣기 없이 끝내야 한다 | `.env`에 Bookmark Insight 키가 하나도 없는 상태에서 `reachy-mini-conversation-app --ui`만 띄우고 Settings 화면의 버튼만으로 `BOOKMARK_INSIGHT_BOOKMARK_REPO`·`BOOKMARK_INSIGHT_CHROME_PROFILES`가 `.env`에 쓰이고 `bookmarks/<프로필>/`에 md 파일이 생긴다. 끝난 뒤 음성으로 `list_bookmarks`가 결과를 돌려준다 |
| R2. 결과가 화면에 보인다 | CLI 출력과 같은 정보를 UI에서 확인한다 | 탐색: 프로필 폴더명·표시 이름·북마크 파일별 개수·마지막 사용 표시. 동기화: 프로필별 created/updated/archived/excluded와 warnings. 상태 카드: 설정 여부·저장소·인사이트 폴더·프로필별 북마크 수·마지막 동기화 시각 |
| R3. Windows에서 동작한다 | 팀 개발 환경에 Windows 포함 (CLAUDE.md) | 경로는 `default_user_data_dir()`·`Path.expanduser()`만 사용, 하드코딩 경로·OS 전용 API 없음. 테스트는 픽스처 `chrome_user_data`를 `tmp_path`에 복사해 돌리므로 Windows CI에서도 통과 |
| R4. 셸을 실행하지 않는다 | 설정 UI는 LAN에 열려 있다(아래 신뢰 경계). 명령 문자열을 만들지 않으면 주입 자체가 없다 | `bookmark_insight_routes.py`·`bookmark_insight/demo.py`에 `subprocess`·`os.system`·`shlex` import가 없음을 테스트가 확인. 모든 버튼은 패키지 함수 직접 호출 |
| R5. 두 번 눌러도 안전하다 | 더블클릭·새로고침 뒤 재클릭이 흔하다 | 동기화 진행 중 재요청은 즉시 `bookmark_insight_sync_running` 오류(대기·중복 실행 없음). 탐색·상태는 읽기 전용이라 몇 번이든 같은 결과. `.env` 적용은 같은 값이면 같은 줄을 덮어쓰고(기존 `_persist_env_values` 동작), 데모 준비는 대상 폴더가 있으면 거부. 동기화 자체는 M2 규칙상 멱등(같은 입력 → updated 0) |
| R6. 저장소 경로를 화면에서 지정·생성 | 첫 사용자는 폴더를 미리 만들 필요가 없어야 한다 | 입력한 경로(`BOOKMARK_INSIGHT_BOOKMARK_REPO`)가 없으면 하위 폴더까지 만들고(`Path.mkdir(parents=True, exist_ok=True)`, 스레드에서, 셸 없음) `.env`에 반영한다. 쓰기 불가 폴더·상대 경로·Chrome User Data 안쪽 경로(탐색된 `root` 또는 입력한 `chrome_dir`의 하위)는 오류로 거부하고 아무것도 만들지 않는다. 같은 경로를 두 번 적용해도 결과(`exists=true`, `.env` 줄 1개)가 같다. 화면에 폴더 존재·쓰기 가능 여부와 이미 든 북마크 md 수·인사이트 노트 수가 보인다 |

**신뢰 경계 (명시):** 설정 UI는 `0.0.0.0:7860`에 바인딩되고, 이미 `backend.config`·`personalities.apply` 등으로 API 키를 포함한 `.env`를 쓴다. M10의 메서드는 모두 같은 `/rpc` 서버에 올라가므로 **같은 신뢰 경계 안**이다. 새 노출 면을 만들지 않는 대신, 이 경계가 LAN까지라는 점을 README에 적고 위험 표에 행을 둔다(아래 `## 위험`). 인증을 추가하는 것은 M10 범위 밖이다.

## 사용자 흐름

첫 사용자(실제 Chrome 북마크를 쓰는 경우):

1. `.env`에 `CONVERSATION_BACKEND`·API 키만 넣고 `reachy-mini-conversation-app --ui` 실행 → 브라우저에서 `http://127.0.0.1:7860/#/settings` 열기.
2. Settings 화면 아래쪽 **Bookmark Insight** 섹션의 상태 카드가 "설정 안 됨 (`BOOKMARK_INSIGHT_BOOKMARK_REPO` 없음)"을 보여 준다.
3. **프로필 탐색** 버튼 → 표: 프로필 폴더명(`Default`, `Profile 1`), Chrome 표시 이름("개인", "회사"), 북마크 파일별 개수(`Bookmarks: 120`, `AccountBookmarks: 244`), 마지막 사용 프로필 표시. 북마크 파일이 없는 프로필은 회색으로 "북마크 파일 없음". Chrome 폴더를 못 찾으면 `chrome://version` 안내와 함께 "Chrome User Data 경로" 입력란이 열린다.
4. 표의 체크박스로 읽을 프로필을 고른다(기본: 마지막 사용 프로필 1개). 다른 사람·회사 프로필은 고르지 않는다(SDD §2.3 허용 목록).
5. **북마크 저장소 경로** 입력란에 마크다운이 쌓일 폴더(옵시디언 볼트)를 적는다. 입력란은 현재 값, 없으면 기본 제안 `~/Documents/bookmarks`(서버가 `Path.home()`으로 풀어 보낸 절대 경로라 Windows에서도 맞다)로 채워져 있다. **폴더 만들기·적용** 버튼 → 없으면 하위 폴더까지 만들고 `BOOKMARK_INSIGHT_BOOKMARK_REPO`를 `.env`에 쓴다. 입력란 아래 한 줄에 "존재함 · 쓰기 가능 · 북마크 md 0개 · 인사이트 노트 0개"가 뜬다.
6. **.env에 적용** 버튼 → `BOOKMARK_INSIGHT_CHROME_PROFILES`(+ 사용자가 Chrome 경로를 직접 적었을 때만 `BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR`)가 `.env`와 현재 프로세스 환경에 반영된다. (5번을 건너뛰고 여기서 저장소 경로까지 같이 적용해도 된다 — 같은 `bookmark_insight.repo.set` 검증을 거친다.) 상태 카드가 "설정됨"으로 바뀐다. 재시작 불필요(도구는 호출마다 `load_settings()`를 읽는다).
7. **동기화 실행** 버튼 → 버튼이 잠기고 스피너가 돌다가 결과 표(프로필별 created/updated/archived/excluded, 경고 목록)가 뜬다. 상태 카드의 프로필별 북마크 수·마지막 동기화 시각이 갱신된다.
8. 이제 로봇에게 "이번 주 북마크 얘기하자"라고 말한다. (요약·인사이트 배치는 기존 `get_insight`의 `generating` 경로가 처리한다. M10이 건드리지 않는다.)

시뮬 데모(실제 Chrome 없이):

1. 같은 섹션의 **데모 데이터 준비** 버튼 → 대상 폴더 입력(기본 `~/bookmark-insight-demo`) → 픽스처(요약된 북마크 3개 + ready 인사이트 노트 1개 + 가짜 `User Data`)가 복사되고, 돌려받은 경로 세 개가 "이 값을 .env에 적용" 버튼으로 바로 6번 단계에 들어간다.
2. 페르소나 `deskmate_ko`는 기존 Personalities 화면에서 고른다("Set as default"가 `REACHY_MINI_CUSTOM_PROFILE`을 저장). M10은 페르소나 키를 쓰지 않는다.
3. "10월 31일부터 11월 6일까지 북마크 얘기하자" → `ready` 노트로 즉시 대화.

## 화면 구성

기존 설정 UI는 탭이 아니라 해시 라우터(`#/`, `#/personalities`, `#/settings`, `#/tools`)로 뷰를 바꾸고, 각 뷰는 `static/js/ui.js`의 `h()`로 DOM을 만든다. Settings 뷰(`static/js/views/settings.js`)는 `settings-section` 섹션(Connection, Camera & Face Recognition, Google Calendar & Schedule, Voice, Current state)을 세로로 쌓는다. **Bookmark Insight 섹션**을 Google Calendar 섹션 다음, Voice 앞에 하나 추가한다. 섹션 빌더는 Tools 뷰가 쓰는 `static/js/components/profile-tools.js` 방식(`build...Section({signal}) → {element, refresh}`)을 따라 **새 파일 `static/js/components/bookmark-insight.js`**에 두고, `settings.js`는 import 1줄 + 요소 추가 1줄만 바뀐다.

섹션 안 구성(위에서 아래로):

- **상태 카드** (`settings-status-grid` 재사용): 설정 여부 배지, 북마크 저장소 경로, 인사이트 폴더, Chrome User Data 경로(자동 탐색값이면 "(자동)" 표시), 허용 프로필 목록, 프로필별 북마크 수(archived 제외)·인사이트 노트 수, 마지막 동기화 시각(없으면 "아직 없음"). 섹션이 마운트될 때와 각 버튼 성공 뒤 `bookmark_insight.status`로 갱신.
- **버튼 4개** (`settings-actions`): `프로필 탐색`, `.env에 적용`, `동기화 실행`, `데모 데이터 준비`. 진행 중엔 모든 버튼 `disabled`, 누른 버튼엔 스피너 텍스트("동기화 중…"). 다른 섹션과 같게 `settings-status`(`role="status"`, `aria-live="polite"`) 한 줄에 성공·실패 메시지를 쓴다.
- **저장소 폴더 필드** (`settings-field`): 텍스트 입력란(현재 `BOOKMARK_INSIGHT_BOOKMARK_REPO` 값, 없으면 `bookmark_insight.status`의 `suggested_repo` = `Path.home() / "Documents" / "bookmarks"`로 미리 채움) + `폴더 만들기·적용` 버튼 + 그 아래 한 줄 `settings-hint`: "존재함/없음 · 쓰기 가능/불가 · 북마크 md N개 · 인사이트 노트 N개". 입력란을 바꾸면(`input` 이벤트, 300ms 디바운스) `bookmark_insight.repo.set`을 `create=false`로 불러 그 줄만 갱신한다(만들지 않음). 버튼은 `create=true`.
- **입력란**: Chrome User Data 경로(텍스트, 기본 접힘·탐색 실패 시 펼침), 데모 대상 폴더(텍스트, 데모 버튼 옆).
- **결과 영역** (`settings-test-area` 재사용): 탐색 결과 표(체크박스 열 포함), 동기화 결과 표 + 경고 목록, 데모 결과(경로 세 줄 + "이 값을 .env에 적용" 버튼). 마지막 동작의 결과만 보여 준다(누적 안 함).

바꾸는 정적 파일: `static/js/components/bookmark-insight.js`(신규), `static/js/api.js`(래퍼 5개 + 타임아웃 상수), `static/js/views/settings.js`(2줄), `static/style.css`(표 셀·체크박스 열 등 최소 클래스. 기존 `.settings-section`, `.settings-actions`, `.settings-hint`, `.settings-test-area`, `.settings-status-grid` 재사용이 우선).

## 라우트 명세

**기존 코드가 강제한 조정:** `tool_space_routes.py`·`personality_routes.py`는 REST `/api/...`가 아니라 **JSON-RPC over WebSocket(`/rpc`)** 메서드를 등록하고(`rpc.register("tool_spaces.add", handler)`), 프런트는 `api.js`의 `rpcCall(method, params, {timeoutMs})`로 부른다. REST `/api/...`는 `console.py` 안에 인라인으로 남은 오래된 경로(camera, calendar, vision)뿐이다. 따라서 M10도 `bookmark_insight.*` JSON-RPC 메서드로 만든다. 오류는 HTTP 상태 코드가 아니라 `JsonRpcError(detail, reason="bookmark_insight_...", data={"detail": detail})`로 돌려주며, 프런트에서는 `error.body.error === reason`, `describeError(error)`가 메시지를 보여 준다(기존 계약). 아래 표의 "오류 reason"이 그 값이다.

| 메서드 (JSON-RPC, `/rpc`) | params | result (필드) | 비고 / 오류 reason |
| :--- | :--- | :--- | :--- |
| `bookmark_insight.status` | `{}` | `configured: bool`, `repo: str\|null`, `suggested_repo: str`(= `str(Path.home() / "Documents" / "bookmarks")`, 입력란 기본값), `insights_dir: str\|null`, `chrome_user_data_dir: str`, `chrome_dir_is_default: bool`, `chrome_dir_exists: bool`, `allow_list: [str]`, `default_profile: str\|null`, `profiles: [{folder, bookmark_count, archived_count, insight_note_count}]`, `last_sync: {at, profiles, created, updated, archived} \| null`, `sync_running: bool` | 읽기 전용. `load_settings()`를 매번 다시 읽는다(적용 직후 반영). 파일 개수는 `store.list_profiles` + `iter_bookmarks(include_archived=True)`로 세고 `asyncio.to_thread`. `last_sync`는 `.bookmark_insight/last_sync.json`(아래 `bookmark_insight.sync`가 씀). 저장소 미설정이면 `configured=false`에 `profiles=[]`, 오류 아님 |
| `bookmark_insight.discover` | `{chrome_dir?: str}` | `root: str`, `root_is_default: bool`, `last_used: str\|null`, `profiles: [{folder, name: str\|null, is_last_used: bool, usable: bool, bookmark_files: [{name, count: int\|null, error: str\|null}]}]`, `suggested_env: {BOOKMARK_INSIGHT_CHROME_PROFILES: str, BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR?: str}` | `sync/__main__.py`의 `discover()`를 dict 반환 함수로 옮긴 것. `chrome_dir` 생략 시 `settings.chrome_user_data_dir or default_user_data_dir()`. `suggested_env`의 USER_DATA_DIR은 기본 경로가 아닐 때만. 오류: `bookmark_insight_chrome_dir_not_found`(폴더 없음), `bookmark_insight_no_profiles`(프로필 0개). 북마크 파일이 하나도 없으면 오류가 아니라 `usable=false`만 (Ctrl+D 안내는 UI 문구) |
| `bookmark_insight.sync` | `{profiles?: [str], repo?: str, chrome_dir?: str}` | `profiles: [str]`, `skipped_profiles: [str]`, `counts: {<folder>: {created, updated, archived, excluded}}`, `warnings: [str]`, `started_at: str`, `finished_at: str`, `duration_ms: int` | `SyncReport`를 그대로 JSON화. `profiles`가 오면 허용 목록을 덮어쓴다(CLI `--profile`과 같음). `repo`·`chrome_dir`은 `dataclasses.replace(settings, ...)`로 그 호출에만 적용(저장 안 함). `asyncio.to_thread` + 프로세스 안 `threading.Lock` 비차단 획득. 오류: `bookmark_insight_not_configured`(저장소 없음), `bookmark_insight_sync_running`(이미 실행 중), `bookmark_insight_repo_locked`(`repo_lock` `TimeoutError`, 60초), `bookmark_insight_sync_failed`(그 외 예외, detail에 메시지). 성공 시 `.bookmark_insight/last_sync.json` 갱신 |
| `bookmark_insight.repo.set` | `{path: str, create: bool}` | `path: str`(정규화된 절대 경로), `created: bool`, `exists: bool`, `writable: bool`, `bookmark_count: int`, `insight_count: int`, `applied: bool`(`.env`에 썼는지) | 저장소 폴더 지정·생성(R6). 검증 순서: 빈 값·줄바꿈 → `bookmark_insight_repo_path_invalid`; `Path(path).expanduser()`가 절대 경로가 아니면 `bookmark_insight_repo_path_relative`; `resolve()`한 경로가 탐색된 Chrome `root`(설정값 또는 `default_user_data_dir()`) 자신이거나 그 하위면 `bookmark_insight_repo_inside_chrome`; 파일시스템 루트·홈 디렉터리 자체(`Path.home()`, `Path(Path.home().anchor)`)면 `bookmark_insight_repo_path_reserved`. `create=false`: 만들지 않고 현재 상태만(`exists`·`writable`·개수) 돌려주며 `.env`도 안 쓴다(입력란 미리보기용). `create=true`: `Path.mkdir(parents=True, exist_ok=True)`를 `asyncio.to_thread`에서; `PermissionError`/`OSError` → `bookmark_insight_repo_not_writable`(detail에 OS 메시지); 만든 뒤 쓰기 가능 검사(`tempfile.NamedTemporaryFile(dir=path)` 생성·삭제) 실패 → 같은 오류; 통과하면 `persist_env({BOOKMARK_INSIGHT_BOOKMARK_REPO: path})` 1회. `bookmark_count`는 `bookmarks/**/*.md`, `insight_count`는 `insights/**/*.md`(없으면 0). 두 번 호출해도 `created=false, exists=true`로 같은 결과 |
| `bookmark_insight.env.apply` | `{BOOKMARK_INSIGHT_CHROME_PROFILES?: str, BOOKMARK_INSIGHT_BOOKMARK_REPO?: str, BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR?: str}` | `applied: [str]`(실제 쓴 키), `ignored: [str]`(빈 값이라 건너뜀), `warnings: [str]`, `status: <bookmark_insight.status result>` | 허용 키 세 개 외는 `bookmark_insight_env_key_not_allowed`. 값은 `str`만, 줄바꿈 포함 시 `bookmark_insight_env_value_invalid`. 경로는 `expanduser()`한 문자열로 저장. `BOOKMARK_INSIGHT_BOOKMARK_REPO`가 들어 있으면 `bookmark_insight.repo.set`과 **같은 검증 함수**(`validate_repo_path`)를 거치고 없는 폴더는 만든다(= `create=true`) — 두 경로가 다른 규칙을 갖지 않게. 세 키를 **한 번의** `persist_env(dict)` 호출로 넘겨 `.env` 쓰기가 1회가 되게 한다. 쓰기 자체는 기존 `LocalStream._persist_env_values`(빈 값 무시, `os.environ` 갱신, `.env` 줄 치환/추가, `load_dotenv`) — 키 삭제는 지원하지 않는다(범위 외) |
| `bookmark_insight.demo_setup` | `{target: str}` | `target: str`, `repo: str`, `chrome_user_data_dir: str`, `env: {BOOKMARK_INSIGHT_BOOKMARK_REPO, BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR, BOOKMARK_INSIGHT_CHROME_PROFILES}`, `personality: "deskmate_ko"`, `note_period: {since: "2026-10-31", until: "2026-11-06"}` | `bookmark_insight.demo.create_demo_repo(target)` 직접 호출(스크립트 로직을 패키지로 옮김). `asyncio.to_thread`. 오류: `bookmark_insight_demo_target_exists`(폴더 존재, 덮어쓰기 없음), `bookmark_insight_fixtures_missing`(editable 설치가 아니어서 `tests/bookmark_insight/fixtures`가 없음), `bookmark_insight_demo_failed`. `.env`는 쓰지 않는다 — UI가 `env`를 `bookmark_insight.env.apply`로 넘긴다 |

프런트 타임아웃: `bookmark_insight.status`·`discover`·`repo.set`·`env.apply`는 기본 8초, `bookmark_insight.sync`·`demo_setup`은 `api.js`에 `BOOKMARK_INSIGHT_LONG_TIMEOUT_MS = 180000`을 두고 넘긴다(첫 동기화 수백 파일 + Windows 쓰기 재시도 여유).

## 범위 외

- 백필 진행률 UI, 부분 인사이트 표시 (M7)
- 대화 로그 뷰어 (M8)
- 깃 미러 켜기·커밋 재구성 (M9)
- 북마크 편집·삭제·archived 복구 (옵시디언에서 한다)
- 예약 동기화·자동 주기 실행 (대화 시 `get_insight`가 필요한 만큼만 돈다)
- `.env` 키 삭제·그 외 Bookmark Insight 설정(포함/제외 폴더, 배치 모델 등) 편집 — 기존 `.env.example` 주석으로 충분
- 설정 UI 인증·접근 제어 (신뢰 경계 변경은 upstream 영역)
- 요약·인사이트 배치를 버튼으로 시작 (대화 흐름으로 충분, 비용 통제도 그쪽)

## Global Constraints

- 게이트: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v`
- 신규 pip 의존성 금지 (`uv.lock` 불변). FastAPI·`JsonRpcServer`·`dotenv`는 이미 있다
- upstream 파일 수정은 `console.py`의 등록 호출 1곳(`register_bookmark_insight_methods(...)`를 감싼 `try/except` 블록, 기존 Tool Space 등록 블록 바로 아래)뿐. `tool_settings.py`, `personality_routes.py`, `tool_space_routes.py`, `jsonrpc_server`는 건드리지 않는다
- `subprocess`·`os.system`·셸 문자열 금지. 패키지 함수 직접 호출
- 하드코딩 경로·OS 전용 API 금지. 경로 표시는 `str(Path)`, 입력은 `Path(value).expanduser()`. 한글 경로는 UTF-8 JSON으로 그대로 오간다
- ruff/mypy strict: 공개 함수·클래스 docstring 필수, `Any` 반환 금지(`dict[str, object]`), 핸들러 시그니처는 `Callable[[dict[str, Any]], Awaitable[dict[str, object]]]`
- 실패는 대화 루프를 죽이지 않는다: 핸들러는 예외를 `JsonRpcError(reason=...)`로 변환, `asyncio.CancelledError`는 전파
- `src/bookmark_insight/`는 앱을 몰라야 한다(SDD 기반 B): `bookmark_insight/demo.py`는 `reachy_mini_conversation_app`을 import하지 않는다
- 튜닝 상수는 모듈 상단: `SYNC_LOCK_TIMEOUT_S`, `DEMO_DEFAULT_DIRNAME = "bookmark-insight-demo"`, `LAST_SYNC_FILENAME = "last_sync.json"`

---

### Task 1: `bookmark_insight_routes.py` 뼈대 — BookmarkInsightOps(상태·탐색) + RPC 등록 + 테스트

**Files:**
- Create: `src/reachy_mini_conversation_app/bookmark_insight_routes.py`
- Modify: `src/bookmark_insight/sync/__main__.py` (`discover()`의 수집 부분을 `discover_profiles(root) -> dict`로 떼어 CLI는 그 dict를 출력만 하게)
- Test: `tests/bookmark_insight/test_bookmark_insight_routes.py` (신규; `tests/bookmark_insight/conftest.py`의 `chrome_user_data`·`repo`·`settings` 픽스처 재사용)

**Interfaces:**
- `class BookmarkInsightOps` — 전송 독립 (`PersonalityOps` 패턴)
  - `__init__(self, *, persist_env: Callable[[dict[str, str]], None], load_settings_fn: Callable[[], BookmarkInsightSettings] = load_settings, fixtures_dir: Path | None = None)`
  - `status(self) -> dict[str, object]` — 위 표의 `bookmark_insight.status`
  - `discover(self, chrome_dir: str | None = None) -> dict[str, object]` — 위 표의 `bookmark_insight.discover`; 실패는 `BookmarkInsightRouteError(reason, detail)` raise
- `class BookmarkInsightRouteError(Exception)`: `reason: str`, `detail: str` (`personality_routes.RouteError`와 같은 꼴, 모듈 내부용)
- `def build_bookmark_insight_ops(*, persist_env: Callable[[dict[str, str]], None]) -> BookmarkInsightOps`
- `def register_bookmark_insight_methods(rpc: JsonRpcServer, ops: BookmarkInsightOps) -> None` — `bookmark_insight.status`, `bookmark_insight.discover` 등록(Task 2·4에서 나머지 추가). 각 핸들러는 `async def _x(params: dict[str, Any]) -> dict[str, object]`, 본체는 `await asyncio.to_thread(ops.method, ...)`, `BookmarkInsightRouteError` → `JsonRpcError(detail, reason=reason, data={"detail": detail})`
- `bookmark_insight.sync.__main__.discover_profiles(root: Path) -> dict[str, object]` — `{root, last_used, profiles: [...], usable: [str]}`; 기존 `discover(chrome_dir)`는 이를 받아 print만 (출력 문구 불변, 기존 CLI 테스트 통과)
- 테스트 헬퍼: `_mount(ops) -> TestClient` (`FastAPI()` + `JsonRpcServer()` + `register_bookmark_insight_methods` + `rpc.mount(app)`), `_rpc_call(client, method, params)` — `tests/test_tool_space_routes.py`의 것을 복사

**Steps:**
- [ ] 실패 테스트 작성: (a) 저장소 미설정 → `status.configured=false`, `profiles=[]`, 오류 아님; (b) `settings` 픽스처(샘플 저장소) → `profiles[0].folder=="Default"`, `bookmark_count==3`, `insight_note_count==1`, `last_sync is None`; (c) `discover(chrome_user_data)` → 프로필 2개, `Default.is_last_used`, `bookmark_files` 개수가 픽스처 JSON의 URL 노드 수와 일치, `suggested_env.BOOKMARK_INSIGHT_CHROME_PROFILES=="Default"`, 기본 경로가 아니므로 `BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR` 포함; (d) 없는 폴더 → RPC 응답 `error.data.reason=="bookmark_insight_chrome_dir_not_found"`; (e) `discover_profiles`로 바꾼 뒤에도 기존 `python -m bookmark_insight.sync --discover` 출력 테스트(있다면 `test_bookmark_insight_sync.py`) 불변; (f) 모듈 소스에 `subprocess`/`os.system` 문자열 없음
- [ ] 실패 확인 → 구현 → 통과 → 게이트
- [ ] 커밋 `feat(deskmate): settings-UI ops for status and Chrome profile discovery`

### Task 2: 동기화·저장소 지정·.env 적용 — 스레드·잠금·중복 방지 + console 등록

**Files:**
- Modify: `src/reachy_mini_conversation_app/bookmark_insight_routes.py` (ops 메서드 3개, 핸들러 3개 추가)
- Modify: `src/reachy_mini_conversation_app/console.py` (`_init_settings_ui_if_needed` 안, Tool Space 등록 블록 아래 `try: register_bookmark_insight_methods(rpc, build_bookmark_insight_ops(persist_env=self._persist_env_values)) except Exception: logger.exception(...)` — 이 1곳)
- Test: `tests/bookmark_insight/test_bookmark_insight_routes.py` (추가), `tests/test_console.py` (선택: `_init_settings_ui_if_needed` 후 `rpc._methods`에 `bookmark_insight.sync`가 있는지 1건)

**Interfaces:**
- `BookmarkInsightOps.sync(self, profiles: list[str] | None = None, repo: str | None = None, chrome_dir: str | None = None) -> dict[str, object]` — 상단 상수 `SYNC_LOCK_TIMEOUT_S = 60.0`; `self._sync_guard = threading.Lock()`을 `acquire(blocking=False)`로 잡고 실패 시 `BookmarkInsightRouteError("bookmark_insight_sync_running", ...)`; `sync_profiles(settings, profiles=profiles)` 호출(내부에서 프로필마다 `repo_lock`); `TimeoutError` → `bookmark_insight_repo_locked`; 성공 시 `settings.state_dir / LAST_SYNC_FILENAME`에 `{at, profiles, created, updated, archived}`를 `store.atomic_write_text`로 기록
- `validate_repo_path(raw: str, *, chrome_root: Path) -> Path` (모듈 함수, 순수) — 빈 값·줄바꿈 → `bookmark_insight_repo_path_invalid`; `expanduser()` 후 `is_absolute()` 아니면 `bookmark_insight_repo_path_relative`; `resolve()`가 `chrome_root.resolve()`와 같거나 그 하위(`Path.is_relative_to`)면 `bookmark_insight_repo_inside_chrome`; `Path.home()` 자신이나 드라이브/파일시스템 루트(`path == Path(path.anchor)`)면 `bookmark_insight_repo_path_reserved`; 통과 시 정규화된 절대 `Path` 반환. 상수 `RESERVED_REPO_PATHS`는 함수 안에서 `Path.home()`으로 계산(모듈 import 시 고정 금지, 테스트가 `HOME`을 바꾼다)
- `BookmarkInsightOps.set_repo(self, path: str, *, create: bool) -> dict[str, object]` — `validate_repo_path` → (`create`일 때) `Path.mkdir(parents=True, exist_ok=True)`, `tempfile.NamedTemporaryFile(dir=path)`로 쓰기 검사, `OSError` → `bookmark_insight_repo_not_writable` → `self._persist_env({ENV_BOOKMARK_REPO: str(path)})`; 개수는 `rglob("*.md")`로 `bookmarks/`·`insights/` 아래만. 반환은 위 표
- `BookmarkInsightOps.apply_env(self, values: dict[str, object]) -> dict[str, object]` — `ALLOWED_ENV_KEYS = (ENV_CHROME_PROFILES, ENV_BOOKMARK_REPO, ENV_CHROME_USER_DATA_DIR)`; 정규화(strip, 경로 키는 `str(Path(v).expanduser())`); `ENV_BOOKMARK_REPO`가 있으면 `validate_repo_path` + mkdir + 쓰기 검사를 `set_repo`와 같은 내부 함수 `_prepare_repo(path)`로 수행; 비어 있지 않은 값만 모아 `self._persist_env(updates)` **1회** 호출; 반환에 `status=self.status()` 포함
- `BookmarkInsightOps.status()`의 `sync_running` = `self._sync_guard.locked()`, `suggested_repo = str(Path.home() / "Documents" / "bookmarks")`
- RPC: `bookmark_insight.sync`(params → `ops.sync`), `bookmark_insight.repo.set`(`path`, `create` → `ops.set_repo`; `create`는 `bool(params.get("create", False))`), `bookmark_insight.env.apply`(params 전체를 `ops.apply_env`에)

**Steps:**
- [ ] 실패 테스트: (a) `settings` 픽스처로 `bookmark_insight.sync` → `counts.Default.created>=0`, `profiles==["Default"]`, `skipped_profiles==["Profile 1"]`, 같은 호출 두 번째는 `created==0`(멱등); (b) 저장소 미설정 → `bookmark_insight_not_configured`; (c) `_sync_guard`를 테스트가 먼저 잡은 상태에서 호출 → 즉시 `bookmark_insight_sync_running`(1초 이내); (d) 다른 스레드가 `repo_lock`을 들고 있고 `SYNC_LOCK_TIMEOUT_S`를 monkeypatch로 0.2초 → `bookmark_insight_repo_locked`; (e) sync 뒤 `status.last_sync.at`이 ISO 문자열이고 `profiles==["Default"]`; (f) `apply_env`: 가짜 `persist_env`가 **한 번** 호출되고 dict에 세 키, 빈 값 키는 `ignored`에, 허용 밖 키(`OPENAI_API_KEY`) → `bookmark_insight_env_key_not_allowed`, 줄바꿈 값 → `bookmark_insight_env_value_invalid`, `~`가 홈으로 풀림(`Path.home()` 비교, OS 무관); (g) `apply_env` 뒤 `status`가 새 설정을 읽음(`load_settings_fn`을 `os.environ` 기반 실제 함수로 두고 `monkeypatch.setenv`를 persist_env 가짜가 수행); (h) `set_repo(tmp_path/"vault"/"bookmarks", create=True)` → 폴더가 하위까지 생기고 `created=true, exists=true, writable=true, bookmark_count=0, insight_count=0, applied=true`, `persist_env`가 `{BOOKMARK_INSIGHT_BOOKMARK_REPO: <절대 경로>}`로 1회 호출; 같은 호출 두 번째 → `created=false, exists=true`, `.env` 가짜에 키 1개(멱등); (i) `create=False`로 없는 경로 → 폴더가 생기지 않고 `exists=false, applied=false`, `persist_env` 미호출; (j) `repo` 픽스처(샘플 저장소)로 `create=False` → `exists=true, bookmark_count=3, insight_count=1`; (k) 거부 케이스 각각 reason 확인 — 상대 경로 `"bookmarks"` → `bookmark_insight_repo_path_relative`, `chrome_user_data/"Default"` → `bookmark_insight_repo_inside_chrome`, `Path.home()` 자체(`monkeypatch.setenv("HOME"/"USERPROFILE", tmp_path)`) → `bookmark_insight_repo_path_reserved`, 줄바꿈 → `bookmark_insight_repo_path_invalid`; 거부 시 폴더 미생성·`persist_env` 미호출; (l) 쓰기 불가: `tmp_path/"ro"`를 `chmod(0o500)`(POSIX 전용이라 `pytest.mark.skipif(os.name == "nt")`) → `bookmark_insight_repo_not_writable`; (m) `apply_env`에 없는 `BOOKMARK_INSIGHT_BOOKMARK_REPO`를 넣으면 폴더가 생기고, Chrome 안쪽 경로를 넣으면 `bookmark_insight_repo_inside_chrome`로 거부되며 다른 키도 쓰이지 않음(전부 아니면 전무)
- [ ] 실패 확인 → 구현 → 통과 → `console.py` 등록 1곳 추가 → 게이트
- [ ] 수동 확인(시뮬): 앱 `--ui`로 띄우고 브라우저 콘솔에서 `/rpc`로 `bookmark_insight.sync` 호출 → 음성 대화가 끊기지 않고(이벤트 루프 비차단) 결과 JSON 수신. 동기화 중 `get_insight`가 `generating`으로 배치 중이면 UI 쪽이 `bookmark_insight_repo_locked` 또는 대기 후 성공 중 하나로 끝나는지 기록
- [ ] 커밋 `feat(deskmate): sync, repository setup and .env apply over the settings RPC, registered in console`

### Task 3: 정적 패널 — Bookmark Insight 섹션(HTML/JS) + .env 적용 흐름

**Files:**
- Create: `src/reachy_mini_conversation_app/static/js/components/bookmark-insight.js`
- Modify: `src/reachy_mini_conversation_app/static/js/api.js` (`getBookmarkInsightStatus`, `discoverBookmarkInsightProfiles`, `runBookmarkInsightSync`, `applyBookmarkInsightEnv`, `setupBookmarkInsightDemo` + `BOOKMARK_INSIGHT_LONG_TIMEOUT_MS`), `src/reachy_mini_conversation_app/static/js/views/settings.js` (import 1줄, `calendarSection.element` 다음에 `bookmarkInsightSection.element` 1줄, `onSaved` 갱신 목록에 `bookmarkInsightSection.refresh()`), `src/reachy_mini_conversation_app/static/style.css` (표·체크박스 열·회색 행 최소 클래스)
- Test: 정적 JS는 단위 테스트 틀이 없다(기존 뷰도 없음). 브라우저 수동 체크리스트를 이 태스크의 완료 기준으로 둔다

**Interfaces:**
- `export function buildBookmarkInsightSection({ signal }) -> { element: HTMLElement, refresh(): Promise<void> }` (`profile-tools.js`와 같은 모양)
- `api.js`: `export const runBookmarkInsightSync = (params) => rpcCall("bookmark_insight.sync", params, { timeoutMs: BOOKMARK_INSIGHT_LONG_TIMEOUT_MS })` 등 6개(`setBookmarkInsightRepo(path, create)` 포함); 나머지는 기본 타임아웃
- 패널 상태(모듈 지역): `discovered`(마지막 탐색 결과), `selectedProfiles: Set<string>`, `busy: string | null`(진행 중 동작 이름). `busy`가 있으면 네 버튼 `disabled`
- 화면 문구는 한국어(기존 Settings의 Vision·Calendar 섹션이 이미 한국어 문구를 쓴다). 오류는 `describeError(error)`로 `settings-status` 한 줄에

**Steps:**
- [ ] `bookmark-insight.js`: 상태 카드 렌더(`bookmark_insight.status`), 탐색 버튼 → 표(체크박스는 `is_last_used` 기본 체크, `usable=false` 행은 체크 불가·회색), 저장소 폴더 필드(`suggested_repo` 미리 채움, 입력 디바운스 → `setBookmarkInsightRepo(path, false)`로 존재·쓰기·개수 줄 갱신, `폴더 만들기·적용` → `setBookmarkInsightRepo(path, true)` → 거부 reason별 한국어 문구: 상대 경로/Chrome 안쪽/예약 경로/쓰기 불가), Chrome 경로 입력란, `.env에 적용` → 선택 프로필을 쉼표로 합쳐 `applyBookmarkInsightEnv({...})` → 응답의 `status`로 카드 갱신 + "저장됨" 메시지, `동기화 실행` → 스피너 → 결과 표 + 경고 목록 → `refresh()`
- [ ] `api.js` 래퍼 5개와 타임아웃 상수, `settings.js` 2~3줄, `style.css` 최소 클래스
- [ ] 브라우저 수동 체크리스트(시뮬, macOS와 Windows 각 1회): (1) Bookmark Insight 키 없는 `.env`로 시작 → 카드 "설정 안 됨"; (2) 탐색 → 실제 프로필 표가 뜬다(Windows는 `%LOCALAPPDATA%` 자동 경로); (3) 저장소 입력란에 기본 제안이 들어 있고(Windows는 `C:\Users\...\Documents\bookmarks`), 없는 하위 경로를 적고 `폴더 만들기·적용` → 폴더 생성 + 줄에 "존재함 · 쓰기 가능 · 0개"; Chrome User Data 안쪽 경로를 적으면 거부 문구; 프로필 체크 → `.env에 적용` → `.env` 파일에 두 줄 추가 확인, 카드 "설정됨", API 키 줄은 그대로; (4) 동기화 → 버튼 잠김·스피너 → 결과 표 → 카드 북마크 수 갱신; (5) 동기화 중 재클릭 → 즉시 "이미 동기화 중" 메시지; (6) 음성 대화가 동기화 중에도 응답함; (7) 새로고침 후에도 카드가 같은 상태
- [ ] `ruff`/`mypy`는 JS를 보지 않으므로 게이트는 파이썬만. 그래도 게이트 실행 → 커밋 `feat(deskmate): DeskMate setup panel in the settings UI`

### Task 4: 데모 데이터 버튼 + 문서

> 사용자 문서: [`src/bookmark_insight/SETUP.md`](../src/bookmark_insight/SETUP.md) (패키지 안의 상세 가이드) + [README "Bookmark Insight" 절](../README.md#bookmark-insight-chrome-북마크-인사이트)의 요약. AGENTS.md 규칙대로 `docs/` 아래에는 md를 두지 않는다.

**Files:**
- Create: `src/bookmark_insight/demo.py`
- Modify: `scripts/bookmark_insight_demo_setup.py` (복사 로직을 `bookmark_insight.demo.create_demo_repo` 호출로 바꾸고 출력만 남김), `src/reachy_mini_conversation_app/bookmark_insight_routes.py` (`BookmarkInsightOps.demo_setup` + `bookmark_insight.demo_setup` 등록), `static/js/components/bookmark-insight.js` (데모 버튼·대상 폴더 입력·"이 값을 .env에 적용"), `.env.example` 7절 주석(“설정 UI(7860) Settings → Bookmark Insight 섹션에서 버튼으로도 할 수 있다” 1~2줄), `README.md` Bookmark Insight 절에 "웹 UI로 첫 세팅" 소절(스크린샷 없이 글로; 설정 UI가 LAN에 열려 있다는 한 줄 포함)
- Test: `tests/bookmark_insight/test_bookmark_insight_demo.py` (신규), `tests/bookmark_insight/test_bookmark_insight_routes.py` (추가)

**Interfaces:**
- `bookmark_insight.demo.DEFAULT_FIXTURES_DIR: Path | None` — `Path(__file__).resolve().parents[2] / "tests" / "bookmark_insight" / "fixtures"`가 존재하면 그 값, 아니면 `None`(배포 설치)
- `bookmark_insight.demo.create_demo_repo(target: Path, *, fixtures_dir: Path | None = DEFAULT_FIXTURES_DIR) -> DemoRepo` — `@dataclass(frozen=True) DemoRepo(target: Path, repo: Path, chrome_user_data_dir: Path, profile: str = "Default", note_since: str = "2026-10-31", note_until: str = "2026-11-06")`; `target.exists()` → `FileExistsError`; `fixtures_dir is None or not .is_dir()` → `FileNotFoundError`; `shutil.copytree` 두 번(기존 스크립트와 동일)
- `DemoRepo.env_lines() -> dict[str, str]` — `{BOOKMARK_INSIGHT_BOOKMARK_REPO, BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR, BOOKMARK_INSIGHT_CHROME_PROFILES}` (스크립트와 RPC가 같은 dict를 쓴다)
- `BookmarkInsightOps.demo_setup(self, target: str) -> dict[str, object]` — `Path(target).expanduser()`; `FileExistsError` → `bookmark_insight_demo_target_exists`, `FileNotFoundError` → `bookmark_insight_fixtures_missing`; 위 표의 result
- 스크립트 `main()`은 `create_demo_repo` 호출 + 기존 출력 문구 유지(종료 코드 2 유지)

**Steps:**
- [ ] 실패 테스트: (a) `create_demo_repo(tmp_path/"demo")` → `repo/bookmarks/Default`에 md 3개, `repo/insights/Default`에 노트 1개, `User Data/Local State` 존재, `env_lines()` 키 3개; (b) 존재하는 대상 → `FileExistsError`; (c) `fixtures_dir=None` → `FileNotFoundError`; (d) RPC `bookmark_insight.demo_setup` 성공 result에 `env`·`personality=="deskmate_ko"`, 두 번째 호출 → `bookmark_insight_demo_target_exists`; (e) 스크립트 `main([str(tmp)])` 반환 0과 출력에 `BOOKMARK_INSIGHT_BOOKMARK_REPO=` 포함(기존 동작 회귀 방지)
- [ ] 실패 확인 → 구현 → 패널에 데모 버튼 연결(결과의 `env`를 `applyBookmarkInsightEnv`로 넘기는 버튼) → 통과 → 게이트
- [ ] 문서: `.env.example` 7절 힌트, README 소절(흐름 8단계 요약 + 데모 3단계 + 신뢰 경계 한 줄)
- [ ] 수동 확인(시뮬): 빈 `.env`에서 데모 버튼 → 적용 → Personalities에서 `deskmate_ko` 기본 지정 → "10월 31일부터 11월 6일까지 북마크 얘기하자" → `ready` 대화 성립
- [ ] 커밋 `feat(deskmate): demo data button and setup docs for the settings UI`

## 위험

| 위험 | 대응 |
| :--- | :--- |
| 설정 UI가 `0.0.0.0:7860`으로 LAN에 열려 있어 같은 네트워크의 누구나 동기화·`.env` 쓰기를 누를 수 있다 | 기존 경계와 동일(이미 API 키를 쓰는 `backend.config`가 같은 서버에 있다)이며 M10이 새 노출 면을 만들지 않는다. 셸 실행 없음(R4), `.env` 키는 허용 목록 세 개뿐, 데모는 덮어쓰기 거부. README에 "집 Wi-Fi 밖에서는 7860을 열지 말 것"을 적는다. 인증은 범위 외 |
| 음성 대화 중(`get_insight` `generating` 배치가 `repo_lock`을 든 채) 동기화 버튼을 누름 | `sync_profiles`가 프로필마다 `repo_lock`을 잡으므로 직렬화된다. 60초 내 못 잡으면 `bookmark_insight_repo_locked`로 돌려주고 UI는 "배치가 끝난 뒤 다시 누르세요"를 띄운다. 핸들러는 `asyncio.to_thread`라 이벤트 루프·오디오는 멈추지 않는다 |
| 같은 버튼 중복 클릭 → 동기화 두 번 | 프로세스 안 `threading.Lock` 비차단 획득 → 두 번째는 즉시 `bookmark_insight_sync_running`. 프런트도 `busy` 동안 버튼 `disabled`. `status.sync_running`으로 새로고침 뒤에도 표시 |
| `.env` 부분 쓰기 (`_persist_env_values`는 `write_text`로 통째로 쓰며 원자적이지 않다) | 세 키를 한 번의 호출로 넘겨 쓰기 1회로 줄인다. 값은 줄바꿈 금지·strip. 기존 메커니즘의 한계(원자 교체 없음)는 upstream 몫이므로 바꾸지 않고 위험으로만 기록. 적용 뒤 `status`를 다시 읽어 화면이 파일과 같은지 보여 준다 |
| Chrome이 북마크 파일을 쓰는 도중 읽어 JSON이 깨짐 | 이미 `chrome.load_json`이 재시도한다. 그래도 실패하면 `SyncReport.warnings`에 담겨 오고 그 프로필은 건너뛰므로(부분 읽기로 archived 처리 안 함, M2 규칙) 화면에 경고로 보인다 |
| 첫 동기화가 크다 (실측 364 파일; Windows는 쓰기 재시도 포함) | 진행률은 M10에서 만들지 않는다. 버튼 잠금 + 스피너 + 끝난 뒤 프로필별 집계로 충분. 프런트 타임아웃 180초. 더 길면 `BOOKMARK_INSIGHT_SYNC_SINCE`로 범위를 줄이라는 힌트를 경고와 함께 표시. 진행률이 필요해지면 `rpc.broadcast_threadsafe("bookmark_insight.sync.progress", ...)` + `api.subscribe`로 붙일 수 있다(설계만 남김) |
| 사용자가 Chrome 디렉터리나 시스템 폴더(홈 자체, 드라이브 루트)를 저장소로 지정 → 동기화가 그 안에 `bookmarks/`·`.bookmark_insight/`를 만들고 archived 규칙이 엉뚱한 md를 건드릴 수 있다 | `validate_repo_path`가 만들기 전에 거부: 상대 경로, Chrome `root` 자신·하위, `Path.home()` 자신, 파일시스템/드라이브 루트. `bookmark_insight.repo.set`과 `bookmark_insight.env.apply`가 같은 함수를 쓰므로 우회 경로 없음. 거부 시 폴더 미생성·`.env` 미기록(전부 아니면 전무). 테스트 (k) |
| 배포 설치(editable 아님)에서는 `tests/bookmark_insight/fixtures`가 없어 데모 버튼이 실패 | `DEFAULT_FIXTURES_DIR=None` → `bookmark_insight_fixtures_missing`에 "저장소 체크아웃에서 실행하세요" 안내. 시뮬 데모는 체크아웃 전제(CLAUDE.md) |
| `.env` 적용이 실행 중 프로세스에만 반영되고 재시작 뒤 사라진다는 오해 | `_persist_env_values`는 파일과 `os.environ` 둘 다 갱신하고 `load_dotenv`를 다시 돈다. 적용 응답에 `env_path`를 넣어 어느 파일에 썼는지 보여 준다 |
