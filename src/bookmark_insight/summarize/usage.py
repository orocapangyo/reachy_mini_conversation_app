"""Token usage log: one JSONL line per batch LLM call (§2.5, §4)."""

from __future__ import annotations
from typing import Any

from bookmark_insight.llm import Usage
from bookmark_insight.store import utc_now_iso, append_jsonl
from bookmark_insight.settings import BookmarkInsightSettings


def usage_record(
    *, profile: str, op: str, usage: Usage, bookmark_id: str | None = None, ts: str | None = None
) -> dict[str, Any]:
    """Build the usage log line (shared by the summarizer and the insight batch)."""
    record: dict[str, Any] = {"ts": ts or utc_now_iso(), "profile": profile, "op": op}
    if bookmark_id is not None:
        record["bookmark_id"] = bookmark_id
    record.update(
        {
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "thinking_tokens": usage.thinking_tokens,
        }
    )
    return record


def log_usage(
    settings: BookmarkInsightSettings,
    *,
    profile: str,
    op: str,
    usage: Usage,
    bookmark_id: str | None = None,
    ts: str | None = None,
) -> None:
    """Append one usage line to ``settings.resolved_usage_log``."""
    append_jsonl(
        settings.resolved_usage_log, usage_record(profile=profile, op=op, usage=usage, bookmark_id=bookmark_id, ts=ts)
    )
