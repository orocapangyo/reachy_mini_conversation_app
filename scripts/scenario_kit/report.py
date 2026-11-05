"""터미널 리포트."""

from __future__ import annotations
from collections import Counter

from scenario_kit.runner import FAIL, PASS, ERROR, FIXED, PENDING, KNOWN_BUG, ScenarioResult


TARGET_MEANING = {
    "offline": "가짜 모델: 호출 이후의 처리만 검증합니다. 모델이 도구를 잘 고르는지는 live로 확인하세요.",
    "live": "실제 Gemini + Mock 로봇: 텍스트 입력 기준입니다. 음성 인식, VAD, 모션은 sim --voice로 확인하세요.",
    "sim": "실행 중인 앱: 도구 인자와 interrupted 신호는 RPC로 볼 수 없어 판정에서 제외합니다.",
}


def print_step_detail(result: ScenarioResult, *, verbose: bool) -> None:
    """실패 사유와 선택적으로 대화 내용을 출력한다."""
    for failure in result.failures:
        print(f"      ✗ {failure}")
    if not verbose:
        return
    for step in result.steps:
        print(f"      · {step.label}")
        print(f"        도구: {step.tools}")
        print(f"        답변: {step.reply}")
        for line in step.trace:
            print(f"        trace: {line}")


def print_summary(target: str, runs: dict[str, list[ScenarioResult]], pending: dict[str, str]) -> None:
    """시나리오별 통과율과 합계를 출력한다."""
    print(f"\n=== 요약 ({target}) ===\n{TARGET_MEANING[target]}\n")
    totals: Counter[str] = Counter()
    for scenario_id, results in runs.items():
        passed = sum(result.status in (PASS, FIXED) for result in results)
        statuses = Counter(result.status for result in results)
        worst = next((s for s in (ERROR, FAIL, KNOWN_BUG, FIXED, PASS) if statuses[s]), PASS)
        totals[worst] += 1
        print(f"  {worst:<10} {passed}/{len(results)}  {scenario_id}")
    for scenario_id, reason in pending.items():
        totals[PENDING] += 1
        print(f"  {PENDING:<10} -    {scenario_id}  ({reason})")
    print("\n  " + "  ".join(f"{status}={count}" for status, count in sorted(totals.items())))
    if totals[FIXED]:
        print("  FIXED?: known_bug로 표시했지만 통과했습니다. 고쳐졌다면 known_bug 줄을 지우세요.")


def exit_code(runs: dict[str, list[ScenarioResult]]) -> int:
    """FAIL이나 ERROR가 하나라도 있으면 1."""
    return int(any(result.status in (FAIL, ERROR) for results in runs.values() for result in results))
