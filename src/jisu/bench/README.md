# jisu 벤치마크 (`jisu.bench`)

## 기억 검색 벤치마크 (`jisu.bench.memory`)

LLM 없이 기억 저장소를 직접 호출한다. 한국어 사실을 저장한 뒤 질의로 찾아서, **얼마나 잘 찾는지(정확도)**와 **얼마나 빠른지(지연)**를 잰다.

### 실행

```bash
python -m jisu.bench.memory                          # json, gbrain, gbrain-native · k=5 · repeat=3 · 결과 저장
python -m jisu.bench.memory --subjects json gbrain --k 3 --repeat 5
python -m jisu.bench.memory --detail                 # 실패한 질의까지 출력
python -m jisu.bench.memory --dataset my.toml --no-save
python -m jisu.bench.memory --list                   # 저장된 결과 목록
python -m jisu.bench.memory --show <파일>            # 저장된 결과를 다시 출력
```

- gbrain 대상은 `gbrain` 실행 파일이 필요하다 (`src/jisu/dashboard/README.md`의 GBrain 설정 참고). 없으면 그 대상만 `skipped`로 남고 나머지는 실행된다.
- 대상마다 임시 폴더에 새 저장소를 만들고, 끝나면 지운다. 사용자의 실제 기억(앱 데이터·`JISU_GBRAIN_HOME`)은 건드리지 않는다.
- gbrain 대상은 호출 하나가 subprocess라서 기본 설정 전체 실행에 약 3분이 걸린다. `--repeat 1`로 줄일 수 있다.
- 종료 코드: 0 = 정상, 2 = 데이터셋·설정 오류이거나 실행된 대상이 없음.

### 측정 대상

| 대상 | 저장 | 검색 |
|---|---|---|
| `json` | upstream `memory.v1.json` | 로컬 키워드 검색 |
| `gbrain` | GBrain (카테고리 = entity + kind) | 앱이 실제로 쓰는 경로: GBrain 순위 → 비면 로컬 키워드 |
| `gbrain-native` | GBrain | GBrain 자체 순위만 (대체 검색 없음) |

`gbrain`과 `gbrain-native`의 차이가 곧 대체 검색이 보탠 몫이다. 새 백엔드는 `subjects.py`에 `Subject`를 구현하고 `SUBJECT_BUILDERS`에 등록하면 된다.

### 지표

| 지표 | 정의 (정답 사실 E, 반환 상위 k개 R) |
|---|---|
| hit@1 | R의 첫 결과가 E에 있음 |
| hit@k | R 중 하나라도 E에 있음 |
| recall@k | \|R ∩ E\| / \|E\| |
| MRR | 첫 정답 순위의 역수 평균 (없으면 0) |
| FP | 부정 질의(E가 빈 질의)에 결과가 하나라도 나온 비율 |
| 지연 | 저장 1건 / 검색 1건 / 세션 프롬프트 생성 1회의 p50·p95 (ms), gbrain 초기화 시간 |
| 카테고리 왕복 | 저장한 카테고리가 그대로 읽혀 나온 비율 (카테고리 저장소만) |

정확도는 첫 검색으로 계산한다(결정적). `--repeat`는 지연 표본만 늘린다.

### 데이터셋 작성

`src/jisu/bench/memory/datasets/<이름>.toml` (기본: `ko_desk_v1.toml`, 사실 30개 · 질의 41개)

```toml
schema_version = 1
title = "설명"

[[facts]]
id = "dog_name"
category = "user_fact"   # jisu.persona 카테고리 키
text = "사용자의 강아지 이름은 모찌다"

[[queries]]
id = "dog_multi"
text = "강아지 이름"
expected = ["dog_name"]  # [] = 부정 질의
tags = ["multi_word"]
```

- 태그: `exact`, `short_word`, `multi_word`, `paraphrase`, `english`, `persona`, `promise`, `negative`. 태그는 자유롭게 추가할 수 있고, 태그별 표에 자동으로 나온다.
- 같은 문장(공백·대소문자 무시)의 사실을 두 번 넣을 수 없다. 결과를 텍스트로 사실 id에 되돌려 매칭하기 때문이다.

### 대시보드 연동 지점 (예정)

CLI는 아래 공개 API의 얇은 껍데기다. 대시보드도 이 API만 쓰면 된다.

```python
from jisu.bench.memory import BenchConfig, run_memory_bench, save_report, list_reports, load_report

report = run_memory_bench(BenchConfig(subjects=("json", "gbrain")), on_event=handle)  # 동기, 스레드에서 실행
path = save_report(report)          # 기본 폴더: <앱 데이터>/jisu-bench/memory (JISU_BENCH_RESULTS_DIR로 변경)
summaries = list_reports()          # 최신순: started_at, dataset, k, 대상별 hit@k, skipped
```

- `on_event`는 `BenchEvent(kind, subject, done, total, message)`를 받는다.
  - `kind`: `subject_start` → `subject_ready` | `subject_skipped` → `fact_added`×N → `query_done`×M → `subject_done`
  - 진행 막대는 `done/total`로 그린다.
- 결과 JSON 구조는 `report.py` 모듈 docstring에 있다 (`schema_version` 1). 질의별 상세에는 기대·반환·순위·메모(예: `degraded: no_query_terms`)가 있어서, 실패 질의 화면을 바로 만들 수 있다.
