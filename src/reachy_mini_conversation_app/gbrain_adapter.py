"""Garry Tan's GBrain Integration Adapter.

Provides seamless integration between Reachy Mini Conversation App and
the official gbrain CLI / MCP server running on Bun runtime with PGLite.
Supports ultra-low-latency HTTP MCP communication (sub-5ms) with CLI fallback.
"""

from __future__ import annotations
import os
import time
import json
import urllib.request
import urllib.error
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

BUN_BIN = Path(r"C:\Users\ldhco\.bun\bin\bun.exe")
GBRAIN_CLI = Path(r"C:\Users\ldhco\.bun\install\global\node_modules\gbrain\src\cli.ts")


class GBrainAdapter:
    """Wrapper around gbrain HTTP MCP server and CLI with latency benchmarking."""

    def __init__(
        self,
        bun_path: Path = BUN_BIN,
        cli_path: Path = GBRAIN_CLI,
        server_url: str = "http://127.0.0.1:3131",
    ):
        self.bun_path = bun_path
        self.cli_path = cli_path
        self.server_url = server_url.rstrip("/")
        self.api_key = os.getenv("GBRAIN_API_KEY", "")
        self.is_available = self.bun_path.exists() and self.cli_path.exists()

    def call_http_mcp(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """Call gbrain MCP tool endpoint over HTTP (2ms) so it shows in the live dashboard."""
        start_time = time.perf_counter()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        api_key = self.api_key or os.getenv("GBRAIN_API_KEY", "")
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
                api_key = os.getenv("GBRAIN_API_KEY", "")
            except Exception:
                pass

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        try:
            req = urllib.request.Request(
                f"{self.server_url}/mcp",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_text = resp.read().decode("utf-8")
                json_str = raw_text
                for line in raw_text.splitlines():
                    if line.startswith("data:"):
                        json_str = line[5:].strip()
                        break
                data = json.loads(json_str)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                logger.info("gbrain HTTP MCP %s succeeded in %.2f ms", tool_name, elapsed_ms)
                return {
                    "success": "error" not in data,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "output": data.get("result", {}),
                    "error": data.get("error", ""),
                }
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.debug("gbrain HTTP error (%s): %s", e.code, err_body)
            return {
                "success": False,
                "elapsed_ms": round(elapsed_ms, 2),
                "error": f"HTTP {e.code}: {err_body}",
                "output": "",
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return {
                "success": False,
                "elapsed_ms": round(elapsed_ms, 2),
                "error": str(e),
                "output": "",
            }

    def run_gbrain_command(self, args: List[str], timeout: float = 15.0) -> Dict[str, Any]:
        """Execute a gbrain CLI command via Bun and measure execution latency in ms."""
        if not self.is_available:
            return {
                "success": False,
                "elapsed_ms": 0.0,
                "error": "gbrain or bun binary not found",
                "output": "",
            }

        cmd = [str(self.bun_path), "run", str(self.cli_path)] + args
        start_time = time.perf_counter()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            return {
                "success": proc.returncode == 0,
                "elapsed_ms": round(elapsed_ms, 2),
                "output": stdout if stdout else stderr,
                "error": stderr if proc.returncode != 0 else "",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "elapsed_ms": timeout * 1000,
                "error": "gbrain command timed out",
                "output": "",
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return {
                "success": False,
                "elapsed_ms": round(elapsed_ms, 2),
                "error": str(e),
                "output": "",
            }

    def remember_fact(self, fact: str, entity: str = "user_hong", provenance: str = "reachy_conversation") -> Dict[str, Any]:
        """Save fact via HTTP MCP (fast & dashboard-visible) or CLI fallback."""
        # 1. Try HTTP MCP first (triggers live dashboard SSE feed)
        http_res = self.call_http_mcp("remember", {"fact": fact, "entity": entity, "provenance": provenance})
        if http_res["success"]:
            return http_res

        # 2. Fallback to CLI
        args = ["remember", fact, "--entity", entity, "--provenance", provenance]
        return self.run_gbrain_command(args)

    def recall_facts(self, query: str = "", entity: Optional[str] = None) -> Dict[str, Any]:
        """Recall facts via HTTP MCP or CLI fallback."""
        args_dict: Dict[str, Any] = {}
        if entity:
            args_dict["entity"] = entity
        if query:
            args_dict["query"] = query

        http_res = self.call_http_mcp("recall", args_dict)
        if http_res["success"]:
            return http_res

        args = ["recall"]
        if entity:
            args.append(entity)
        if query:
            args.extend(["--query", query])
        return self.run_gbrain_command(args)

    def query_hybrid(self, question: str) -> Dict[str, Any]:
        """Execute hybrid search via HTTP MCP or CLI fallback."""
        http_res = self.call_http_mcp("query", {"question": question})
        if http_res["success"]:
            return http_res

        args = ["query", question]
        return self.run_gbrain_command(args)


# Global adapter instance
gbrain_instance = GBrainAdapter()
