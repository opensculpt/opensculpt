"""MCP Client — bridges Model Context Protocol servers into AGOS ToolRegistry.

Connects to any MCP server, discovers its tools, and registers them so that
agents can use them alongside built-in tools. Each MCP tool becomes a standard
ToolSchema in the ToolRegistry.

Usage:
    manager = MCPManager(registry=tool_registry, event_bus=bus)
    await manager.add_server(MCPServerConfig(name="sqlite", command="npx",
                                              args=["-y", "@modelcontextprotocol/server-sqlite"]))
    # Now agents see mcp_sqlite_* tools via get_anthropic_tools()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from pydantic import BaseModel, Field

from agos.tools.schema import ToolSchema, ToolParameter
from agos.tools.registry import ToolRegistry

_logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server connection."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


def _json_schema_to_params(input_schema: dict) -> list[ToolParameter]:
    """Convert an MCP inputSchema (JSON Schema) to AGOS ToolParameter list."""
    params: list[ToolParameter] = []
    properties = input_schema.get("properties", {})
    required_list = input_schema.get("required", [])
    for name, prop in properties.items():
        params.append(ToolParameter(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required_list,
        ))
    return params


class MCPConnection:
    """Manages a single MCP server connection lifecycle."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session: Any = None
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._tools: list[dict] = []
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tool_names(self) -> list[str]:
        return [t["name"] for t in self._tools]

    async def connect(self) -> None:
        """Connect to the MCP server via stdio transport."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            raise ImportError(
                "MCP SDK not installed. Run: pip install mcp"
            )

        self._exit_stack = contextlib.AsyncExitStack()

        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env if self.config.env else None,
        )

        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

        await self._session.initialize()

        # Discover tools
        tools_result = await self._session.list_tools()
        self._tools = []
        for tool in tools_result.tools:
            self._tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            })

        self._connected = True
        _logger.info(
            "Connected to MCP server '%s' — %d tools available",
            self.config.name, len(self._tools),
        )

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None
        self._connected = False
        self._tools = []

    async def list_tools(self) -> list[dict]:
        """Return the list of tools exposed by this server."""
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a tool on the MCP server and return the text result."""
        if not self._session or not self._connected:
            raise RuntimeError(f"Not connected to MCP server '{self.config.name}'")

        result = await self._session.call_tool(name, arguments)

        # Extract text content from MCP result
        parts: list[str] = []
        for content_block in result.content:
            if hasattr(content_block, "text"):
                parts.append(content_block.text)
            elif hasattr(content_block, "data"):
                parts.append(f"[binary data: {len(content_block.data)} bytes]")
            else:
                parts.append(str(content_block))

        return "\n".join(parts) if parts else "(no output)"


class MCPManager:
    """Manages multiple MCP server connections and bridges tools to ToolRegistry.

    When a server is added and connected, all its tools are automatically
    registered in the ToolRegistry with prefixed names (mcp_{server}_{tool}).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        event_bus: Any | None = None,
    ) -> None:
        self._registry = registry
        self._bus = event_bus
        self._connections: dict[str, MCPConnection] = {}
        self._registered_tools: dict[str, list[str]] = {}  # server_name -> [tool_names]

    async def add_server(self, config: MCPServerConfig) -> MCPConnection:
        """Add and connect to an MCP server, registering its tools."""
        if config.name in self._connections:
            await self.remove_server(config.name)

        conn = MCPConnection(config)
        await conn.connect()
        self._connections[config.name] = conn

        self._register_mcp_tools(conn)

        if self._bus:
            await self._bus.emit("mcp.server_connected", {
                "name": config.name,
                "tools": conn.tool_names,
            }, source="mcp_manager")

        return conn

    async def remove_server(self, name: str) -> None:
        """Disconnect from an MCP server and unregister its tools."""
        conn = self._connections.pop(name, None)
        if conn is None:
            return

        self._unregister_mcp_tools(name)

        await conn.disconnect()

        if self._bus:
            await self._bus.emit("mcp.server_disconnected", {
                "name": name,
            }, source="mcp_manager")

    async def connect_all(self) -> None:
        """Reconnect to all configured servers."""
        for conn in self._connections.values():
            if not conn.is_connected:
                try:
                    await conn.connect()
                    self._register_mcp_tools(conn)
                except Exception as e:
                    _logger.warning(
                        "Failed to connect to MCP server '%s': %s",
                        conn.config.name, e,
                    )

    async def disconnect_all(self) -> None:
        """Disconnect from all servers and unregister all tools."""
        for name in list(self._connections.keys()):
            await self.remove_server(name)

    def list_servers(self) -> list[dict]:
        """List all configured MCP servers and their status."""
        servers: list[dict] = []
        for name, conn in self._connections.items():
            servers.append({
                "name": name,
                "command": conn.config.command,
                "args": conn.config.args,
                "connected": conn.is_connected,
                "tools": conn.tool_names,
                "tool_count": len(conn.tool_names),
            })
        return servers

    def _register_mcp_tools(self, connection: MCPConnection) -> None:
        """Register MCP server tools in the ToolRegistry."""
        server_name = connection.config.name
        registered: list[str] = []

        for tool_info in asyncio.get_event_loop().run_until_complete(
            connection.list_tools()
        ) if not connection._tools else connection._tools:
            original_name = tool_info["name"]
            prefixed_name = f"mcp_{server_name}_{original_name}"

            params = _json_schema_to_params(tool_info.get("input_schema", {}))
            schema = ToolSchema(
                name=prefixed_name,
                description=f"[MCP:{server_name}] {tool_info.get('description', '')}",
                parameters=params,
            )

            # Create a closure to capture the connection and original tool name
            async def _handler(
                _conn=connection,
                _name=original_name,
                **kwargs: Any,
            ) -> str:
                return await _conn.call_tool(_name, kwargs)

            self._registry.register(schema, _handler)
            registered.append(prefixed_name)

        self._registered_tools[server_name] = registered
        _logger.info(
            "Registered %d tools from MCP server '%s'",
            len(registered), server_name,
        )

    def _unregister_mcp_tools(self, server_name: str) -> None:
        """Remove all tools from a server from the ToolRegistry."""
        tool_names = self._registered_tools.pop(server_name, [])
        for name in tool_names:
            self._registry.unregister(name)
