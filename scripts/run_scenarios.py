"""대화 시나리오 실행기. 자세한 사용법은 docs/scenario-testing.md 참고.

python scripts/run_scenarios.py --target offline
python scripts/run_scenarios.py --target live --group tool --repeat 3
python scripts/run_scenarios.py --target sim --group sleep [--voice]
"""

import sys
import asyncio
import logging
import argparse

from scenario_kit.report import exit_code, print_summary, print_step_detail
from scenario_kit.runner import ScenarioResult, run_scenario
from scenario_kit.schema import GROUPS, TARGETS, Scenario, load_scenarios
from scenario_kit.targets import Target, SimTarget, LiveTarget, OfflineTarget


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=TARGETS, default="offline")
    parser.add_argument("--group", choices=GROUPS, help="이 그룹만 실행")
    parser.add_argument("--only", nargs="+", metavar="ID", help="이 시나리오만 실행")
    parser.add_argument("--repeat", type=int, default=1, help="반복 횟수 (LLM 통과율 측정)")
    parser.add_argument("--include-pending", action="store_true", help="미구현 시나리오도 실행해 현재 동작을 측정")
    parser.add_argument("--voice", action="store_true", help="sim: 텍스트 주입 대신 마이크로 직접 말함")
    parser.add_argument("-v", "--verbose", action="store_true", help="도구, 답변, 수신 이벤트까지 출력")
    parser.add_argument("--list", action="store_true", help="시나리오 목록만 출력")
    return parser.parse_args()


def _make_target(args: argparse.Namespace) -> Target:
    if args.target == "offline":
        return OfflineTarget(trace=args.verbose)
    if args.target == "live":
        return LiveTarget(trace=args.verbose)
    return SimTarget(voice=args.voice)


def _select(args: argparse.Namespace) -> list[Scenario]:
    selected = load_scenarios()
    if args.group:
        selected = [s for s in selected if s.group == args.group]
    if args.only:
        missing = set(args.only) - {s.id for s in selected}
        if missing:
            raise SystemExit(f"찾을 수 없는 시나리오: {sorted(missing)}")
        selected = [s for s in selected if s.id in args.only]
    return selected


async def _run(args: argparse.Namespace) -> int:
    runs: dict[str, list[ScenarioResult]] = {}
    pending: dict[str, str] = {}
    for scenario in _select(args):
        if args.target not in scenario.targets:
            continue
        if scenario.pending and not args.include_pending:
            pending[scenario.id] = scenario.pending
            continue
        print(f"\n▶ {scenario.id}: {scenario.title}", flush=True)
        for attempt in range(args.repeat):
            result = await run_scenario(scenario, _make_target(args))
            runs.setdefault(scenario.id, []).append(result)
            print(f"  [{attempt + 1}/{args.repeat}] {result.status}", flush=True)
            print_step_detail(result, verbose=args.verbose)
    print_summary(args.target, runs, pending)
    return exit_code(runs)


def main() -> int:
    """스크립트 진입점."""
    args = _parse_args()
    logging.basicConfig(level=logging.ERROR)
    if args.list:
        for scenario in load_scenarios():
            tag = f" (미구현: {scenario.pending})" if scenario.pending else ""
            print(f"{scenario.group:<9} {scenario.id:<28} {scenario.title}{tag}")
        return 0
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
