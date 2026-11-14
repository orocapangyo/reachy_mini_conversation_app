"""Markdown front matter parse/serialize (§2.4 file format).

Serialization is hand-rolled so the on-disk order and style stay stable
(Obsidian shows the keys in this order). Parsing uses PyYAML ``safe_load``.
"""

from __future__ import annotations
import re
import json
from typing import Any
from datetime import date, datetime

import yaml


FENCE = "---"


class BlockList(list[Any]):
    """A list of scalars that must be written one ``- item`` per line (Obsidian shows link lists this way)."""


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NEEDS_QUOTE_CHARS = set(":#{}[],&*?|<>=!%@`\"'\\")


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value)
    if _ISO_DATE.fullmatch(text):
        return text  # YAML parses it as a date; loaders normalize back to the ISO string
    if (
        text == ""
        or text != text.strip()
        or text.lower() in {"true", "false", "null", "yes", "no", "on", "off", "~"}
        or any(ch in _NEEDS_QUOTE_CHARS for ch in text)
        or text[0] in "-?"
        or text[0].isdigit()
    ):
        return json.dumps(text, ensure_ascii=False)
    return text


def _is_block(value: Any) -> bool:
    """Return whether ``value`` must be rendered on its own indented lines."""
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, BlockList):
        return bool(value)
    if isinstance(value, list):
        return bool(value) and any(isinstance(item, (dict, list)) for item in value)
    return False


def _render_value(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if _is_block(item):
                lines.append(f"{pad}{key}:")
                lines.append(_render_value(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {_render_value(item, indent + 2)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        if not isinstance(value, BlockList) and all(not isinstance(item, (dict, list)) for item in value):
            return "[" + ", ".join(_scalar(item) for item in value) + "]"
        lines = []
        for item in value:
            if isinstance(item, dict):
                rendered = _render_value(item, indent + 2).splitlines()
                first = rendered[0].strip() if rendered else ""
                lines.append(f"{pad}- {first}")
                lines.extend(rendered[1:])
            else:
                lines.append(f"{pad}- {_render_value(item, indent + 2)}")
        return "\n".join(lines)
    return _scalar(value)


def dump_frontmatter(data: dict[str, Any]) -> str:
    """Render ``data`` as a YAML block (without fences)."""
    return _render_value(data)


def _normalize_loaded(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_loaded(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_loaded(item) for key, item in value.items()}
    return value


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (front matter dict, body).

    Dates in the YAML are returned as ISO strings, never ``date`` objects, so
    callers get one type regardless of quoting. A document without a front
    matter block yields ``({}, text)``.
    """
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith(FENCE + "\n"):
        return {}, normalized
    end = normalized.find("\n" + FENCE, len(FENCE))
    if end == -1:
        return {}, normalized
    yaml_block = normalized[len(FENCE) + 1 : end]
    body_start = end + len(FENCE) + 1
    body = normalized[body_start:]
    if body.startswith("\n"):
        body = body[1:]
    loaded = yaml.safe_load(yaml_block) if yaml_block.strip() else {}
    if not isinstance(loaded, dict):
        raise ValueError("front matter must be a mapping")
    return _normalize_loaded(loaded), body


def join_frontmatter(data: dict[str, Any], body: str) -> str:
    """Assemble a markdown document from front matter and body."""
    body = body.replace("\r\n", "\n")
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{FENCE}\n{dump_frontmatter(data)}\n{FENCE}\n{body}"
