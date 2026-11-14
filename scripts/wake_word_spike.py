#!/usr/bin/env python3
"""Wake-word engine spike: 3-candidate comparison for Korean "리치미니" detection.

Candidates
----------
A. openWakeWord  — pre-trained English only; Korean requires custom training pipeline
B. Vosk + vosk-model-small-ko-0.22 — STT-based substring matching (offline, Apache 2.0)
C. Porcupine (Picovoice) — eliminated: AccessKey required + non-commercial free tier

Test audio produced with:
    say -v Yuna -r <rate> -o <file>.aiff "..."
    afconvert -f WAVE -d LEI16@16000 -c 1 <file>.aiff <file>.wav

Usage:
    python scripts/wake_word_spike.py --model-dir <path_to_vosk_model_dir> [--audio-dir <path>]

The script prints a verdict table and recommendation.
"""

from __future__ import annotations
import os
import re
import json
import time
import wave
import argparse
from typing import Sequence
from pathlib import Path


# ---------------------------------------------------------------------------
# Wake phrase matching (mirrors openai_realtime.py:matches_wake_phrase)
# ---------------------------------------------------------------------------

_WAKE_PHRASES = ["리치미니", "일어나", "깨어나"]


def _normalize_for_wake(text: str) -> str:
    """Strip whitespace/punctuation and lowercase so STT spelling variants match."""
    return re.sub(r"[\W_]+", "", text.lower())


def matches_wake_phrase(transcript: str, phrases: Sequence[str] = _WAKE_PHRASES) -> bool:
    """Return whether the transcript loosely contains any wake phrase."""
    normalized = _normalize_for_wake(transcript)
    if not normalized:
        return False
    for phrase in phrases:
        normalized_phrase = _normalize_for_wake(phrase)
        if normalized_phrase and normalized_phrase in normalized:
            return True
    return False


# ---------------------------------------------------------------------------
# Engine A: openWakeWord — capability investigation only
# ---------------------------------------------------------------------------

OPENWAKEWORD_VERDICT: dict[str, object] = {
    "engine": "openWakeWord",
    "pretrained_models": ["alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy", "timer", "weather"],
    "pretrained_languages": "English only",
    "korean_without_training": False,
    "training_required": True,
    "training_summary": (
        "Custom Korean model requires: (1) synthetic TTS data generation "
        "(several thousand samples minimum, 30 k-hour negative corpus used by released models), "
        "(2) Google Colab training notebook — estimated < 1 hour on free Colab GPU for basic model; "
        "production-quality training takes longer and needs own negative audio dataset. "
        "Separate open-source repo (github.com/dscripka/openWakeWord-training) provides scripts."
    ),
    "license": "Apache 2.0",
    "offline": True,
    "positive_rate": "N/A (not tested — training required)",
    "false_positive_rate": "N/A",
    "latency_ms": "N/A",
    "memory_mb": "N/A",
    "verdict": "학습 필요 — 사전 학습 한국어 모델 없음, 커스텀 학습 파이프라인(Colab < 1h) 필요",
}


# ---------------------------------------------------------------------------
# Engine B: Vosk + vosk-model-small-ko-0.22
# ---------------------------------------------------------------------------


def _wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def run_vosk_on_file(model: object, wav_path: Path) -> tuple[str, float]:
    """Run Vosk recognizer on a 16kHz mono WAV file.

    Returns (transcript, elapsed_seconds).
    """
    from vosk import KaldiRecognizer  # type: ignore[import]

    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(False)

    t0 = time.perf_counter()
    with wave.open(str(wav_path), "rb") as wf:
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            rec.AcceptWaveform(data)

    result = json.loads(rec.FinalResult())
    elapsed = time.perf_counter() - t0
    transcript = result.get("text", "")
    return transcript, elapsed


def run_vosk_spike(
    model_dir: Path,
    audio_pos_dir: Path,
    audio_neg_dir: Path,
) -> dict[str, object]:
    """Run all positive and negative files through Vosk; return verdict dict."""
    try:
        from vosk import Model, SetLogLevel  # type: ignore[import]
    except ImportError:
        return {
            "engine": "Vosk",
            "error": "vosk package not installed — run: pip install vosk",
        }

    if not model_dir.exists():
        return {
            "engine": "Vosk",
            "error": f"Model directory not found: {model_dir}",
        }

    SetLogLevel(-1)  # suppress Vosk verbose logging

    print(f"[Vosk] Loading model from {model_dir} ...")
    t_load = time.perf_counter()
    model = Model(str(model_dir))
    load_time = time.perf_counter() - t_load
    print(f"[Vosk] Model loaded in {load_time:.2f}s")

    pos_files = sorted(audio_pos_dir.glob("*.wav"))
    neg_files = sorted(audio_neg_dir.glob("*.wav"))

    pos_hits = 0
    pos_results: list[dict[str, object]] = []
    for wav in pos_files:
        dur = _wav_duration_seconds(wav)
        transcript, elapsed = run_vosk_on_file(model, wav)
        hit = matches_wake_phrase(transcript)
        pos_hits += int(hit)
        rtf = elapsed / dur if dur > 0 else 0.0
        pos_results.append(
            {
                "file": wav.name,
                "transcript": transcript,
                "hit": hit,
                "elapsed_s": round(elapsed, 4),
                "duration_s": round(dur, 3),
                "rtf": round(rtf, 3),
            }
        )
        print(f"  [POS] {wav.name:15s} | hit={hit} | '{transcript}' | {elapsed * 1000:.1f}ms ({rtf:.2f}x realtime)")

    neg_false_positives = 0
    neg_results: list[dict[str, object]] = []
    for wav in neg_files:
        dur = _wav_duration_seconds(wav)
        transcript, elapsed = run_vosk_on_file(model, wav)
        fp = matches_wake_phrase(transcript)
        neg_false_positives += int(fp)
        rtf = elapsed / dur if dur > 0 else 0.0
        neg_results.append(
            {
                "file": wav.name,
                "transcript": transcript,
                "false_positive": fp,
                "elapsed_s": round(elapsed, 4),
                "duration_s": round(dur, 3),
                "rtf": round(rtf, 3),
            }
        )
        print(f"  [NEG] {wav.name:15s} | fp={fp}  | '{transcript}' | {elapsed * 1000:.1f}ms ({rtf:.2f}x realtime)")

    all_elapsed = [r["elapsed_s"] for r in pos_results + neg_results]
    avg_elapsed_ms = (sum(all_elapsed) / len(all_elapsed) * 1000) if all_elapsed else 0.0

    all_rtf = [r["rtf"] for r in pos_results + neg_results]
    avg_rtf = sum(all_rtf) / len(all_rtf) if all_rtf else 0.0

    # Rough RSS measurement
    try:
        import resource

        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    except Exception:
        mem_mb = -1.0

    positive_rate = pos_hits / len(pos_files) if pos_files else 0.0
    false_positive_rate = neg_false_positives / len(neg_files) if neg_files else 0.0

    verdict_label = (
        "추천"
        if positive_rate >= 0.7 and false_positive_rate <= 0.1
        else "조건부"
        if positive_rate >= 0.5
        else "부적합"
    )

    return {
        "engine": "Vosk + vosk-model-small-ko-0.22",
        "model_size_mb": "~82",
        "license": "Apache 2.0",
        "offline": True,
        "positive_files_tested": len(pos_files),
        "positive_hits": pos_hits,
        "positive_rate": round(positive_rate, 3),
        "negative_files_tested": len(neg_files),
        "false_positives": neg_false_positives,
        "false_positive_rate": round(false_positive_rate, 3),
        "avg_latency_ms": round(avg_elapsed_ms, 1),
        "avg_rtf": round(avg_rtf, 3),
        "memory_rss_mb": round(mem_mb, 1),
        "pos_detail": pos_results,
        "neg_detail": neg_results,
        "verdict": verdict_label,
        "notes": (
            "STT-then-match approach: entire utterance is transcribed then checked for wake phrase. "
            "Not true keyword spotting (no streaming mid-utterance detection). "
            "Latency = full-utterance transcription time. "
            "합성 음성(say -v Yuna) 기준 — 실제 사람 목소리와 다를 수 있음."
        ),
    }


# ---------------------------------------------------------------------------
# Engine C: Porcupine — licensing investigation only
# ---------------------------------------------------------------------------

PORCUPINE_VERDICT: dict[str, object] = {
    "engine": "Porcupine (Picovoice)",
    "korean_support": True,
    "custom_keyword_training": "Picovoice Console 필요 (online 학습 서비스)",
    "license": "Apache 2.0 (SDK) — 그러나 AccessKey는 Picovoice Console 계정 필수",
    "accesskey_required": True,
    "accesskey_online_validation": "초기화 시 온라인 검증 필요 (완전 오프라인 불가 추정)",
    "free_tier_restriction": "Personal 무료 티어 — 비상업 용도에 한정 (strictly non-commercial)",
    "offline": False,
    "positive_rate": "N/A (미실측)",
    "false_positive_rate": "N/A",
    "latency_ms": "N/A",
    "memory_mb": "N/A",
    "verdict": (
        "탈락 — AccessKey 필수(계정 가입·온라인 검증)이며 무료 티어가 비상업 제한. "
        "오픈소스 공모전 제출 및 완전 로컬(네트워크 없는) 환경에 부적합."
    ),
}


# ---------------------------------------------------------------------------
# Bonus D: sherpa-onnx — investigation only
# ---------------------------------------------------------------------------

SHERPA_ONNX_VERDICT: dict[str, object] = {
    "engine": "sherpa-onnx (keyword spotting)",
    "korean_asr_models": True,
    "korean_keyword_spotting_pretrained": False,
    "notes": (
        "sherpa-onnx는 한국어 ASR 모델(zipformer, SenseVoice 멀티링구얼)을 제공하나, "
        "한국어 전용 keyword spotting 사전 학습 모델은 문서상 확인되지 않음. "
        "keyword spotting API는 주로 중국어/영어 모델 예제. "
        "Vosk STT+match 방식과 동일한 접근이 가능하지만 모델 설정 복잡도 증가. "
        "라이선스: Apache 2.0. 완전 오프라인. "
        "향후 한국어 KWS 모델이 나오면 재검토 가치 있음."
    ),
    "license": "Apache 2.0",
    "offline": True,
    "verdict": "보류 — 한국어 KWS 전용 모델 미확인; ASR+match는 Vosk와 동등",
}


# ---------------------------------------------------------------------------
# Results table printer
# ---------------------------------------------------------------------------

DIVIDER = "-" * 100


def _fmt(val: object) -> str:
    if isinstance(val, float):
        return f"{val:.3f}"
    if isinstance(val, bool):
        return "O" if val else "X"
    return str(val)


def print_verdict_table(results: list[dict[str, object]]) -> None:
    """Print a fixed-column comparison table of engine verdicts."""
    cols = [
        "engine",
        "positive_rate",
        "false_positive_rate",
        "avg_latency_ms",
        "memory_rss_mb",
        "offline",
        "license",
        "verdict",
    ]
    header = " | ".join(f"{c:>22}" for c in cols)
    print("\n" + DIVIDER)
    print("VERDICT TABLE")
    print(DIVIDER)
    print(header)
    print(DIVIDER)
    for r in results:
        row = " | ".join(f"{_fmt(r.get(c, 'N/A')):>22}" for c in cols)
        print(row)
    print(DIVIDER)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEFAULT_SCRATCH = (
    "/private/tmp/claude-501"
    "/-Users-jiyeonlee-Documents-study-reachy-mini-conversation-app"
    "/081e5963-9206-4ab9-9d55-cc47d9ab1b10/scratchpad/wake_spike"
)


def main(argv: list[str] | None = None) -> None:
    """Run all engine investigations and print the verdict table + recommendation."""
    parser = argparse.ArgumentParser(description="Wake-word engine spike comparison")
    parser.add_argument(
        "--model-dir",
        default=os.path.join(DEFAULT_SCRATCH, "models", "vosk-model-small-ko-0.22"),
        help="Path to extracted Vosk Korean model directory",
    )
    parser.add_argument(
        "--audio-dir",
        default=os.path.join(DEFAULT_SCRATCH, "audio"),
        help="Parent directory containing pos/ and neg/ subdirectories",
    )
    parser.add_argument(
        "--output-json",
        default=os.path.join(DEFAULT_SCRATCH, "spike_results.json"),
        help="Where to write full JSON results",
    )
    args = parser.parse_args(argv)

    model_dir = Path(args.model_dir)
    audio_dir = Path(args.audio_dir)
    audio_pos_dir = audio_dir / "pos"
    audio_neg_dir = audio_dir / "neg"
    output_path = Path(args.output_json)

    print("=" * 60)
    print("Wake-word engine spike — Korean '리치미니'")
    print("=" * 60)

    # --- Engine A ---
    print("\n[A] openWakeWord: 사전 학습 모델 조사")
    print(f"    → {OPENWAKEWORD_VERDICT['verdict']}")

    # --- Engine B ---
    print("\n[B] Vosk + vosk-model-small-ko-0.22: 실측")
    vosk_result = run_vosk_spike(model_dir, audio_pos_dir, audio_neg_dir)
    if "error" in vosk_result:
        print(f"    ERROR: {vosk_result['error']}")

    # --- Engine C ---
    print("\n[C] Porcupine: 라이선스·지원 조사")
    print(f"    → {PORCUPINE_VERDICT['verdict']}")

    # --- Bonus D ---
    print("\n[D] sherpa-onnx: 한국어 KWS 지원 조사")
    print(f"    → {SHERPA_ONNX_VERDICT['verdict']}")

    # --- Table ---
    all_results = [OPENWAKEWORD_VERDICT, vosk_result, PORCUPINE_VERDICT, SHERPA_ONNX_VERDICT]
    print_verdict_table(all_results)

    # --- Recommendation ---
    vosk_pos = vosk_result.get("positive_rate", 0.0)
    print("\nRECOMMENDATION")
    print(DIVIDER)

    if isinstance(vosk_pos, float) and vosk_pos >= 0.7:
        print("추천: Vosk + vosk-model-small-ko-0.22 (STT+match 방식)")
        print()
        print("근거:")
        print("  1. 완전 오프라인, 네트워크 불필요 (로봇 수면 중 상시 동작 조건 충족)")
        print("  2. Apache 2.0 — 오픈소스 공모전·상업 배포 모두 제약 없음")
        print("  3. 한국어 전용 소형 모델(~82 MB)으로 RPi/ARM 탑재 가능")
        print("  4. matches_wake_phrase() 로직 그대로 재사용 — 기존 코드베이스와 자연스럽게 연동")
        print("  5. 주요 한계: utterance 전체 전사 후 판정 → 발화 완료까지 대기 필요.")
        print("     스트리밍 부분 인식(partial result)으로 조기 트리거 가능하나 오탐 증가 트레이드오프.")
    else:
        # Vosk small-ko failed: '리치미니' transcribed as '우리 치니' / '우리 침 이니' — phonetically
        # adjacent but never matches the exact wake-phrase substring.
        print(f"실측 결과: Vosk 양성 감지율 {vosk_pos:.0%} (목표 ≥70% 미달)")
        print()
        print("핵심 실패 원인:")
        print("  Vosk small-ko는 '리치미니'를 '우리 치니', '우리 침 이니' 등으로 전사 —")
        print("  음소는 인접하나 정확한 부분 문자열 매칭에 실패. 단순 STT+substring 방식은 부적합.")
        print()
        print("권장 방향 (우선순위 순):")
        print()
        print("  1순위 — Vosk + 음소 유사도 매칭 (추가 개발 비용 낮음)")
        print("    • Vosk partial result 스트리밍 + jamo 분해 기반 유사 발음 집합 확장")
        print("      예: '리치미니' → {'우리치니', '리치니', '리치 이니', ...} 를 모두 매칭")
        print("    • python-jamo (pip install jamo) 로 자모 정규화 후 편집거리 임계값 적용")
        print("    • 오프라인·Apache 2.0 유지, 기존 코드베이스와 자연 연동")
        print()
        print("  2순위 — openWakeWord 커스텀 모델 학습")
        print("    • Google Colab 학습 노트북으로 '리치미니' 합성 TTS 데이터 생성 + 모델 훈련 (<1h)")
        print("    • 한 번 학습하면 이후 추론은 완전 오프라인·실시간 (30ms 이하 예상)")
        print("    • 학습 초기 투자 필요, 모델 파일(.tflite) 저장소 포함 필수")
        print()
        print("  탈락: Porcupine(AccessKey 필수·비상업 제한), sherpa-onnx(한국어 KWS 모델 미확인)")
        print()
        print("즉각 다음 단계:")
        print("  WW-2(설계) 전에 jamo 정규화 매칭 프로토타입을 별도 스크립트로 검증 권장.")
        print("  합성 음성 테스트에서 개선 확인 후 WW-3 구현으로 진행.")

    print()
    print("합성 음성 한계 고지:")
    print("  테스트는 macOS say -v Yuna 합성 음성 기준. 실제 한국인 발화는 음소·리듬이 달라")
    print("  합성 음성 결과와 차이가 날 수 있음 — WW-4(실증) 단계에서 실제 마이크로 재확인 필수.")
    print(DIVIDER)

    # --- Write JSON ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nFull results written to: {output_path}")


if __name__ == "__main__":
    main()
