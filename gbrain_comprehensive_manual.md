# 🧠 gbrain & Gemini AI 에이전트 지식 그래프 종합 매뉴얼

본 문서는 Y Combinator CEO 개리 탄(Garry Tan)의 **`gbrain`** 아키텍처, 로컬 벡터 임베딩, Google Gemini AI 에이전트 연동, 그리고 지식 그래프(온톨로지) 구축 워크플로우에 대한 완벽 가이드입니다.

---

## 📌 목차
1. [핵심 개념 및 설계 철학](#1-핵심-개념-및-설계-철학)
2. [아키텍처 및 내부 동작 원리](#2-아키텍처-및-내부-동작-원리)
3. [지식 링크와 온톨로지 (수동 vs 자동 vs 드림 사이클)](#3-지식-링크와-온톨로지-수동-vs-자동-vs-드림-사이클)
4. [🛠️ 독립 카테고리: 전체 실행 방법 및 명령어 모음](#4-️-독립-카테고리-전체-실행-방법-및-명령어-모음)
5. [환경 설정 및 API 키 관리 체계](#5-환경-설정-및-api-키-관리-체계)
6. [Reachy Mini 음성 로봇 연계 및 벤치마크 실측 비교](#6-reachy-mini-음성-로봇-연계-및-벤치마크-실측-비교)

---

## 1. 핵심 개념 및 설계 철학

### 1.1 gbrain이란?
YC CEO 개리 탄이 개발한 **마크다운 기반 개인 지식 베이스(PKM) 및 에이전트 장기 기억 엔진**입니다.
* **마크다운 진실의 원천 (Markdown Single Source of Truth)**: 모든 지식의 원본은 인간이 읽을 수 있는 마크다운 파일로 보관합니다.
* **하이브리드 검색 (Hybrid RRF)**: 키워드 검색(BM25/Full-Text)과 벡터 시맨틱 검색(Dense Vector Cosine)을 결합하여 오타나 단어 불일치에도 정확하게 기억을 찾아냅니다.
* **지식 그래프 (Knowledge Graph)**: 단순 텍스트 나열을 넘어 문서와 문서 사이의 인과관계(`relieves`, `implements`, `extends`)를 연결합니다.

### 1.2 비파괴적 '샌드위치 구조' 🥪
`gbrain export` 시 사용자가 작성한 **원본 파일은 절대로 삭제되거나 강제로 병합(Merge)되지 않습니다.** 각 파일마다 상단과 하단에 메타데이터만 덧붙여집니다:
```markdown
--- [1. 상단: 프론트매터 (Frontmatter)] ---
---
type: note
title: 러닝 일지
---

--- [2. 중간: 사용자가 작성한 원본 본문 (100% 보존)] ---
# 러닝 일지
- 2026-11-20: 가벼운 조깅 5km.
- 교차 훈련: [[cycling]]

--- [3. 하단: 지식 그래프 관계 테이블 (Links Table)] ---
## Links
<!--- gbrain:links:begin -->
| to | type | source | context |
|---|---|---|---|
| cycling |  | gemini-agent | 사이클링은 러닝의 교차 훈련으로 활용되어... |
<!--- gbrain:links:end -->
```

### 1.3 `memory/unattributed.md`의 정체
* **미분류 포스트잇 보관함**: 아직 특정 마크다운 페이지에 귀속(Attribute)되지 않은 단편적인 팩트(Fact)들을 잃어버리지 않도록 gbrain이 임시로 모아두는 파일입니다.

---

## 2. 아키텍처 및 내부 동작 원리

### 2.1 런타임 및 데이터베이스 구조
* **런타임**: 초고속 자바스크립트 엔진 **Bun v1.4.4**
* **데이터베이스**: 로컬 임베디드 PostgreSQL 엔진인 **PGLite** (`~/.gbrain/brain.pglite`)
* **HNSW 인덱싱**: 고차원 벡터의 근사 최근접 이웃(Approximate Nearest Neighbor) 검색을 로컬 DB 내에서 고속 처리.

### 2.2 벡터 임베딩 모델과 Re-embedding 원리
* **차원 및 의미 공간의 불일치**:
  - OpenAI: 1,536차원 / Ollama(`all-minilm`): 384차원
  - 서로 다른 모델은 좌표 체계(Latent Space)가 다르므로, 임베딩 모델을 변경하면 **반드시 기존 DB의 모든 벡터를 새로운 모델로 다시 계산(Re-embed)하여 HNSW 인덱스를 재구축**해야 합니다.
* **로컬 오프라인 임베딩 채택**:
  - `ollama:all-minilm` (384차원, 45MB)을 적용하여 **OpenAI API 키 없이 100% 무료 & 오프라인**으로 시맨틱 벡터 검색을 완벽 구동.

### 2.3 문서 포맷과 파서(Parser)의 필요성
* `gbrain`과 벡터 DB의 최종 입력 데이터는 반드시 **텍스트(Markdown)**여야 합니다.
* PPT, PDF, 이미지 등 복합 바이너리 파일은 **중간에 파서(Docling, python-pptx, Gemini Flash Vision)를 두어 마크다운으로 변환한 뒤 수집(Ingest)**하는 것이 정석입니다.

---

## 3. 지식 링크와 온톨로지 (수동 vs 자동 vs 드림 사이클)

### 3.1 3가지 링크 방식의 차이점
1. **단순 옵시디언 방식 (`[[위키링크]]`)**:
   - 본문에 `[[문서명]]`만 표기. 파일 하단에 `## Links` 표는 생기지 않으며, 옵시디언 앱 화면(UI)에서 가상 그래프로만 시각화.
2. **gbrain 수동 링크 (`gbrain link`)**:
   - 사람이 명령어로 명시적 엣지를 DB에 등록. `export` 시 파일 하단에 `## Links` 표가 영구 기록됨.
3. **AI 에이전트 / 드림 사이클 (Gemini 2.5 Flash)**:
   - AI가 메모들을 읽고 **단어 불일치 극복, Typed Link(관계명), 인과관계 Context**를 스스로 추론하여 등록.

### 3.2 AI 에이전트 링크가 수동 키워드 링크보다 우수한 이유
* **의미적 연결**: "자전거 도로 라이딩"과 "무릎 연골 마모 완화 운동"처럼 겹치는 단어가 없어도 같은 맥락임을 이해하여 연결.
* **화살표의 이유(Context) 명시**: 단순 연결선이 아니라 *"체중 부하가 적어 무릎 통증 완화에 권장됨"*이라는 이유를 표에 기록.
* **망각 극복**: 수백 개의 메모 중 6개월 전 메모와 오늘 메모를 0.1초 만에 매칭.

---

## 4. 🛠️ [독립 카테고리] 전체 실행 방법 및 명령어 모음

터미널 작업 위치: `C:\work\reachy_mini_conversation_app-gemini`

### A. 시스템 점검 및 설정 확인
```powershell
# 1. 지원 가능한 AI Provider 목록 및 상태 점검
.\gbrain.cmd providers list

# 2. 시스템 무결성 및 건강 점수 확인 (100점 만점 기준)
.\gbrain.cmd doctor --tier ops

# 3. 현재 활성화된 임베딩 모델 확인
.\gbrain.cmd config get embedding_model

# 4. 전체 gbrain 설정 확인
.\gbrain.cmd config show
```

### B. 단편 기억 관리 (`remember` / `recall`)
```powershell
# 1. 새로운 사실 기억시키기 (주제 엔티티와 출처 명시)
.\gbrain.cmd remember "주말에는 건강을 위해 탄천 자전거길에서 라이딩을 합니다." --entity user_hong --provenance "reachy_conversation"

# 2. 자연어 시맨틱 회상하기 (유사 단어 매칭)
.\gbrain.cmd recall user_hong --query "주말 취미 운동"
```

### C. 마크다운 폴더 일괄 관리 (`import` / `embed` / `export`)
```powershell
# 1. 로컬 마크다운 메모 폴더 통째로 불러오기
.\gbrain.cmd import C:\work\test_notes

# 2. 새로 불러온 문서들을 로컬 모델(all-minilm)로 즉시 벡터화
.\gbrain.cmd embed --stale

# 3. DB에 연결된 최신 지식들을 마크다운 볼트로 추출 (Frontmatter + Links 표 자동 생성)
.\gbrain.cmd export --dir C:\work\exported_notes
```

### D. 지식 검색 및 질의응답 (`query`)
```powershell
# 자연어로 하이브리드(시맨틱 벡터 + 키워드) 검색
.\gbrain.cmd query "비 오는 날 무릎에 좋은 운동"
```

### E. 지식 그래프 및 관계망 탐색 (`link` / `backlinks` / `graph`)
```powershell
# 1. 두 문서 간 공식 관계 엣지 등록 (이유 및 출처 명시)
.\gbrain.cmd link cycling health-knee --context "무릎 부담 없는 유산소 운동" --link-source "manual"

# 2. 특정 문서를 참조하고 있는 역링크(Backlinks) 목록 확인
.\gbrain.cmd backlinks health-knee

# 3. 특정 문서의 연결 상태 확인 (links: [] 이면 고립된 메모)
.\gbrain.cmd graph cycling

# 4. 볼트 전체의 링크 출처 통계 확인
.\gbrain.cmd link-sources
```

### F. 백그라운드 자동화 데몬
```powershell
# 1. 실시간 폴더 감시 (새 메모 저장 시 60초마다 자동 벡터화)
.\gbrain.cmd sync --watch --interval 60

# 2. 자가 유지보수 오토파일럿 데몬 (깨진 링크 복구 및 캐시 정리)
.\gbrain.cmd autopilot --interval 3600
```

### G. 🚀 Gemini 에이전트 기반 원스톱 지식 링커 실행
우리가 구축한 파이썬 스크립트([gemini_notes_linker.py](file:///c:/work/gemini_notes_linker.py))를 통해 **[메모 수집 ➔ Gemini 온톨로지 추론 ➔ gbrain link 등록 ➔ 최종 마크다운 볼트 Export]**를 명령어 한 줄로 완벽히 끝냅니다:

```powershell
C:\work\reachy_mini_project\.venv\Scripts\python.exe C:\work\gemini_notes_linker.py
```
* **결과 확인 위치**: [C:\work\gemini_linked_vault](file:///c:/work/gemini_linked_vault)

### H. ⚡ gbrain serve 상주 서버 모드 (부팅 랙 제거 & 2ms 초고속 통신)
매번 프로세스를 켜고 끄는(1.5초 소모) 대신, 백그라운드에 gbrain을 계속 살려두어 **2.2ms 수준으로 초고속 통신**하는 상주 서버 모드입니다.

#### 1) HTTP 웹 서버 모드 (파이썬 / 리치미니 / 웹 대시보드) ⭐
```powershell
# 3131 포트로 상시 HTTP 서버 및 관리자 웹 UI 구동
.\gbrain.cmd serve --http --port 3131 --bind 127.0.0.1
```
* **웹 대시보드 (GUI)**: 브라우저에서 `http://127.0.0.1:3131/admin` 접속 시 기억과 설정을 눈으로 보며 관리 가능.
* **헬스 체크 엔드포인트**: `http://127.0.0.1:3131/health`
* **파이썬 고속 호출 예시 (2.2ms 실측)**:
  ```python
  import requests
  # 이미 서버가 켜져 있으므로 Cold Start 없이 즉시 응답 (약 2ms)
  res = requests.get("http://127.0.0.1:3131/health")
  print(res.json())
  ```

#### 2) MCP 서버 모드 (Cursor / Claude Desktop 연동)
Cursor나 Claude의 설정 파일(`claude_desktop_config.json` 등)에 아래와 같이 등록해 두면, 에디터 시작 시 gbrain이 백그라운드에서 상시 구동되어 AI가 지식을 실시간으로 호출합니다:
```json
{
  "mcpServers": {
    "gbrain": {
      "command": "C:\\Users\\ldhco\\.bun\\bin\\bun.exe",
      "args": [
        "run",
        "C:\\Users\\ldhco\\.bun\\install\\global\\node_modules\\gbrain\\src\\cli.ts",
        "serve"
      ]
    }
  }
}
```

---

## 5. 환경 설정 및 API 키 관리 체계

### 5.1 설정 파일 위치 요약
| 구분 | 파일 경로 | 주요 역할 |
| :--- | :--- | :--- |
| **메인 프로젝트 환경설정** | `C:\work\reachy_mini_project\.env` | Gemini 2.5 Flash / Live API 키, 대시보드 포트, SQLite 경로 |
| **대화 앱 전용 환경설정** | `C:\work\reachy_mini_conversation_app-gemini\.env` | Reachy Mini 음성 대화 설정, Gemini Live Voice |
| **gbrain 전용 전역설정** | `C:\Users\ldhco\.gbrain\config.json` | PGLite 경로, 로컬 임베딩(`ollama:all-minilm`), 384차원 |

### 5.2 gbrain의 키 로딩 우선순위 (Hierarchy)
1. **실행 중인 폴더의 `.env`**: Bun 런타임의 Auto-load 기능에 의해 최우선 탐색.
2. **OS 시스템 환경변수**: Windows 제어판 시스템 환경변수.
3. **전역 설정 파일 (`~/.gbrain/config.json`)**: `.env`가 없을 때 참조하는 전역 기본값.
   - CLI로 전역 키 등록: `gbrain config set google_api_key "API키"`

---

## 6. Reachy Mini 음성 로봇 연계 및 벤치마크 실측 비교

실제 Reachy Mini 대화형 앱 환경에서 나란히 구동하여 측정한 실측 벤치마크 데이터입니다:

```
======================================================================
📊 DUAL MEMORY BENCHMARK COMPARISON MATRIX
======================================================================
Metric / Feature               | Track 1 (Native Python Vault) | Track 2 (Bun gbrain)
----------------------------------------------------------------------
Read Latency (Thinking Speed)  | 0.01 ms                       | 2,143.5 ms
Speed Advantage                | 222,630x Faster ⚡             | Baseline
Write / Ingest Latency         | 1.49 ms                       | 1,412.5 ms
Storage Format                 | Human-readable .md            | PGLite (Postgres)
External Runtimes              | None (Pure Python)            | Bun v1.4.4
Contradiction Resolution       | Built-in Dream Cycle          | Requires Autopilot
Multi-Tool Sharing             | Reachy Only                   | Full MCP Support
======================================================================
```

### 실전 아키텍처 권장안
* **음성 대화 중 (낮)**: 0.01ms 만에 읽어오는 **Track 1 (Native Markdown Vault)**를 사용하여 지연 시간(Lag) 제로로 대화.
* **수면 중 (밤 - 드림 사이클)**: 로봇이 잠드는 순간(`go_to_sleep`) **Gemini 에이전트**를 구동하여 낮 대화와 메모의 온톨로지 에지를 정리하고 `gbrain`과 마크다운 볼트에 최신 상태로 영구 보관.
