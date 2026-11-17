"""Conversation app entry point used by the dashboard: install the memory hook, then run main.

Equivalent to ``python -m reachy_mini_conversation_app.main`` (same CLI flags),
except that ``JISU_MEMORY_BACKEND`` selects the long-term memory backend.
"""

from __future__ import annotations
import logging

from jisu.dashboard.memory.hook import install_memory_hook
from jisu.dashboard.memory.registry import selected_backend_name


logger = logging.getLogger(__name__)


def main() -> None:
    """Install the selected memory backend and hand over to the conversation app."""
    backend_name = selected_backend_name()
    install_memory_hook(backend_name)

    from reachy_mini_conversation_app.main import main as app_main

    app_main()


if __name__ == "__main__":
    main()
