"""What the benchmark measures: one memory store + one search path per subject.

| subject         | store                         | search                                             |
|-----------------|-------------------------------|----------------------------------------------------|
| ``json``        | upstream ``memory.v1.json``   | local keyword search (``JsonMemoryBackend.search``) |
| ``gbrain``      | GBrain, categorized           | the app's path: GBrain, then local keywords if empty |
| ``gbrain-native`` | GBrain, categorized         | GBrain's own ranking only (no fallback)            |

Every subject gets its own store under the run's work directory, so a run never
touches the user's real memory. To benchmark a new backend, add a :class:`Subject`
implementation and register it in :data:`SUBJECT_BUILDERS`.
"""

from __future__ import annotations
import time
from typing import Protocol
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from jisu.bench.memory.dataset import BenchFact, match_key
from jisu.dashboard.memory.base import BackendUnavailableError
from jisu.bench.memory.constants import BENCH_PROVENANCE
from jisu.dashboard.memory.json_backend import JsonMemoryBackend
from jisu.dashboard.memory.gbrain_backend import GBrainCallError, GBrainMemoryBackend, parse_json_object


@dataclass(frozen=True)
class SearchOutcome:
    """Texts a search returned (rank order) plus an optional note for the report."""

    texts: list[str]
    note: str = ""


class Subject(Protocol):
    """One benchmarked store + search path."""

    name: str
    categorized: bool
    # Time spent creating the store (e.g. ``gbrain init``), in ms.
    setup_ms: float

    def add(self, fact: BenchFact) -> bool:
        """Store one fact; False if nothing was saved."""
        ...

    def search(self, query: str, k: int) -> SearchOutcome:
        """Return up to ``k`` results for ``query``."""
        ...

    def format_prompt(self) -> str:
        """Build the session prompt fragment (measured for latency)."""
        ...

    def stored_categories(self) -> dict[str, str]:
        """Map stored text (see :func:`match_key`) to category key; {} if the store has no categories."""
        ...


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


class JsonSubject:
    """The upstream JSON store with its keyword search."""

    categorized = False

    def __init__(self, workdir: Path, name: str = "json") -> None:
        """Create an empty store under ``workdir``."""
        started = time.perf_counter()
        self.name = name
        self._backend = JsonMemoryBackend(workdir)
        self.setup_ms = _elapsed_ms(started)

    def add(self, fact: BenchFact) -> bool:
        """Store the fact text (JSON has no categories)."""
        return self._backend.add_fact(fact.text) is not None

    def search(self, query: str, k: int) -> SearchOutcome:
        """Local keyword search."""
        return SearchOutcome([fact.text for fact in self._backend.search(query, k)])

    def format_prompt(self) -> str:
        """Upstream prompt fragment."""
        return self._backend.format_for_prompt()

    def stored_categories(self) -> dict[str, str]:
        """JSON stores no categories."""
        return {}


GBrainFactory = Callable[[Path], GBrainMemoryBackend]


def create_gbrain(home: Path) -> GBrainMemoryBackend:
    """Create and initialize a fresh, isolated brain at ``home`` (raises BackendUnavailableError)."""
    brain = GBrainMemoryBackend(home=home, require_ready=False)
    if not brain.binary_available():
        raise BackendUnavailableError(brain.readiness_problem() or "gbrain not found")
    try:
        brain.initialize()
    except GBrainCallError as exc:
        raise BackendUnavailableError(_short_gbrain_error(str(exc))) from exc
    return brain


def _short_gbrain_error(text: str) -> str:
    """One line from a GBrain failure: its JSON envelope's message, else the first line."""
    payload = parse_json_object(text)
    if payload and payload.get("message"):
        code = payload.get("code") or payload.get("error") or "error"
        return f"gbrain init failed ({code}): {payload['message']}"
    return text.strip().splitlines()[0][:300] if text.strip() else "gbrain init failed"


class GBrainSubject:
    """A dedicated GBrain brain; ``native=True`` disables the app's local keyword fallback."""

    categorized = True

    def __init__(self, workdir: Path, *, native: bool, factory: GBrainFactory = create_gbrain) -> None:
        """Create the brain under ``workdir`` (raises BackendUnavailableError)."""
        self.name = "gbrain-native" if native else "gbrain"
        self._native = native
        started = time.perf_counter()
        self.backend = factory(workdir / "gbrain")
        self.setup_ms = _elapsed_ms(started)

    def add(self, fact: BenchFact) -> bool:
        """Store the fact under its category (GBrain entity + kind)."""
        return self.backend.add_categorized(fact.text, fact.category, provenance=BENCH_PROVENANCE) is not None

    def search(self, query: str, k: int) -> SearchOutcome:
        """GBrain ranking; the app path falls back to local keywords when GBrain finds nothing."""
        if not self._native:
            return SearchOutcome([item.fact.text for item in self.backend.search_categorized(query, k)])
        try:
            found = self.backend.search_native(query, k)
        except GBrainCallError as exc:
            return SearchOutcome([], note=f"error: {exc}"[:200])
        note = f"degraded: {found.degraded_reason}" if found.degraded_reason else ""
        return SearchOutcome([item.fact.text for item in found.facts], note=note)

    def format_prompt(self) -> str:
        """Sectioned persona/user prompt."""
        return self.backend.format_for_prompt()

    def stored_categories(self) -> dict[str, str]:
        """Categories as GBrain reports them back."""
        return {match_key(item.fact.text): item.category.key for item in self.backend.list_categorized()}


SubjectBuilder = Callable[[Path, GBrainFactory], Subject]

SUBJECT_BUILDERS: dict[str, SubjectBuilder] = {
    "json": lambda workdir, _factory: JsonSubject(workdir),
    "gbrain": lambda workdir, factory: GBrainSubject(workdir, native=False, factory=factory),
    "gbrain-native": lambda workdir, factory: GBrainSubject(workdir, native=True, factory=factory),
}


def build_subject(name: str, workdir: Path, factory: GBrainFactory = create_gbrain) -> Subject:
    """Create subject ``name`` in its own ``workdir`` (KeyError if unknown, BackendUnavailableError if unusable)."""
    try:
        builder = SUBJECT_BUILDERS[name]
    except KeyError:
        known = ", ".join(SUBJECT_BUILDERS)
        raise KeyError(f"unknown subject {name!r} (known: {known})") from None
    workdir.mkdir(parents=True, exist_ok=True)
    return builder(workdir, factory)
