"""Insight note body template (§2.5 step 4).

The body is rendered from the JSON-schema fields only; LLM text appears in
exactly four places per cluster (topic, summary, 관계, 질문) and is flattened
to one line so it can never open a new markdown section.

Bookmark lines are plain markdown web links (``- [title](url)``) so the note
reads the same in any markdown viewer. Obsidian ``[[wikilinks]]`` to the
bookmark files go to the front matter ``related`` list instead (graph and
backlink edges), and only when the note sits inside an Obsidian vault
(``note_links_enabled``).
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass

from bookmark_insight.store import nfc
from bookmark_insight.models import Bookmark, InsightNote
from bookmark_insight.obsidian import find_vault
from bookmark_insight.settings import NOTE_LINKS_ON, NOTE_LINKS_OFF, BookmarkInsightSettings


NOTE_TITLE_PREFIX = "# 북마크 인사이트"
UNAVAILABLE_HEADING = "## 본문 미확보"
PENDING_HEADING = "## 미요약"  # M7 partial note: bookmarks the batch has not summarized yet
PENDING_LINE = "아직 요약하지 않은 북마크 {count}건"
PENDING_MAX_TITLES = 10  # titles listed under the heading; the rest is "… 외 N건"
CORRELATION_LABEL = "**관계**"
QUESTION_LABEL = "**질문**"
_URL_NEEDS_ANGLE_BRACKETS = frozenset(" ()")  # a bare markdown URL ends at these; ``<url>`` keeps it whole


@dataclass(frozen=True)
class LinkTarget:
    """What a bookmark line and a ``related`` entry need from one bookmark."""

    title: str
    url: str
    stem: str | None  # file name without ``.md`` once loaded from disk; None = not on disk (no [[link]])


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _md_link_text(title: str) -> str:
    """Flatten ``title`` and escape the brackets that would end the link text."""
    return _one_line(title).replace("[", "\\[").replace("]", "\\]")


def _md_url(url: str) -> str:
    """Return ``url`` as a markdown link destination (angle brackets when a space or parenthesis would cut it)."""
    url = url.strip()
    return f"<{url}>" if any(ch in _URL_NEEDS_ANGLE_BRACKETS for ch in url) else url


def link_targets(bookmarks: list[Bookmark]) -> dict[str, LinkTarget]:
    """Map guid → title, url and Obsidian link stem (None when the bookmark has no file on disk)."""
    targets: dict[str, LinkTarget] = {}
    for bookmark in bookmarks:
        stem = nfc(Path(bookmark.path).stem) if bookmark.path is not None else None
        targets[bookmark.guid] = LinkTarget(title=bookmark.title, url=bookmark.url, stem=stem)
    return targets


def _link_line(bookmark_id: str, targets: dict[str, LinkTarget]) -> str:
    target = targets.get(bookmark_id)
    if target is None:
        return f"- ({bookmark_id})"
    text = _md_link_text(target.title) or target.url.strip() or bookmark_id
    if not target.url.strip():
        return f"- {text}"
    return f"- [{text}]({_md_url(target.url)})"


def related_links(bookmarks: list[Bookmark]) -> list[str]:
    """``"[[<stem>]]"`` for every bookmark that has a file on disk, in the given order, without duplicates."""
    links: list[str] = []
    for target in link_targets(bookmarks).values():
        if target.stem is not None and (link := f"[[{target.stem}]]") not in links:
            links.append(link)
    return links


def note_links_enabled(settings: BookmarkInsightSettings) -> bool:
    """Whether notes get ``related`` Obsidian links: ``on``/``off`` force it; ``auto`` = the note folder is inside a vault.

    ``auto`` probes the insights dir when ``BOOKMARK_INSIGHT_INSIGHTS_DIR`` is set
    (that is where the notes live), otherwise the bookmark repo.
    """
    if settings.note_links == NOTE_LINKS_ON:
        return True
    if settings.note_links == NOTE_LINKS_OFF:
        return False
    probe = settings.note_links_probe_dir
    return probe is not None and find_vault(probe) is not None


def render_pending_section(note: InsightNote, bookmarks: list[Bookmark]) -> list[str]:
    """Lines of the ``## 미요약`` section of a partial note (empty list when nothing is pending)."""
    if not note.pending:
        return []
    titles = {bookmark.guid: bookmark.title for bookmark in bookmarks}
    lines = ["", PENDING_HEADING, PENDING_LINE.format(count=len(note.pending))]
    shown = note.pending[:PENDING_MAX_TITLES]
    lines.extend(f"- {_one_line(titles.get(bookmark_id) or '') or f'({bookmark_id})'}" for bookmark_id in shown)
    rest = len(note.pending) - len(shown)
    if rest > 0:
        lines.append(f"- … 외 {rest}건")
    return lines


def render_note_body(note: InsightNote, bookmarks: list[Bookmark]) -> str:
    """Render the human-readable body (clusters, correlations, questions, ``- [title](url)`` bookmark lines).

    A partial note (M7) adds ``## 미요약`` with the not-yet-summarized titles
    before ``## 본문 미확보``. Ids without a bookmark degrade to ``- (id)``.
    """
    targets = link_targets(bookmarks)
    lines: list[str] = [f"{NOTE_TITLE_PREFIX} ({note.since.isoformat()} ~ {note.until.isoformat()})"]
    for cluster in note.clusters:
        lines.append("")
        lines.append(f"## {_one_line(cluster.topic) or '(주제 없음)'}")
        lines.append(_one_line(cluster.summary))
        lines.append("")
        lines.extend(_link_line(bookmark_id, targets) for bookmark_id in cluster.bookmark_ids)
        lines.append("")
        lines.append(f"{CORRELATION_LABEL}: {_one_line(cluster.correlation)}")
        lines.append("")
        lines.append(f"{QUESTION_LABEL}: {_one_line(cluster.question)}")
    lines.extend(render_pending_section(note, bookmarks))
    if note.unavailable:
        lines.append("")
        lines.append(UNAVAILABLE_HEADING)
        lines.extend(_link_line(bookmark_id, targets) for bookmark_id in note.unavailable)
    return "\n".join(lines) + "\n"
