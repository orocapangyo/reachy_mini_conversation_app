"""LLM tools that live outside the upstream package.

The dashboard launcher points the conversation app's external-tool loader at this
folder (``REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`` + ``AUTOLOAD_EXTERNAL_TOOLS=1``).
The loader skips ``__init__.py`` and reads every other ``*.py`` here as one tool.
"""

from pathlib import Path


TOOLS_DIR = Path(__file__).parent
