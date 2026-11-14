# 웨이크 워드 모델 — reachy_mini_v4

"리치미니" 음향 감지용 openWakeWord 커스텀 모델. `wake_engines.OpenWakeWordEngine`이
기본으로 로드한다 (`REACHY_MINI_WAKE_MODEL_PATH` 미설정 시).

| 항목 | 값 |
| :--- | :--- |
| 파일 | `reachy_mini_v4.onnx` (+ `.onnx.data`, 합계 약 416KB) |
| 프레임워크 | ONNX (onnxruntime), 입력 (1, 8, 96) |
| 학습일 | 2026-10-27 |
| 임계값 | **0.9997** (`wake_engines._OWW_CONFIDENCE_THRESHOLD`) |
| 합성음 평가 | 감지 12/12 (100%), 오탐 0/12 — 배포 경로(int16 스트리밍 predict) 기준, 메인 세션 독립 재검증 포함 |

## v2→v4에서 배운 핵심 (학습-추론 정합)

- **v2/v3 실패 원인**: 학습 특징을 배치 추출기(`embed_clips`)로 뽑았는데 런타임은
  스트리밍 버퍼를 씀 — 두 경로의 특징 공간이 달라(코사인 유사도 0.83) 런타임
  점수가 ~0이었음. **v4는 학습 특징도 런타임과 동일한 스트리밍 추출기로 수집.**
- **윈도우 8프레임(640ms)**: 기본 16프레임(1280ms)은 0.5~0.8초 웨이크 발화가
  창을 절반도 못 채움 → 8로 축소.
- 엔진 입력은 **int16 그대로** — float 정규화 입력은 내부 int16 캐스팅에서 0으로
  소거됨 (`wake_engines.py` 수정 사유).

## 학습 데이터 구성 (v4)

- 양성 1,855 스트리밍 윈도우: say-TTS(9보이스×5문구×5속도) + OpenAI TTS
  (6보이스×8문구×7속도, 비용 ~$0.03) — 패딩 없이 원클립
- 부정: 한국어 합성 스트리밍 1,108 윈도우 + openWakeWord EN negative 10,000 윈도우
- 실제 한국어 코퍼스는 미포함 (torchcodec 의존 — 후속 과제)

## 재학습 방법 (재현)

1. venv에 `openwakeword`, `speechbrain`, `onnxscript` 설치
2. **주의**: `acoustics`가 scipy 1.15+와 비호환 — `sph_harm` → `sph_harm_y` 1줄 패치
3. 학습: 스파이크 산출물 `train_oww_v2.py` 계열 (5000 steps, CPU 수십 초) —
   특징 추출은 반드시 `AudioFeatures` **스트리밍 경로**로
4. 판정 규약: `Model(..., inference_framework="onnx").predict()`에 1280샘플 int16
   청크 스트리밍 — **학습측 자체 평가 수치는 판정에 쓰지 않는다**

## 알려진 한계

- **마진이 매우 얇음**: 양성 최저 0.9999 vs 부정 최고 0.9996 (0.0003) —
  Google speech embedding의 한국어 음소 변별 한계가 근본 원인. **실발화 검증
  통과 전까지 임계값·모델은 잠정**
- 감지 문구는 학습 시점 고정 — 변경은 재학습 필요
- 짧은 고립 발화 평가 시 발화 뒤 꼬리 오디오(~0.5s)가 있어야 특징 창이 채워짐
  (실사용 스트리밍에선 자연 충족)
