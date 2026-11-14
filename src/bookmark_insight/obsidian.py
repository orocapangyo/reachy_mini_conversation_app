"""Obsidian vault discovery for the setup dashboard.

Insight notes add ``related`` ``[[wikilinks]]`` to their front matter (graph and
backlink edges) only when they live inside an Obsidian vault (a folder with
``.obsidian/``); see ``bookmark_insight.insight.render.note_links_enabled``. These
helpers read Obsidian's global ``obsidian.json`` to list the user's vaults and
tell whether a given path sits inside one. Pure functions, no app imports.
"""

from __future__ import annotations
import os
import sys
import json
import logging
from pathlib import Path
from dataclasses import dataclass

from bookmark_insight.settings import ENV_OBSIDIAN_CONFIG


logger = logging.getLogger(__name__)

VAULT_MARKER_DIRNAME = ".obsidian"
CONFIG_FILENAME = "obsidian.json"


@dataclass(frozen=True)
class Vault:
    """One vault registered in Obsidian's global config."""

    path: Path
    name: str
    open: bool


def find_vault(path: Path) -> Path | None:
    """Return the nearest directory at or above ``path`` that holds ``.obsidian/`` (``path`` need not exist)."""
    for candidate in (path, *path.parents):
        if (candidate / VAULT_MARKER_DIRNAME).is_dir():
            return candidate
    return None


def config_path(platform: str | None = None) -> Path | None:
    """Return Obsidian's global ``obsidian.json`` for ``platform`` (default: this OS), or None when it is missing.

    ``BOOKMARK_INSIGHT_OBSIDIAN_CONFIG`` overrides the location (portable installs, tests).
    """
    override = (os.environ.get(ENV_OBSIDIAN_CONFIG) or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        system = platform or sys.platform
        if system == "darwin":
            base = Path.home() / "Library" / "Application Support"
        elif system.startswith("win"):
            app_data = os.environ.get("APPDATA")
            base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            base = Path(xdg) if xdg else Path.home() / ".config"
        path = base / "obsidian" / CONFIG_FILENAME
    return path if path.is_file() else None


def list_vaults(config: Path | None = None) -> list[Vault]:
    """Return the existing vaults listed in ``config`` (default: ``config_path()``), open ones first, then newest."""
    source = config if config is not None else config_path()
    if source is None:
        return []
    try:
        loaded = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the Obsidian config %s: %s", source, exc)
        return []
    entries = loaded.get("vaults") if isinstance(loaded, dict) else None
    if not isinstance(entries, dict):
        return []
    ranked: list[tuple[bool, float, Vault]] = []
    for entry in entries.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = Path(entry["path"]).expanduser()
        if not path.is_dir():
            continue
        timestamp = entry.get("ts")
        is_open = bool(entry.get("open", False))
        ranked.append(
            (
                is_open,
                float(timestamp) if isinstance(timestamp, (int, float)) else 0.0,
                Vault(path, path.name, is_open),
            )
        )
    ranked.sort(key=lambda item: (not item[0], -item[1]))
    return [vault for _, _, vault in ranked]


def suggest_repo(vaults: list[Vault], default: Path, folder_name: str = "bookmarks") -> Path:
    """Return ``<first vault>/<folder_name>`` when a vault exists, else ``default``."""
    return vaults[0].path / folder_name if vaults else default
