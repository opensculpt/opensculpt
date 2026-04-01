"""Tests for OSAgent sandbox integration."""

import pytest

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.os_agent import OSAgent
from agos.tools.registry import ToolRegistry
from agos.sandbox.executor import SandboxedToolExecutor, SandboxConfig, SandboxLevel


@pytest.fixture
async def event_bus():
    return EventBus()


@pytest.fixture
async def audit():
    a = AuditTrail(":memory:")
    await a.initialize()
    return a


class TestOSAgentSandbox:
    @pytest.mark.asyncio
    async def test_default_no_sandbox(self, event_bus, audit):
        agent = OSAgent(event_bus=event_bus, audit_trail=audit)
        assert isinstance(agent._tools, ToolRegistry)
        assert isinstance(agent._inner_registry, ToolRegistry)
        assert agent._tools is agent._inner_registry

    @pytest.mark.asyncio
    async def test_with_sandbox_none(self, event_bus, audit):
        config = SandboxConfig(level=SandboxLevel.NONE)
        agent = OSAgent(
            event_bus=event_bus, audit_trail=audit,
            sandbox_config=config,
        )
        assert isinstance(agent._tools, SandboxedToolExecutor)
        assert isinstance(agent._inner_registry, ToolRegistry)
        # Tools should still be listable
        tools = agent._tools.get_anthropic_tools()
        assert len(tools) > 0

    @pytest.mark.asyncio
    async def test_with_sandbox_strict(self, event_bus, audit):
        config = SandboxConfig(
            level=SandboxLevel.STRICT,
            allow_network=False,
        )
        agent = OSAgent(
            event_bus=event_bus, audit_trail=audit,
            sandbox_config=config,
        )
        assert isinstance(agent._tools, SandboxedToolExecutor)

        # Network tools should be blocked
        result = await agent._tools.execute("http", {"url": "https://example.com"})
        assert not result.success
        assert "blocked" in result.error.lower()

    @pytest.mark.asyncio
    async def test_inner_registry_available_for_mcp(self, event_bus, audit):
        config = SandboxConfig(level=SandboxLevel.PROCESS)
        agent = OSAgent(
            event_bus=event_bus, audit_trail=audit,
            sandbox_config=config,
        )
        # MCP should register on inner_registry
        assert isinstance(agent._inner_registry, ToolRegistry)
        assert hasattr(agent._inner_registry, "register")
        # Tools registered on inner are visible through executor
        tool_names = [t["name"] for t in agent._tools.get_anthropic_tools()]
        assert "shell" in tool_names
        assert "python" in tool_names
