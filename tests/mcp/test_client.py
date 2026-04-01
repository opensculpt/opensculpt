"""Tests for MCP client — MCPServerConfig, MCPManager, tool bridging."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agos.mcp.client import (
    MCPServerConfig,
    MCPConnection,
    MCPManager,
    _json_schema_to_params,
)
from agos.tools.registry import ToolRegistry
from agos.tools.schema import ToolParameter
from agos.events.bus import EventBus


# ── MCPServerConfig tests ──────────────────────────────────────


def test_mcp_server_config_defaults():
    cfg = MCPServerConfig(name="test", command="echo")
    assert cfg.name == "test"
    assert cfg.command == "echo"
    assert cfg.args == []
    assert cfg.env == {}
    assert cfg.enabled is True


def test_mcp_server_config_full():
    cfg = MCPServerConfig(
        name="sqlite",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sqlite"],
        env={"DB_PATH": "/tmp/test.db"},
        enabled=False,
    )
    assert cfg.name == "sqlite"
    assert len(cfg.args) == 2
    assert cfg.env["DB_PATH"] == "/tmp/test.db"
    assert cfg.enabled is False


# ── JSON Schema to ToolParameter conversion ────────────────────


def test_json_schema_to_params_basic():
    schema = {
        "properties": {
            "query": {"type": "string", "description": "SQL query"},
            "limit": {"type": "integer", "description": "Max rows"},
        },
    }
    params = _json_schema_to_params(schema)
    assert len(params) == 2
    assert isinstance(params[0], ToolParameter)
    assert params[0].name == "query"
    assert params[0].type == "string"
    assert params[0].required is False  # Not in required list


def test_json_schema_to_params_with_required():
    schema = {
        "properties": {
            "query": {"type": "string", "description": "SQL query"},
            "format": {"type": "string", "description": "Output format"},
        },
        "required": ["query"],
    }
    params = _json_schema_to_params(schema)
    query_param = next(p for p in params if p.name == "query")
    format_param = next(p for p in params if p.name == "format")
    assert query_param.required is True
    assert format_param.required is False


def test_json_schema_to_params_empty():
    params = _json_schema_to_params({})
    assert params == []


def test_json_schema_to_params_no_type():
    schema = {
        "properties": {
            "data": {"description": "Some data"},
        },
    }
    params = _json_schema_to_params(schema)
    assert params[0].type == "string"  # Default


# ── MCPConnection tests ───────────────────────────────────────


def test_mcp_connection_init():
    cfg = MCPServerConfig(name="test", command="echo")
    conn = MCPConnection(cfg)
    assert conn.config.name == "test"
    assert conn.is_connected is False
    assert conn.tool_names == []


# ── MCPManager tests ──────────────────────────────────────────


class MockMCPConnection(MCPConnection):
    """Test double that doesn't actually start a subprocess."""

    def __init__(self, config: MCPServerConfig, tools: list[dict] | None = None):
        super().__init__(config)
        self._mock_tools = tools or [
            {
                "name": "query",
                "description": "Run a SQL query",
                "input_schema": {
                    "properties": {
                        "sql": {"type": "string", "description": "SQL statement"},
                    },
                    "required": ["sql"],
                },
            },
            {
                "name": "list_tables",
                "description": "List all tables",
                "input_schema": {"properties": {}},
            },
        ]

    async def connect(self) -> None:
        self._tools = self._mock_tools
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._tools = []

    async def call_tool(self, name: str, arguments: dict) -> str:
        return f"mock result for {name}({arguments})"


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def event_bus():
    return EventBus()


async def test_mcp_manager_add_server(registry, event_bus):
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="testdb", command="echo")

    # Patch MCPConnection to use our mock
    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    servers = manager.list_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == "testdb"
    assert servers[0]["connected"] is True
    assert servers[0]["tool_count"] == 2


async def test_mcp_manager_remove_server(registry, event_bus):
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="testdb", command="echo")

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    assert len(manager.list_servers()) == 1
    await manager.remove_server("testdb")
    assert len(manager.list_servers()) == 0


async def test_mcp_manager_list_servers(registry, event_bus):
    manager = MCPManager(registry=registry, event_bus=event_bus)
    assert manager.list_servers() == []


async def test_mcp_tool_registration(registry, event_bus):
    """MCP tools should appear in the ToolRegistry with prefixed names."""
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="mydb", command="echo")

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    tool_names = [t.name for t in registry.list_tools()]
    assert "mcp_mydb_query" in tool_names
    assert "mcp_mydb_list_tables" in tool_names


async def test_mcp_tool_name_prefixing(registry, event_bus):
    """Each tool should be prefixed with mcp_{server_name}_{tool_name}."""
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="pg", command="echo")

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    anthropic_tools = registry.get_anthropic_tools()
    names = [t["name"] for t in anthropic_tools]
    assert all(n.startswith("mcp_pg_") for n in names)


async def test_mcp_tool_execution(registry, event_bus):
    """Calling a registered MCP tool should invoke call_tool on the connection."""
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="db", command="echo")

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    result = await registry.execute("mcp_db_query", {"sql": "SELECT 1"})
    assert result.success is True
    assert "mock result" in str(result.result)


async def test_mcp_tool_unregistration(registry, event_bus):
    """Removing a server should remove its tools from the registry."""
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="db", command="echo")

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    assert len(registry.list_tools()) == 2
    await manager.remove_server("db")
    assert len(registry.list_tools()) == 0


async def test_mcp_disconnect_all(registry, event_bus):
    """disconnect_all should remove all servers and their tools."""
    manager = MCPManager(registry=registry, event_bus=event_bus)

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(MCPServerConfig(name="a", command="echo"))
        await manager.add_server(MCPServerConfig(name="b", command="echo"))

    assert len(manager.list_servers()) == 2
    await manager.disconnect_all()
    assert len(manager.list_servers()) == 0
    assert len(registry.list_tools()) == 0


async def test_mcp_server_event_emitted(registry, event_bus):
    """Adding a server should emit an mcp.server_connected event."""
    manager = MCPManager(registry=registry, event_bus=event_bus)
    cfg = MCPServerConfig(name="evtest", command="echo")

    events = []

    async def _collect(e):
        events.append(e)

    event_bus.subscribe("mcp.*", _collect)

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(cfg)

    assert len(events) >= 1
    assert events[0].topic == "mcp.server_connected"
    assert events[0].data["name"] == "evtest"


async def test_mcp_replace_server(registry, event_bus):
    """Adding a server with existing name should replace it."""
    manager = MCPManager(registry=registry, event_bus=event_bus)

    with patch("agos.mcp.client.MCPConnection", MockMCPConnection):
        await manager.add_server(MCPServerConfig(name="db", command="echo"))
        await manager.add_server(MCPServerConfig(name="db", command="echo2"))

    assert len(manager.list_servers()) == 1
    assert manager.list_servers()[0]["name"] == "db"
