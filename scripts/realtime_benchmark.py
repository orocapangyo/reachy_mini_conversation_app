"""Benchmark the OpenAI Realtime and Gemini Live backends through the app's own handlers.

Each backend runs the same Korean voice scenarios (``benchmark_scenarios.json``)
through the real ``OpenAIRealtimeHandler`` / ``GeminiRealtimeHandler`` — same
profile prompt, same tool registry, same audio path — with the robot and the
movement manager mocked, so no daemon is needed. Microphone input is a WAV file
streamed in real time, followed by silence, exactly as the console loop would.

Per scenario it records:

- ``ttfa_ms``: end of user speech → first assistant audio chunk (includes VAD wait)
- ``tools``: tool calls the model made, compared to ``expected_tools``
- ``user_transcript`` / ``assistant_transcript``: STT and spoken reply text
- ``audio_out_s``: seconds of assistant audio
- ``turn_ms``: end of user speech → turn settled
- session ``connect_ms`` and ``greeting_ttfa_ms`` (launch → first greeting audio)

Usage (venv active, keys in ``.env``)::

    python scripts/realtime_benchmark.py --backends openai gemini --repeats 3

Speech WAVs are synthesized once with macOS ``say`` (voice Yuna) into
``--audio-dir``. On other platforms, record ``<scenario id>.wav`` files
(16-bit mono PCM, any rate) into that directory instead.
"""

from __future__ import annotations
import sys
import json
import time
import wave
import shutil
import asyncio
import logging
import argparse
import statistics
import subprocess
from typing import Any
from pathlib import Path
from dataclasses import field, asdict, dataclass
from unittest.mock import MagicMock

import numpy as np
from numpy.typing import NDArray


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENARIOS = REPO_ROOT / "scripts" / "benchmark_scenarios.json"
DEFAULT_AUDIO_DIR = REPO_ROOT / "benchmarks" / "audio"
DEFAULT_OUT_DIR = REPO_ROOT / "benchmarks" / "results"

# Tuning parameters
FRAME_MS = 20  # microphone chunk size fed to handler.receive()
LEAD_SILENCE_S = 0.3  # silence before the utterance
CONNECT_TIMEOUT_S = 30.0
GREETING_TIMEOUT_S = 20.0
TURN_TIMEOUT_S = 35.0
SETTLE_S = 2.5  # quiet time after the last event that ends a turn
BETWEEN_TURNS_S = 1.0
SAY_VOICE = "Yuna"
SAY_RATE_HZ = 24000

logger = logging.getLogger("realtime_benchmark")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class TurnEvents:
    """Timestamped events observed on the handler's output queue for one turn."""

    first_audio_at: float | None = None
    last_event_at: float | None = None
    last_tool_at: float | None = None
    audio_after_last_tool: bool = False
    audio_samples: int = 0
    audio_rate: int = 24000
    tools: list[str] = field(default_factory=list)
    user_transcript: str = ""
    assistant_transcript: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    """One scenario outcome for one backend and repeat."""

    backend: str
    model: str
    repeat: int
    scenario_id: str
    scene: str
    prompt: str
    expected_tools: list[str]
    tools: list[str]
    tool_match: bool
    ttfa_ms: float | None
    turn_ms: float | None
    audio_out_s: float
    user_transcript: str
    assistant_transcript: str
    errors: list[str]
    timed_out: bool


@dataclass
class SessionResult:
    """Session-level metrics for one backend and repeat."""

    backend: str
    model: str
    repeat: int
    connect_ms: float | None
    greeting_ttfa_ms: float | None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Audio helpers
# --------------------------------------------------------------------------- #


def ensure_scenario_audio(scenarios: list[dict[str, Any]], audio_dir: Path) -> None:
    """Synthesize missing scenario WAVs with macOS ``say``; fail clearly elsewhere."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    missing = [s for s in scenarios if not (audio_dir / f"{s['id']}.wav").exists()]
    if not missing:
        return
    if shutil.which("say") is None:
        names = ", ".join(f"{s['id']}.wav" for s in missing)
        raise SystemExit(f"Missing scenario audio in {audio_dir}: {names}. Record them (16-bit mono WAV).")
    for scenario in missing:
        target = audio_dir / f"{scenario['id']}.wav"
        subprocess.run(
            [
                "say",
                "-v",
                SAY_VOICE,
                "--file-format=WAVE",
                f"--data-format=LEI16@{SAY_RATE_HZ}",
                "-o",
                str(target),
                scenario["text"],
            ],
            check=True,
        )
        logger.info("Synthesized %s", target.name)


def load_wav(path: Path) -> tuple[int, NDArray[np.int16]]:
    """Load a 16-bit PCM WAV as mono int16."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise SystemExit(f"{path} must be 16-bit PCM")
        rate = wav.getframerate()
        channels = wav.getnchannels()
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels)[:, 0].copy()
    return rate, pcm


# --------------------------------------------------------------------------- #
# Handler driving
# --------------------------------------------------------------------------- #


def build_handler(backend: str) -> Any:
    """Build the app's realtime handler for ``backend`` with a mocked robot."""
    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

    config.CONVERSATION_BACKEND = backend
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    if backend == "openai":
        from reachy_mini_conversation_app.openai_realtime import OpenAIRealtimeHandler

        return OpenAIRealtimeHandler(deps)
    from reachy_mini_conversation_app.gemini_realtime import GeminiRealtimeHandler

    return GeminiRealtimeHandler(deps)


def model_name(backend: str) -> str:
    """Return the configured model id for ``backend``."""
    from reachy_mini_conversation_app.config import config

    if backend == "openai":
        return str(config.OPENAI_REALTIME_MODEL)
    return str(config.GEMINI_LIVE_MODEL)


def _tool_name_from_message(role: str, content: str) -> str | None:
    """Extract a tool name from the handlers' tool-call notices."""
    if role == "tool_use" and content.startswith("Used tool "):
        return content.removeprefix("Used tool ").split()[0]
    if role == "assistant" and content.startswith("🛠️ Used tool "):
        return content.removeprefix("🛠️ Used tool ").split()[0]
    return None


class EventCollector:
    """Drain the handler's output queue and attribute events to the current turn."""

    def __init__(self, handler: Any) -> None:
        """Start with an empty turn."""
        self._handler = handler
        self.turn = TurnEvents()

    def reset(self) -> None:
        """Begin a new turn."""
        self.turn = TurnEvents()

    async def run(self) -> None:
        """Consume output-queue items forever (cancel to stop)."""
        from reachy_mini_conversation_app.streaming import AdditionalOutputs

        while True:
            item = await self._handler.output_queue.get()
            now = time.perf_counter()
            turn = self.turn
            turn.last_event_at = now
            if isinstance(item, AdditionalOutputs):
                for message in item.args:
                    self._on_message(turn, now, message)
                continue
            rate, audio = item
            if turn.first_audio_at is None:
                turn.first_audio_at = now
            turn.audio_rate = int(rate)
            turn.audio_samples += int(np.asarray(audio).size)
            if turn.last_tool_at is not None:
                turn.audio_after_last_tool = True

    @staticmethod
    def _on_message(turn: TurnEvents, now: float, message: Any) -> None:
        if not isinstance(message, dict):
            return
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        tool = _tool_name_from_message(role, content)
        if tool is not None:
            turn.tools.append(tool)
            turn.last_tool_at = now
            turn.audio_after_last_tool = False
        elif role == "user":
            turn.user_transcript = content
        elif role == "assistant":
            if content.startswith("[error]"):
                turn.errors.append(content)
            elif not content.lstrip().startswith("{"):  # skip echoed tool results
                turn.assistant_transcript.append(content)


async def wait_for_settle(collector: EventCollector, started_at: float, timeout_s: float) -> bool:
    """Wait until the turn has audio and no events for ``SETTLE_S``; return False on timeout."""
    deadline = started_at + timeout_s
    while time.perf_counter() < deadline:
        await asyncio.sleep(0.1)
        turn = collector.turn
        if turn.first_audio_at is None or turn.last_event_at is None:
            continue
        if turn.last_tool_at is not None and not turn.audio_after_last_tool:
            continue  # tool still running or spoken follow-up not started
        if time.perf_counter() - turn.last_event_at >= SETTLE_S:
            return True
    return False


async def stream_utterance(handler: Any, rate: int, pcm: NDArray[np.int16]) -> float:
    """Feed ``pcm`` to the handler in real time; return the time the speech ended."""
    frame = max(1, rate * FRAME_MS // 1000)
    lead = np.zeros(int(rate * LEAD_SILENCE_S), dtype=np.int16)
    audio = np.concatenate([lead, pcm])
    t0 = time.perf_counter()
    for i, start in enumerate(range(0, audio.size, frame)):
        await handler.receive((rate, audio[start : start + frame].reshape(1, -1)))
        target = t0 + (i + 1) * FRAME_MS / 1000
        await asyncio.sleep(max(0.0, target - time.perf_counter()))
    return time.perf_counter()


async def stream_silence(handler: Any, rate: int, stop: asyncio.Event) -> None:
    """Keep the mic 'open' with silence, as the console loop does between utterances."""
    frame = np.zeros((1, max(1, rate * FRAME_MS // 1000)), dtype=np.int16)
    while not stop.is_set():
        await handler.receive((rate, frame))
        await asyncio.sleep(FRAME_MS / 1000)


async def wait_connected(handler: Any, task: asyncio.Task[None]) -> None:
    """Wait for the handler's realtime connection, surfacing startup failures."""
    deadline = time.perf_counter() + CONNECT_TIMEOUT_S
    while time.perf_counter() < deadline:
        if task.done():
            task.result()  # re-raise the startup error
            raise RuntimeError("handler start_up() returned before connecting")
        if handler.connection is not None:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"no connection after {CONNECT_TIMEOUT_S:.0f}s")


async def run_session(
    backend: str,
    repeat: int,
    scenarios: list[dict[str, Any]],
    audio_dir: Path,
) -> tuple[SessionResult, list[TurnResult]]:
    """Run every scenario in one live session for ``backend``."""
    handler = build_handler(backend)
    model = model_name(backend)
    collector = EventCollector(handler)
    collector_task = asyncio.create_task(collector.run())
    t_connect = time.perf_counter()
    startup_task: asyncio.Task[None] = asyncio.create_task(handler.start_up())
    turns: list[TurnResult] = []
    try:
        try:
            await wait_connected(handler, startup_task)
        except Exception as e:
            return SessionResult(backend, model, repeat, None, None, error=f"{type(e).__name__}: {e}"), turns
        connected_at = time.perf_counter()
        connect_ms = (connected_at - t_connect) * 1000

        # Startup greeting is sent by the handler itself.
        greeted = await wait_for_settle(collector, connected_at, GREETING_TIMEOUT_S)
        greeting_first = collector.turn.first_audio_at
        # Measured from start_up(): what a user waits for from launch to the first spoken word.
        greeting_ttfa_ms = (greeting_first - t_connect) * 1000 if greeted and greeting_first else None
        session = SessionResult(backend, model, repeat, connect_ms, greeting_ttfa_ms)

        for scenario in scenarios:
            rate, pcm = load_wav(audio_dir / f"{scenario['id']}.wav")
            await asyncio.sleep(BETWEEN_TURNS_S)
            collector.reset()
            speech_end = await stream_utterance(handler, rate, pcm)
            stop_silence = asyncio.Event()
            silence_task = asyncio.create_task(stream_silence(handler, rate, stop_silence))
            settled = await wait_for_settle(collector, speech_end, TURN_TIMEOUT_S)
            stop_silence.set()
            await silence_task

            turn = collector.turn
            expected = list(scenario.get("expected_tools", []))
            turns.append(
                TurnResult(
                    backend=backend,
                    model=model,
                    repeat=repeat,
                    scenario_id=scenario["id"],
                    scene=scenario.get("scene", ""),
                    prompt=scenario["text"],
                    expected_tools=expected,
                    tools=turn.tools,
                    tool_match=sorted(set(turn.tools)) == sorted(set(expected)),
                    ttfa_ms=(turn.first_audio_at - speech_end) * 1000 if turn.first_audio_at else None,
                    turn_ms=(turn.last_event_at - speech_end) * 1000 if settled and turn.last_event_at else None,
                    audio_out_s=turn.audio_samples / turn.audio_rate if turn.audio_rate else 0.0,
                    user_transcript=turn.user_transcript,
                    assistant_transcript=" ".join(turn.assistant_transcript).strip(),
                    errors=turn.errors,
                    timed_out=not settled,
                )
            )
            logger.info(
                "[%s #%d] %s ttfa=%s tools=%s",
                backend,
                repeat,
                scenario["id"],
                f"{turns[-1].ttfa_ms:.0f}ms" if turns[-1].ttfa_ms is not None else "-",
                turn.tools,
            )
        return session, turns
    finally:
        collector_task.cancel()
        try:
            await handler.shutdown()
        except Exception as e:
            logger.debug("shutdown ignored: %s", e)
        startup_task.cancel()
        await asyncio.gather(startup_task, collector_task, return_exceptions=True)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _fmt(value: float | None, unit: str = "") -> str:
    return "-" if value is None else f"{value:,.0f}{unit}"


def summarize(sessions: list[SessionResult], turns: list[TurnResult]) -> str:
    """Render a Markdown summary of the raw results."""
    backends = sorted({s.backend for s in sessions})
    lines = ["# Realtime backend benchmark — raw summary", ""]
    lines += ["| 지표 | " + " | ".join(backends) + " |", "| :--- |" + " ---: |" * len(backends)]

    def row(label: str, fn: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(b) for b in backends) + " |")

    def ok_turns(b: str) -> list[TurnResult]:
        return [t for t in turns if t.backend == b]

    def ttfa(b: str) -> list[float]:
        return [t.ttfa_ms for t in ok_turns(b) if t.ttfa_ms is not None]

    row("모델", lambda b: next((s.model for s in sessions if s.backend == b), "-"))
    row(
        "세션 수 (실패)",
        lambda b: (
            f"{sum(s.backend == b for s in sessions)} ({sum(s.backend == b and s.error is not None for s in sessions)})"
        ),
    )
    row(
        "연결 시간 중앙값",
        lambda b: _fmt(
            statistics.median([s.connect_ms for s in sessions if s.backend == b and s.connect_ms is not None] or [0]),
            " ms",
        ),
    )
    row(
        "기동→인사 첫 음성 중앙값",
        lambda b: _fmt(
            _pct([s.greeting_ttfa_ms for s in sessions if s.backend == b and s.greeting_ttfa_ms is not None], 0.5),
            " ms",
        ),
    )
    row("응답 첫 음성 (TTFA) p50", lambda b: _fmt(_pct(ttfa(b), 0.5), " ms"))
    row("응답 첫 음성 (TTFA) p90", lambda b: _fmt(_pct(ttfa(b), 0.9), " ms"))
    row("턴 완료 p50", lambda b: _fmt(_pct([t.turn_ms for t in ok_turns(b) if t.turn_ms is not None], 0.5), " ms"))
    row("도구 선택 정확도", lambda b: f"{sum(t.tool_match for t in ok_turns(b))}/{len(ok_turns(b))}")
    row("무응답·타임아웃", lambda b: str(sum(t.timed_out for t in ok_turns(b))))
    row("평균 응답 음성 길이", lambda b: f"{statistics.mean([t.audio_out_s for t in ok_turns(b)] or [0]):.1f} s")

    lines += ["", "## 시나리오별 TTFA 중앙값 / 도구 일치", ""]
    lines += ["| 시나리오 | " + " | ".join(backends) + " |", "| :--- |" + " :---: |" * len(backends)]
    for scenario_id in dict.fromkeys(t.scenario_id for t in turns):

        def cell(b: str, sid: str = scenario_id) -> str:
            rows = [t for t in turns if t.backend == b and t.scenario_id == sid]
            values = [t.ttfa_ms for t in rows if t.ttfa_ms is not None]
            match = sum(t.tool_match for t in rows)
            return f"{_fmt(_pct(values, 0.5), ' ms')} · 도구 {match}/{len(rows)}"

        lines.append(f"| {scenario_id} | " + " | ".join(cell(b) for b in backends) + " |")

    lines += ["", "## 응답 샘플 (repeat 1)", ""]
    for t in turns:
        if t.repeat != 1:
            continue
        lines.append(
            f"- **{t.backend} / {t.scenario_id}** — STT: `{t.user_transcript or '-'}` · 도구: {t.tools or '없음'}"
        )
        lines.append(f"  - 응답: {t.assistant_transcript or '(전사 없음)'}")
    errors = [s for s in sessions if s.error]
    if errors:
        lines += ["", "## 세션 오류", ""] + [f"- {s.backend} #{s.repeat}: {s.error}" for s in errors]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def check_keys(backends: list[str]) -> None:
    """Exit early when a selected backend has no API key."""
    from reachy_mini_conversation_app.config import config

    missing = []
    if "openai" in backends and not (config.OPENAI_API_KEY or "").strip():
        missing.append("OPENAI_API_KEY")
    if "gemini" in backends and not (config.GEMINI_API_KEY or "").strip():
        missing.append("GEMINI_API_KEY")
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in .env before running the benchmark.")


async def main_async(args: argparse.Namespace) -> int:
    """Run the benchmark and write results."""
    from reachy_mini_conversation_app.config import set_custom_profile

    spec = json.loads(args.scenarios.read_text(encoding="utf-8"))
    scenarios = spec["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in set(args.only)]
    set_custom_profile(args.profile or spec.get("profile"))
    check_keys(args.backends)
    ensure_scenario_audio(scenarios, args.audio_dir)

    sessions: list[SessionResult] = []
    turns: list[TurnResult] = []
    for repeat in range(1, args.repeats + 1):
        # Alternate the order so neither backend always runs first.
        order = args.backends if repeat % 2 else list(reversed(args.backends))
        for backend in order:
            logger.info("=== %s — repeat %d/%d ===", backend, repeat, args.repeats)
            session, session_turns = await run_session(backend, repeat, scenarios, args.audio_dir)
            sessions.append(session)
            turns.extend(session_turns)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw_path = args.out_dir / f"benchmark-{stamp}.json"
    raw_path.write_text(
        json.dumps(
            {"sessions": [asdict(s) for s in sessions], "turns": [asdict(t) for t in turns]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_path = args.out_dir / f"benchmark-{stamp}.md"
    summary_path.write_text(summarize(sessions, turns), encoding="utf-8")
    print(f"Raw results: {raw_path}\nSummary:     {summary_path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backends", nargs="+", choices=["openai", "gemini"], default=["openai", "gemini"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--only", nargs="*", help="Run only these scenario ids")
    parser.add_argument("--profile", help="Profile override (default: the scenario file's profile)")
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("reachy_mini_conversation_app").setLevel(logging.WARNING)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
