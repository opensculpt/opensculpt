"""Tests for MCP config persistence."""

from __future__ import annotations


import pytest

from agos.mcp.client import MCPServerConfig
from agos.mcp.config import (
    load_mcp_configs,
    save_mcp_configs,
    add_mcp_config,
    remove_mcp_config,
)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


async def test_load_missing_file(workspace):
    configs = await load_mcp_configs(workspace)
    assert configs == []


async def test_save_and_load_configs(workspace):
    configs = [
        MCPServerConfig(name="sqlite", command="npx", args=["-y", "server-sqlite"]),
        MCPServerConfig(name="github", command="npx", args=["-y", "server-github"]),
    ]
    await save_mcp_configs(workspace, configs)
    loaded = await load_mcp_configs(workspace)
    assert len(loaded) == 2
    assert loaded[0].name == "sqlite"
    assert loaded[1].name == "github"


async def test_add_config(workspace):
    await add_mcp_config(workspace, MCPServerConfig(name="a", command="echo"))
    configs = await load_mcp_configs(workspace)
    assert len(configs) == 1
    assert configs[0].name == "a"


async def test_add_duplicate_overwrites(workspace):
    await add_mcp_config(workspace, MCPServerConfig(name="a", command="echo"))
    await add_mcp_config(workspace, MCPServerConfig(name="a", command="echo2"))
    configs = await load_mcp_configs(workspace)
    assert len(configs) == 1
    assert configs[0].command == "echo2"


async def test_remove_config(workspace):
    await add_mcp_config(workspace, MCPServerConfig(name="a", command="echo"))
    await add_mcp_config(workspace, MCPServerConfig(name="b", command="echo"))
    await remove_mcp_config(workspace, "a")
    configs = await load_mcp_configs(workspace)
    assert len(configs) == 1
    assert configs[0].name == "b"


async def test_remove_nonexistent(workspace):
    """Removing a non-existent name should not error."""
    await add_mcp_config(workspace, MCPServerConfig(name="a", command="echo"))
    await remove_mcp_config(workspace, "zzz")
    configs = await load_mcp_configs(workspace)
    assert len(configs) == 1
