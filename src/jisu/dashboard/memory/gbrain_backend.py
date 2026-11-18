"""GBrain backend: long-term memory through the GBrain CLI memory verbs.

Uses ``gbrain call <op> '<json>'`` (the operation contract shared by the CLI and
MCP, see ``vendor/gbrain/docs/protocol/MEMORY_VERBS_v1.md``):

- ``remember {fact, provenance, kind}`` -> ``{id, status}``
- ``recall {limit}`` / ``recall {question, limit}`` -> ``{facts: [{fact_id, fact, created_at, ...}]}``
- ``forget {id, reason}``

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
    format_facts_for_prompt,
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

    def _recall(self, params: Mapping[str, Any]) -> list[MemoryFact]:
        payload = self.call("recall", params)
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, list):
            return []
        facts: list[MemoryFact] = []
        for item in raw_facts:
            fact = fact_from_wire(item)
            if fact is not None:
                facts.append(fact)
        return facts

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
        """Remember one fact; None when empty, refused or on failure."""
        normalized = normalize_memory_text(text)
        if not normalized:
            return None
        try:
            payload = self.call(
                "remember",
                {"fact": normalized, "provenance": GBRAIN_PROVENANCE, "kind": "fact"},
            )
        except GBrainCallError as exc:
            logger.warning("GBrain remember failed: %s", exc)
            return None
        fact_id = payload.get("id")
        if fact_id is None:
            logger.warning("GBrain remember returned no id: %s", payload)
            return None
        return MemoryFact(id=str(fact_id), text=normalized, created_at=int(time.time() * 1000))

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
        """Render the newest facts with the upstream prompt header."""
        return format_facts_for_prompt(self.list_facts())


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
