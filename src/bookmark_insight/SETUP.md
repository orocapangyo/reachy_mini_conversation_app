# Bookmark Insight 첫 세팅

Bookmark Insight는 Chrome 북마크를 마크다운 저장소(옵시디언 볼트)로 옮겨 두고, 로봇과 "이번 주 북마크 얘기하자" 같은
대화를 나누는 기능입니다. 동기화된 북마크는 LLM이 요약하고, 한 주 단위로 묶음·상관관계·질문이 담긴 인사이트 노트가
저장소 안에 생깁니다. 이 문서는 그 첫 세팅을 설정 웹 UI의 버튼만으로 끝내는 순서와, 같은 일을 터미널로 하는 방법을
설명합니다. 전체 앱 설치와 실행은 루트 [README](../../README.md#빠른-시작-한국어-데모)를 먼저 따라 하세요.

## 준비물

- `.env`에 백엔드와 로봇 연결이 적혀 있어야 합니다. 시뮬레이터에서 Gemini로 돌리는 최소 구성은 세 줄입니다
  (코드 기본값은 `hf`, `.env.example` 기본값은 `openai`라서 백엔드를 꼭 적어야 합니다):

  ```env
  CONVERSATION_BACKEND=gemini
  GEMINI_API_KEY=AIza...
  REACHY_MINI_HOST=sim
  ```

- 데몬이 먼저 떠 있어야 합니다. 앱은 데몬에 붙지 못하면 그대로 종료하므로 설정 웹 UI도 뜨지 않습니다.
  실물이 없으면 [시뮬레이터 데몬 띄우기](../../README.md#시뮬레이터-데몬-띄우기)를 참고하세요.
- 앱을 `--ui`로 띄웁니다: `reachy-mini-conversation-app --ui` → `http://127.0.0.1:7860/#/settings`.
  첫 시작은 이 주소가 응답하기까지 90초쯤 걸립니다.

## 설정 대시보드로 세팅하기

Settings 화면 아래쪽의 **Bookmark Insight** 섹션이 첫 세팅 전부를 맡습니다. 위의 상태 카드가 설정 상태·북마크
저장소·인사이트 폴더·Chrome User Data 경로·허용 프로필·프로필별 북마크 수·마지막 동기화 시각을 보여 주고, 그 아래
버튼들이 설정부터 동기화까지 대신해 줍니다. 모든 버튼은 `bookmark_insight` 패키지의 함수를 바로 부르며 셸 명령을 만들지
않습니다.

처음이라면 이 순서로 누릅니다.

1. **프로필 탐색** — 내 PC의 Chrome 프로필이 폴더명·표시 이름·북마크 파일별 개수와 함께 표로 뜨고, 마지막에 쓴
   프로필이 기본으로 체크됩니다. 다른 사람이나 회사 프로필은 체크하지 마세요. Chrome 폴더를 자동으로 못 찾으면
   "Chrome User Data 경로 직접 지정"을 펼쳐 `chrome://version`의 "프로필 경로"에서 마지막 폴더를 뺀 상위 폴더를 적고
   다시 탐색합니다.
2. **북마크 저장소 폴더** 입력란을 확인하고 **폴더 만들기·적용** — 기본값 `~/Documents/bookmarks`가 절대 경로로
   채워져 있고, 입력하는 동안 폴더가 있는지·쓸 수 있는지·이미 든 북마크 수가 한 줄로 갱신됩니다. 버튼을 누르면 없는
   폴더를 하위까지 만들고 `.env`에 저장합니다. 상대 경로, Chrome User Data 안쪽, 홈 폴더 자체, 드라이브 루트는
   거부합니다. 폴더 위치는 아래 [저장소는 옵시디언 볼트 안에](#저장소는-옵시디언-볼트-안에) 참고.
3. **.env에 적용** — 체크한 프로필(직접 적었다면 Chrome 경로도)을 저장합니다. 상태 카드가 "설정됨"으로 바뀌고
   재시작은 필요 없습니다.
4. **동기화 실행** — 버튼이 잠기고 스피너가 돌다가 프로필별 created/updated/archived/excluded 표와 경고가 나옵니다.

이제 로봇에게 "이번 주 북마크 얘기하자"라고 말해 보세요.

### 어느 `.env`에 쓰이나

이 화면이 쓰는 값은 세 가지뿐입니다: 저장소 경로 `BOOKMARK_INSIGHT_BOOKMARK_REPO`, 읽을 프로필 목록 `BOOKMARK_INSIGHT_CHROME_PROFILES`,
그리고 Chrome 폴더를 직접 적었을 때만 `BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR`. 쓰는 파일은 앱을 어떻게 띄웠는지에 따라 다릅니다.

- 터미널(`reachy-mini-conversation-app --ui`)에서 띄웠다면 앱이 읽어 들인 그 `.env`(현재 폴더에서부터 찾은 파일,
  없으면 현재 폴더에 새로 만듭니다).
- Reachy 대시보드에서 띄웠다면 앱 인스턴스 폴더의 `.env`.

값은 저장과 동시에 실행 중인 앱에도 반영되므로 재시작하지 않아도 됩니다.

## 설정 전에 말을 걸면

세팅을 안 한 채로 "이번 주 북마크 얘기하자"라고 하면 로봇이 북마크 내용을 지어내지 않고, 브라우저에서
`127.0.0.1:7860` 설정 페이지를 열어 Bookmark Insight 칸의 버튼 네 개(프로필 탐색, 폴더 만들기·적용, .env에 적용,
동기화 실행)를 차례로 누르라고 두 문장 안에 안내합니다. 재시작이 필요 없으니 끝나면 "설정했어"라고만 말하면 바로
이어집니다 (정확한 동작은 `src/bookmark_insight/api.py`의 `NOT_CONFIGURED_NEXT_ACTION`).

## 터미널로 하기

같은 일을 터미널에서 하려면 (venv 활성화, 프로젝트 루트):

| 할 일 | 명령 |
|------|------|
| Chrome 프로필 탐색 + `.env` 줄 출력 | `python -m bookmark_insight.sync --discover` |
| 동기화 | `python -m bookmark_insight.sync` (`--profile NAME`, `--repo PATH`, `--chrome-dir PATH`, `-v`) |
| 깃 미러 이력 재구성 | `python -m bookmark_insight.sync --git-rebuild` |
| 데모 데이터 준비 | `python scripts/bookmark_insight_demo_setup.py ~/bookmark-insight-demo` |

`--discover`가 출력한 `.env` 줄은 직접 복사해 넣어야 합니다 (터미널 명령은 `.env`를 쓰지 않습니다).

## 실제 Chrome 없이 시연하기 (데모 데이터)

시뮬레이터로 시연할 때는 같은 섹션의 **데모 데이터 준비**를 누릅니다. 대상 폴더(기본 `~/bookmark-insight-demo`)에 요약된
북마크 3개와 ready 인사이트 노트 1개(2026-10-31 ~ 11-06), 가짜 `User Data`가 복사되고, 돌려받은 값은 **이 값을 .env에
적용** 버튼 한 번으로 반영됩니다. 이미 있는 폴더는 덮어쓰지 않고 거부하며, 픽스처(`tests/bookmark_insight/fixtures`)는 저장소
체크아웃에만 있습니다. 그다음 Personalities 화면에서 `deskmate_ko`를 기본 페르소나로 지정하고 "10월 31일부터 11월
6일까지 북마크 얘기하자"라고 말하면 네트워크나 LLM 키 없이 바로 대화가 됩니다.

## 나머지 설정값

대시보드가 건드리지 않는 값은 `.env.example` 7절 주석을 보고 직접 적습니다. 대표적인 것:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BOOKMARK_INSIGHT_DAILY_MAX_SUMMARIES` | `100` | 요약 배치의 하루 상한. 넘치면 다음 날 이어서 백필 (무료 등급 한도 보호) |
| `BOOKMARK_INSIGHT_CALL_INTERVAL_S` | `6` | 요약 호출 간격(초, 분당 요청 한도 보호) |
| `BOOKMARK_INSIGHT_GIT_MIRROR` | `0` | `1`이면 동기화가 만든·바꾼·보관한 북마크 파일을 북마크 날짜로 커밋 (git 설치 필요). 동기화 결과에 프로필별 커밋 수가 같이 나옵니다 |
| `BOOKMARK_INSIGHT_BATCH_MODEL` | `gemini-3.5-flash` | 요약·상관관계 배치용 텍스트 모델. `GEMINI_API_KEY`를 같이 씁니다 |

## 저장소는 옵시디언 볼트 안에

인사이트 노트 본문의 북마크 줄은 어디서나 열리는 **웹 링크**입니다 (`- [제목](https://…)`). 옵시디언이 없어도,
볼트 밖에 두어도 노트는 일반 md 파일로 그대로 읽힙니다.

저장소(정확히는 노트 폴더: `BOOKMARK_INSIGHT_INSIGHTS_DIR`을 지정했으면 그 폴더, 아니면 `BOOKMARK_INSIGHT_BOOKMARK_REPO`)가
**옵시디언 볼트 안**(상위에 `.obsidian/` 폴더가 있는 곳)에 있으면 노트 프런트매터에 `related:` 목록으로 북마크 파일
`"[[파일명]]"` 링크를 함께 적습니다. 옵시디언은 속성(properties)의 `[[링크]]`를 그래프·백링크 간선으로 다루므로
노트와 북마크가 그래프에서 이어집니다. 볼트 밖이면 옵시디언 전용 내용은 아무것도 쓰지 않습니다.
자동 판정을 바꾸려면 `BOOKMARK_INSIGHT_NOTE_LINKS=on|off` (기본 `auto`).

저장소 폴더 입력란은 옵시디언이 설치돼 있으면 전역 설정(`obsidian.json`)에서 볼트를 찾아 `<볼트>/bookmarks`를
미리 채우고, 검증 줄에 "옵시디언 볼트 안/밖"을 표시한다. 옵시디언을 비표준 위치에 설치했으면
`BOOKMARK_INSIGHT_OBSIDIAN_CONFIG=<obsidian.json 경로>`로 지정한다.

북마크 파일명은 `[[링크]]`가 깨지지 않도록 `[ ] # ^ |`도 제목에서 뺍니다 (Windows 금지 문자와 함께). 이 규칙 전에
만들어진 파일은 이름을 그대로 둡니다 (동기화는 guid로 파일을 찾으므로 동작에 영향 없음).

> [!WARNING]
> 설정 UI는 `0.0.0.0:7860`에 열려 같은 네트워크의 누구나 접근할 수 있고, 이 섹션의 버튼은 `.env`에 씁니다
> (기존 Connection 설정과 같은 경계입니다). 집 Wi-Fi 밖, 특히 공용 네트워크에서는 7860 포트를 열어 두지 마세요.
