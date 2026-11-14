"""``## 요약`` section handling inside a bookmark body (§2.4 쓰기 소유권 규칙).

The summarizer owns exactly one section of the body. Everything else (user
notes below it, other headings) is preserved verbatim.
"""

from __future__ import annotations
import re

from bookmark_insight.models import SUMMARY_HEADING, FETCH_FAILED_SUMMARY_TEXT


_SECTION_BOUNDARY_RE = re.compile(r"^#{1,2} ")


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return ``(start, end)`` line indexes of the ``## 요약`` section (end exclusive), or ``None``."""
    start = next((index for index, line in enumerate(lines) if line.rstrip() == SUMMARY_HEADING), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _SECTION_BOUNDARY_RE.match(lines[index]):
            end = index
            break
    return start, end


def summary_text(body: str) -> str | None:
    """Return the text under ``## 요약`` (stripped), or ``None`` when the section is missing or empty."""
    lines = body.replace("\r\n", "\n").split("\n")
    bounds = _section_bounds(lines)
    if bounds is None:
        return None
    text = "\n".join(lines[bounds[0] + 1 : bounds[1]]).strip()
    return text or None


def has_real_summary(body: str) -> bool:
    """Return whether ``## 요약`` holds something other than the fetch-failed placeholder."""
    text = summary_text(body)
    return text is not None and text != FETCH_FAILED_SUMMARY_TEXT


def replace_summary_section(body: str, text: str) -> str:
    """Return ``body`` with the ``## 요약`` section set to ``text`` (added at the top when absent)."""
    section = f"{SUMMARY_HEADING}\n{text.strip()}\n"
    normalized = body.replace("\r\n", "\n")
    lines = normalized.split("\n")
    bounds = _section_bounds(lines)
    if bounds is None:
        rest = normalized.lstrip("\n")
        return section if not rest.strip() else f"{section}\n{rest}"
    start, end = bounds
    before = "\n".join(lines[:start])
    after = "\n".join(lines[end:]).lstrip("\n")
    result = section
    if before.strip():
        result = before.rstrip("\n") + "\n\n" + result
    if after.strip():
        result += "\n" + after
    return result if result.endswith("\n") else result + "\n"
