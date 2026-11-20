# 기억 검색 벤치마크 — 설계와 첫 측정 결과

- 날짜: 2026-11-19
- 브랜치: `feat/simulator-dashboard`
- 코드: `src/jisu/bench/memory/` (사용법은 `src/jisu/bench/README.md`)
- 이전 작업: [`2026-11-19-gbrain-persona-categories.md`](2026-11-19-gbrain-persona-categories.md)

## 1. 목적과 범위

GBrain 기반 장기 기억이 한국어 데스크 대화에서 얼마나 잘 찾아지는지 수치로 보인다.

사용자 결정은 다음과 같다.
- **검색 벤치마크만** 만든다. LLM 없이 저장소를 직접 호출한다. scenario_kit 대화 시나리오는 확장하지 않는다.
- **CLI로만 실행**한다. 대시보드 화면은 구상이 끝나면 붙인다. 그래서 실행 API(`run_memory_bench` + 진행 이벤트)와 결과 JSON(`schema_version` 1)을 CLI와 분리했다.

## 2. 구성

- 측정 대상
  - `json`: upstream 키워드 검색
  - `gbrain`: 앱 경로. GBrain 순위가 비면 로컬 키워드 검색으로 넘어간다
  - `gbrain-native`: GBrain 순위만
- 대상마다 임시 폴더에 새 저장소를 만든다. 실제 사용자 기억은 건드리지 않는다.
- 데이터셋 `ko_desk_v1`: 사실 30개(카테고리 6종 × 5), 질의 41개(부정 질의 5개 포함). 태그로 약점을 나눠 본다.
- 지표: hit@1, hit@k, recall@k, MRR, 오탐률(FP), 지연 p50/p95, 카테고리 왕복률.
- GBrain에 대체 검색 없는 `search_native()`와 `version()`을 추가했다(`src/jisu/dashboard/memory/gbrain_backend.py`).
- 구현 중 발견: macOS 임시 폴더(`/var` → `/private/var` 심볼릭 링크)에서는 `gbrain init`이 `local_conflict`로 실패한다. 작업 폴더를 `resolve()`한 실제 경로로 넘겨 해결했다.

## 3. 첫 측정 결과

환경: macOS arm64, Python 3.12.13, gbrain 0.60.687.0 (PGLite, `--no-embedding`, search mode conservative). k=5, repeat=3, 전체 197초.

| 대상 | hit@1 | hit@5 | recall@5 | MRR | FP | 검색 p50 | 저장 p50 | 프롬프트 p50 | 초기화 |
|---|---|---|---|---|---|---|---|---|---|
| json | 80.6% | 80.6% | 80.6% | 0.806 | 20.0% | 0.06 ms | 0.24 ms | 0.06 ms | - |
| gbrain | 77.8% | 80.6% | 80.6% | 0.785 | 20.0% | 870 ms | 675 ms | 463 ms | 2.3 s |
| gbrain-native | 30.6% | 33.3% | 33.3% | 0.312 | 0.0% | 438 ms | 670 ms | 455 ms | 2.3 s |

태그별 hit@5 (negative는 오탐률):

| 태그 | n | json | gbrain | gbrain-native |
|---|---|---|---|---|
| exact | 10 | 100% | 100% | 60% |
| multi_word | 12 | 100% | 100% | 83.3% |
| short_word | 10 | 100% | 100% | **0%** |
| persona | 7 | 100% | 100% | 14.3% |
| promise | 5 | 100% | 100% | 40% |
| paraphrase | 8 | 12.5% | 12.5% | 0% |
| english | 2 | 50% | 50% | 50% |
| negative | 5 | FP 20% | FP 20% | FP 0% |

카테고리 왕복: gbrain 100%, gbrain-native 100%.

## 4. 해석

1. **GBrain 자체 검색은 한국어에 약하다 (hit@5 33.3%).**
   - 2글자 이하 질의는 전부 실패했다. `facts_degraded: no_query_terms`, 3글자 미만 단어를 버리기 때문이다.
   - 3글자 이상이어도 실패한다. 단어 전체 일치 방식이라 조사가 붙은 어절("마포구**에**", "안테나**를**", "해요체**로**")과는 맞지 않는다. 그래서 persona 14%, exact 60%다.
2. **앱 경로(`gbrain`)는 대체 검색 덕분에 json과 같은 정확도(80.6%)다.** 다만 이 점수의 대부분은 GBrain이 아니라 로컬 부분 문자열 검색에서 나온다.
   지금 GBrain의 이점은 정확도가 아니라 카테고리·출처·철회 이력 같은 **구조화된 저장**이다.
3. **유의어(paraphrase)는 모든 대상이 0~12.5%다.** "직업"으로 "백엔드 개발자"를 찾거나, "낮잠"으로 "졸린다"를 찾지 못한다. 임베딩 없이는 해결되지 않는다.
4. **오탐 20%는 대체 검색의 부작용이다.** "고양이 이름"에서 "이름"만 맞아도 강아지 사실이 나온다. 키워드 점수에 하한이 없기 때문이다.
5. **지연:** GBrain 호출 하나가 subprocess(Bun 기동)라 검색 0.4~0.9초, 저장 0.7초가 든다.
   - 대화 중 `remember`/`update_persona` 호출마다 이만큼 늦어진다.
   - 세션 프롬프트 생성도 0.46초 걸린다. 세션을 시작할 때 1회다.

## 5. 다음 개선 후보 (벤치마크로 효과 측정)

| 후보 | 기대 효과 | 확인 지표 |
|---|---|---|
| 임베딩 활성화 (로컬 Ollama 등) | 유의어·영어 질의 개선 | paraphrase, english hit@5 |
| 질의/사실의 한국어 조사 분리 (또는 형태소 분석) 후 GBrain에 전달 | native 정확도 회복 | gbrain-native exact·persona |
| 대체 검색 점수 하한 (단어 일치 비율) | 오탐 감소 | negative FP |
| GBrain 상주 프로세스 (`gbrain serve`/MCP) 연결 | subprocess 비용 제거 | 검색·저장 p50 |

다음 측정에서 같은 데이터셋(`ko_desk_v1`)과 같은 k로 비교한다. 데이터셋을 바꾸면 이름을 `ko_desk_v2`로 올린다.

## 6. 남은 일

- 대시보드 '벤치마크' 화면: 구상이 끝나면 `jisu.bench.memory` 공개 API와 결과 JSON으로 붙인다(`src/jisu/bench/README.md`의 연동 지점).
- CI에서는 json 대상과 가짜 GBrain CLI로 하네스만 검증한다(`tests/jisu/test_bench_*.py`). 실제 gbrain 측정은 로컬에서 실행한다.
