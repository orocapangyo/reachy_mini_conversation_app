"""Name -> factory registry for memory backends.

To add a backend (SQLite, ontology, ...)::

    from jisu.dashboard.memory.registry import register_backend
    register_backend("sqlite", "SQLite", lambda instance_path: SqliteMemoryBackend(instance_path))

and import that module from :func:`_register_builtin_backends`. It then shows up
in the dashboard's backend selector and is selectable with ``JISU_MEMORY_BACKEND``.
"""

from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from jisu.dashboard.constants import MEMORY_BACKEND_ENV, DEFAULT_MEMORY_BACKEND
from jisu.dashboard.memory.base import MemoryBackend, BackendUnavailableError


BackendFactory = Callable[[str | Path | None], MemoryBackend]


@dataclass(frozen=True)
class BackendInfo:
    """Registered backend metadata."""

    name: str
    label: str
    factory: BackendFactory


_REGISTRY: dict[str, BackendInfo] = {}


def register_backend(name: str, label: str, factory: BackendFactory, *, replace: bool = False) -> None:
    """Register a backend factory under ``name`` (lowercase identifier)."""
    key = name.strip().lower()
    if not key:
        raise ValueError("backend name must not be empty")
    if key in _REGISTRY and not replace:
        raise ValueError(f"memory backend {key!r} is already registered")
    _REGISTRY[key] = BackendInfo(name=key, label=label, factory=factory)


def available_backends() -> list[BackendInfo]:
    """Return registered backends in registration order."""
    _register_builtin_backends()
    return list(_REGISTRY.values())


def create_backend(name: str, instance_path: str | Path | None = None) -> MemoryBackend:
    """Instantiate a backend; raise KeyError (unknown) or BackendUnavailableError."""
    _register_builtin_backends()
    key = name.strip().lower()
    info = _REGISTRY.get(key)
    if info is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown memory backend {name!r} (known: {known})")
    return info.factory(instance_path)


def backend_status(name: str, instance_path: str | Path | None = None) -> tuple[bool, str]:
    """Return ``(usable, detail)`` for a backend without raising."""
    try:
        create_backend(name, instance_path)
    except KeyError as exc:
        return False, str(exc.args[0]) if exc.args else str(exc)
    except BackendUnavailableError as exc:
        return False, str(exc)
    return True, "ready"


def selected_backend_name() -> str:
    """Return the backend chosen through ``JISU_MEMORY_BACKEND`` (default json)."""
    return (os.getenv(MEMORY_BACKEND_ENV, "") or DEFAULT_MEMORY_BACKEND).strip().lower()


_BUILTINS_REGISTERED = False


def _register_builtin_backends() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    _BUILTINS_REGISTERED = True

    from jisu.dashboard.memory.json_backend import JsonMemoryBackend
    from jisu.dashboard.memory.gbrain_backend import GBrainMemoryBackend

    register_backend("json", "JSON (기본 memory.v1.json)", JsonMemoryBackend, replace=True)
    register_backend("gbrain", "GBrain (로컬 PGLite)", GBrainMemoryBackend, replace=True)
