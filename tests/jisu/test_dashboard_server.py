"""HTTP/WebSocket tests for the dashboard server with a fake app controller."""

from __future__ import annotations
from typing import Any
from pathlib import Path
from collections.abc import Iterator

import httpx
import pytest
from gbrain_fakes import FakeGBrain, make_gbrain
from fastapi.testclient import TestClient

from jisu.dashboard.memory import registry
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
        "camera_stream_path": "/api/camera/stream",
        "default_mic_muted": True,
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


@pytest.fixture
def fake_gbrain(tmp_path: Path) -> Iterator[FakeGBrain]:
    """Register a 'fakegb' backend: a real GBrainMemoryBackend over an in-memory CLI."""
    fake = FakeGBrain()
    backend = make_gbrain(tmp_path, fake)
    registry.available_backends()
    registry.register_backend("fakegb", "Fake GBrain", lambda _path: backend, replace=True)
    yield fake
    registry._REGISTRY.pop("fakegb", None)


def test_memory_categories_endpoint(setup: tuple[TestClient, FakeController, LogBuffer]) -> None:
    """The dashboard reads the category table from the server."""
    client, _controller, _logs = setup
    data = client.get("/api/memory/categories").json()
    assert data["default"] == "user_fact"
    assert {g["key"] for g in data["groups"]} == {"persona", "promise", "user"}


def test_categorized_memory_on_gbrain(
    setup: tuple[TestClient, FakeController, LogBuffer], fake_gbrain: FakeGBrain
) -> None:
    """List with counts and filters, add by category, forget by id, search and preview."""
    client, controller, _logs = setup
    controller.backend = "fakegb"
    assert (
        client.post("/api/memory", json={"text": "나는 농담을 짧게 한다", "category": "persona_trait"}).status_code
        == 200
    )
    client.post("/api/memory", json={"text": "나는 졸면 깨워 주기로 했다", "category": "promise"})
    added = client.post("/api/memory", json={"text": "사용자는 매운 음식을 좋아한다", "category": "user_preference"})
    assert added.json()["fact"]["categoryLabel"] == "사용자 · 취향"
    assert client.post("/api/memory", json={"text": "x", "category": "diary"}).status_code == 422

    data = client.get("/api/memory").json()
    assert data["categorized"] is True and data["total"] == 3
    assert data["counts"] == {"persona_trait": 1, "promise": 1, "user_preference": 1}
    persona = client.get("/api/memory", params={"category": "persona"}).json()["facts"]
    assert [(f["text"], f["group"]) for f in persona] == [("나는 농담을 짧게 한다", "persona")]

    fake_gbrain.rank_questions = False
    found = client.post("/api/memory/search", json={"query": "농담"}).json()
    assert [f["category"] for f in found["facts"]] == ["persona_trait"]

    prompt = client.get("/api/memory/prompt-preview").json()["prompt"]
    assert prompt.index("농담") < prompt.index("졸면") < prompt.index("매운")

    promise_id = next(f["id"] for f in data["facts"] if f["category"] == "promise")
    assert client.post("/api/memory/forget", json={"id": promise_id}).json()["removed"]["id"] == promise_id
    assert client.post("/api/memory/forget", json={"id": promise_id}).status_code == 404
    assert client.get("/api/memory").json()["total"] == 2


def test_persona_seed_route(setup: tuple[TestClient, FakeController, LogBuffer], fake_gbrain: FakeGBrain) -> None:
    """Seeding writes the profile's seed once; repeating it skips duplicates."""
    client, controller, _logs = setup
    controller.backend = "fakegb"
    first = client.post("/api/memory/persona/seed", json={"profile": "desk_companion_ko"}).json()
    assert first["found"] is True and first["saved"] > 0
    assert {f["category"] for f in client.get("/api/memory").json()["facts"]} <= {"persona_trait", "persona_style"}
    again = client.post("/api/memory/persona/seed", json={"profile": "desk_companion_ko"}).json()
    assert (again["saved"], again["existing"]) == (0, first["saved"])
    assert client.post("/api/memory/persona/seed", json={"profile": "nobody"}).json()["found"] is False
    assert client.post("/api/memory/persona/seed", json={"backend": "json"}).status_code == 409


def test_json_backend_ignores_categories_gracefully(setup: tuple[TestClient, FakeController, LogBuffer]) -> None:
    """JSON lists plain facts, refuses non-default categories and id-based forget."""
    client, _controller, _logs = setup
    client.post("/api/memory", json={"text": "Likes jazz", "category": "user_fact"})
    data = client.get("/api/memory").json()
    assert data["categorized"] is False and "category" not in data["facts"][0]
    assert client.post("/api/memory", json={"text": "나는 A", "category": "promise"}).status_code == 409
    assert client.post("/api/memory/forget", json={"id": data["facts"][0]["id"]}).status_code == 409
    assert "- Likes jazz" in client.get("/api/memory/prompt-preview").json()["prompt"]


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


def test_camera_proxy_forwards_to_app(tmp_path: Path) -> None:
    """Device listing and selection are forwarded to the conversation app."""
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        if request.url.path == "/api/camera/devices":
            return httpx.Response(200, json={"devices": [{"id": "0", "name": "USB 웹캠 0"}], "active": "auto"})
        return httpx.Response(200, json={"ok": True, "active": "0"})

    http = httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(handler))
    logs = LogBuffer()
    client = TestClient(create_app(FakeController(), logs, app_port=7860, instance_path=tmp_path, app_http=http))

    assert client.get("/api/camera/devices").json()["active"] == "auto"
    assert client.post("/api/camera/select", json={"device": "0"}).json() == {"ok": True, "active": "0"}
    assert client.post("/api/camera/select", json={}).status_code == 422
    assert seen == [("GET", "/api/camera/devices", ""), ("POST", "/api/camera/select", "device=0")]
    assert "camera source -> 0" in logs.snapshot()[-1].message


def test_camera_proxy_reports_unreachable_app(tmp_path: Path) -> None:
    """When the app is down, the proxy answers 502 instead of raising."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(handler))
    client = TestClient(
        create_app(FakeController(), LogBuffer(), app_port=7860, instance_path=tmp_path, app_http=http)
    )
    response = client.get("/api/camera/devices")
    assert response.status_code == 502
    assert "not reachable" in response.json()["detail"]
