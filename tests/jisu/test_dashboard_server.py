"""HTTP/WebSocket tests for the dashboard server with a fake app controller."""

from __future__ import annotations
from typing import Any
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jisu.dashboard.server import create_app
from jisu.dashboard.log_buffer import LogBuffer


class FakeController:
    """Records lifecycle calls instead of spawning processes."""

    def __init__(self) -> None:
        """Initialize the test double."""
        self.running = False
        self.backend = "json"
        self.calls: list[tuple[str, str | None]] = []
        self.fail_start = False

    def status(self) -> dict[str, Any]:
        """Test double for ``status``."""
        return {
            "app": {"running": self.running, "pid": 1 if self.running else None, "returncode": None},
            "daemon": {"managed": True, "reused": False, "pid": 2},
            "memory_backend": self.backend,
        }

    def start_app(self, memory_backend: str | None = None) -> None:
        """Test double for ``start_app``."""
        self.calls.append(("start", memory_backend))
        if self.fail_start:
            raise RuntimeError("port 7860 is already in use")
        if memory_backend:
            self.backend = memory_backend
        self.running = True

    def stop_app(self) -> None:
        """Test double for ``stop_app``."""
        self.calls.append(("stop", None))
        self.running = False

    def restart_app(self, memory_backend: str | None = None) -> None:
        """Test double for ``restart_app``."""
        self.calls.append(("restart", memory_backend))
        if memory_backend:
            self.backend = memory_backend
        self.running = True


@pytest.fixture
def setup(tmp_path: Path) -> tuple[TestClient, FakeController, LogBuffer]:
    """Provide the objects under test."""
    controller = FakeController()
    logs = LogBuffer()
    app = create_app(controller, logs, app_port=7860, instance_path=tmp_path)
    return TestClient(app), controller, logs


def test_index_and_config(setup: tuple[TestClient, FakeController, LogBuffer]) -> None:
    """The page and its static assets are served; config points at the app's /rpc."""
    client, _controller, _logs = setup
    page = client.get("/")
    assert page.status_code == 200 and "Simulator Dashboard" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/config").json() == {
        "app_port": 7860,
        "rpc_path": "/rpc",
        "default_memory_backend": "gbrain",
    }


def test_lifecycle_routes(setup: tuple[TestClient, FakeController, LogBuffer]) -> None:
    """Start/stop/restart reach the controller; failures become 409 + a log line."""
    client, controller, logs = setup
    assert client.post("/api/app/start", json={"backend": "json"}).json()["app"]["running"] is True
    assert client.post("/api/app/restart", json={"backend": "gbrain"}).json()["memory_backend"] == "gbrain"
    assert client.post("/api/app/stop").json()["app"]["running"] is False
    assert controller.calls == [("start", "json"), ("restart", "gbrain"), ("stop", None)]

    controller.fail_start = True
    response = client.post("/api/app/start", json={})
    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]
    assert logs.snapshot()[-1].level == "ERROR"


def test_memory_crud_on_json_backend(setup: tuple[TestClient, FakeController, LogBuffer], tmp_path: Path) -> None:
    """Add, list, search, forget and clear against the instance's JSON store."""
    client, _controller, _logs = setup
    assert client.post("/api/memory", json={"text": "Likes jazz"}).status_code == 200
    client.post("/api/memory", json={"text": "Has a cat named Momo"})
    assert (tmp_path / "memory.v1.json").exists()

    facts = client.get("/api/memory").json()["facts"]
    assert [f["text"] for f in facts] == ["Has a cat named Momo", "Likes jazz"]

    found = client.post("/api/memory/search", json={"query": "momo"}).json()["facts"]
    assert [f["text"] for f in found] == ["Has a cat named Momo"]

    removed = client.post("/api/memory/forget", json={"query": "jazz"}).json()["removed"]
    assert removed["text"] == "Likes jazz"
    assert client.post("/api/memory/forget", json={"query": "jazz"}).status_code == 404

    client.post("/api/memory/clear", json={})
    assert client.get("/api/memory").json()["facts"] == []
    assert client.post("/api/memory", json={"text": "  "}).status_code == 422


def test_memory_backend_listing_and_errors(
    setup: tuple[TestClient, FakeController, LogBuffer], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Backends are listed with availability; unknown/unavailable ones give 404/409."""
    monkeypatch.setenv("JISU_GBRAIN_BIN", str(tmp_path / "missing-gbrain"))
    client, _controller, _logs = setup
    data = client.get("/api/memory/backends").json()
    by_name = {b["name"]: b for b in data["backends"]}
    assert data["current"] == "json"
    assert by_name["json"]["available"] is True
    assert by_name["gbrain"]["available"] is False
    assert "not found" in by_name["gbrain"]["detail"]

    assert client.get("/api/memory", params={"backend": "nope"}).status_code == 404
    assert client.get("/api/memory", params={"backend": "gbrain"}).status_code == 409
    assert client.post("/api/memory/gbrain/init").status_code == 409


def test_logs_websocket_sends_backlog_then_entries(setup: tuple[TestClient, FakeController, LogBuffer]) -> None:
    """The log socket replays the buffer, then streams new lines."""
    client, _controller, logs = setup
    logs.append("app", "2026-11-17 10:00:00,000 INFO x.y:1 | booted")
    with client.websocket_connect("/ws/logs") as ws:
        backlog = ws.receive_json()
        assert backlog["type"] == "backlog"
        assert backlog["entries"][0]["message"] == "booted"
        logs.append("daemon", "WARNING: slow")
        entry = ws.receive_json()
        assert entry["type"] == "entry"
        assert (entry["entry"]["source"], entry["entry"]["level"]) == ("daemon", "WARNING")
