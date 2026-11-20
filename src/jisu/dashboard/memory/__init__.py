"""Pluggable long-term memory backends (JSON, GBrain, ...) for the conversation app.

Add a backend by writing ``<name>_backend.py`` that implements
:class:`~jisu.dashboard.memory.base.MemoryBackend` and registering it in
:mod:`jisu.dashboard.memory.registry`.
"""

from jisu.dashboard.memory.base import MemoryBackend, BackendUnavailableError
from jisu.dashboard.memory.registry import (
    BackendInfo,
    backend_status,
    create_backend,
    register_backend,
    available_backends,
    selected_backend_name,
)


__all__ = [
    "BackendInfo",
    "BackendUnavailableError",
    "MemoryBackend",
    "available_backends",
    "backend_status",
    "create_backend",
    "register_backend",
    "selected_backend_name",
]
