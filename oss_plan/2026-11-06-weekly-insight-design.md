# 소프트웨어 설계서 (SDD): 리치미니 데스크메이트 — 기능 1 위클리 인사이트

- **문서 버전**: 0.9.1
- **작성일**: 2026-11-06
- **상태**: 요구사항 확정 · 1~6장 확정 · 검수 반영 · Chrome 다중 프로필 반영 · **구현 M0~M9 완료, M10 1차 초안, 에뮬레이터 시나리오 1~6 통과** (2026-11-07)
- **검수**: 2026-11-06 20년차 풀스택 관점 서브에이전트 검수 반영 (부록 C)
- **베이스**: upstream `reachy_mini_conversation_app` (OpenAI/Gemini realtime 백엔드, `tools/`의 `Tool` 서브클래스로 기능 확장)

## 1. 시스템 개요 및 목표

### 1.1 배경 및 문제 정의

- **방향**: 비서봇이 아니라 **반려봇**이다. 평가 기준은 "시킨 걸 잘 하느냐"가 아니라
  "같이 있으면 재밌고, 주말에도 노트북 앞에 앉고 싶어지느냐"다. 봇은 사용자를 평가하거나
  재촉하지 않는다.
- **문제**: 브라우저 북마크는 저장만 되고 다시 보지 않는다. "왜 저장했는지"는 기록되지
  않고, 북마크 사이의 연관성과 관심사의 시간적 이동을 사용자 본인도 알지 못한다.
- **접근**: 사용자의 습관(Chrome Cmd+D)을 바꾸지 않는다. 봇이 북마크 본문을 읽고 요약해,
  북마크들 사이의 상관관계를 추론한 뒤 질문으로 대화를 연다 (사용자에게 저장 이유를 묻지
  않는다). 개발은 에뮬레이터(MuJoCo) 검증 → 실기기 포팅 순서로 진행한다.

### 1.2 기능 구조

| 구분 | 내용 | 상태 |
| :--- | :--- | :--- |
| 기능 1. 북마크 인사이트 | 특정 기간에 추가된 북마크를 바탕으로 인사이트를 나누는 대화 | **이번 범위** |
| 기능 2. 장기 라포 | 리치미니와 나눈 대화를 쌓아 관계를 만든다 | 보류 (라포 지표 기획 필요) |
| 기능 2+. 성격 진화 | MBTI로 초기 성격 선택, 라포에 따라 디테일이 붙는다 | 보류 (기능 2 이후) |
| 기반 A. 북마크 저장소 | Chrome 북마크 → 마크다운 (깃 미러는 M9 선택 사양) | 기능 1의 데이터 |
| 기반 B. 데스크메이트 패키지 (`src/bookmark_insight/`) | 북마크 동기화·요약·인사이트 생성을 담은 순수 파이썬 패키지. 앱을 모른다. 플랜 B 교체 지점 | 기능 1·2 공용 |

기능 1만 이번 범위다. 기능 2·2+는 데이터 형식만 확장 가능하게 둔다.

### 1.3 목표 KPI

KPI는 기술 3개(테스트·에뮬레이터로 자동 검증)와 경험 2개(실사용 수 주간 측정)로 나눈다.
목표 수치는 시작값이며 첫 달 측정 후 조정한다.

| 구분 | 목표 지표 (KPI) | 목표 수준 | 측정 및 검증 방식 |
| :--- | :--- | :--- | :--- |
| 기술 | 동기화 정확도 | 북마크 수·날짜 일치 100% | 테스트용 Chrome 북마크 파일 N개 → 마크다운 N개 생성, 프런트매터 `date`가 `date_added`와 일치하는지 자동 테스트 |
| 기술 | private 누출 | 0건 | private 폴더 북마크가 저장소·도구 응답·인사이트 노트 어디에도 없음을 테스트로 전수 확인 |
| 기술 | 가설 규칙 준수 | 봇의 인사이트 발화 중 질문으로 끝나는 비율 90% 이상 (측정은 대화 로그 PR(M8) 이후. M6 데모 기준에서 제외) | 대화 전사 로그에서 봇 발화 검사 (LLM 채점 또는 패턴 검사) |
| 경험 | 대화 지속 | 인사이트 시작 후 3턴 이상 이어진 세션 비율 (첫 달 측정, 목표는 이후 설정. 측정은 M8 이후) | 대화 전사 로그의 턴 수 |
| 경험 | 사용자 자기 평가 | 주 1회 "재밌었나" 5점 척도 평균 4 이상 | 인사이트 노트 끝의 한 줄 체크를 집계. 집계 방식은 보류(누가 쓰고 누가 집계하는지 미정) |

**측정 전제**: 가설 규칙 준수·대화 지속은 대화 전사 로그가 있어야 측정할 수 있다. 따라서
대화 로그 저장을 요구사항에 포함한다 (2.6, 별도 PR M8). 이 로그는 기능 2(장기 라포)의
에피소드 층과 같은 파일이다. 로그 PR 전의 M6 데모에서는 로그 의존 항목을 제외하고 본다.

**"질문으로 끝남"은 형식 지표이지 환각 지표가 아니다.** 환각(노트에 없는 북마크 내용을
말함)은 발화의 북마크 언급을 노트 `bookmark_ids`와 대조해 잡는다 (v1은 수동 샘플링, 5장).

## 2. 아키텍처 및 도구 명세

### 2.1 논리 아키텍처 및 데이터 흐름

합의된 큰 그림이다. 세부는 구현 계획에서 정한다.

```
Chrome Bookmarks(JSON, 허용 목록의 프로필만) --동기화--> 북마크 저장소(bookmarks/<프로필>/*.md)
                                        |
                              요약기: URL 본문 가져오기 → 북마크별 요약 → 본문에 기록
                                        |
                              인사이트 배치: 요약 묶음 → 주제·상관관계·질문 → insights/<프로필>/*.md
                                        |
          src/bookmark_insight/ (순수 파이썬 패키지) ←── tools/ 의 얇은 Tool 4개가 import 해 호출 (앱과 같은 프로세스)
                                        |
                              (선택) MCP 서버 스크립트: 같은 패키지를 HTTP로 감쌈. 로봇 앱은 쓰지 않음 — M9 구현: python -m bookmark_insight.mcp_server (stdio) / --http
```

- **앱 연동은 같은 프로세스의 Tool로 한다.** 검수 결과, 원격 도구는 `tool_spaces.py`의
  매니페스트(`*.hf.space` 주소만 허용)로만 로드되고 `config.py`에 로컬 MCP 설정이 없어
  로컬 MCP 서버는 upstream 수정 없이 붙지 않는다. 또 원격 도구 결과는
  `{"status":"ok",...}` 봉투로 감싸이고, 실패 시 자동 1회 재시도, 타임아웃 30초 고정이라
  2.2 계약과 맞지 않는다. 따라서 CLAUDE.md 규칙대로 `tools/`에 `Tool` 서브클래스 4개를
  두고 `src/bookmark_insight/` 함수를 직접 호출한다. 긴 작업은 `BackgroundToolManager`(뽀모도로와
  같은 메커니즘)로 돌리고 완료 시 모델이 발화한다.
- 패키지는 "빠른 조회"와 "느린 배치"를 분리한다. 음성 대화 중 무거운 요약을 하지 않는다.
  요약기와 인사이트 배치는 느린 배치 쪽이고, 도구 호출은 그 결과물(노트·요약 본문)을
  읽는 빠른 조회다.
- 요약은 북마크 파일 본문에 기록해 캐시로 재사용한다. 같은 북마크를 다시 요약하지 않는다.
- 플랜 B(로컬 LLM·RAG)는 `src/bookmark_insight/` 내부만 교체한다. 교체 경계는 `src/bookmark_insight/`의
  함수 시그니처이고, 도구 계약(2.2)은 유지.
- 봇이 먼저 말 거는 이벤트 처리는 패키지가 아니라 앱 모듈의 역할이다 (이번 범위 외).
- MCP 서버는 선택 사양(M9 이후)이다. 옵시디언·다른 LLM 클라이언트에서 같은 패키지를
  쓰고 싶을 때 띄운다. 로봇 앱은 쓰지 않는다.

### 2.2 도구 계약 (확정, 앱 내 Tool)

설계 원칙:

- 도구는 `tools/`의 `Tool` 서브클래스다 (파일 1개 = 도구 1개). 시그니처는
  `async __call__(self, deps, **kwargs) -> dict`, 실패 시 raise 대신 `{"error": ...}`를
  반환한다 (CLAUDE.md 규칙. 대화 루프를 죽이지 않는다).
- 음성 대화 중 호출되므로 도구는 **적고 빨라야** 한다. 무거운 작업은 2.5의 배치가 미리
  끝내 두고, 도구는 결과물을 읽는다. 대화 중 URL 가져오기·LLM 호출은 절대 하지 않는다.
- **날짜 계산은 패키지가 한다.** 인자는 세 도구가 동일하게 `days`(기본 7) 또는
  `since`/`until`(ISO 날짜, `days`와 함께 쓰지 않음)이고, 여기에 선택 인자 `profile`(Chrome
  프로필 폴더명, 생략 시 기본 프로필. 2.3)이 붙는다. 모델이 날짜 문자열을 직접 만들지
  않게 해 오계산을 줄이고, 결과에 `today`(ISO)를 항상 포함해 모델의 날짜 계산을 돕는다.
  `tags` 인자는 두지 않는다 (검수 F12: 음성 LLM이 폴더명을 지어낼 위험, 쓰임 드묾).
- **프로필 인자는 허용 목록(2.3) 안에서만 받는다.** 도구 설명에 허용 목록의 프로필
  이름(폴더명과 표시 이름)을 넣어 모델이 지어내지 않게 한다. 허용 목록에 없는 값이면
  `{"error": "unknown profile", "available": [...]}`를 반환한다. 결과 dict에는 어느
  프로필을 대상으로 했는지 `profile`을 항상 포함한다.
- 결과 dict 최상위에 모델이 **다음에 할 행동 지시(`next_action`)** 를 포함한다 (기존
  `pomodoro_timer` 도구의 패턴).

```python
class GetInsight(Tool):  # tools/get_insight.py
    name = "get_insight"

    async def __call__(self, deps, days: int = 7, since: str | None = None, until: str | None = None, profile: str | None = None) -> dict:
        """기간의 인사이트 노트를 즉시 반환한다. 절대 대화 중에 URL 가져오기·LLM 호출을 하지 않는다.
        profile: Chrome 프로필 폴더명 (선택; 생략 시 기본 프로필). 허용 목록(2.3)의 프로필
        이름(폴더명·표시 이름)을 도구 설명에 넣어 모델이 지어내지 않게 한다. 목록에 없는 값이면
        {"error": "unknown profile", "available": [...]}.
        - 노트가 있고 생성 이후 기간 내 신규 북마크가 없으면 status "ready" + clusters.
        - 노트가 없거나 낡았으면(stale) status "generating": BackgroundToolManager로
          파이프라인(동기화→요약→인사이트)을 시작하고 즉시 반환. 완료 시 매니저가 결과를 모델에
          전달하고 모델이 발화한다 (뽀모도로 패턴). next_action: "잠깐 훑어볼게"류 멘트 후 대기.
        - 기간 내 북마크가 없으면 status "empty". next_action: 3.2의 빈 기간 멘트.
        호출 시점을 인사이트 세션 시작으로 기록한다 (3.3, 앱 측 상태 기계). 열린 세션이 있으면
        암묵 마감 후 새로 연다.

        Returns: {"status": "ready"|"generating"|"empty", "today": str, "profile": str,
                  "period": {"since","until"}, "note_path": str,
                  "clusters": [{"topic","bookmark_ids","summary","correlation","question"}],
                  "unavailable": [bookmark_id...],   # 본문 미확보
                  "next_action": str}
        """


class GetBookmark(Tool):  # tools/get_bookmark.py
    name = "get_bookmark"

    async def __call__(self, deps, id: str) -> dict:
        """북마크 1개의 프런트매터와 요약 본문. 후속 질문("그 글 좀 더 자세히")용. id는 guid.
        guid는 전역 고유이므로 profile 인자는 받지 않는다. 결과에 어느 프로필의 북마크인지 profile을 포함.

        Returns: {"id", "profile", "title", "url", "tags", "date", "summary_status", "summary": str, "next_action": str}
        """


class ListBookmarks(Tool):  # tools/list_bookmarks.py
    name = "list_bookmarks"

    async def __call__(self, deps, days: int = 7, since: str | None = None, until: str | None = None, limit: int = 20, profile: str | None = None) -> dict:
        """기간 내 북마크 목록. 보조 도구 (인사이트 노트에 없는 것을 찾을 때). archived 제외.
        profile: Chrome 프로필 폴더명 (선택; 생략 시 기본 프로필). get_insight와 같은 규칙:
        허용 목록의 이름을 도구 설명에 넣고, 목록에 없으면 {"error": "unknown profile", "available": [...]}.

        Returns: {"today": str, "profile": str, "count": int,
                  "bookmarks": [{"id","title","url","tags","date","summary_status"}],
                  "next_action": str}
        """


class FinishInsight(Tool):  # tools/finish_insight.py
    name = "finish_insight"

    async def __call__(self, deps, conclusion: str, reason: str = "explicit") -> dict:
        """인사이트 대화를 마친다.

        conclusion: LLM이 정리한 대화의 결론 (몇 문장).
        reason: "explicit"(사용자 명시) | "confirmed"(주제 전환 확인).
        "auto"는 LLM 인자가 아니다: 앱 측 상태 기계가 무응답 10분·슬립·앱 종료 시 직접 마감한다 (3.3).
        profile 인자는 없다: 세션 상태 기계가 get_insight 때의 프로필을 기억한다 (3.3).

        노트에 "## 대화 기록 (YYYY-MM-DD HH:MM)" 절을 덧붙인다: LLM 결론 + (대화 로그 PR 이후)
        접히는 원문.

        Returns: {"ok": bool, "note_path": str}
        """
```

- 상태값은 `ready`·`generating`·`empty`에 M7의 `partial`·`backfilling`을 더해 다섯 개다. `partial`(두 도구 공통): 노트가 `complete: false`인 상태로, `summarized_count`·`pending_count`·`daily_cap_reached`가 함께 오고 세션은 ready처럼 열린다. next_action은 본 것으로 대화를 시작하고 'N건은 아직 안 봤어, 이어서 정리해 달라고 하면 계속 볼게'(하루 한도면 '내일 이어서')를 전하라고 지시한다. `backfilling`(get_insight만): 같은 프로필·기간의 build_insight가 이 프로세스에서 도는 중이면 세션 없이 '아직 훑는 중이야'만 전한다. 재호출된 build_insight는 pending인 북마크부터 이어서 요약한다(북마크 단위로 파일에 쓰므로 중단 후 재개가 자동).
- `status: empty`일 때 `next_action`은 3장의 빈 기간 멘트를 지시한다.
- `status: generating`일 때 지연 안내 멘트("잠깐 훑어볼게")를 허용한다. 완료 발화는
  `BackgroundToolManager`가 결과를 모델에 전달한 뒤 모델이 한다.

#### 2.2.1 구현 메모 (M0·M5 구현 중 확정, 2026-11-06)

- **도구는 5개다.** 앱의 모든 도구 호출은 `BackgroundToolManager`로 실행되고 코루틴이 끝나야
  결과가 모델에 전달된다. 따라서 `get_insight`가 스스로 백그라운드 작업을 "걸어 두는" 형태는
  불가능하고, 뽀모도로의 focus→break 패턴과 같은 2단계가 된다: `get_insight`는 즉시
  `generating`을 돌려주며 `next_action`으로 "잠깐 훑어볼게" 멘트 후 **`build_insight`를 같은
  인자로 즉시 호출**하라고 지시하고, `build_insight`(5번째 도구)가 동기화→요약→상관관계→노트
  파이프라인을 워커 스레드에서 끝까지 기다린다. 완료 결과가 모델에 전달되면 모델이 묶음 하나와
  질문 하나로 대화를 시작한다. `task_status`/`task_cancel`이 그대로 적용된다.
- **설정은 `src/bookmark_insight/settings.py`가 단일 출처다** (`BOOKMARK_INSIGHT_*` 환경변수). 패키지가 앱을
  모르게 하기 위해 `config.py`는 수정하지 않았고, `.env.example`에 7절을 추가했다.
- 도구는 `load_settings()`를 호출 시점에 읽는다 (테스트에서 환경변수로 저장소 경로를 바꾸기 위함).
- 저장소 미설정(`BOOKMARK_INSIGHT_BOOKMARK_REPO` 없음)이면 다섯 도구 모두 `{"error": "bookmark_insight not configured: ..."}`에 `settings_url`(`http://127.0.0.1:7860/#/settings`, `--ui` 필요)·`settings_section`(`Bookmark Insight`)·`setup_steps`(대시보드 버튼 4개)·`restart_required: false`를 함께 돌려준다 (2026-11-13). 설정 UI의 적용은 `os.environ`을 먼저 갱신하므로 다음 호출부터 재시작 없이 동작한다.
- 자동 종료(`reason="auto"`)는 2.2대로 LLM 인자가 아니다. `finish_insight`는 `auto`를 받으면
  오류를 돌려준다.

### 2.3 설정 (.env)

| 설정 | 기본값 | 의미 |
| :--- | :--- | :--- |
| Chrome User Data 경로 | OS별 기본 위치 자동 탐색 | 비표준 설치·Chromium 계열은 직접 지정 |
| Chrome 프로필 허용 목록 | 비어 있음 = 마지막 사용 프로필 1개 | 쉼표 구분 프로필 폴더명(예: `Default, Profile 1`). 목록에 없는 프로필은 파일이 있어도 읽지 않는다. 합치려면 명시적으로 나열 |
| 기본 프로필 | 허용 목록의 첫 항목 | 대화에서 `profile` 인자를 생략했을 때 대상 |
| 포함 폴더 | 비어 있음 = 전체 | 쉼표 구분, `bookmark_bar/개발` 형식 |
| 제외 폴더 | `private` | 이름만 쓰면 어느 위치든 제외 |
| 동기화 시작 날짜 | 비어 있음 = 전체 이력 | 첫 동기화 범위 제한 |
| 북마크 저장소 경로 | 필수 | 마크다운이 쌓이는 곳 (옵시디언 볼트) |
| 인사이트 노트 폴더 | `북마크 저장소/insights/` | 바탕화면 등으로 변경 가능. 지정하면 아래 노트 링크 `auto` 판정은 이 폴더 기준 |
| 노트 링크 (`BOOKMARK_INSIGHT_NOTE_LINKS`) | `auto` | 프런트매터 `related:`에 북마크 `"[[파일명]]"` 링크(옵시디언 그래프·백링크)를 쓸지. `auto` = 노트 폴더 상위에 `.obsidian/`이 있을 때만, `on`/`off` 강제. 본문의 북마크 줄은 항상 웹 링크 (2.5 4단계, 2026-11-13) |
| 제외 URL 패턴 | 사설 대역·`localhost`·`127.0.0.1` | 본문을 가져오지 않을 URL 패턴(사내 위키 등), 쉼표 구분. 기본값에 사설 대역 포함 (2.5) |
| 인사이트 자동 종료 무응답 시간 | 10분 | 3장 종료 규칙 |
| 대화 로그 폴더 | 북마크 저장소 옆 기본 폴더 | KPI 측정·기능 2 재료, 외부 전송 없음 (2.6, M8) |
| 토큰 사용량 로그 | `북마크 저장소/.bookmark_insight/usage.jsonl` | 배치 1회당 `usage` 기록 (4장) |
| 깃 미러 | 꺼짐 | M9. 켜면 북마크별 과거 날짜 커밋 |
| 배치용 LLM 모델 | gemini-3.5-flash | 요약·상관관계 배치 전용. 음성 대화의 실시간(Live) 모델과 분리 2026-11-07 실측: 3.8-flash는 호출당 230~270초, 3.5-flash는 5초 |
| 본문 상한 글자 수 | 20000 | 북마크당 LLM에 보내는 본문 길이. 튜닝 상수와 동일 값 |
| 배치 1회 최대 요약 건수 | 20 | 한 요청에서 즉시 처리하는 상한. 넘으면 백필로 전환 (M7) |
| 하루 최대 요약 건수 | 100 | 무료 등급 한도 보호. 넘으면 다음 날 이어서 |
| 요약 호출 간격 | 6초 | 분당 요청 한도 보호 |

- 하드코딩 경로·OS 전용 API 금지 (팀에 Windows 포함).
- 프로필 표시 이름(사용자가 Chrome에서 붙인 "개인", "회사")은 `Local State`의
  `profile.info_cache`에서 읽어 로그와 도구 설명에 함께 보여 준다. 설정과 저장소 폴더명은
  표시 이름이 아니라 프로필 폴더명(`Default`, `Profile 1`) 기준이다.
- 무료 등급 한도는 모델과 시기에 따라 바뀌므로 문서에 못 박지 않고 설정값으로 둔다. 구현 시
  당시 한도를 확인해 기본값을 맞춘다.
- 하루 최대 요약 건수·요약 호출 간격은 `src/bookmark_insight/summarize/` 상단의 모듈 상수로 두었다가 M7에서 .env 설정으로 올렸다.
- 하루 최대 요약 건수·요약 호출 간격은 M7에서 `BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES`(기본 100)·`BOOKMARK_INSIGHT_CALL_INTERVAL_S`(기본 6.0)로 .env에 올렸다 (모듈 상수는 기본값으로만 남음).
- 처음 세팅할 때는 `python -m bookmark_insight.sync --discover`로 자동 탐색된 User Data 경로, 프로필(폴더명 + 표시 이름), 북마크 파일과 개수를 확인하고 출력된 .env 줄을 복사한다. 탐색이 안 되면 Chrome의 `chrome://version`에 보이는 "프로필 경로"의 상위 폴더를 `BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR`에 적는다.

### 2.4 북마크 동기화 요구사항 (기반 A)

- Chrome 북마크 파일(JSON)을 읽어 북마크 1개 = 마크다운 1개로 저장소에 쓴다. **v1은 깃
  커밋을 만들지 않는다** (깃 미러는 M9 선택 사양, 부록 A 4). M9 구현 후에는 `BOOKMARK_INSIGHT_GIT_MIRROR=1`일 때만 동기화가 생성·갱신·보관 파일을 북마크 날짜(`date_added_utc`, 없으면 `date` 정오)로 커밋한다. 기본값은 여전히 꺼짐.
- **Chrome 프로필 탐색**: macOS `~/Library/Application Support/Google/Chrome/<Profile>/Bookmarks`,
  Windows `%LOCALAPPDATA%\Google\Chrome\User Data\<Profile>\Bookmarks`. `Local State`에서
  프로필 목록(폴더명 + `profile.info_cache`의 표시 이름)을 읽어 로그에 열거한다. **허용
  목록(2.3)에 있는 프로필만 순회한다.** 허용 목록이 비어 있으면 `profile.last_used` 하나.
  목록에 있는데 폴더가 없으면 경고하고 건너뛴다. User Data 경로를 .env로 직접 지정하면
  위치 탐색을 건너뛴다 (프로필 목록 읽기는 동일). 파싱 실패(Chrome이 쓰는 도중 등)는 1회
  재시도.
- **계정 저장소 파일**: 구글 계정으로 로그인한 Chrome은 북마크를 `<Profile>/AccountBookmarks`(같은 JSON 형식)에 두며 `Bookmarks`가 없을 수 있다. 두 파일을 모두 읽어 같은 프로필 안에서 합친다. 프로필 간 비병합 원칙은 그대로다.
- **안정 키는 Chrome `guid`.** `chrome_id`(프로필 로컬 id, Chrome Sync 시 기기마다 다름)
  대신 `guid`를 북마크 id로 쓴다. 같은 프로필 안에서 같은 URL이 두 폴더에 있으면 URL
  정규화 키로 파일 1개만 만들고 `tags`를 병합한다. **프로필 간에는 병합하지 않는다**
  (회사 프로필과 개인 프로필에 같은 URL이 있어도 각자 파일 1개).
- **파일명 규칙 (한 함수에 모으고 테스트를 집중한다)**:
  `bookmarks/<프로필 폴더명>/YYYY/YYYY-MM-DD <제목> <guid앞6자>.md`. 프로필 간 섞임이
  구조적으로 불가능하도록 프로필 폴더로 먼저 나눈다. 날짜를 앞에 두면 옵시디언에서
  시간순으로 정렬되고, 연도 폴더로 파일 수를 분산한다. 제목은 NFC 정규화, Windows 금지
  문자(`: * ? " < > | /`)와 옵시디언 `[[링크]]`를 끊는 문자(`[ ] # ^ |`, 2026-11-13 추가) 제거, 끝 점·공백 제거, 예약어(CON, NUL 등) 회피, 길이
  제한(파일명 120자, 전체 경로 240자 이하). 디렉토리 목록은 읽은 뒤 NFC 정규화해
  비교한다(macOS NFD ↔ Windows NFC). 여러 PC에서 저장소를 열어도 같은 파일로 인식되어야
  한다.
- 프런트매터: `url`, `title`, `tags`(북마크 폴더 경로에서 유도), `date`, `date_added_utc`,
  `date_source`, `guid`(id·이동·갱신 추적용), `profile`(프로필 폴더명),
  `profile_name`(`Local State`의 표시 이름), `last_used`(있으면 보존, 인사이트에는
  쓰지 않음), `archived`, `summary_status`(`pending` | `ok` | `fetch_failed`),
  `summarized_at`, `fetch_attempts`, `last_fetch_at`.
- **`date`**: Chrome `date_added`(WebKit epoch 1601-01-01 UTC, μs)에서 변환해 **로컬
  시간대의 날짜**로 기록하고 `date_added_utc`(ISO)도 보존한다. 값 0은 동기화 시각으로
  대체하고 `date_source: sync`로 표시한다 (정상은 `chrome`). "기간 내 추가" 판정 기준은
  `date`다. 폴더 이동은 `tags`만 갱신하고 `date`는 최초 추가 시각을 유지한다.
- **Chrome 삭제는 `archived: true`, 제목 변경은 파일명 유지.** Chrome에서 사라진 북마크는
  파일을 지우지 않고 프런트매터 `archived: true`로 표시해 인사이트·목록에서 제외한다
  (사용자 메모·요약 보존, 관심사 이동 신호 보존). 제목이 바뀌면 파일명은 유지하고 `title`만
  갱신한다 (`[[링크]]` 보존).
- **쓰기 소유권 규칙**: 동기화는 프런트매터만 갱신하고 본문은 건드리지 않는다. 요약기는
  본문(`## 요약`)과 상태 필드(`summary_status`, `summarized_at`, `fetch_attempts`,
  `last_fetch_at`)만 쓴다. 다만 **영역이 달라도 read-modify-write 경합은 있으므로** 아래
  쓰기 안전 규칙이 필수다.
- **쓰기 안전**: 모든 파일 쓰기는 임시 파일 + `os.replace` 원자 교체. 저장소 단위
  잠금(동기화·요약기·인사이트 배치가 동시에 같은 저장소를 쓰지 않게). 쓰기 전 mtime을
  비교해 바뀌었으면 재읽기 후 병합(사용자가 옵시디언에서 고친 본문을 덮지 않도록).
  `encoding="utf-8", newline="\n"`. 옵시디언이 파일을 열고 있어 `PermissionError`(Windows)면
  짧게 대기 후 재시도.
- **프로필 폴더 경계**: 동기화는 자기 프로필 폴더(`bookmarks/<프로필 폴더명>/`) 밖을 쓰지
  않는다. 다른 프로필 폴더의 파일을 읽거나 `archived` 처리하지 않는다.
- 동기화 직후 본문은 비어 있어도 된다. 옵시디언에서 그대로 열린다.
- 제외 폴더(기본 `private`)는 저장소에 쓰지 않고 개수도 노출하지 않는다.

예시 파일:

```markdown
---
title: WebRTC 재접속 처리 패턴
url: https://...
tags: [개발, WebRTC]
date: 2026-11-03
date_added_utc: 2026-11-02T16:12:40Z
date_source: chrome
guid: 7f3a9c2e-5b1d-4e8a-9c3f-2d7e6a1b4c05
profile: Default
profile_name: 개인
last_used: 2026-11-04
archived: false
summary_status: ok
summarized_at: 2026-11-08
---
## 요약
(요약기가 쓴 본문. fetch_failed면 "본문을 가져오지 못했다. 제목 기준으로만 다룸.")
```
- 파일명 예: `bookmarks/Default/2026/2026-11-03 WebRTC 재접속 처리 패턴 7f3a9c.md`.
- 첫 동기화는 전체 이력. 시작 날짜 설정으로 범위를 자를 수 있다.
- Chrome이 파일을 쓰는 시점이 즉시가 아닐 수 있다. 위클리 단위에서는 문제 없음.
- Chrome 파일 형식은 공식 API가 아니다. 형식이 바뀌면 동기화만 고치면 되도록 격리한다.

### 2.5 요약·인사이트 생성 요구사항 (배치)

파이프라인은 5단계다. `get_insight`가 노트 없는(또는 낡은) 기간을 요청받으면 1~4를
`BackgroundToolManager`로 백그라운드에서 실행하고 즉시 `generating`을 돌려준다 (2.2).
모든 단계는 `src/bookmark_insight/`의 함수이며 앱을 모른다.

1. **본문 가져오기**: 기간 내 북마크(private·archived 제외)의 URL 본문을 가져온다. 제외
   URL 패턴(2.3)에 걸리는 것은 건너뛴다. 로그인·유료 벽·유튜브·PDF 등 못 가져오는 것은
   `summary_status: fetch_failed`로 기록하고 제목·URL만으로 다룬다.
   - **다운로드 상한은 가져오기 단계에 둔다**: `Content-Length` 또는 스트리밍 2MB 컷.
     (20,000자 절단은 LLM 전송 전 단계라 큰 응답 자체를 막지 못한다.) 이진
     Content-Type(`application/pdf`, `image/*`, `application/octet-stream` 등)은 즉시
     `fetch_failed`. 리다이렉트 상한 5. 인코딩은 meta charset 우선(EUC-KR 페이지). JS
     렌더로 비어 있는 본문은 최소 본문 길이(예: 200자) 미만이면 `fetch_failed`.
   - 제외 URL 패턴 기본값에 사설 대역(10/8, 172.16/12, 192.168/16)·`localhost`·`127.0.0.1`을
     포함한다 (사내 페이지를 LLM에 보내지 않도록).
   - `fetch_failed` 재시도는 프런트매터 `fetch_attempts`, `last_fetch_at`으로 백오프한다
     (1일 → 3일 → 7일, 3회 후 중단). 하루 상한·호출 간격 카운터는 파일로 영속한다. 429는
     `Retry-After`를 존중한다.
   - HTML→본문 추출 라이브러리를 `pyproject.toml`에 추가하고 `uv lock` (M0).
2. **북마크별 요약**: 본문을 LLM API로 요약해 북마크 파일의 `## 요약`에 기록한다. 이 요약은
   캐시 역할을 하며, 이미 `ok`인 북마크는 다시 요약하지 않는다.
3. **상관관계·질문 생성**: 기간 내 요약 묶음을 LLM에 보내 주제 묶음, 묶음 안·묶음 사이의
   상관관계, 사용자에게 던질 질문을 만든다. 응답은 **JSON 스키마**로 받는다 (묶음 배열:
   `topic`, `bookmark_ids`, `summary`, `correlation`, `question`). LLM 자유 텍스트를 노트에
   직접 쓰지 않는다.
4. **노트 작성**: JSON 응답을 템플릿으로 렌더해 `insights/<프로필 폴더명>/<since>_<until>.md`에
   주제별 요약·상관관계·질문, 본문 미확보 목록, 북마크 줄을 쓴다. **북마크 줄은 웹 링크**
   `- [제목](URL)`(제목의 `[ ]`는 `\[ \]`로 이스케이프, URL에 공백·괄호가 있으면 `<URL>`)이며
   어떤 마크다운 뷰어에서도 열린다. 옵시디언 `[[파일명]]` 링크는 본문이 아니라 프런트매터
   `related:` 목록(`"[[파일명]]"`, `unavailable:` 다음)에 쓰고, 노트 폴더가 옵시디언 볼트 안일 때만
   쓴다 (`BOOKMARK_INSIGHT_NOTE_LINKS`, 2.3). 옵시디언은 속성의 `[[링크]]`를 그래프·백링크 간선으로
   다루므로 노트↔북마크 연결은 유지되고, 볼트 밖에서는 옵시디언 전용 내용이 전혀 없다
   (2026-11-13 변경, 이전 노트 형식 `- [[파일명]]`은 파서가 그대로 읽는다). 노트 키는
   프로필 + 기간이며 겹치는 기간을 허용한다 (지난 7일 노트와 지난 30일 노트가 공존).
   요약(1~2단계)·인사이트 배치(3~4단계)는 프로필 단위로 돈다. 한 노트에 여러 프로필의
   북마크를 섞지 않는다 (통합 보기는 부록 B).
5. **대화 기록 덧붙임**: 대화 종료(`finish_insight`) 시 `## 대화 기록 (YYYY-MM-DD HH:MM)`
   절을 덧붙인다 (LLM의 결론 + 대화 로그 PR(M8) 이후 접히는 대화 원문).

**노트 수명주기**

- `ready` 조건: 노트가 있고, **노트 생성 이후 기간 내 신규·변경 북마크가 없음**. 있으면
  낡은(stale) 노트로 보고 재생성한다 (`generating`).
- 재생성 시 기존 `## 대화 기록` 절은 보존한다. 같은 기간에 여러 번 대화하면 절 제목의
  시각으로 구분한다.
- `partial` 노트는 프런트매터 `complete: false`와 `pending: [guid, …]`(아직 요약하지 않은 북마크)로 표시하고, 본문에 `## 미요약` 절(제목 최대 10개)을 `## 본문 미확보` 앞에 둔다. 백필이 끝나면 `complete: true`로 재생성되고 `pending` 키는 사라진다 (M7). `unavailable`은 본문 수집 실패만 뜻한다.

**토큰 사용량 로그**: 배치 1회당 `usage`를 `<북마크 저장소>/.bookmark_insight/usage.jsonl`에
기록한다 (설정으로 위치 변경 가능. 4장). 사용량 로그는 프로필별로 나누지 않고 공통이다
(줄마다 `profile` 필드로 구분).

**과거 기간 백필 (M7 구현 2026-11-07)**

- 요약은 요청된 기간의 북마크만 만든다. 첫 동기화 때 들어온 과거 북마크는 요약하지 않는다.
- 과거 기간을 요청하면 미요약 건수를 세고, 배치 1회 상한(2.3)을 넘으면 백그라운드 백필
  작업을 건다. 북마크 단위로 요약을 즉시 파일에 쓰므로 중단돼도 `summary_status: pending`이
  남은 것만 이어서 처리한다.
- 백필 중 다시 요청하면 요약된 것만으로 부분 인사이트를 만든다 (`status: partial`).
- 구현: build_insight 1회가 배치 상한(기본 20)·하루 상한 안에서 한 묶음을 요약하고 그때까지 요약된 것으로 노트를 만든다. 남은 것이 있으면 `partial`, 사용자가 '이어서 정리해 줘'라고 하면 모델이 build_insight를 다시 불러 이어간다. 하루 한도에 먼저 걸리면 묶음 호출 없이 빈 partial 노트를 쓰고 '내일 이어서'라고 안내한다.

- 입력: 기간 내 북마크의 url, title, tags, date, 그리고 요약 본문. private·archived 제외.
- 출력: 옵시디언용 마크다운 노트 1개 (기간당 1개). 사람이 읽기 좋은 형식이어야 하고,
  봇은 이 노트를 읽어 대화한다.
- **실패 처리 (2026-11-07 확정)**: 배치 LLM 호출은 30초 타임아웃, 재시도 2회, thinking 예산 0. 북마크 1건의 모델 호출 실패는 summary_status: pending + summary_error로 남기고 계속한다(연속 3회 실패 시 중단). 묶음 호출이 실패하고 같은 기간의 이전 노트가 있으면 그 노트를 degraded ready로 돌려주고 미반영 북마크 제목을 함께 준다. build_insight가 도는 동안 앱은 효과음·안테나 모션으로 진행 중임을 알린다(소리·간격은 bookmark_insight_progress.py 상단 상수).

### 2.6 대화 로그 저장 요구사항

- 세션마다 사용자·봇 발화를 타임스탬프와 함께 로컬 파일로 남긴다 (전사 텍스트 기준;
  오디오는 저장하지 않는다).
- 용도: KPI 측정(1.3), 기능 2 에피소드 층의 원본.
- 외부로 전송하지 않는다. 저장 경로는 .env로 지정한다 (2.3 설정 표의 "대화 로그 폴더").
- **형식**: 세션당 JSONL 파일 1개 (`<대화 로그 폴더>/YYYY-MM-DD_HHMMSS.jsonl`), 줄마다
  `{"ts", "role": "user"|"assistant", "text"}`. 최종 전사만 기록한다 (`final=False` 델타는
  버림).
- **보관 기간**: 무제한. 외부 전송 없음.
- **구현**: 앱의 `ConversationHandler.set_transcript_observer`에 로거를 붙인다 (핸들러
  수정 아님). 슬롯이 하나라 `console.py`의 기존 observer와 공존하려면 fan-out 래퍼가
  필요하다. **별도 PR(M8)** 로 진행한다.
- **구현 (M8, 2026-11-07)**: `bookmark_insight_transcript_log.py`의 `TranscriptLogger`가 `LocalStream.add_transcript_observer` 팬아웃(M6의 activity observer와 같은 방식)으로 최종 전사만 받아 `<대화 로그 폴더>/YYYY-MM-DD_HHMMSS.jsonl`에 쓴다(첫 발화 시각으로 파일명, 지연 생성). 세션 경계: 슬립·앱 종료 훅에서 파일을 닫고, 그다음 발화가 새 파일을 연다(기상 후 인사가 새 세션의 첫 줄). 프로세스 메모리에 최근 2,000줄 링 버퍼를 두고, `finish_insight`가 세션 시작 시각 이후 줄을 `- HH:MM 사용자/리치미니: …`로 렌더해 접힌 '대화 원문' 절(최대 200줄, 넘치면 앞부분 생략 표시)로 결론 아래에 붙인다. 앱 측 자동 종료는 마커만 쓴다. 설정이 없으면 로거는 no-op.
- 로거가 없거나(설정 없음) 세션 구간에 줄이 없으면 `finish_insight`는 결론만 기록한다 (부록 A 17).

## 3. 대화 강령 (시스템 프롬프트 원칙)

### 3.1 핵심 원칙 (확정)

시스템 프롬프트(활성 프로필)는 인사이트 대화에서 다음 다섯 원칙을 지킨다. 세부
규칙(3.2~3.3)은 이 원칙의 적용이다.

1. **노트 우선 원칙 (Note-First)**: 인사이트 발화는 반드시 `get_insight`로 받은 노트를
   근거로 한다. 노트 없이 기억이나 추측으로 북마크 내용을 말하지 않는다. (환각 방지의 핵심)
2. **한 묶음, 한 질문 원칙**: 한 번의 발화에 주제 묶음 하나와 질문 하나만 꺼낸다. 사용자가
   반응한 뒤 다음 묶음으로 넘어간다. 음성으로 듣기 좋은 길이를 유지한다.
3. **평가 금지 원칙**: 읽었는지, 몇 개 모았는지, 얼마나 오래 묵혔는지를 말하지 않는다.
   북마크는 관심의 흔적이지 할 일 목록이 아니다.
4. **근거 표시 원칙**: 상관관계를 말할 때 어느 북마크끼리인지 **짧게 줄인 제목**으로 짚는다
   (예: "WebRTC 재접속 글이랑 모바일 백그라운드 글"). 긴 제목을 그대로 읽지 않는다. 본문을
   못 가져온 북마크는 "제목만 보고 하는 얘긴데"라고 밝힌다.
5. **말투는 활성 프로필을 따른다**: 어조·존댓말 여부는 3장이 정하지 않는다. 반려봇이므로
   정중함보다 다정함을 우선하되, 구체적 말투는 프로필(기능 2+ 페르소나)이 결정한다.

### 3.2 세부 규칙

- 묶음 하나당 가설 하나를 먼저 말하고 묻는다. 한 번에 다 쏟지 않는다.
- 후속 질문("그 글 뭐였지?")에는 `get_bookmark`의 요약으로 답한다.
- **프로필 선택**: 사용자가 "회사 북마크로", "개인 거로"처럼 프로필을 말하면 `profile`
  인자로 전달한다. 말하지 않으면 생략한다 (기본 프로필). 프로필 이름을 추측하지 않는다.
  도구가 `{"error": "unknown profile"}`를 돌려주면 `available`의 이름을 말해 주고 고르게
  한다.
- **미설정 안내**: 도구 결과에 `settings_url`이 오면 기억으로 북마크 얘기를 하지 않고, `next_action`대로
  설정 페이지(127.0.0.1 7860, Bookmark Insight 칸의 버튼 네 개)로 안내한 뒤 그 주제를 끝낸다. 재시작은
  필요 없으므로 사용자가 설정을 마쳤다고 하면 `get_insight`를 다시 호출한다.
- **금지**: 읽었는지 묻기, 저장만 하고 안 읽었다고 지적하기, 북마크 개수로 평가하기,
  "왜 저장했는지" 묻기 (상관관계는 봇이 본문에서 추론한다).
- **빈 기간 멘트**: "지난 일주일 동안은 새로 저장한 게 없네. 요즘 관심 있는 건 뭐야?"
  (기간을 지정했으면 "지난 일주일" 자리에 그 기간). 재촉이 아니라 대화를 잇는 질문으로
  끝낸다.

### 3.3 인사이트 대화 종료 규칙

`get_insight` 호출부터 `finish_insight` 호출까지가 인사이트 대화 한 회다. 종료는 세 가지
경로뿐이다.

1. **명시 종료**: 사용자가 "인사이트 요약", "정리", "종료"라고 하면
   `finish_insight(reason="explicit")`.
2. **주제 전환**: 다른 기능을 요청하거나 주제가 바뀌면 "북마크 인사이트 대화를 마칠까요?"라고
   **한 번만** 묻는다. 예 → `finish_insight(reason="confirmed")`. 아니오 → 계속하고
   당분간 다시 묻지 않는다. "한 번만 묻기"는 프롬프트 의존이므로 KPI가 아닌 최선 노력
   항목이다 (7장 시나리오 3은 통과율로 본다).
3. **자동 종료는 앱 측 상태 기계가 한다** (LLM·패키지가 아님). `get_insight`가
   세션(`profile`, `note_path`, `started_at`, `last_activity`)을 열고, 앱이
   `conversation_handler.set_activity_observer`로 발화마다 heartbeat를 보낸다. 무응답
   10분(2.3 설정), `go_to_sleep`, 앱 종료 훅에서 상태 기계가 `conclusion` 공백으로
   마감하고 노트에 "자동 종료"를 표시한다. 근거: 슬립 진입 시 앱은 진행 중 도구 결과를
   버리고(`gemini_realtime.py` `enter_local_standby`), 실시간 LLM은 이벤트 없이 스스로
   도구를 부르지 않으며, Gemini는 재연결 시 맥락을 잃는다. 따라서 `reason="auto"`는 LLM
   인자가 아니다 (2.2). 상태 기계는 `src/bookmark_insight/session.py`에 두고 앱 훅이 호출한다
   (6장 M6). 세션은 `profile`을 보유하므로 `finish_insight`는 프로필 인자 없이 올바른
   프로필의 노트에 기록한다.
4. **안전망**: 세션 관리자는 마지막으로 닫힌 세션(프로필·노트 경로)을 기억한다. 열린 세션이 없는 상태에서 finish_insight가 오면 그 노트에 결론을 한 번 기록하고 reopened_last=true를 돌려준다(조기 종료·자동 종료 뒤 결론 유실 방지). 그 다음 호출은 오류다.

## 4. 비기능 요구사항

| 항목 | 요구사항 규격 | 구현 메커니즘 |
| :--- | :--- | :--- |
| 대화 중 응답 지연 | 노트가 있을 때 `get_insight` 1.5초 이내, `get_bookmark` 0.5초 이내. `get_insight`는 `generating`이어도 즉시 반환(백그라운드 시작만) | 사전 생성 노트와 요약을 파일에서 읽기만 한다. 대화 중 LLM 호출·URL 가져오기 없음. 긴 작업은 `BackgroundToolManager` |
| 배치 처리 시간 | 북마크 30개 기준 3분 이내. 초과해도 실패 아님 | URL 가져오기 병렬, URL당 타임아웃 10초, 요약 캐시 재사용 |
| 비용 | 숫자 상한 없음. 배치 1회당 토큰 사용량(입력·출력·캐시)을 로그에 남기고 첫 달 뒤 상한을 정한다 | 응답 `usage` 필드 기록. 요약은 한 번만 생성하고 재사용. 본문은 북마크당 20,000자로 자른다 (튜닝 상수) |
| 배치 모델 분리 | 요약·상관관계 배치는 텍스트 모델(Gemini Flash 계열), 음성 대화는 실시간 모델 | .env의 배치용 모델 설정 (2.3) |
| 프라이버시 | private 폴더 북마크는 저장소·도구 응답·노트 어디에도 없음. 제외 URL 패턴(2.3, 기본값에 사설 대역·localhost)은 본문을 가져오지 않음. 가져온 본문·요약·대화 내용은 LLM API로 나간다 (개인용, 허용. 부록 A 11). 대화 로그는 외부 전송 없음 | 동기화 단계 필터 한 곳. 요약기는 제외 패턴 검사 후 가져오기 |
| 안정성 | 배치 중 URL 하나가 실패해도 나머지는 완료. 패키지 예외가 앱 대화를 끊지 않음 | 북마크 단위 예외 격리(`summary_status: fetch_failed`). 패키지 예외는 Tool이 `{"error": ...}`로 변환해 대화 루프 보호 |
| 이식성 | macOS·Windows에서 동일 동작 | 경로는 OS에 묻고 하드코딩 없음. 한글 파일명·본문은 UTF-8 |

### 4.1 본문 상한 근거 (조사 2026-11-06)

- **컨텍스트 한도**: Gemini 입력 컨텍스트는 약 100만 토큰이다 (공식 모델 페이지, 최신
  모델에 "1M tokens" 명시. 2.5·3 계열도 같은 100만이 여러 자료의 공통 수치).
- **토큰당 글자 수**: 영어는 약 4자 (공식 토큰 문서). 한국어는 공식 수치가 없고 약 1~2자로
  추정한다. 보수적으로 1자 = 1토큰으로 계산한다.
- **요금 구간**: 한 요청이 약 12만~20만 토큰을 넘으면 단가가 오른다 (자료마다 기준이 달라
  미확정).
- **파이프라인 대입**: 북마크별 요약은 건당 호출 1회이므로 20,000자여도 약 2만 토큰,
  한도의 2%다. 상관관계 추론은 요약만 보낸다 (30건 × 300자 ≈ 1만 자). 따라서 컨텍스트
  한도와 요금 구간 모두 닿지 않으며, 상한은 건당 비용과 요약 품질 기준으로 정한 값이다.
- **토큰 측정**: 응답 `usage`(입력·출력·thinking·캐시·도구 토큰 구분)를 배치 로그에
  기록한다. 보내기 전 검사는 `count_tokens`.
- **출처**:
  - Gemini models (official): https://aistudio.google.com/docs/models
  - Gemini tokens guide (official): https://aistudio.google.com/docs/tokens
  - Gemini token limits comparison 2026: https://www.ai-toolbox.co/gemini-models/gemini-context-window-token-limits-2026 (비공식 블로그, 가격 근거로는 쓰지 않음)
  - Gemini API pricing 2026: https://www.morphllm.com/gemini-api-pricing (비공식 블로그, 가격 근거로는 쓰지 않음)

## 5. 위험 관리 및 예외 처리 매트릭스

| 위험 식별 (Risk) | 심각도 | 영향도 | 완화 및 대응 전략 (Mitigation Strategy) |
| :--- | :--- | :--- | :--- |
| Chrome 파일 형식 변경 (Chrome 북마크 파일 형식은 공식 API가 아니라 예고 없이 바뀔 수 있다) | 보통 | 중 | 형식이 바뀌면 동기화만 고치면 되도록 동기화 스크립트를 격리한다 |
| Chrome 파일 쓰기 지연 (Chrome이 북마크 파일을 쓰는 시점이 즉시가 아닐 수 있어 최신 북마크가 동기화에 늦게 반영된다) | 낮음 | 하 | 위클리 단위에서는 문제 없음 |
| URL 본문 가져오기 실패 (로그인·유료 벽·차단·타임아웃) | 보통 | 중 | `summary_status: fetch_failed`로 기록하고 제목만으로 묶음에 포함. 노트에 본문 미확보 목록 표시. 봇은 "제목만 보고 하는 얘긴데"라고 밝힌다 (3.1 근거 표시 원칙) |
| LLM이 노트에 없는 내용을 지어냄 | 높음 | 상 | 노트 우선 원칙(3.1). 도구 결과 `next_action`에 "노트 밖 내용은 말하지 않는다" 지시. 발화의 북마크 언급을 노트 `bookmark_ids`와 대조(v1 수동 샘플링) |
| 배치 도중 중단 (네트워크, API 오류, 사용자 종료) | 보통 | 중 | 북마크 단위로 요약을 즉시 파일에 써서 재실행 시 이어서 처리. `summary_status: pending`으로 미완 표시 |
| 동기화가 요약 본문을 덮어씀 | 높음 | 상 | 쓰기 소유권 규칙(2.4): 동기화는 프런트매터만 수정. 본문 보존을 자동 테스트로 검증 |
| 동시 쓰기 경합 (동기화·요약기·옵시디언 편집) | 높음 | 상 | 원자 쓰기 + 저장소 잠금 + mtime 비교 후 재읽기 (2.4) |
| 한글 파일명 정규화 불일치 (macOS NFD ↔ Windows NFC) | 보통 | 중 | 쓰기 시 NFC 정규화, 읽기 시 정규화 비교 (2.4). 여러 PC 공유 전제 |
| 사설 IP·사내 페이지를 가져와 LLM에 전송 | 보통 | 중 | 제외 URL 패턴 기본값에 사설 대역·localhost (2.5) |
| 거대 응답·이진 파일 다운로드 | 낮음 | 중 | 가져오기 단계 2MB 컷, 이진 타입 즉시 실패 (2.5) |
| 주제 전환 질문이 반복돼 귀찮음 | 보통 | 중 | 한 번만 묻기(3.3). "아니오" 뒤 일정 턴 동안 재질문 금지. 프롬프트 의존이라 통과율로 측정 |
| 자동 종료가 대화 중에 발동 | 낮음 | 하 | 무응답 10분 기준, 앱 측 상태 기계가 발화마다 heartbeat로 타이머 리셋(3.3). 노트에 "자동 종료" 표시로 사후 확인 가능 |
| Gemini 세션 재연결로 인사이트 대화 맥락 상실 | 보통 | 중 | 세션 상태는 앱 측 상태 기계가 보유. `get_insight` 재호출로 복구 (노트가 `ready`라 즉시) |
| 북마크 파일명 충돌 (같은 날 같은 제목) | 낮음 | 하 | 파일명에 `guid` 앞 6자 접미사 추가 (2.4) |
| 비용 폭증 (첫 동기화 시 수년치 북마크 전부 요약) | 보통 | 중 | 요청된 기간만 요약. 과거 기간은 요청 시 배치 1회 상한·하루 상한·호출 간격(2.3) 안에서 백그라운드 백필. 진행 중 요청은 부분 인사이트(`status: partial`) |
| 무료 등급 한도 초과 (429) | 보통 | 중 | 하루 상한·호출 간격 설정으로 예방. 429 수신 시 해당 배치를 중단하고 다음 날 이어서. 토큰 사용량 로그(4장)로 추적 |
| 타인·회사 프로필 북마크가 동기화되어 LLM으로 나감 (친구가 내 노트북에서 로그인 등) | 높음 | 상 | 프로필 허용 목록(2.3): 목록에 없는 프로필은 읽지 않음. 프로필별 폴더 분리로 섞임 불가. 기본값은 프로필 1개 |

## 6. 구현 마일스톤 및 병렬 개발 계획

### 6.1 전제

- 혼자 개발하되 **서브에이전트를 병렬로** 돌린다. 서브에이전트는 서로 대화하지 않으므로
  파일 충돌을 구조로 막는다.
- 트랙마다 자기 디렉토리만 쓴다. 공유 파일(`config.py`, `.env.example`, `pyproject.toml`,
  `uv.lock`)은 M0에서 메인이 먼저 고정하고 트랙은 건드리지 않는다.
- 트랙마다 별도 git worktree·브랜치(`feat/weekly-insight-<track>`), 메인이
  `feat/weekly-insight`로 합친다. 합류 지점마다 전체 게이트:
  `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v`.
  공통 검증 기준: **Windows CI 녹색**.
- 에뮬레이터(MuJoCo 데몬)는 하나뿐이므로 D 트랙(앱 연동)은 병렬이 아니라 메인이 직접 한다.
- 시작 날짜 설정은 기본 "전체 이력"을 유지한다(관심사 타임라인·기능 2 재료). 요약만 요청
  기간으로 제한한다.

### 6.2 디렉토리 배치

```
src/bookmark_insight/
  __init__.py
  sync/        ← A 트랙: Chrome 읽기, 파일명 규칙, 원자 쓰기, archived
  summarize/   ← B 트랙: URL 가져오기, 요약, 캐시, 백오프
  insight/     ← B 트랙: 묶음·상관관계(JSON 스키마) → 노트 템플릿 렌더
  session.py   ← D: 인사이트 세션 상태 기계 (heartbeat, 10분, 슬립 훅)
  llm.py       ← M0: 배치 LLM 클라이언트 인터페이스 + 가짜 구현
src/reachy_mini_conversation_app/tools/
  get_insight.py get_bookmark.py list_bookmarks.py finish_insight.py   ← C 트랙 (얇은 Tool)
profiles/deskmate_ko/profile.md   ← D
tests/bookmark_insight/
  fixtures/ (가짜 Bookmarks JSON, 북마크 md 샘플, 인사이트 노트 샘플, 가짜 LLM 응답)
  test_sync.py test_summarize.py test_insight.py test_tools.py test_session.py
```

### 6.3 마일스톤 표

| 마일스톤 | 트랙 | 산출물 | 검증 기준 | 의존 |
| :--- | :--- | :--- | :--- | :--- |
| M0 계약·픽스처·뼈대 | 메인 | 북마크 md 형식(2.4)·인사이트 노트 템플릿·도구 반환 JSON(2.2) 확정; `tests/bookmark_insight/fixtures/` (한글 제목, private 하위폴더, 중복 URL, `date_added` 0, Windows 금지 문자 제목, 프로필 2개 + `Local State`(`info_cache` 포함) 포함); `src/bookmark_insight/` 빈 모듈과 `llm.py` 가짜 LLM(usage 포함); `config.py`·`.env.example`·`pyproject.toml`(HTML 추출 라이브러리)·`uv lock`; 프로필 복제 정책 결정(`desk_companion_ko` 복제 vs 절 추가) | 빈 모듈 import 성공, 게이트 통과, 픽스처 로드 테스트 | 없음 |
| M1 대화 품질 선검증 | 메인(에뮬레이터) | 손으로 쓴 인사이트 노트 1개 + 고정 결과를 돌려주는 stub Tool 4개 + `deskmate_ko` 초안 프롬프트(3장) | 에뮬레이터에서 시나리오 1·2·3이 "재밌는 대화"로 성립. 프롬프트 반복 수정 | M0 |
| M2 북마크 동기화 | A | `src/bookmark_insight/sync/` + `test_sync.py` | 가짜 Chrome JSON N건 → md N건, `date`=`date_added`(로컬 날짜), guid 키, private 제외, 중복 URL 1파일, archived·제목 변경 정책, 한글·금지 문자·긴 제목 파일명, NFC 비교, 본문 보존(원자 쓰기·mtime), 허용 목록 밖 프로필 무시, 프로필별 폴더 분리, 프로필 내 URL 병합·프로필 간 비병합, Windows CI | M0 |
| M3 요약기 | B | `src/bookmark_insight/summarize/` + `test_summarize.py` | 가짜 HTTP 서버(`tests/test_mcp_client_integration.py`의 uvicorn 패턴 재사용)로 성공·실패·거대 응답·이진·사설 IP·빈 본문 케이스. `fetch_failed`+백오프 기록, 20,000자 절단, 가짜 LLM usage 로그 | M0 |
| M4 인사이트 배치 | B | `src/bookmark_insight/insight/` + `test_insight.py` | 요약 3건 → JSON 스키마 응답(가짜 LLM) → 템플릿 노트. 구조·`[[링크]]`·본문 미확보 목록 검증. 빈 기간 `empty`. stale 재생성 시 대화 기록 절 보존 | M0 (M3와 병렬, 형식만 공유) |
| M5 앱 내 Tool | C | `tools/` 4개 + `test_tools.py` | 샘플 노트로 `ready`/`empty` 즉시 반환, `generating` 경로가 `BackgroundToolManager`로 시작되고 완료 결과가 전달됨, 예외 → `{"error"}`, `profile` 인자 미지정=기본 프로필, 미허용 값=`{"error"}`, `__call__` 양 끝에서 지연 측정 1.5초/0.5초 | M0 (M2~M4와 병렬, 샘플 파일로 동작) |
| M6 세션 상태 기계·앱 연동 | 메인(D) | `session.py`, `deskmate_ko` 완성, activity observer heartbeat, 슬립·종료 훅 | 에뮬레이터에서 7장 시나리오 1~5. 시나리오 3은 통과율. KPI 중 로그 의존 항목 제외 | M1, M5 + (M2·M3·M4 합류) |
| M7 백필·부분 인사이트 | B | `backfilling`/`partial` 상태, 쪼개기 배치, 하루 상한·간격 .env | 상한 초과 시 백그라운드 전환, 중단 후 재개, 부분 인사이트 | M4, M5 (데모 범위 밖) |
| M8 대화 로그 PR | 메인 | transcript observer 로거 + fan-out 래퍼 (별도 PR) | JSONL 생성, 콘솔 observer 공존, `finish_insight`가 원문 접힘 절 추가 | M6 (별도 PR) |
| M9 깃 미러·MCP 서버 (선택) | — | 북마크별 과거 날짜 커밋 재구성 스크립트, 패키지를 감싸는 MCP 서버 | 프런트매터 `date`로 재구성한 로그가 시간순, 서버가 같은 결과 반환 | M2 (후순위) |
| M10 셋업 대시보드 | 메인 | 설정 웹 UI(7860)에 Bookmark Insight 패널: 프로필 탐색·동기화·.env 적용·데모 데이터 버튼 (`bookmark_insight_routes.py`, 셸 실행 없음) | 터미널 없이 첫 세팅 완료, 결과가 화면에 표시, Windows 동작, 라우트 테스트 | M6 (에뮬레이터 검증 후), 계획: `2026-11-07-deskmate-setup-dashboard-plan.md` |

#### 6.3.1 진행 상황 (2026-11-07 새벽, 브랜치 `feat/weekly-insight`)

| 마일스톤 | 상태 | 비고 |
| :--- | :--- | :--- |
| M0 | 완료 | `src/bookmark_insight/` 계약 모듈, 픽스처, `.env.example` 7절, `pyproject`(pyyaml·trafilatura) |
| M1 | 준비됨 | `profiles/deskmate_ko` + `scripts/bookmark_insight_demo_setup.py`(픽스처 저장소 복사). 에뮬레이터 대화 검증은 사용자 세션에서 |
| M2 | 완료 | `bookmark_insight.sync` (A 트랙, 테스트 43) + `python -m bookmark_insight.sync` CLI |
| M3·M4 | 완료 | `bookmark_insight.summarize`·`bookmark_insight.insight` (B 트랙, 테스트 25) |
| M5 | 완료 | 도구 5개 (C 트랙, 테스트 28) + 종단 테스트 `test_bookmark_insight_e2e.py` (A+B+C 합류) |
| M6 | 완료 | `bookmark_insight.session`에 `close_if_idle`·`auto_finish`(노트에 `_자동 종료: <사유>_` 절 기록) 추가. 앱 훅 `bookmark_insight_hooks.py`: activity observer heartbeat(`LocalStream.add_activity_observer` 팬아웃), 무응답 감시 스레드(2초 폴링, `BOOKMARK_INSIGHT_INSIGHT_IDLE_TIMEOUT_MIN`), `go_to_sleep_action` 진입·앱 종료 `finally`에서 자동 마감. `finish_insight` 세션 없음 오류에 `next_action` 추가. 코드 완료 + 실기기(시뮬 + 실제 Chrome 북마크) 검증 2026-11-07: 시나리오 1 통과(생성 → 묶음+질문 → explicit 종료 → 노트), 2 통과(빈 기간 멘트), 4 통과(본문 미확보 → 노트 '본문 미확보' 절, 기존 대화 기록 절 보존), 5 통과(무응답 1분·앱 종료·슬립 전환 모두 실측; 슬립 시 봇이 finish_insight를 먼저 불러 결론 기록 → 훅은 닫을 세션 없음이 정상 경로), 6 통과(동기화 로그에 프로필 열거·Profile 1 건너뜀·폴더 미생성, 미허용 프로필 요청 → unknown profile + available). 시나리오 3은 프롬프트 보강 전 1/4, 보강 후 2/2 (누계 2/6). 월간(30일) 노트 생성 17.6초, get_insight 0.35초(동기화 포함) |
| M7 | 완료 | 2026-11-07 커밋 d0ff88d·dd7d5fe (브랜치 feat/deskmate-backfill 병합 429f75b). partial/backfilling 상태, pending 프런트매터, ## 미요약 절, BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES·BOOKMARK_INSIGHT_CALL_INTERVAL_S, 테스트 +12 |
| M8 | 완료 | 2026-11-07 커밋 5386a01·42ae2d2. 세션당 JSONL 전사 로그 + 팬아웃, finish_insight 대화 원문 접힘 절, 테스트 +14 |
| M9 | 완료 | 2026-11-07 커밋 4b3dce4·a18ce41. 깃 미러(src/bookmark_insight/sync/git_mirror.py: 북마크별 과거 날짜 커밋 재구성 rebuild_history, 동기화 시 자동 커밋은 BOOKMARK_INSIGHT_GIT_MIRROR=1일 때만, CLI --git-rebuild/--git-mirror, bookmark:/update:/archive: 메시지, insights/는 미러 대상 아님) + 도구 로직을 src/bookmark_insight/api.py로 추출하고 FastMCP 서버 src/bookmark_insight/mcp_server.py(stdio 기본, --http 시 http://127.0.0.1:8765/mcp)가 같은 5개 도구를 노출. 앱 도구는 얇은 래퍼(효과음·대화 원문 첨부만 앱 측). 테스트 +38 |
| M10 | 1차 초안 | 브랜치 feat/deskmate-dashboard (포크 푸시, PR 없음), 라우트 테스트 29, 브라우저 확인 대기 |

전체 게이트: ruff·mypy strict·pytest 749개 통과 (macOS). Windows CI는 PR 시 확인.

- 2026-11-07 추가 (M2 보완): Chrome 로그인 계정 북마크 파일 `AccountBookmarks`도 읽어 같은 프로필 안에서 `Bookmarks`와 합친다 (로그인 상태의 Chrome은 `Bookmarks` 없이 `AccountBookmarks`만 가질 수 있음. 사용자 PC에서 확인). 파일 하나라도 깨지면 그 프로필은 건너뛴다(부분 읽기로 인한 오보관 방지). 세팅 확인용 `python -m bookmark_insight.sync --discover`(프로필·북마크 파일·개수와 .env 줄 출력) 추가.
- 2026-11-07 시나리오 1 실제 북마크로 통과 (generating → ready → 묶음+질문 → explicit 종료 → 노트 기록). build_insight 894초 소요 원인은 배치 모델 지연 → 기본값을 gemini-3.5-flash로 변경.
- 2026-11-07 실측 이슈와 대응 (커밋 cb6604c, 8150ee9, 6e65159): (a) Gemini Live 백엔드가 도구 응답을 앱 전체 하나의 묶음으로 보내 긴 도구(build_insight, pomodoro_timer)가 도는 동안 task_status 응답이 전달되지 않아 봇이 침묵 → 서버 메시지 단위 묶음으로 수정. (b) 배치 모델 호출이 SDK 기본 재시도(5회, 1→60초)로 2~4분 대기 후 503 → 30초 타임아웃·2회 재시도·thinking_budget 0, 요약 실패는 pending 유지 후 계속, 묶음 실패 시 직전 노트를 degraded ready로 제공. (c) 사용자가 작업 중인지 알 수 없음 → build_insight 동안 효과음(시작·1초 틱·완료)과 안테나 모션(bookmark_insight_progress.py). (d) Chrome에 새로 저장한 북마크가 build_insight 전엔 보이지 않음 → get_insight/list_bookmarks가 읽기 전 동기화(30초 스로틀). (e) 봇이 맞장구를 마무리로 오해해 세션을 조기 종료, 이후 진짜 종료가 유실 → 마지막 닫힌 세션 기억 후 finish_insight가 그 노트에 기록(reopened_last), 프롬프트에 명시 마무리 규칙. (f) generating 뒤 봇이 말없이 build_insight만 호출 → 프롬프트에 '먼저 소리 내어 말하기'.
- 2026-11-07 관찰 (미해결): Live 모델의 thinking 텍스트가 assistant 전사로 로그에 섞임(role=assistant content=thought …); 앱이 회의 중 Google Meet 음성을 그대로 듣고 응답함(마이크 게이팅 필요); 첫 묶음 발화가 길어 되물음(프롬프트로 2문장 제한 적용, 재측정 필요); 슬립 5초 뒤 웨이크 워드 엔진이 점수 1.000으로 자동 기상(17:45:45, 사용자가 부르지 않음) → 웨이크 워드 모듈 오탐 가능성, 담당 전달; 효과음 틱 간격은 사용자 피드백으로 1초 확정(커밋 1cffc0d).
- 2026-11-13 실측 이슈 (M10, 새 클론 테스트): CLI(`reachy-mini-conversation-app --ui`)로 띄우면 instance_path가 없어 Bookmark Insight 패널·Connection 저장이 `os.environ`에만 반영되고 `.env`에 쓰이지 않아 재시작 시 사라짐 → `console.py`가 앱이 읽은 `.env`(`find_dotenv(usecwd=True)`, 없으면 현재 폴더에 생성)로 폴백, `REACHY_MINI_SKIP_DOTENV`면 메모리만. 회귀 테스트 4개 (커밋 687c38b). 같은 테스트에서 README에 git lfs install·`uv venv --python python3.12`·시뮬 데몬 띄우는 법·Gemini 최소 .env를 보강.
- 2026-11-13 실측 이슈 (M10): 인사이트 노트의 `[[위키링크]]`는 저장소가 옵시디언 볼트(`.obsidian/`이 있는 폴더) 안에 있을 때만 열리는데, 대시보드 기본 추천(`~/Documents/bookmarks`)은 볼트 밖이어서 링크가 전부 "찾을 수 없음"으로 보임 → `src/bookmark_insight/obsidian.py`(앱 비의존)가 옵시디언 전역 설정 `obsidian.json`(macOS `~/Library/Application Support/obsidian`, Windows `%APPDATA%\obsidian`, Linux `$XDG_CONFIG_HOME`/`~/.config/obsidian`, 재정의 `BOOKMARK_INSIGHT_OBSIDIAN_CONFIG`)에서 볼트를 읽어(open 우선·최신순) `bookmark_insight.status`가 `<첫 볼트>/bookmarks`를 추천하고 `obsidian: {installed, vaults, suggested_repo_reason}`를 돌려준다. `bookmark_insight.repo.set` 미리보기는 `in_obsidian_vault`·`vault_path`·`vault_name`을 추가로 알려 주고, 패널은 옵시디언이 무엇인지 한 줄 설명과 "볼트 안/밖" 표시(차단 아님)를 보인다. 테스트 +7.
- 2026-11-13 결정 (M10): 노트 본문의 북마크 줄을 `[[파일명]]`에서 **웹 링크** `- [제목](URL)`로 바꿈 (제목 `[ ]` 이스케이프, 공백·괄호 URL은 `<URL>`; 북마크 미로드 시 `(guid)` 유지, `## 미요약`은 제목만). 옵시디언 그래프 연결은 프런트매터 `related:` 목록(`"[[파일명]]"`, `unavailable:` 다음, 한 줄에 하나)으로 옮기고 노트 폴더(`BOOKMARK_INSIGHT_INSIGHTS_DIR`, 없으면 저장소)가 볼트 안일 때만 씀 → 새 설정 `BOOKMARK_INSIGHT_NOTE_LINKS=auto|on|off`(`render.note_links_enabled`). 파서는 `related` 유무·구형 노트 모두 읽음(`InsightNote.related`). 파일명 금지 문자에 `[ ] # ^`(`|`는 기존) 추가(`naming.OBSIDIAN_LINK_CHARS`); 기존 파일은 guid 매칭이라 이름 유지, 이름 바꾸기 마이그레이션은 하지 않음. 패널 "볼트 밖" 문구·SETUP.md 8절·.env.example 갱신. 테스트 +5(갱신 포함).
- 2026-11-13 결정 (이름 변경): 패키지 `deskmate`를 `bookmark_insight`로 바꿈 (앱 모듈 `bookmark_insight_*.py`, 환경변수 접두어 `BOOKMARK_INSIGHT_*`, JSON-RPC `bookmark_insight.*`, 저장소 내부 상태 폴더 `.bookmark_insight/`, 테스트·스크립트 동명 변경). 이유: "DeskMate"는 팀 프로젝트 전체의 이름이고 이 패키지는 그중 기능 하나이므로 기능 이름을 쓴다. 사용자 노출 문구도 "위클리 인사이트"에서 "북마크 인사이트 / Bookmark Insight"로 통일(노트 제목 줄 포함, 파서는 제목에 의존하지 않아 기존 노트도 그대로 읽힘). 기존 저장소의 `.deskmate/`는 처음 접근할 때 한 번 `.bookmark_insight/`로 이름을 바꾼다. 페르소나 `profiles/deskmate_ko`와 이 문서·브랜치 이름은 그대로 둔다.

### 6.4 병렬 트랙과 합류

```
M0 (메인) ──┬── A: M2 동기화 ──────────┐
            ├── B: M3 요약기 → M4 배치 ──┼── 합류 1: A+B (실제 동기화 결과에 요약기, 본문 보존 검증)
            ├── C: M5 Tool ─────────────┤── 합류 2: B+C (Tool이 샘플 대신 실제 노트)
            └── 메인: M1 대화 품질 ──────┴── 합류 3: C+메인 → M6 (에뮬레이터 시나리오 1~5)
                                                         └── M7 백필 (B, 데모 후) / M8 로그 PR / M9 선택
```

A·B·C는 M0 직후 동시 시작. 메인은 M1을 에뮬레이터로 진행하면서 합류 지점마다 게이트를
돌린다.

### 6.5 서브에이전트 브리프 틀 (트랙별로 복사해 쓴다)

```
목표: (한 문장)
쓸 수 있는 디렉토리: (트랙 디렉토리 + 자기 테스트 파일)
건드리면 안 되는 파일: 공유 파일(config.py, .env.example, pyproject.toml, uv.lock),
  다른 트랙 디렉토리, upstream 모듈(src/reachy_mini_conversation_app/ 중 tools/ 밖)
입력 계약: (읽는 픽스처, 형식이 정의된 절 번호)
출력 계약: (써야 하는 형식이 정의된 절 번호)
완료 기준: (통과해야 하는 테스트 파일·케이스)
게이트 명령: ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v
금지: git push, 의존성 추가, uv lock 변경, 다른 트랙 파일 수정
보고 형식: 바뀐 파일 목록, 못 한 것과 이유, 계약(2.x)과 어긋난다고 본 지점
```

**브리프 A — 북마크 동기화 (M2)**

```
목표: Chrome Bookmarks JSON을 2.4 규칙대로 bookmarks/<프로필 폴더명>/*.md로 동기화하는 src/bookmark_insight/sync/ 구현
쓸 수 있는 디렉토리: src/bookmark_insight/sync/, tests/bookmark_insight/test_sync.py
  (런타임 쓰기는 저장소의 bookmarks/<프로필 폴더명>/ 안으로만. 다른 프로필 폴더는 읽지도 쓰지도 않음)
입력 계약: tests/bookmark_insight/fixtures/의 가짜 Bookmarks JSON (한글 제목, private 하위폴더, 중복 URL,
  date_added 0, 금지 문자 제목, 프로필 2개 + Local State). 프로필 허용 목록(2.3)·탐색·guid 키·date 변환 규칙은 2.4
출력 계약: 2.4 프런트매터(profile, profile_name 포함)와 예시 파일. 프로필별 폴더 분리.
  파일명 규칙 함수 하나(build_filename)에 집중. 쓰기 안전(원자 교체, 저장소 잠금, mtime 비교) 2.4
완료 기준: test_sync.py — N건→N파일, date=로컬 날짜, private 0건, 같은 프로필 내 중복 URL 1파일+tags 병합,
  프로필 간 비병합, 허용 목록 밖 프로필은 폴더조차 생성되지 않음,
  archived·제목 변경, NFC 비교, 본문 보존(요약 본문이 있는 파일을 재동기화해도 본문 불변)
게이트 명령: (틀과 같음)
금지: 깃 커밋 생성(M9), 요약기·인사이트 코드 호출, 공유 파일 수정
보고 형식: (틀과 같음)
```

**브리프 B — 요약기·인사이트 배치 (M3 → M4)**

```
목표: 2.5의 1~4단계를 src/bookmark_insight/summarize/(가져오기·요약·캐시·백오프)와
  src/bookmark_insight/insight/(JSON 스키마 → 템플릿 노트)로 구현
쓸 수 있는 디렉토리: src/bookmark_insight/summarize/, src/bookmark_insight/insight/,
  tests/bookmark_insight/test_summarize.py, tests/bookmark_insight/test_insight.py
입력 계약: 2.4 형식의 북마크 md 샘플(fixtures), llm.py의 가짜 LLM(M0, usage 포함),
  tests/test_mcp_client_integration.py의 uvicorn 가짜 HTTP 서버 패턴
출력 계약: 북마크 파일 ## 요약 + 상태 필드(2.4 소유권 규칙·쓰기 안전 준수),
  insights/<프로필 폴더명>/<since>_<until>.md 템플릿 노트(2.5 4단계, 프로필 단위로 실행), usage.jsonl(2.5, 공통)
완료 기준: test_summarize.py — 성공·실패·2MB 초과·이진 타입·사설 IP·빈 본문·리다이렉트 5회,
  fetch_failed 백오프 기록, 20,000자 절단, ok 캐시 재사용 없음(재요약 안 함);
  test_insight.py — 요약 3건→노트 구조·[[링크]]·미확보 목록, 빈 기간 empty, stale 재생성 시 대화 기록 절 보존
게이트 명령: (틀과 같음)
금지: 실제 네트워크·실제 LLM 호출, 하루 상한·간격의 .env 승격(M7), 공유 파일 수정
보고 형식: (틀과 같음)
```

**브리프 C — 앱 내 Tool 4개 (M5)**

```
목표: 2.2 계약대로 tools/get_insight.py, get_bookmark.py, list_bookmarks.py, finish_insight.py 구현.
  src/bookmark_insight/ 함수를 얇게 감싸고 BackgroundToolManager로 generating 경로를 시작한다
쓸 수 있는 디렉토리: src/reachy_mini_conversation_app/tools/ 위 4개 파일, tests/bookmark_insight/test_tools.py
입력 계약: src/bookmark_insight/의 함수 시그니처(M0 뼈대. 구현 전이면 fixtures의 샘플 노트·북마크로 동작),
  tools/core_tools.py의 Tool 베이스, tools/pomodoro_timer.py의 BackgroundToolManager 사용 예
출력 계약: 2.2 반환 JSON(today, next_action 포함). 실패는 {"error": ...}. tags 인자 없음
완료 기준: test_tools.py — 샘플 노트로 ready/empty 즉시 반환, generating이 매니저로 시작되고
  완료 결과가 전달됨, 패키지 예외→{"error"}, get_insight 1.5초·get_bookmark 0.5초 이내(샘플 파일 기준),
  archived 북마크가 list_bookmarks에 없음
게이트 명령: (틀과 같음)
금지: 다른 tools/ 파일·core_tools.py·conversation_handler.py 수정, 세션 상태 기계 구현(M6), 공유 파일 수정
보고 형식: (틀과 같음)
```

## 7. 검증 시나리오

MuJoCo 시뮬 로봇을 띄우고, 테스트용 Chrome 북마크 파일에 북마크 세 개를 넣어 동기화한
뒤 진행한다.

### 시나리오 1 (정상)

- **사용자 발화**: "이번 주 북마크 얘기하자"
- **기대 동작**: `get_insight` 호출 → `status: generating` → 봇이 "잠깐 훑어볼게"라고
  말하고 대기 → 백그라운드 파이프라인 완료 후 `BackgroundToolManager`가 결과를 전달하면
  봇이 스스로 감정 모션과 함께 주제 묶음 하나와 상관관계 가설을 말하고 질문으로 끝낸다 →
  사용자가 "정리해 줘" → `finish_insight(reason="explicit")` → `insights/` 노트에
  `## 대화 기록 (시각)` 절(결론. 원문 접힘은 M8 이후)이 생성됐는지 확인. 같은 기간을 다시
  요청하면 `ready`로 즉시 반환되는지도 확인.

### 시나리오 2 (빈 기간)

- **사용자 발화**: 북마크가 없는 기간을 지정해 인사이트를 요청한다.
- **기대 동작**: 빈 기간 멘트가 나온다 (3장).

### 시나리오 3 (주제 전환 종료)

- **사용자 발화**: 인사이트 대화 중 "뽀모도로 시작해 줘"
- **기대 동작**: 봇이 "북마크 인사이트 대화를 마칠까요?"라고 1회 질문 → 사용자 "응" →
  `finish_insight(reason="confirmed")` → 뽀모도로 시작. 프롬프트 의존이므로 여러 번 돌려
  통과율로 측정한다 (3.3).

### 시나리오 4 (본문 미확보)

- **조건**: 테스트 북마크 중 하나의 URL이 응답하지 않는다.
- **기대 동작**: 해당 북마크가 `summary_status: fetch_failed`로 기록되고, 노트의 본문
  미확보 목록에 표시되며, 제목으로만 주제 묶음에 포함된다.

### 시나리오 5 (자동 종료)

- **조건**: 인사이트 대화 중 사용자가 10분(테스트에서는 설정값을 줄임) 동안 아무 말도
  하지 않는다.
- **기대 동작**: 앱 측 상태 기계가 LLM 개입 없이 세션을 마감하고 노트에 "자동 종료"를
  표시한다. 이후 사용자가 다시 "이번 주 북마크 얘기하자"라고 하면 `get_insight`가 새
  세션을 열고 노트에 새 `## 대화 기록 (시각)` 절이 추가된다. 슬립 전환(`go_to_sleep`)
  으로도 같은 마감이 일어나는지 확인.

### 시나리오 6 (프로필 허용 목록)

- **조건**: 허용 목록에 `Default`만 있고, PC의 Chrome에는 `Profile 1`(친구 계정)도 있다.
- **기대 동작**: 동기화 로그에 두 프로필(폴더명 + 표시 이름)이 열거되고 `Profile 1`은
  건너뛴다. 저장소에 `bookmarks/Profile 1/` 폴더가 생기지 않는다. 대화에서 "회사 북마크로
  해 줘"(미허용 프로필) → `get_insight`가 `{"error": "unknown profile", "available": [...]}`
  를 돌려주고, 봇이 쓸 수 있는 프로필 이름을 말해 준다 (3.2).

## 부록 A. 결정 사항 (회의 2026-11-06)

1. **트리거는 사용자 요청만.** "북마크 인사이트 해줘", "지난 3일 북마크 얘기하자" 등.
   봇이 먼저 제안하는 것은 이번 범위에서 제외.
2. **기간**: 사용자가 말하면 그 값, 말하지 않으면 **지난 7일**.
3. **북마크 입력은 Chrome 북마크 그대로.** 사용자는 Cmd+D만 한다. 별도 저장 동작 없음.
   1차는 Chrome 계열만 지원 (Safari·Firefox는 범위 외).
4. **북마크 추가 시각은 프런트매터 `date`가 기준.** 깃 미러(북마크별 과거 날짜 커밋)는
   v1에서 끈다 (M9 선택 사양). 근거: 기능 1은 깃 로그를 읽지 않고, 수천 건 커밋 서브프로세스
   비용(Windows 건당 100ms+)·git 설치 의존·동시 쓰기 복잡도가 데모 가치보다 크다. `date`가
   파일에 남으므로 타임라인은 M9에서 언제든 동일하게 재구성 가능.
   - 프런트매터 `date`는 Chrome의 `date_added` (로컬 날짜. 2.4).
   - 폴더 이동은 `tags`만 갱신하되 `date`는 최초 추가 시각 유지.
   - "기간 내 추가" 판정 기준은 프런트매터 `date`.
5. **읽을 폴더는 .env로 지정, 기본값은 전체.** Chrome의 세 루트(`bookmark_bar`,
   `other`, `synced`)를 가상의 최상위로 보고, 포함 목록이 비어 있으면 셋을 모두 순회.
   폴더 경로는 화면 이름이 아니라 JSON 키 기준 (`bookmark_bar/개발`). 화면 이름은
   Chrome 언어 설정에 따라 바뀌므로 쓰지 않는다.
6. **인사이트 생성은 배치.** 기간 내 북마크를 모아 LLM API로 주제 묶음과 가설을 만들어
   노트로 저장하고, 대화 중에는 그 노트를 읽어서 말한다. 요청한 기간의 노트가 없으면
   백그라운드로 생성하며(2.2 `generating`), 지연 안내 멘트("잠깐 훑어볼게")를 허용한다.
7. **인사이트 노트는 사람이 옵시디언에서 읽는 용도.** 봇 전용 내부 파일은 두지 않는다.
   저장 경로는 .env, 기본값은 `북마크 저장소/insights/` (바탕화면은 설정으로 변경 가능).
   2026-11-13 수정: 본문의 북마크 줄은 웹 링크 `[제목](URL)`로 통일해 옵시디언 없이도 읽히게
   하고, 옵시디언 볼트 안일 때만 프런트매터 `related:`에 `"[[파일명]]"`을 적어 그래프·백링크를
   잇는다 (`BOOKMARK_INSIGHT_NOTE_LINKS` auto/on/off). 파일명에서 `[ ] # ^ |`도 제거한다.
8. **빈 기간 멘트**: "지난 일주일 동안은 새로 저장한 게 없네. 요즘 관심 있는 건 뭐야?"
   (기간을 지정했으면 "지난 일주일" 자리에 그 기간). 재촉이 아니라 대화를 잇는 질문으로 끝낸다.
9. **인사이트는 북마크 본문을 읽고 만든다.** 봇이 URL 본문을 가져와 북마크별 요약을 만들고,
   요약들 사이의 상관관계를 추론해 질문을 만든다. 사용자에게 "왜 저장했는지"를 묻지 않는다.
10. **본문을 못 가져오면 제목·URL만으로 다룬다.** 로그인·유료 벽·유튜브·PDF 등은 1차
    범위에서 본문 추출하지 않는다. `summary_status: fetch_failed`로 기록하고 노트에
    "본문 미확보"로 표시한다. 제목은 파일명·프런트매터에 있으므로 최소 정보는 확보된다.
11. **본문이 LLM API로 나가는 것은 허용한다.** 개인용이므로 private 폴더 제외로 충분하다.
    다만 제외 URL 패턴 설정을 둔다 (사내 위키 등).
12. **인사이트 노트는 대화가 끝난 뒤 완성한다.** 배치가 요약·상관관계·질문을 쓰고, 대화
    종료 시 LLM의 결론과 대화 원문(접히는 절)을 모두 덧붙인다.
13. **과거 기간은 요청 시에만, 쪼개서 요약한다.** 요약은 요청된 기간의 북마크만 만들고, 과거
    기간은 사용자가 기간을 말하면 무료 한도 안에서 배치를 나눠 백그라운드로 돌린다. 진행 중에
    다시 물으면 요약된 것만으로 부분 인사이트를 준다. (M7, 데모 범위 밖)
14. **Chrome 삭제는 `archived: true`, 제목 변경은 파일명 유지.** 삭제된 북마크는 파일을
    지우지 않고 프런트매터 `archived: true`로 표시해 인사이트·목록에서 제외한다 (사용자
    메모·요약 보존, 관심사 이동 신호 보존). 제목 변경 시 파일명은 유지하고 `title`만
    갱신한다 (`[[링크]]` 보존).
15. **안정 키는 Chrome `guid`.** `chrome_id`(프로필 로컬 id, Chrome Sync 시 기기마다 다름)
    대신 `guid`를 id로 쓴다. 같은 프로필 안에서 중복 URL(두 폴더)은 URL 정규화 키로 파일
    1개, 태그 병합. 프로필 간에는 병합하지 않는다 (19).
16. **도구 인자는 `days` 또는 `since/until`만.** `tags` 인자 제거. 결과에 `today` 포함.
17. **대화 기록 절은 두 단계.** 대화 로그 PR(M8) 전에는 LLM 결론만, 로그 PR 후 접히는
    원문 추가. 설계는 "둘 다" 유지.
18. **대화 품질을 요약기보다 먼저 검증한다.** 손으로 쓴 인사이트 노트 + stub 도구로
    에뮬레이터 대화를 M2 전에 확인한다 (6장 M1).
19. **Chrome 프로필은 허용 목록으로 분리 관리하고, 대화 시 프로필을 고른다.** Chrome은
    프로필(사용자)마다 북마크를 따로 관리한다. 프로필을 합치면 회사 프로필이나 타인(친구가
    내 노트북에서 로그인) 프로필의 북마크가 LLM으로 나갈 수 있다. 따라서 **허용 목록에 있는
    프로필만, 서로 섞지 않고** 동기화하고(`bookmarks/<프로필 폴더명>/`,
    `insights/<프로필 폴더명>/`), 대화에서는 `profile` 인자로 어느 프로필 얘기를 할지
    고른다 (생략 시 기본 프로필). 프로필 간 URL 병합은 하지 않는다 (같은 프로필 안에서만
    병합). 허용 목록 기본값은 마지막 사용 프로필 1개.

## 부록 B. 범위 외 / 보류

- 봇이 먼저 인사이트를 제안하는 것 (자리 감지·기상 시점 연동 필요)
- 기능 2 라포 지표 정의, 기능 2+ 성격 진화
- CLI·브라우저 확장·단축어 저장 경로 (Chrome 북마크로 대체)
- Safari·Firefox 지원
- 벡터 검색(RAG). 북마크 수백 개 이하에선 태그·날짜·키워드로 충분
- 유튜브 자막·PDF 텍스트 추출 (2단계)
- 실기기 포팅 (에뮬레이터 검증 후)
- MCP 서버 (옵시디언·외부 클라이언트용) 구현함 (M9에서 구현, 선택 사양·기본 꺼짐)
- 깃 미러 구현함 (M9에서 구현, 선택 사양·기본 꺼짐)
- 백필·부분 인사이트 (M7, 데모 범위 밖)
- `list_bookmarks`의 `tags` 인자
- 프로필 통합 보기 (여러 프로필을 하나의 인사이트로)

## 부록 C. 검수 반영 기록 (2026-11-06)

20년차 풀스택 관점 서브에이전트 검수의 지적(F1~F14)과 결정. 마일스톤 번호는 6.3 기준이다.

| 지적 | 결정 | 반영 위치 |
| :--- | :--- | :--- |
| F1 앱 연동: 로컬 MCP 서버는 upstream 수정 없이 붙지 않음 | 같은 프로세스의 `tools/` Tool 4개 + `src/bookmark_insight/` 패키지 | 2.1, 2.2, 6장 |
| F2 원격 도구 결과 봉투·자동 재시도·타임아웃 30초 고정 | F1로 해소 (원격 도구 경로를 쓰지 않음) | 2.1 |
| F3 대화 중 동기 생성 | `generating` + `BackgroundToolManager` 백그라운드, 완료 시 모델 발화 | 2.2, 2.5, 7장 시나리오 1 |
| F4 자동 종료를 LLM에 맡김 | 앱 측 상태 기계(heartbeat, 슬립·종료 훅), `auto`는 LLM 인자 아님 | 3.3, 6장 M6, 7장 시나리오 5 |
| F5 대화 로그 구현 경로 | `set_transcript_observer` 로거 + fan-out 래퍼, 별도 PR | 2.6, M8 |
| F6 깃 미러 비용·복잡도 | v1 끔, M9 선택 사양 | 부록 A 4, 2.4, 2.3 |
| F7 Chrome 데이터: id 안정성, 시간대, 프로필, 중복 URL, 삭제·제목 변경 | `guid` 키, 로컬 날짜 + `date_added_utc`, `Local State` 프로필 탐색, 중복 URL 1파일, `archived`, 파일명 유지 | 2.4, 부록 A 14·15 |
| F8 요약기: 거대 응답, 사설 IP, 빈 본문, 재시도 | 가져오기 단계 2MB 컷·이진 즉시 실패, 사설 대역 기본 제외, 최소 본문 길이, 백오프 | 2.5, 2.3, 5장 |
| F9 노트 수명주기 미정의 | stale 재생성, 절 제목 시각, `complete: false`, 겹치는 기간 허용 | 2.5 |
| F10 KPI가 아직 없는 로그에 의존 | 로그 의존 항목을 M6 데모에서 제외, 환각은 `bookmark_ids` 대조 | 1.3, 5장 |
| F11 마일스톤 부재 | M0 계약·픽스처, M1 대화 품질 선검증, JSON 스키마, 병렬 트랙 | 6장 |
| F12 범위: 백필, `tags`, 대화 기록, 검증 순서 | 백필은 M7 유지(삭제 안 함), `tags` 제거, 대화 기록 두 단계, 대화 품질 선검증 | 부록 A 16~18, 부록 B |
| F13 Windows: 파일명, 쓰기 경합, 파일 잠금 | 파일명 규칙 함수, NFC 비교, 원자 쓰기, `PermissionError` 재시도 | 2.4, 5장 |
| F14 문서 내부 모순 (2.2 상태값 ↔ 4장 지연 ↔ 5장 완화) | 2.2 v1 상태값 세 개, 4장 `generating` 즉시 반환, 5장 완화 문구 정합 | 2.2, 4장, 5장 |
| 회의 추가 (프로필) | Chrome 다중 프로필: 허용 목록·폴더 분리·대화 시 선택 | 2.2, 2.3, 2.4, 2.5, 3.2, 5장, 6장, 7장 시나리오 6, 부록 A 19 |
