"""웨이크 워드 실발화 검증 — 노트북 마이크로 reachy_mini_v4 모델 + 런타임 가드를 라이브 테스트.

실제 앱과 동일한 경로를 탄다: 마이크 → WakeWordListener.feed() → (워커 스레드)
엔진 추론 → 연속-청크 가드 + 쿨다운 → on_wake 콜백.  즉 이 테스트의 ✅ WAKE는
"엔진 점수가 높았다"가 아니라 "가드까지 통과해 실제로 로봇을 깨웠다"를 뜻한다.

사용법 (마이크 권한이 있는 터미널에서):
  .venv/bin/python scripts/wake_word_live_test.py

절차:
  1. "리치미니"를 1~2초 간격으로 10회 발화 → 감지 시 ✅ WAKE
  2. "리치", "미니", "리치리치", "미니미니"를 각각 여러 번 — 가드가 걸러내면
     ⛔ (가드 차단) 또는 아무 출력 없음이 정상
  3. 일상 대화/영상을 2~3분 들려주며 방치 → ✅ WAKE 가 안 나와야 함
  4. Ctrl-C 로 종료 → 요약 출력

판정 (설계 문서): 의도 발화 10회 중 감지 ≥ 7 / 부분음절·일상 구간 오탐 0.
"""

import time
import threading

import numpy as np
import sounddevice as sd

from reachy_mini_conversation_app.audio.wake_word import (
    WAKE_CONSECUTIVE_CHUNKS,
    WakeWordListener,
)
from reachy_mini_conversation_app.audio.wake_engines import (
    _OWW_CONFIDENCE_THRESHOLD,
    OpenWakeWordEngine,
)


CHUNK = 1280  # 80ms @ 16kHz — openwakeword 권장 청크
RATE = 16000
MODEL = "src/reachy_mini_conversation_app/audio/models/reachy_mini_v4.onnx"


def main() -> None:
    """Run the live microphone wake-word test through the real listener path."""
    engine = OpenWakeWordEngine(MODEL)
    wake_times: list[float] = []
    started = time.monotonic()
    lock = threading.Lock()

    def on_wake(phrase: str) -> None:
        with lock:
            elapsed = time.monotonic() - started
            wake_times.append(elapsed)
            print(f"✅ WAKE  [{elapsed:7.1f}s]  로봇이 깨어남 (가드 통과: {phrase})")

    # loop=None → on_wake는 워커 스레드에서 직접 호출 (이 스크립트엔 asyncio 루프 없음)
    listener = WakeWordListener(engine=engine, on_wake=on_wake, loop=None)
    listener.start()

    print(f"모델: {MODEL}")
    print(f"임계값: {_OWW_CONFIDENCE_THRESHOLD}  |  연속 청크 가드: {WAKE_CONSECUTIVE_CHUNKS}  |  쿨다운: 3.0s")
    print("듣는 중...  '리치미니'로 깨우고, '리치'/'미니'/'리치리치'로 오탐을 시험하세요 (Ctrl-C 종료)\n")

    # 가드 전(前) 엔진 원점수도 같이 관찰 — 가드가 무엇을 걸렀는지 보기 위함.
    last_raw_peak = [0.0]

    def on_block(indata: np.ndarray, frames: int, _time: object, status: object) -> None:
        if status:
            print(f"[audio status] {status}")
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        raw = float(engine._model.predict(pcm).get(engine._label, 0.0))
        last_raw_peak[0] = max(last_raw_peak[0], raw)
        # 실제 앱 경로: 리스너에 프레임을 feed (가드가 여기서 작동)
        listener.feed(RATE, pcm)

    try:
        with sd.InputStream(samplerate=RATE, channels=1, blocksize=CHUNK, callback=on_block):
            while True:
                time.sleep(5)
                with lock:
                    peak = last_raw_peak[0]
                    last_raw_peak[0] = 0.0
                    n = len(wake_times)
                over = "⚠️ 가드가 차단한 고점 있음" if peak >= _OWW_CONFIDENCE_THRESHOLD else ""
                print(f"   ... 최근 5초 엔진 최고점 {peak:.4f} | 실제 깨움 {n}회 {over}")
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        engine.close()

    total = time.monotonic() - started
    print("\n===== 요약 =====")
    print(f"총 {total:.0f}초 동안 실제 깨움(가드 통과) {len(wake_times)}회")
    for i, t in enumerate(wake_times, 1):
        print(f"  {i:2d}. {t:7.1f}s")
    print("\n판정: 의도 '리치미니' 10회 중 ≥7 깨움 + '리치'/'미니'/일상 구간 깨움 0 이면 통과")


if __name__ == "__main__":
    main()
