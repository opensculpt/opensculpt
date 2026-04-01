"""Tests for A2A server — Agent Card endpoint + JSON-RPC dispatcher."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from agos.a2a.server import A2AServer, set_server
from agos.dashboard.app import dashboard_app


@pytest.fixture
def mock_os_agent():
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value={
        "ok": True,
        "action": "execute",
        "message": "Task completed successfully.",
        "data": {"turns": 2, "tokens_used": 500},
    })
    return agent


@pytest.fixture
def mock_registry():
    reg = MagicMock()
    reg.list_agents.return_value = [
        {
            "name": "ironclaw",
            "display_name": "IronClaw Agent",
            "runtime": "python",
            "status": "available",
            "description": "Python data analysis agent",
        },
    ]
    return reg


@pytest.fixture
def a2a_server(mock_os_agent, mock_registry):
    server = A2AServer(
        os_agent=mock_os_agent,
        agent_registry=mock_registry,
    )
    server.set_base_url("http://localhost:8420")
    return server


@pytest.fixture
def client(a2a_server):
    set_server(a2a_server)
    with TestClient(dashboard_app) as c:
        yield c
    set_server(None)


class TestAgentCardEndpoint:
    def test_returns_valid_card(self, client):
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "OpenSculpt"
        assert "skills" in card
        assert len(card["skills"]) >= 1  # at least os_agent skill

    def test_includes_os_agent_skill(self, client):
        resp = client.get("/.well-known/agent-card.json")
        card = resp.json()
        skill_ids = [s["id"] for s in card["skills"]]
        assert "os_agent" in skill_ids

    def test_includes_installed_agents(self, client):
        resp = client.get("/.well-known/agent-card.json")
        card = resp.json()
        skill_ids = [s["id"] for s in card["skills"]]
        assert "ironclaw" in skill_ids

    def test_returns_503_when_not_initialized(self):
        set_server(None)
        with TestClient(dashboard_app) as c:
            resp = c.get("/.well-known/agent-card.json")
            assert resp.status_code == 503


class TestJsonRpcEndpoint:
    def test_message_send_creates_task(self, client):
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello world"}],
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert "result" in data
        assert "taskId" in data["result"]

    def test_tasks_get(self, client, a2a_server):
        # First create a task
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "test"}]}},
        })
        task_id = resp.json()["result"]["taskId"]

        # Then get it
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 2, "method": "tasks/get",
            "params": {"taskId": task_id},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["taskId"] == task_id

    def test_tasks_cancel(self, client):
        # Create a task
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "test"}]}},
        })
        task_id = resp.json()["result"]["taskId"]

        # Cancel it
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 2, "method": "tasks/cancel",
            "params": {"taskId": task_id},
        })
        data = resp.json()
        assert data["result"]["status"]["state"] in ("canceled", "working", "completed")

    def test_unknown_method(self, client):
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "unknown/method",
            "params": {},
        })
        data = resp.json()
        assert data["error"] is not None
        assert data["error"]["code"] == -32601

    def test_task_not_found(self, client):
        resp = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "tasks/get",
            "params": {"taskId": "nonexistent"},
        })
        data = resp.json()
        assert data["error"] is not None

    def test_invalid_json_rpc(self, client):
        resp = client.post("/a2a", json={"bad": "request"})
        data = resp.json()
        assert data.get("error") is not None


class TestA2AServer:
    def test_build_agent_card(self, a2a_server):
        card = a2a_server.build_agent_card()
        assert card.name == "OpenSculpt"
        assert len(card.skills) == 2  # os_agent + ironclaw

    def test_list_tasks_empty(self, a2a_server):
        assert a2a_server.list_tasks() == []

    @pytest.mark.asyncio
    async def test_handle_message_send(self, a2a_server):
        from agos.a2a.models import JsonRpcRequest
        req = JsonRpcRequest(
            method="message/send", id=1,
            params={
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            },
        )
        resp = await a2a_server.handle_rpc(req)
        assert resp.error is None
        assert "taskId" in resp.result

    def test_list_tasks_endpoint(self, client):
        resp = client.get("/api/a2a/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
