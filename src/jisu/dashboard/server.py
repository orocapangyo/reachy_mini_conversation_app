"""FastAPI app for the simulator dashboard (app control, memory, live logs, static UI)."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Protocol
from pathlib import Path

import httpx
from fastapi import Body, Query, FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jisu.dashboard.constants import (
    APP_RPC_PATH,
    DEFAULT_MIC_MUTED,
    APP_PROXY_TIMEOUT_S,
    APP_CAMERA_STREAM_PATH,
    DEFAULT_MEMORY_BACKEND,
    MEMORY_SEARCH_DEFAULT_LIMIT,
)
from jisu.dashboard.log_buffer import LogBuffer
from jisu.dashboard.memory.base import MemoryBackend, BackendUnavailableError
from jisu.dashboard.memory.registry import backend_status, create_backend, available_backends
from jisu.dashboard.memory.gbrain_backend import GBrainCallError, GBrainMemoryBackend
from reachy_mini_conversation_app.memory import MemoryFact


logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class AppController(Protocol):
    """What the server needs from the launcher (a fake is used in tests)."""

    def status(self) -> dict[str, Any]:
        """Return a JSON-serializable status summary."""
        ...

    def start_app(self, memory_backend: str | None = None) -> None:
        """Start the conversation app."""
        ...

    def stop_app(self) -> None:
        """Stop the conversation app."""
        ...

    def restart_app(self, memory_backend: str | None = None) -> None:
        """Restart the conversation app."""
        ...


def _fact_json(fact: MemoryFact) -> dict[str, Any]:
    return {"id": fact.id, "text": fact.text, "createdAt": fact.created_at}


def create_app(
    controller: AppController,
    logs: LogBuffer,
    *,
    app_port: int,
    instance_path: str | Path | None = None,
    app_http: httpx.Client | None = None,
) -> FastAPI:
    """Build the dashboard FastAPI application.

    ``app_http`` talks to the conversation app's HTTP API (camera proxy); a mock
    transport is injected in tests.
    """
    app = FastAPI(title="Reachy Mini Simulator Dashboard")
    http = app_http or httpx.Client(base_url=f"http://127.0.0.1:{app_port}", timeout=APP_PROXY_TIMEOUT_S)

    def current_backend_name() -> str:
        return str(controller.status().get("memory_backend") or DEFAULT_MEMORY_BACKEND)

    def open_backend(name: str | None) -> MemoryBackend:
        backend_name = (name or current_backend_name()).strip().lower()
        try:
            return create_backend(backend_name, instance_path)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]) if exc.args else str(exc)) from exc
        except BackendUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- page + config ------------------------------------------------------

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    def _config() -> dict[str, Any]:
        return {
            "app_port": app_port,
            "rpc_path": APP_RPC_PATH,
            "camera_stream_path": APP_CAMERA_STREAM_PATH,
            "default_mic_muted": DEFAULT_MIC_MUTED,
            "default_memory_backend": DEFAULT_MEMORY_BACKEND,
        }

    # -- app lifecycle ------------------------------------------------------

    @app.get("/api/app/status")
    def _status() -> dict[str, Any]:
        return controller.status()

    def _run_lifecycle(action: str, backend: str | None) -> dict[str, Any]:
        try:
            if action == "start":
                controller.start_app(backend)
            elif action == "stop":
                controller.stop_app()
            else:
                controller.restart_app(backend)
        except RuntimeError as exc:
            logs.append("dashboard", f"ERROR: {action} failed: {exc}")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return controller.status()

    @app.post("/api/app/start")
    def _start(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        return _run_lifecycle("start", payload.get("backend"))

    @app.post("/api/app/stop")
    def _stop() -> dict[str, Any]:
        return _run_lifecycle("stop", None)

    @app.post("/api/app/restart")
    def _restart(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        return _run_lifecycle("restart", payload.get("backend"))

    # -- memory -------------------------------------------------------------

    @app.get("/api/memory/backends")
    def _backends() -> dict[str, Any]:
        items = []
        for info in available_backends():
            usable, detail = backend_status(info.name, instance_path)
            items.append({"name": info.name, "label": info.label, "available": usable, "detail": detail})
        return {"current": current_backend_name(), "backends": items}

    @app.get("/api/memory")
    def _list(backend: str | None = Query(default=None)) -> dict[str, Any]:
        store = open_backend(backend)
        return {"backend": store.name, "facts": [_fact_json(f) for f in store.list_facts()]}

    @app.post("/api/memory")
    def _add(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        store = open_backend(payload.get("backend"))
        fact = store.add_fact(text)
        if fact is None:
            raise HTTPException(
                status_code=409, detail="nothing was saved (empty, refused or backend error; see logs)"
            )
        logs.append("dashboard", f"INFO: memory[{store.name}] add: {fact.text}")
        return {"backend": store.name, "fact": _fact_json(fact)}

    @app.post("/api/memory/forget")
    def _forget(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        store = open_backend(payload.get("backend"))
        result = store.forget_fact(str(payload.get("query") or ""))
        if result.removed is None:
            raise HTTPException(status_code=404, detail="no memory matched")
        logs.append("dashboard", f"INFO: memory[{store.name}] forget: {result.removed.text}")
        return {
            "backend": store.name,
            "removed": _fact_json(result.removed),
            "other_matches": [_fact_json(f) for f in result.candidates[1:]],
        }

    @app.post("/api/memory/search")
    def _search(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        store = open_backend(payload.get("backend"))
        query = str(payload.get("query") or "")
        limit = int(payload.get("limit") or MEMORY_SEARCH_DEFAULT_LIMIT)
        return {"backend": store.name, "facts": [_fact_json(f) for f in store.search(query, limit)]}

    @app.post("/api/memory/clear")
    def _clear(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        store = open_backend(payload.get("backend"))
        try:
            store.clear()
        except GBrainCallError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        logs.append("dashboard", f"WARNING: memory[{store.name}] cleared")
        return {"backend": store.name, "facts": []}

    @app.post("/api/memory/gbrain/init")
    def _gbrain_init() -> dict[str, Any]:
        brain = GBrainMemoryBackend(instance_path, require_ready=False)
        if not brain.binary_available():
            raise HTTPException(status_code=409, detail=brain.readiness_problem() or "gbrain not found")
        try:
            output = brain.initialize()
        except GBrainCallError as exc:
            logs.append("dashboard", f"ERROR: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        logs.append("dashboard", f"INFO: GBrain initialized at {brain.home}")
        return {"home": str(brain.home), "output": output[-4000:]}

    # -- camera (proxied to the conversation app, which has no CORS) ---------

    def _proxy_app(method: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = http.request(method, path, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"conversation app not reachable: {exc}") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail="conversation app returned unexpected data")
        return data

    @app.get("/api/camera/devices")
    def _camera_devices() -> dict[str, Any]:
        return _proxy_app("GET", "/api/camera/devices")

    @app.post("/api/camera/select")
    def _camera_select(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        device = str(payload.get("device") or "").strip()
        if not device:
            raise HTTPException(status_code=422, detail="device is required")
        result = _proxy_app("POST", "/api/camera/select", {"device": device})
        logs.append("dashboard", f"INFO: camera source -> {result.get('active', device)}")
        return result

    # -- logs ---------------------------------------------------------------

    @app.post("/api/logs/clear")
    def _clear_logs() -> dict[str, Any]:
        logs.clear()
        return {"ok": True}

    @app.websocket("/ws/logs")
    async def _logs_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = logs.subscribe()
        try:
            backlog = [entry.to_json() for entry in logs.snapshot()]
            await websocket.send_json({"type": "backlog", "entries": backlog})
            while True:
                entry = await queue.get()
                await websocket.send_json({"type": "entry", "entry": entry.to_json()})
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            logs.unsubscribe(queue)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
