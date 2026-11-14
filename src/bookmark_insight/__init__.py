"""Bookmark Insight: bookmark sync, summarization and bookmark-insight generation.

Pure-Python package that knows nothing about the robot app. The conversation
app reaches it only through the thin ``Tool`` wrappers in
``reachy_mini_conversation_app.tools``. Everything that would change under
"플랜 B" (local LLM, ontology, vector search) lives behind the function
boundaries of this package.

Layout (see oss_plan/2026-11-06-weekly-insight-design.md §6.2):

- ``settings``   env-driven configuration (shared contract, M0)
- ``models``     dataclasses for bookmarks, insight clusters and notes (M0)
- ``frontmatter`` markdown front matter parse/serialize (M0)
- ``store``      atomic file I/O, repository lock, bookmark/note lookup (M0)
- ``periods``    ``days`` / ``since`` / ``until`` → concrete date range (M0)
- ``llm``        batch LLM client protocol + fake implementation (M0)
- ``sync``       Chrome bookmark → markdown mirror (track A)
- ``summarize``  URL fetch + per-bookmark summary (track B)
- ``insight``    clusters/correlations → insight note (track B)
- ``session``    insight-conversation state machine (M6)
- ``api``        the five operations as plain functions; the app tools and
                 the MCP server both call these (M9)
- ``mcp_server`` optional MCP server over ``api`` for external clients (M9)
"""

from bookmark_insight.models import Bookmark, InsightNote, InsightCluster
from bookmark_insight.periods import Period, resolve_period
from bookmark_insight.settings import BookmarkInsightSettings, load_settings


__all__ = [
    "Bookmark",
    "BookmarkInsightSettings",
    "InsightCluster",
    "InsightNote",
    "Period",
    "load_settings",
    "resolve_period",
]
