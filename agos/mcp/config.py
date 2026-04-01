"""MCP server configuration persistence.

Stores MCP server configs as JSON in the workspace directory so they
survive restarts. Format: {workspace_dir}/mcp_servers.json
"""

from __future__ import annotations

from pathlib import Path

import orjson

from agos.mcp.client import MCPServerConfig

_CONFIG_FILENAME = "mcp_servers.json"


def _config_path(workspace_dir: Path) -> Path:
    return workspace_dir / _CONFIG_FILENAME


async def load_mcp_configs(workspace_dir: Path) -> list[MCPServerConfig]:
    """Load MCP server configurations from disk."""
    path = _config_path(workspace_dir)
    if not path.exists():
        return []
    try:
        data = orjson.loads(path.read_bytes())
        return [MCPServerConfig(**item) for item in data]
    except Exception:
        return []


async def save_mcp_configs(
    workspace_dir: Path, configs: list[MCPServerConfig]
) -> None:
    """Save MCP server configurations to disk."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    path = _config_path(workspace_dir)
    data = [c.model_dump() for c in configs]
    path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))


async def add_mcp_config(
    workspace_dir: Path, config: MCPServerConfig
) -> None:
    """Add or update an MCP server configuration."""
    configs = await load_mcp_configs(workspace_dir)
    # Replace if name exists
    configs = [c for c in configs if c.name != config.name]
    configs.append(config)
    await save_mcp_configs(workspace_dir, configs)


async def remove_mcp_config(workspace_dir: Path, name: str) -> None:
    """Remove an MCP server configuration by name."""
    configs = await load_mcp_configs(workspace_dir)
    configs = [c for c in configs if c.name != name]
    await save_mcp_configs(workspace_dir, configs)
