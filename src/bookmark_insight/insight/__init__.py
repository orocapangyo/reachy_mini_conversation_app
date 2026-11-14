"""Track B (M4): summaries → clusters → insight note (§2.5 steps 3–4) and the note lifecycle.

Public entry points:

    build_insight(settings, llm, profile, period, *, now=None, client=None) -> InsightNote
    load_insight(settings, profile, period) -> tuple[InsightNote, str] | None
    render_note_body(note, bookmarks) -> str

``build_insight`` runs the whole pipeline for one profile and period:
sync is NOT part of it (callers run ``bookmark_insight.sync.sync_profiles`` first),
then ``bookmark_insight.summarize.summarize_bookmarks`` for bookmarks in the period,
then clustering via ``llm.cluster`` and note rendering. It preserves existing
``## 대화 기록`` sections when regenerating a stale note.

Submodules: ``render`` (the markdown template), ``builder`` (the batch).
"""

from __future__ import annotations

from bookmark_insight.insight.render import render_note_body
from bookmark_insight.insight.builder import ClusterUnavailableError, load_insight, build_insight


__all__ = ["ClusterUnavailableError", "build_insight", "load_insight", "render_note_body"]
