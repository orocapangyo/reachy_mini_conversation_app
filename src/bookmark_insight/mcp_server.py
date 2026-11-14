"""Optional MCP server exposing the Bookmark Insight package to external clients (design doc §2.1, M9).

The robot app does **not** use this: its ``tools/`` call ``bookmark_insight.api`` in
the same process. This script wraps the very same functions for Obsidian or
any other MCP client, so both hosts get identical results.

    python -m bookmark_insight.mcp_server            # stdio (for a client that spawns the server)
    python -m bookmark_insight.mcp_server --http     # streamable HTTP on http://127.0.0.1:8765/mcp

Settings come from ``.env`` / the environment exactly like the app
(``BOOKMARK_INSIGHT_BOOKMARK_REPO`` etc.). ``build_insight`` is long (page fetches,
batch-model calls) and runs in a worker thread so the server keeps answering.
Requires the ``mcp`` package (a core dependency of the app).
"""

from __future__ import annotations
import sys
import asyncio
import logging
import argparse
from typing import TYPE_CHECKING, Any

from dotenv import find_dotenv, load_dotenv

from bookmark_insight import api


if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

SERVER_NAME = "bookmark_insight"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOOL_NAMES: tuple[str, ...] = ("get_insight", "build_insight", "get_bookmark", "list_bookmarks", "finish_insight")
MISSING_MCP_MESSAGE = (
    "the 'mcp' package is not installed; install the app dependencies (uv sync) or `pip install mcp` "
    "to run the Bookmark Insight MCP server"
)
INSTRUCTIONS = (
    "Bookmark Insight over the user's Chrome bookmarks (markdown repository). "
    "Start with get_insight; when it returns status 'generating', call build_insight with the same "
    "since/until/profile (slow). Every result carries 'next_action', Korean guidance for a voice assistant; "
    "other clients may ignore it. Periods: days (default 7) or since/until (YYYY-MM-DD), never both."
)


def create_server(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> FastMCP:
    """Build the FastMCP server with the five Bookmark Insight tools bound to ``bookmark_insight.api``."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - mcp is a core dependency
        raise RuntimeError(MISSING_MCP_MESSAGE) from exc

    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, host=host, port=port)
    hint = api.profile_hint()

    @server.tool(
        description=(
            "Get the Bookmark Insight (topic clusters, correlations, one question each) for the bookmarks of a "
            "period. Returns immediately: status 'ready' has the clusters, 'generating' means call build_insight "
            "next, 'empty' means no bookmarks. Use days (default 7) or since/until, never both. " + hint
        )
    )
    def get_insight(
        days: int | None = None, since: str | None = None, until: str | None = None, profile: str | None = None
    ) -> dict[str, Any]:
        return api.get_insight(days=days, since=since, until=until, profile=profile)

    @server.tool(
        description=(
            "Build the insight note for a period (sync bookmarks, summarize, cluster). Slow; call it when "
            "get_insight returned 'generating', with the same since/until/profile. " + hint
        )
    )
    async def build_insight(since: str, until: str, profile: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(api.build_insight, since=since, until=until, profile=profile)

    @server.tool(
        description=(
            "Get one bookmark's details and stored summary by id (the id from get_insight clusters or "
            "list_bookmarks). Ids are global: no profile argument."
        )
    )
    def get_bookmark(id: str) -> dict[str, Any]:  # noqa: A002 - the tool argument is named ``id``
        return api.get_bookmark(id=id)

    @server.tool(
        description=(
            "List bookmarks saved in a period, newest first, archived ones excluded. Use days (default 7) or "
            f"since/until, never both; limit defaults to {api.DEFAULT_LIMIT}. " + hint
        )
    )
    def list_bookmarks(
        days: int | None = None,
        since: str | None = None,
        until: str | None = None,
        profile: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return api.list_bookmarks(days=days, since=since, until=until, profile=profile, limit=limit)

    @server.tool(
        description=(
            "Finish the current insight conversation: the conclusion is appended to the insight note opened by "
            "get_insight / build_insight. reason is 'explicit' (user asked) or 'confirmed' (user agreed)."
        )
    )
    def finish_insight(conclusion: str, reason: str = "explicit") -> dict[str, Any]:
        return api.finish_insight(conclusion=conclusion, reason=reason)

    return server


def main(argv: list[str] | None = None) -> int:
    """Run the server over stdio (default) or streamable HTTP (``--http``)."""
    parser = argparse.ArgumentParser(
        prog="python -m bookmark_insight.mcp_server",
        description="Expose the Bookmark Insight package as an MCP server.",
    )
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address for --http (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port for --http (default {DEFAULT_PORT})")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    # stdio carries the protocol on stdout: logs must go to stderr.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    try:
        server = create_server(host=args.host, port=args.port)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.http:
        logger.info("Bookmark Insight MCP server: http://%s:%d/mcp", args.host, args.port)
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
