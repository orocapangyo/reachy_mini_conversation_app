"""Route the conversation app's memory calls to a selected backend, without editing it.

``prompts.py``, ``tools/remember.py`` and ``tools/forget.py`` bind the memory
functions by name (``from ...memory import add_memory_fact``), so the hook
replaces those module attributes. Built-in tools are loaded with
``importlib.import_module`` and cached in ``sys.modules``, so the replacement
holds for the whole process. ``reachy_mini_conversation_app.memory`` itself is
left untouched so the JSON backend keeps using the real implementation.
"""

from __future__ import annotations
import logging
import importlib
import threading
from typing import Any
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from jisu.dashboard.constants import DEFAULT_MEMORY_BACKEND, FALLBACK_MEMORY_BACKEND
from jisu.dashboard.memory.base import MemoryBackend, BackendUnavailableError
from jisu.dashboard.memory.registry import create_backend, selected_backend_name
from reachy_mini_conversation_app.memory import MemoryFact, ForgetMemoryResult


logger = logging.getLogger(__name__)

# (module, attribute) call sites that use the memory store. If upstream renames
# one of these, install fails loudly instead of silently bypassing the backend.
HOOK_TARGETS: tuple[tuple[str, str], ...] = (
    ("reachy_mini_conversation_app.prompts", "format_memory_for_prompt"),
    ("reachy_mini_conversation_app.tools.remember", "add_memory_fact"),
    ("reachy_mini_conversation_app.tools.forget", "forget_memory_fact"),
)


@dataclass
class InstalledHook:
    """Handle returned by :func:`install_memory_hook`; call :meth:`uninstall` to restore."""

    backend_name: str
    originals: dict[tuple[str, str], Any]

    def uninstall(self) -> None:
        """Restore the original call-site functions."""
        for (module_name, attr), original in self.originals.items():
            setattr(importlib.import_module(module_name), attr, original)
        _set_active_resolver(None)


class _BackendResolver:
    """Creates one backend per instance path, falling back to JSON if unavailable."""

    def __init__(self, backend_name: str) -> None:
        self.backend_name = backend_name
        self._cache: dict[str, MemoryBackend] = {}
        self._lock = threading.Lock()

    def get(self, instance_path: str | Path | None) -> MemoryBackend:
        key = "" if instance_path is None else str(Path(instance_path).expanduser())
        with self._lock:
            backend = self._cache.get(key)
            if backend is None:
                backend = self._create(instance_path)
                self._cache[key] = backend
            return backend

    def _create(self, instance_path: str | Path | None) -> MemoryBackend:
        try:
            backend = create_backend(self.backend_name, instance_path)
        except (KeyError, BackendUnavailableError) as exc:
            logger.warning(
                "Memory backend %r unavailable (%s); falling back to %r.",
                self.backend_name,
                exc,
                FALLBACK_MEMORY_BACKEND,
            )
            backend = create_backend(FALLBACK_MEMORY_BACKEND, instance_path)
        logger.info("Long-term memory backend: %s", backend.name)
        return backend


_active_resolver: _BackendResolver | None = None
_active_lock = threading.Lock()


def active_backend(instance_path: str | Path | None) -> MemoryBackend:
    """Return the backend the app's memory call sites use (same instance, same json fallback).

    Tools outside the upstream package (e.g. ``update_persona``) call this so they
    write to the very store the prompt reads. Works without :func:`install_memory_hook`
    too, using ``JISU_MEMORY_BACKEND``.
    """
    global _active_resolver
    with _active_lock:
        if _active_resolver is None:
            _active_resolver = _BackendResolver(selected_backend_name() or DEFAULT_MEMORY_BACKEND)
        resolver = _active_resolver
    return resolver.get(instance_path)


def _set_active_resolver(resolver: _BackendResolver | None) -> None:
    global _active_resolver
    with _active_lock:
        _active_resolver = resolver


def _build_replacements(resolver: _BackendResolver) -> dict[str, Callable[..., Any]]:
    def format_memory_for_prompt(instance_path: str | Path | None = None) -> str:
        return resolver.get(instance_path).format_for_prompt()

    def add_memory_fact(instance_path: str | Path | None, text: str) -> MemoryFact | None:
        return resolver.get(instance_path).add_fact(text)

    def forget_memory_fact(instance_path: str | Path | None, *, query: str | None = None) -> ForgetMemoryResult:
        return resolver.get(instance_path).forget_fact(query)

    return {
        "format_memory_for_prompt": format_memory_for_prompt,
        "add_memory_fact": add_memory_fact,
        "forget_memory_fact": forget_memory_fact,
    }


def install_memory_hook(backend_name: str) -> InstalledHook:
    """Point the app's memory call sites at ``backend_name``.

    ``json`` is a no-op apart from validation (upstream behavior stays exact).
    Raises RuntimeError if a call site no longer exists upstream.
    """
    name = backend_name.strip().lower() or DEFAULT_MEMORY_BACKEND
    modules = {}
    for module_name, attr in HOOK_TARGETS:
        module = importlib.import_module(module_name)
        if not hasattr(module, attr):
            raise RuntimeError(f"memory hook target {module_name}.{attr} no longer exists; update HOOK_TARGETS")
        modules[(module_name, attr)] = module

    resolver = _BackendResolver(name)
    _set_active_resolver(resolver)
    if name == FALLBACK_MEMORY_BACKEND:
        logger.info("Long-term memory backend: json (upstream memory.py)")
        return InstalledHook(backend_name=name, originals={})

    replacements = _build_replacements(resolver)
    originals: dict[tuple[str, str], Any] = {}
    for (module_name, attr), module in modules.items():
        originals[(module_name, attr)] = getattr(module, attr)
        setattr(module, attr, replacements[attr])
    logger.info("Memory hook installed for backend %r", name)
    return InstalledHook(backend_name=name, originals=originals)
