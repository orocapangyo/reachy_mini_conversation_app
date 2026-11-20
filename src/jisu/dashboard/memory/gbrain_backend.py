"""GBrain backend: long-term memory through the GBrain CLI memory verbs.

Uses ``gbrain call <op> '<json>'`` (the operation contract shared by the CLI and
MCP, see ``vendor/gbrain/docs/protocol/MEMORY_VERBS_v1.md``):

- ``remember {fact, provenance, kind, entity}`` -> ``{id, status, entity_slug}``
- ``recall {limit}`` / ``recall {question, limit}`` -> ``{facts: [{fact_id, fact, kind, entity_slug, created_at, ...}]}``
- ``forget {id, reason}``

Categories (:mod:`jisu.persona.categories`) map onto GBrain's own ``entity`` +
``kind`` fields, so a recalled fact carries its category without extra storage.
Facts saved before categories existed have no entity and read back as user facts.

Each instance gets its own brain under ``GBRAIN_HOME=<data dir>/gbrain`` (GBrain
appends ``.gbrain``), initialized keyless with PGLite (``init --pglite
--no-embedding``), so it never touches the user's personal ``~/.gbrain``.

Note: GBrain ``forget`` withdraws a claim; remembering the exact same text again
is refused (``fact_withdrawn``) and ``add_fact`` then returns None.
"""

from __future__ import annotations
import os
import json
import time
import shutil
import logging
import subprocess
from typing import Any
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Sequence

from jisu.persona.prompt import format_categorized_prompt
from jisu.persona.constants import CATEGORIZED_LIST_LIMIT
from jisu.persona.categories import DEFAULT_CATEGORY_KEY, CategorizedFact, category_for, get_category
from jisu.dashboard.constants import (
    GBRAIN_BIN_ENV,
    BUN_INSTALL_ENV,
    GBRAIN_HOME_ENV,
    GBRAIN_PROVENANCE,
    DEFAULT_GBRAIN_BIN,
    GBRAIN_SEARCH_MODE,
    GBRAIN_TIMEOUT_ENV,
    GBRAIN_HOME_DIRNAME,
    GBRAIN_FORGET_REASON,
    GBRAIN_INIT_TIMEOUT_S,
    DEFAULT_GBRAIN_TIMEOUT_S,
)
from jisu.dashboard.memory.base import (
    BackendUnavailableError,
    keyword_search,
    substring_candidates,
)
from reachy_mini_conversation_app.memory import (
    MAX_FACTS,
    MemoryFact,
    ForgetMemoryResult,
    normalize_memory_text,
    memory_path_for_instance,
)


logger = logging.getLogger(__name__)

# Env vars that would redirect GBrain to a shared Postgres instead of the local PGLite brain.
_ISOLATION_DROPPED_ENV = ("GBRAIN_DATABASE_URL", "DATABASE_URL", "GBRAIN_BRAIN_ID", "GBRAIN_SOURCE")


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one CLI invocation."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Mapping[str, str], float], CommandResult]


@dataclass(frozen=True)
class NativeSearch:
    """GBrain's own ranking for one question, without the local fallback."""

    facts: list[CategorizedFact]
    # ``facts_degraded.reason`` from GBrain (e.g. ``no_query_terms``), "" when not degraded.
    degraded_reason: str = ""


class GBrainCallError(RuntimeError):
    """Raised when a GBrain CLI call fails or returns an error envelope."""


def run_command(args: Sequence[str], env: Mapping[str, str], timeout_s: float) -> CommandResult:
    """Run a command and capture its output (default :data:`CommandRunner`)."""
    completed = subprocess.run(
        list(args),
        env=dict(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text`` (tolerates notice lines around it)."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    index = stripped.find("{")
    while index != -1:
        try:
            candidate, _end = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError:
            index = stripped.find("{", index + 1)
            continue
        if isinstance(candidate, dict):
            return candidate
        index = stripped.find("{", index + 1)
    return None


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Seconds vs milliseconds heuristic: anything before 2001 in ms is really seconds.
        return int(value * 1000) if value < 1e12 else int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(datetime.fromisoformat(value.strip()).timestamp() * 1000)
        except ValueError:
            return None
    return None


def fact_from_wire(value: object) -> MemoryFact | None:
    """Convert one GBrain recall fact into a :class:`MemoryFact`."""
    if not isinstance(value, Mapping):
        return None
    text = value.get("fact", value.get("text"))
    fact_id = value.get("fact_id", value.get("id"))
    if not isinstance(text, str) or fact_id is None or isinstance(fact_id, bool):
        return None
    normalized = normalize_memory_text(text)
    if not normalized:
        return None
    created_at = _timestamp_ms(value.get("created_at"))
    if created_at is None:
        created_at = _timestamp_ms(value.get("valid_from"))
    return MemoryFact(id=str(fact_id), text=normalized, created_at=created_at or 0)


def categorized_from_wire(value: object) -> CategorizedFact | None:
    """Convert one GBrain recall fact into a :class:`CategorizedFact` (category from entity_slug + kind)."""
    fact = fact_from_wire(value)
    if fact is None or not isinstance(value, Mapping):
        return None
    entity, kind = value.get("entity_slug"), value.get("kind")
    return CategorizedFact(
        fact=fact,
        category=category_for(entity if isinstance(entity, str) else None, kind if isinstance(kind, str) else None),
    )


def _categorized_keyword_search(facts: Sequence[CategorizedFact], query: str, limit: int) -> list[CategorizedFact]:
    by_id = {item.fact.id: item for item in facts}
    return [by_id[fact.id] for fact in keyword_search([item.fact for item in facts], query, limit)]


def default_gbrain_home(instance_path: str | Path | None) -> Path:
    """Return the GBRAIN_HOME used for this app instance."""
    override = os.getenv(GBRAIN_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (memory_path_for_instance(instance_path).parent / GBRAIN_HOME_DIRNAME).resolve()


def bun_bin_dir() -> Path:
    """Return Bun's global bin directory (``$BUN_INSTALL/bin``, default ``~/.bun/bin``)."""
    root = os.getenv(BUN_INSTALL_ENV, "").strip()
    return (Path(root).expanduser() if root else Path.home() / ".bun") / "bin"


def resolve_gbrain_binary(configured: str) -> str:
    """Find gbrain on PATH, else in Bun's bin dir (PATH is often only set in ``.zshrc``)."""
    found = shutil.which(configured)
    if found:
        return found
    if "/" not in configured and "\\" not in configured:
        for name in (configured, f"{configured}.exe", f"{configured}.cmd"):
            candidate = bun_bin_dir() / name
            if candidate.is_file():
                return str(candidate)
    return configured


def _error_detail(payload: Mapping[str, Any]) -> str:
    """Render a GBrain error envelope (``{error, code, message}``) as ``code: message``."""
    code = payload.get("code") or payload.get("error")
    message = payload.get("message")
    if message:
        return f"{code}: {message}" if code else str(message)
    return str(code)


def _default_timeout_s() -> float:
    raw = os.getenv(GBRAIN_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_GBRAIN_TIMEOUT_S
    except ValueError:
        return DEFAULT_GBRAIN_TIMEOUT_S
    return value if value > 0 else DEFAULT_GBRAIN_TIMEOUT_S


class GBrainMemoryBackend:
    """Long-term memory stored in a dedicated local GBrain brain."""

    name = "gbrain"

    def __init__(
        self,
        instance_path: str | Path | None = None,
        *,
        runner: CommandRunner | None = None,
        binary: str | None = None,
        home: Path | None = None,
        timeout_s: float | None = None,
        require_ready: bool = True,
    ) -> None:
        """Bind to one brain; raise :class:`BackendUnavailableError` if it is not usable.

        ``require_ready=False`` skips the binary/initialization checks (used to run
        :meth:`initialize` from the dashboard).
        """
        self._runner: CommandRunner = runner or run_command
        configured = binary or os.getenv(GBRAIN_BIN_ENV, "").strip() or DEFAULT_GBRAIN_BIN
        self._binary = configured if runner is not None else resolve_gbrain_binary(configured)
        self.home = home or default_gbrain_home(instance_path)
        self._timeout_s = timeout_s if timeout_s is not None else _default_timeout_s()

        if require_ready:
            problem = self.readiness_problem()
            if problem is not None:
                raise BackendUnavailableError(problem)

    # -- readiness ---------------------------------------------------------

    @property
    def config_path(self) -> Path:
        """Path of the brain's config file (exists once ``gbrain init`` ran)."""
        return self.home / ".gbrain" / "config.json"

    def binary_available(self) -> bool:
        """Return whether the gbrain executable can be found."""
        return Path(self._binary).is_file() or shutil.which(self._binary) is not None

    def is_initialized(self) -> bool:
        """Return whether this brain was initialized."""
        return self.config_path.is_file()

    def readiness_problem(self) -> str | None:
        """Explain why the backend cannot run yet, or None when it is ready."""
        if not self.binary_available():
            return (
                f"gbrain executable not found ({self._binary!r}). Install Bun, then run "
                "`bun install && bun link` in vendor/gbrain, or set JISU_GBRAIN_BIN."
            )
        if not self.is_initialized():
            return f"GBrain brain not initialized at {self.home}. Use the dashboard's 'GBrain 초기화' button."
        return None

    def version(self) -> str:
        """Return ``gbrain --version`` output ("" if it cannot run)."""
        try:
            result = self._runner([self._binary, "--version"], self._env(), self._timeout_s)
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def initialize(self) -> str:
        """Create the keyless PGLite brain (idempotent). Return the CLI output."""
        self.home.mkdir(parents=True, exist_ok=True)
        result = self._runner(
            [
                self._binary,
                "init",
                "--pglite",
                "--no-embedding",
                "--search-mode",
                GBRAIN_SEARCH_MODE,
                "--non-interactive",
                "--json",
            ],
            self._env(),
            GBRAIN_INIT_TIMEOUT_S,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            raise GBrainCallError(f"gbrain init failed (exit {result.returncode}): {output[-2000:]}")
        return output

    # -- CLI plumbing ------------------------------------------------------

    def _env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in _ISOLATION_DROPPED_ENV}
        env["GBRAIN_HOME"] = str(self.home)
        # gbrain is a `#!/usr/bin/env bun` script: make sure bun resolves even when the
        # dashboard was started from a shell whose PATH lacks ~/.bun/bin.
        extra_dirs = [str(bun_bin_dir())]
        if os.path.isabs(self._binary):
            extra_dirs.insert(0, os.path.dirname(self._binary))
        path_parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
        for directory in reversed(extra_dirs):
            if directory not in path_parts:
                path_parts.insert(0, directory)
        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def call(self, op: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Invoke one GBrain operation and return its JSON result."""
        args = [self._binary, "call", op, json.dumps(dict(params), ensure_ascii=False)]
        try:
            result = self._runner(args, self._env(), self._timeout_s)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GBrainCallError(f"gbrain {op} could not run: {exc}") from exc

        payload = parse_json_object(result.stdout)
        if result.returncode != 0:
            if payload and payload.get("error"):
                detail = _error_detail(payload)
            else:
                detail = result.stderr.strip() or result.stdout.strip()
            raise GBrainCallError(f"gbrain {op} failed (exit {result.returncode}): {detail[:500]}")
        if payload is None:
            raise GBrainCallError(f"gbrain {op} returned no JSON: {result.stdout.strip()[:500]}")
        if payload.get("error"):
            raise GBrainCallError(f"gbrain {op} error: {_error_detail(payload)[:500]}")
        return payload

    @staticmethod
    def _facts_from_payload(payload: Mapping[str, Any]) -> list[CategorizedFact]:
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, list):
            return []
        facts: list[CategorizedFact] = []
        for item in raw_facts:
            fact = categorized_from_wire(item)
            if fact is not None:
                facts.append(fact)
        return facts

    def _recall_categorized(self, params: Mapping[str, Any]) -> list[CategorizedFact]:
        return self._facts_from_payload(self.call("recall", params))

    def search_native(self, query: str, limit: int) -> NativeSearch:
        """GBrain's own relevance ranking only (no local fallback); raises :class:`GBrainCallError`.

        Used by the memory benchmark to measure what the fallback adds.
        """
        question = normalize_memory_text(query)
        if not question or limit <= 0:
            return NativeSearch(facts=[])
        payload = self.call("recall", {"question": question, "limit": limit})
        degraded = payload.get("facts_degraded")
        reason = degraded.get("reason") if isinstance(degraded, Mapping) else None
        return NativeSearch(facts=self._facts_from_payload(payload), degraded_reason=str(reason or ""))

    def _recall(self, params: Mapping[str, Any]) -> list[MemoryFact]:
        return [item.fact for item in self._recall_categorized(params)]

    # -- MemoryBackend -----------------------------------------------------

    def list_facts_strict(self) -> list[MemoryFact]:
        """Like :meth:`list_facts` but raise :class:`GBrainCallError` on failure."""
        return self._recall({"limit": MAX_FACTS})

    def list_facts(self) -> list[MemoryFact]:
        """Return active facts, newest first (empty on failure)."""
        try:
            return self.list_facts_strict()
        except GBrainCallError as exc:
            logger.warning("GBrain list failed: %s", exc)
            return []

    def add_fact(self, text: str) -> MemoryFact | None:
        """Remember one user fact (the upstream remember tool); None when empty, refused or on failure."""
        stored = self.add_categorized(text, DEFAULT_CATEGORY_KEY)
        return stored.fact if stored is not None else None

    def forget_fact(self, query: str | None) -> ForgetMemoryResult:
        """Withdraw the first fact containing ``query`` (same matching as the JSON store)."""
        candidates = substring_candidates(self.list_facts(), query)
        if not candidates:
            return ForgetMemoryResult(removed=None, candidates=())
        removed = candidates[0]
        try:
            self.call("forget", {"id": removed.id, "reason": GBRAIN_FORGET_REASON})
        except GBrainCallError as exc:
            logger.warning("GBrain forget failed for %s: %s", removed.id, exc)
            return ForgetMemoryResult(removed=None, candidates=())
        return ForgetMemoryResult(removed=removed, candidates=candidates)

    def clear(self) -> None:
        """Withdraw every active fact (raises on the first failure)."""
        for fact in self.list_facts_strict():
            self.call("forget", {"id": fact.id, "reason": GBRAIN_FORGET_REASON})

    def search(self, query: str, limit: int) -> list[MemoryFact]:
        """Relevance-ranked recall, with a local substring fallback.

        Without embeddings GBrain ranks facts by whole-word term share and drops
        query words shorter than 3 characters (``search/fact-relevance.ts``), so
        most Korean queries ("매운 음식") return nothing. When GBrain finds no
        fact, fall back to :func:`keyword_search` over the stored facts.
        """
        question = normalize_memory_text(query)
        if not question or limit <= 0:
            return []
        try:
            ranked = self._recall({"question": question, "limit": limit})
        except GBrainCallError as exc:
            logger.warning("GBrain search failed: %s", exc)
            return []
        if ranked:
            return ranked
        return keyword_search(self.list_facts(), question, limit)

    def format_for_prompt(self) -> str:
        """Render the facts as category sections (persona, promises, user)."""
        return format_categorized_prompt(self.list_categorized())

    # -- CategorizedMemory -------------------------------------------------

    def list_categorized_strict(self) -> list[CategorizedFact]:
        """Like :meth:`list_categorized` but raise :class:`GBrainCallError` on failure."""
        return self._recall_categorized({"limit": CATEGORIZED_LIST_LIMIT})

    def list_categorized(self) -> list[CategorizedFact]:
        """Return active facts of every category, newest first (empty on failure)."""
        try:
            return self.list_categorized_strict()
        except GBrainCallError as exc:
            logger.warning("GBrain list failed: %s", exc)
            return []

    def add_categorized(self, text: str, category: str, *, provenance: str | None = None) -> CategorizedFact | None:
        """Remember one fact under ``category`` (GBrain entity + kind); None when empty, refused or on failure."""
        target = get_category(category)
        normalized = normalize_memory_text(text)
        if not normalized:
            return None
        params = {
            "fact": normalized,
            "provenance": provenance or GBRAIN_PROVENANCE,
            "kind": target.kind,
            "entity": target.entity,
        }
        try:
            payload = self.call("remember", params)
        except GBrainCallError as exc:
            logger.warning("GBrain remember failed: %s", exc)
            return None
        fact_id = payload.get("id")
        if fact_id is None:
            logger.warning("GBrain remember returned no id: %s", payload)
            return None
        fact = MemoryFact(id=str(fact_id), text=normalized, created_at=int(time.time() * 1000))
        return CategorizedFact(fact=fact, category=target, duplicate=payload.get("status") == "duplicate")

    def forget_by_id(self, fact_id: str) -> MemoryFact | None:
        """Withdraw the active fact with this id; None if there is none or the call fails."""
        wanted = str(fact_id).strip()
        match = next((item.fact for item in self.list_categorized() if item.fact.id == wanted), None)
        if match is None:
            return None
        try:
            self.call("forget", {"id": match.id, "reason": GBRAIN_FORGET_REASON})
        except GBrainCallError as exc:
            logger.warning("GBrain forget failed for %s: %s", match.id, exc)
            return None
        return match

    def search_categorized(self, query: str, limit: int) -> list[CategorizedFact]:
        """Relevance-ranked recall with categories, with the same local fallback as :meth:`search`."""
        question = normalize_memory_text(query)
        if not question or limit <= 0:
            return []
        try:
            ranked = self._recall_categorized({"question": question, "limit": limit})
        except GBrainCallError as exc:
            logger.warning("GBrain search failed: %s", exc)
            return []
        if ranked:
            return ranked
        return _categorized_keyword_search(self.list_categorized(), question, limit)


def ensure_brain_initialized(
    instance_path: str | Path | None = None,
    *,
    brain: GBrainMemoryBackend | None = None,
) -> str | None:
    """Initialize the brain once when gbrain is installed but has no brain yet.

    Never raises: returns a log line describing what happened, or None when the
    brain was already initialized. If gbrain is missing or init fails, the app's
    memory hook falls back to json as before.
    """
    brain = brain or GBrainMemoryBackend(instance_path, require_ready=False)
    if brain.is_initialized():
        return None
    if not brain.binary_available():
        return f"WARNING: {brain.readiness_problem()} Falling back to json."
    try:
        brain.initialize()
    except GBrainCallError as exc:
        return f"ERROR: {exc} Falling back to json."
    if not brain.is_initialized():
        return f"WARNING: gbrain init finished but no brain config at {brain.config_path}. Falling back to json."
    return f"INFO: GBrain initialized at {brain.home}"
