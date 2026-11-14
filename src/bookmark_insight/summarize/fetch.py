"""URL body fetch with the §2.5 step-1 guards (size cap, binary types, redirects, charset).

The HTTP layer is injectable: tests pass an ``httpx.Client`` built on
``httpx.MockTransport`` through ``build_client``/``fetch_url`` so no test ever
touches the network. ``fetch_url`` never raises for a bad page; it returns a
``FetchResult`` whose ``reason`` explains the failure.
"""

from __future__ import annotations
import re
import logging
from typing import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from bookmark_insight.summarize import (
    MIN_BODY_CHARS,
    FETCH_MAX_BYTES,
    FETCH_TIMEOUT_S,
    FETCH_MAX_REDIRECTS,
)


logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; BookmarkInsight/1.0; +https://github.com/pollen-robotics/reachy_mini)"
ACCEPT_HEADER = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"

TEXT_CONTENT_TYPES: frozenset[str] = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
BINARY_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
BINARY_CONTENT_TYPE_PREFIXES: tuple[str, ...] = ("image/", "audio/", "video/", "font/")

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_META_SNIFF_BYTES = 4096
_RETRY_AFTER_DEFAULT_S = 60.0


@dataclass
class FetchResult:
    """Outcome of fetching one URL."""

    url: str
    ok: bool
    text: str = ""  # extracted main text (empty on failure)
    reason: str | None = None  # why ``ok`` is False
    status_code: int | None = None
    content_type: str | None = None
    retry_after_s: float | None = None  # set when the server answered 429 / 503 with Retry-After

    @classmethod
    def failure(
        cls,
        url: str,
        reason: str,
        *,
        status_code: int | None = None,
        content_type: str | None = None,
        retry_after_s: float | None = None,
    ) -> FetchResult:
        """Build a failed result."""
        return cls(
            url=url,
            ok=False,
            reason=reason,
            status_code=status_code,
            content_type=content_type,
            retry_after_s=retry_after_s,
        )


def build_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Create the HTTP client with the §2.5 limits (redirects, timeout); ``transport`` is for tests."""
    return httpx.Client(
        transport=transport,
        follow_redirects=True,
        max_redirects=FETCH_MAX_REDIRECTS,
        timeout=FETCH_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER},
    )


@contextmanager
def client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Yield ``client`` as is, or a temporary default client that is closed afterwards."""
    if client is not None:
        yield client
        return
    with build_client() as owned:
        yield owned


def split_content_type(header: str | None) -> tuple[str, str | None]:
    """Return ``(media_type, charset)`` from a ``Content-Type`` header."""
    if not header:
        return "", None
    parts = [part.strip() for part in header.split(";")]
    media_type = parts[0].lower()
    charset: str | None = None
    for param in parts[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip("\"'").lower() or None
    return media_type, charset


def is_binary_content_type(media_type: str) -> bool:
    """Return whether the media type is one the summarizer never downloads."""
    return media_type in BINARY_CONTENT_TYPES or media_type.startswith(BINARY_CONTENT_TYPE_PREFIXES)


def meta_charset(raw: bytes) -> str | None:
    """Return the ``<meta charset>`` declared in the document head, if any."""
    match = _META_CHARSET_RE.search(raw[:_META_SNIFF_BYTES])
    if match is None:
        return None
    return match.group(1).decode("ascii", errors="ignore").lower() or None


def _try_decode(raw: bytes, encoding: str | None) -> str | None:
    if not encoding:
        return None
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return None


def decode_text(raw: bytes, header_charset: str | None) -> str:
    """Decode a ``text/plain`` body: header charset → UTF-8 → CP949 → lossy UTF-8."""
    for encoding in (header_charset, "utf-8", "cp949"):
        decoded = _try_decode(raw, encoding)
        if decoded is not None:
            return decoded
    return raw.decode("utf-8", errors="replace")


def extract_html_text(raw: bytes, url: str, header_charset: str | None) -> str:
    """Extract the main text of an HTML page (meta charset first, then header, then detection)."""
    import trafilatura  # imported lazily: slow import, only the batch path needs it

    document: bytes | str = raw
    decoded = _try_decode(raw, meta_charset(raw)) or _try_decode(raw, header_charset)
    if decoded is not None:
        document = decoded
    extracted = trafilatura.extract(document, url=url, include_comments=False, include_tables=True)
    if extracted is None and isinstance(document, str):
        extracted = trafilatura.extract(raw, url=url, include_comments=False, include_tables=True)
    return (extracted or "").strip()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return max(0.0, float(header.strip()))
    except ValueError:
        return _RETRY_AFTER_DEFAULT_S  # HTTP-date form: treat as "wait a minute"


def _read_capped(response: httpx.Response, limit: int) -> bytes | None:
    """Read the body up to ``limit`` bytes; return ``None`` when the stream exceeds it."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(url: str, *, client: httpx.Client | None = None) -> FetchResult:
    """Download ``url`` and return its main text, or a failed result with a reason (never raises)."""
    try:
        with client_scope(client) as http, http.stream("GET", url) as response:
            status = response.status_code
            media_type, charset = split_content_type(response.headers.get("content-type"))
            if status == 429 or status == 503:
                return FetchResult.failure(
                    url,
                    f"http {status}",
                    status_code=status,
                    content_type=media_type,
                    retry_after_s=_retry_after_seconds(response),
                )
            if status >= 400:
                return FetchResult.failure(url, f"http {status}", status_code=status, content_type=media_type)
            if is_binary_content_type(media_type):
                return FetchResult.failure(
                    url, f"binary content type {media_type}", status_code=status, content_type=media_type
                )
            if media_type and media_type not in TEXT_CONTENT_TYPES:
                return FetchResult.failure(
                    url, f"unsupported content type {media_type}", status_code=status, content_type=media_type
                )
            declared = response.headers.get("content-length")
            if declared is not None and declared.strip().isdigit() and int(declared) > FETCH_MAX_BYTES:
                return FetchResult.failure(
                    url,
                    f"content-length {declared} exceeds {FETCH_MAX_BYTES}",
                    status_code=status,
                    content_type=media_type,
                )
            raw = _read_capped(response, FETCH_MAX_BYTES)
            if raw is None:
                return FetchResult.failure(
                    url, f"body exceeds {FETCH_MAX_BYTES} bytes", status_code=status, content_type=media_type
                )
    except httpx.TooManyRedirects:
        return FetchResult.failure(url, f"more than {FETCH_MAX_REDIRECTS} redirects")
    except httpx.TimeoutException:
        return FetchResult.failure(url, f"timeout after {FETCH_TIMEOUT_S:g}s")
    except httpx.HTTPError as exc:
        return FetchResult.failure(url, f"{type(exc).__name__}: {exc}")

    try:
        text = (
            decode_text(raw, charset).strip() if media_type == "text/plain" else extract_html_text(raw, url, charset)
        )
    except Exception as exc:  # extraction libraries can throw on odd markup
        logger.warning("Text extraction failed for %s: %s", url, exc)
        return FetchResult.failure(url, f"extraction error: {exc}", status_code=status, content_type=media_type)
    if len(text) < MIN_BODY_CHARS:
        return FetchResult.failure(
            url, f"body too short ({len(text)} < {MIN_BODY_CHARS} chars)", status_code=status, content_type=media_type
        )
    return FetchResult(url=url, ok=True, text=text, status_code=status, content_type=media_type)
