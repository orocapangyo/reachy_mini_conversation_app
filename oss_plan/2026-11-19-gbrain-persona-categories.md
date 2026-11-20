# GBrain 페르소나·기억 카테고리 — 설계 및 구현 기록

- 날짜: 2026-11-19
- 브랜치: `feat/simulator-dashboard`
- 관련 문서: [`2026-11-17-simulator-dashboard-design.md`](2026-11-17-simulator-dashboard-design.md), 사용 가이드는 `src/jisu/dashboard/README.md`의 "기억 카테고리와 페르소나"

## 1. 배경과 우선순위

이번 주 목표는 **GBrain 기반 장기 기억 테스트 + 페르소나 적용**이다. 후보 작업 네 가지의 순서를 이렇게 정했다.

| 순서 | 작업 | 이유 |
|---|---|---|
| ① (이번 작업) | GBrain 기억 카테고리화 + 페르소나를 GBrain에서 관리 | 주간 목표이자 '지켜보는 DeskMate'의 저장 계층 |
| ② | scenario_kit 기반 기억 벤치마크 + 대시보드 결과 화면 | GBrain 벤치마킹과 테스트 도구 설명을 한 번에 처리 |
| ③ | 지켜보는 DeskMate 작은 시제품 (웹캠 관찰 → `user_behavior` 기록 → 반응) | ①의 카테고리를 그대로 사용 |
| ④ | 앱 연동 | ③에서 생기는 사건·약속이 있어야 무엇을 보낼지 정해진다 |

## 2. 결정: 층 분리

| 층 | 위치 | 내용 |
|---|---|---|
| 고정 정체성 | `profiles/<이름>/profile.md` (수정하지 않음) | 이름·정체성, 응답 규칙, 도구 목록, 목소리, 인사 |
| 성장하는 페르소나·기억 | GBrain | 대화하며 쌓이는 성격·말투, 약속, 사용자 취향·행동 |

완전 이전(지시문까지 GBrain으로 옮기기)은 택하지 않았다. 이유는 세 가지다.
- 도구 목록·목소리·인사는 앱이 시작할 때 읽는 설정이라 검색형 기억과 맞지 않는다.
- 프로필을 읽는 upstream 경로를 여러 곳 바꿔 끼워야 한다.
- 한국어 키워드 검색이 약해서 핵심 규칙이 프롬프트에서 빠질 위험이 있다.

## 3. 카테고리 = GBrain `entity` + `kind`

GBrain `remember`가 `entity`와 `kind`를 받고, `recall`은 응답에 `entity_slug`·`kind`를 돌려준다.
실제 gbrain 0.60으로 확인했고, entity 페이지가 없어도 entity별로 걸러진다. 그래서 카테고리를 따로 저장하지 않는다.

| 카테고리 | entity | kind | 그룹(칩) |
|---|---|---|---|
| `persona_trait` | `reachy-mini` | `belief` | 페르소나 |
| `persona_style` | `reachy-mini` | `preference` | 페르소나 |
| `promise` | `reachy-mini` | `commitment` | 약속 |
| `user_preference` | `user` | `preference` | 사용자 |
| `user_behavior` | `user` | `event` | 사용자 |
| `user_fact` (기본) | `user` | `fact` | 사용자 |

- 기존 `remember` 도구는 `user_fact`로 저장한다.
- entity 없이 저장된 예전 기억은 `user_fact`로 읽힌다.
- `reachy-mini` 쪽에서 kind가 맞지 않는 사실은 `persona_trait`로 분류한다.

## 4. 구현 내용

모든 새 소스는 `src/jisu/` 아래에 둔다. `src/reachy_mini_conversation_app/**`와 `profiles/**`는 수정하지 않았다.

```
src/jisu/
├── persona/                 # 신규 (대시보드에 의존하지 않음)
│   ├── constants.py         # entity slug, 섹션별 상한, 도구 저장 상한 등 튜닝 상수
│   ├── categories.py        # 카테고리 표, category_for(entity, kind), CategorizedFact
│   ├── prompt.py            # 섹션별 기억 프롬프트
│   ├── store.py             # CategorizedMemory 프로토콜
│   ├── seed.py              # 시드 읽기/적용 (saved / existing / skipped)
│   └── seeds/desk_companion_ko.toml
├── tools/                   # 신규: 앱의 외부 도구 폴더
│   └── update_persona.py
└── dashboard/               # 수정
    ├── memory/gbrain_backend.py  # add/list/search_categorized, forget_by_id, 섹션별 프롬프트
    ├── memory/hook.py            # active_backend(): 도구가 앱과 같은 백엔드를 쓰도록 연결
    ├── launcher.py               # 외부 도구 env 주입, 첫 GBrain 초기화 때 시드 적용
    ├── server.py                 # 카테고리 API
    └── static/*                  # 기억 패널 UI
```

### 4.1 프롬프트
`prompts.get_session_instructions()`가 이미 `기억 블록 + profile 지시문`을 합치고, 기억 블록 함수는 jisu 훅이 교체하고 있다.
그래서 GBrain 백엔드의 `format_for_prompt()`만 섹션별로 바꿨다. 순서는 `[나(Reachy)…] → [약속] → [사용자…]`이고, 빈 섹션은 넣지 않는다.
머리말에 "profile 지시문과 부딪히면 지시문을 따른다"를 넣었다.

### 4.2 `update_persona` 도구
- 앱의 외부 도구 로더를 쓴다. 런처가 `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY=src/jisu/tools`와 `AUTOLOAD_EXTERNAL_TOOLS=1`을 넣어 주고, 실제 로더로 등록되는 것을 확인했다.
- 저장할 수 있는 카테고리는 `persona_trait`, `persona_style`, `promise`뿐이다. 실패하면 `{"error": ...}`를 돌려준다.
- 프로세스당 저장 상한은 10개다. 이미 아는 사실(`already_known`)은 상한에 세지 않는다.
- json 백엔드에서는 저장하지 않고 gbrain으로 바꾸라는 오류를 돌려준다.

### 4.3 시드
- `seeds/<프로필>.toml`에 둔다(`schema_version = 1`, `[[facts]] category/text`).
- GBrain을 처음 초기화할 때 자동으로 넣고, 대시보드 버튼으로 다시 넣을 수 있다.
- GBrain은 같은 문장에 `status: duplicate`로 답한다. 이것은 "이미 있음"으로 따로 센다.
- 사용자가 지운(철회된) 문장은 다시 들어가지 않는다.

### 4.4 대시보드 API / UI

| API | 용도 |
|---|---|
| `GET /api/memory/categories` | 카테고리·그룹 표 |
| `GET /api/memory?category=` | 카테고리별 개수(`counts`)와 `categorized` 여부 포함 |
| `POST /api/memory {category}` | 카테고리 지정 추가 (json은 기본값만 허용) |
| `POST /api/memory/forget {id}` | id로 삭제 (기존 `query` 방식도 유지) |
| `POST /api/memory/search` | 결과에 카테고리 포함 |
| `GET /api/memory/prompt-preview` | 실제로 들어갈 기억 프롬프트 |
| `POST /api/memory/persona/seed` | 프로필 시드 적용 |

UI는 기억 패널에 다음을 추가했다.
- 필터 칩(전체/페르소나/약속/사용자, 개수 표시)
- 카테고리별 그룹 헤더(Reachy 쪽은 accent 색, 사용자 쪽은 ok 색)
- 추가할 때 고르는 카테고리 선택
- '페르소나 시드 넣기', '프롬프트 미리보기'

json 백엔드에서는 칩·선택·시드를 숨기고 기존 화면을 그대로 보여준다.

## 5. 검증

- 게이트: `ruff check` / `ruff format` / `mypy`(strict) / `pytest tests/` → 660 passed.
- 신규·확장 테스트:
  - 신규: `tests/jisu/test_persona_{categories,prompt,seed}.py`, `test_update_persona_tool.py`
  - 확장: backends / hook / server / launcher 테스트
  - 공용 가짜 CLI: `tests/jisu/gbrain_fakes.py`
- 실제 gbrain(scratch home)에서 확인한 것:
  - entity/kind 왕복
  - 시드를 두 번째 넣으면 5개 모두 "이미 있음"
  - 섹션별 프롬프트
- 앱 tool loader에 `update_persona`가 등록되는 것을 확인했다.
- 별도 포트(7871)에 테스트용 대시보드를 띄워 브라우저에서 칩 필터·그룹 헤더·프롬프트 미리보기를 확인했다.

## 6. 남은 일

- 시뮬 실제 대화에서 LLM이 `update_persona`를 적절히 호출하는지 확인한다 (예: "앞으로 대답 짧게 해줘" → 생각 패널).
- `.env`에 `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`가 있으면 dotenv override로 런처 값이 덮인다. 팀 공지가 필요하다.
- 시드 `.toml`은 `pyproject` package-data에 없다. 편집 가능 설치에서만 동작한다.
- 다음 작업은 ② scenario_kit 기억 벤치마크다. 카테고리별 recall 정확도와 한국어 키워드 검색의 한계를 수치로 보여준다.
