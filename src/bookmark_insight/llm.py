"""Batch LLM client boundary (§2.5 steps 2–3, §4 usage logging).

Two implementations share one protocol:

- ``FakeLLM``  deterministic, offline; used by every test and by M1 demos.
- ``GeminiLLM`` the real text model (``BOOKMARK_INSIGHT_BATCH_MODEL``), JSON-schema
  responses. Never used in tests. Swap this class for a local model under
  플랜 B; nothing else in the package changes.
"""

from __future__ import annotations
import json
import logging
from typing import Any, Protocol
from dataclasses import field, asdict, dataclass

from bookmark_insight.models import InsightCluster


logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 1200

# --- fail-fast settings for the real model (2026-11-07: calls waited 2–4 min, then 503) ---
BATCH_TIMEOUT_MS = 30_000  # google-genai HttpOptions.timeout is in milliseconds
BATCH_RETRY_ATTEMPTS = 2  # attempts including the first request (SDK default is 5)
BATCH_RETRY_INITIAL_DELAY_S = 2.0
BATCH_RETRY_MAX_DELAY_S = 5.0
BATCH_THINKING_BUDGET: int | None = 0  # 0 = no thinking tokens; None = model default


@dataclass(frozen=True)
class Usage:
    """Token usage of one LLM call (subset of Gemini ``usage_metadata``)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for the usage JSONL log."""
        return asdict(self)


@dataclass
class SummaryResult:
    """Per-bookmark summary."""

    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class ClusterResult:
    """Topic clusters for one period."""

    clusters: list[InsightCluster]
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class SummaryInput:
    """What the clustering step knows about one bookmark."""

    id: str
    title: str
    url: str
    tags: tuple[str, ...]
    date: str
    summary: str | None  # None when the body could not be fetched


class BatchLLM(Protocol):
    """Contract every batch model must satisfy."""

    def summarize(self, *, title: str, url: str, text: str) -> SummaryResult:
        """Summarize one page body in Korean (a few sentences)."""
        ...

    def cluster(self, items: list[SummaryInput]) -> ClusterResult:
        """Group summaries into topics with correlations and one question each."""
        ...


CLUSTER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "bookmark_ids": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "correlation": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["topic", "bookmark_ids", "summary", "correlation", "question"],
            },
        }
    },
    "required": ["clusters"],
}

SUMMARY_PROMPT = (
    "다음 웹 페이지 본문을 한국어로 3~5문장으로 요약해라. 핵심 주장과 다루는 기술·개념을 담고, "
    "광고·내비게이션 문구는 무시해라. 요약문만 출력해라.\n\n제목: {title}\nURL: {url}\n\n본문:\n{text}"
)

CLUSTER_PROMPT = (
    "아래는 사용자가 한 기간 동안 저장한 북마크들의 요약이다. 주제별로 묶고(1~5개 묶음), 묶음마다 "
    "(1) 한 줄 요약, (2) 묶음 안 북마크들 사이의 상관관계 또는 다른 묶음과의 연결 한두 문장, "
    "(3) 사용자에게 던질 열린 질문 하나를 만들어라. 질문은 왜 저장했는지를 묻지 말고, 내용에 대한 "
    "생각이나 적용 계획을 묻는다. 요약이 없는(본문 미확보) 북마크는 제목만으로 다루되 그 사실을 "
    "correlation에 짧게 밝혀라. bookmark_ids에는 주어진 id만 쓴다. JSON으로만 답해라.\n\n{items}"
)


def parse_clusters(payload: Any) -> list[InsightCluster]:
    """Validate a JSON payload against the cluster schema (lenient on extras)."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        raise ValueError("cluster response must be an object with a 'clusters' list")
    clusters: list[InsightCluster] = []
    for item in payload["clusters"]:
        if not isinstance(item, dict):
            continue
        clusters.append(
            InsightCluster(
                topic=str(item.get("topic", "")).strip(),
                bookmark_ids=[str(x) for x in item.get("bookmark_ids", [])],
                summary=str(item.get("summary", "")).strip(),
                correlation=str(item.get("correlation", "")).strip(),
                question=str(item.get("question", "")).strip(),
            )
        )
    return clusters


class FakeLLM:
    """Deterministic offline model. Optionally replays canned responses from a fixture dict."""

    def __init__(self, canned: dict[str, Any] | None = None) -> None:
        """Store optional canned responses (fixture JSON) and start a call log."""
        self.canned = canned or {}
        self.calls: list[dict[str, Any]] = []

    def summarize(self, *, title: str, url: str, text: str) -> SummaryResult:
        """Return the canned summary for ``url`` or a trimmed excerpt."""
        self.calls.append({"op": "summarize", "url": url})
        canned = self.canned.get("summaries", {}).get(url)
        if canned:
            return SummaryResult(
                text=str(canned), usage=Usage(input_tokens=len(text), output_tokens=len(str(canned)), model="fake")
            )
        excerpt = " ".join(text.split())[:200]
        summary = f"{title}: {excerpt}" if excerpt else title
        return SummaryResult(
            text=summary, usage=Usage(input_tokens=len(text), output_tokens=len(summary), model="fake")
        )

    def cluster(self, items: list[SummaryInput]) -> ClusterResult:
        """Replay canned clusters, else one cluster per first tag."""
        self.calls.append({"op": "cluster", "ids": [item.id for item in items]})
        canned = self.canned.get("clusters")
        if canned:
            return ClusterResult(clusters=parse_clusters(canned), usage=Usage(model="fake"))
        groups: dict[str, list[SummaryInput]] = {}
        for item in items:
            groups.setdefault(item.tags[0] if item.tags else "기타", []).append(item)
        clusters = [
            InsightCluster(
                topic=topic,
                bookmark_ids=[member.id for member in members],
                summary=" / ".join(member.title for member in members),
                correlation=f"{len(members)}개 북마크가 '{topic}' 주제로 묶였다.",
                question=f"'{topic}' 쪽에서 요즘 가장 궁금한 건 뭐야?",
            )
            for topic, members in groups.items()
        ]
        return ClusterResult(clusters=clusters, usage=Usage(model="fake"))


class GeminiLLM:
    """Real batch model via ``google-genai`` (text model, not the Live model)."""

    def __init__(self, api_key: str, model: str) -> None:
        """Create the Gemini client for ``model`` with a short timeout and a single retry."""
        from google import genai  # imported lazily: tests never need it
        from google.genai import types

        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=BATCH_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(
                    attempts=BATCH_RETRY_ATTEMPTS,
                    initial_delay=BATCH_RETRY_INITIAL_DELAY_S,
                    max_delay=BATCH_RETRY_MAX_DELAY_S,
                ),
            ),
        )
        self.model = model
        self._thinking_supported = True

    def _config(self, **extra: Any) -> Any:
        """Build a ``GenerateContentConfig`` with the thinking budget (when supported) plus ``extra``."""
        from google.genai import types

        if BATCH_THINKING_BUDGET is not None and self._thinking_supported:
            extra["thinking_config"] = types.ThinkingConfig(thinking_budget=BATCH_THINKING_BUDGET)
        return types.GenerateContentConfig(**extra)

    def _generate(self, prompt: str, **extra: Any) -> Any:
        """Call the model; if it rejects the thinking budget, retry once without it and remember."""
        try:
            return self._client.models.generate_content(
                model=self.model, contents=prompt, config=self._config(**extra)
            )
        except Exception as exc:
            if self._thinking_supported and BATCH_THINKING_BUDGET is not None and "thinking" in str(exc).lower():
                logger.warning(
                    "%s rejected thinking_budget=%s; retrying without it", self.model, BATCH_THINKING_BUDGET
                )
                self._thinking_supported = False
                return self._client.models.generate_content(
                    model=self.model, contents=prompt, config=self._config(**extra)
                )
            raise

    def _usage(self, response: Any) -> Usage:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return Usage(model=self.model)
        return Usage(
            input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
            cached_tokens=int(getattr(meta, "cached_content_token_count", 0) or 0),
            thinking_tokens=int(getattr(meta, "thoughts_token_count", 0) or 0),
            model=self.model,
        )

    def summarize(self, *, title: str, url: str, text: str) -> SummaryResult:
        """Summarize with the text model."""
        prompt = SUMMARY_PROMPT.format(title=title, url=url, text=text)
        response = self._generate(prompt)
        summary = (getattr(response, "text", None) or "").strip()[:SUMMARY_MAX_CHARS]
        return SummaryResult(text=summary, usage=self._usage(response))

    def cluster(self, items: list[SummaryInput]) -> ClusterResult:
        """Cluster with a JSON-schema constrained response."""
        payload = [
            {
                "id": item.id,
                "title": item.title,
                "url": item.url,
                "tags": list(item.tags),
                "date": item.date,
                "summary": item.summary,
            }
            for item in items
        ]
        prompt = CLUSTER_PROMPT.format(items=json.dumps(payload, ensure_ascii=False, indent=1))
        response = self._generate(
            prompt,
            response_mime_type="application/json",
            response_schema=CLUSTER_RESPONSE_SCHEMA,
        )
        clusters = parse_clusters(getattr(response, "text", None) or "{}")
        return ClusterResult(clusters=clusters, usage=self._usage(response))


def build_llm(settings_api_key: str | None, model: str, *, fake: bool = False) -> BatchLLM:
    """Pick the batch model: fake when requested or when no API key is configured."""
    if fake or not settings_api_key:
        if not fake:
            logger.warning("GEMINI_API_KEY is not set; Bookmark Insight batch uses FakeLLM")
        return FakeLLM()
    return GeminiLLM(api_key=settings_api_key, model=model)
