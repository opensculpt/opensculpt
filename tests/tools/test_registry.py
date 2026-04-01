"""Tests for the tool registry."""

import pytest

from agos.tools.schema import ToolSchema, ToolParameter
from agos.tools.registry import ToolRegistry
from agos.exceptions import ToolNotFoundError


@pytest.mark.asyncio
async def test_register_and_execute():
    registry = ToolRegistry()

    async def echo(text: str) -> str:
        return f"echo: {text}"

    registry.register(
        ToolSchema(
            name="echo",
            description="Echo text",
            parameters=[ToolParameter(name="text", description="Text to echo")],
        ),
        echo,
    )

    result = await registry.execute("echo", {"text": "hello"})
    assert result.success
    assert result.result == "echo: hello"
    assert result.execution_time_ms >= 0


@pytest.mark.asyncio
async def test_execute_nonexistent_raises():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await registry.execute("nonexistent", {})


@pytest.mark.asyncio
async def test_execute_handler_error():
    registry = ToolRegistry()

    async def fail() -> str:
        raise ValueError("boom")

    registry.register(
        ToolSchema(name="fail", description="Always fails"),
        fail,
    )

    result = await registry.execute("fail", {})
    assert not result.success
    assert "boom" in result.error


def test_list_tools():
    registry = ToolRegistry()

    async def noop() -> str:
        return ""

    registry.register(ToolSchema(name="tool1", description="T1"), noop)
    registry.register(ToolSchema(name="tool2", description="T2"), noop)

    tools = registry.list_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"tool1", "tool2"}


def test_anthropic_format():
    registry = ToolRegistry()

    async def noop() -> str:
        return ""

    registry.register(
        ToolSchema(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(name="arg1", description="First arg"),
                ToolParameter(name="arg2", type="integer", description="Second arg", required=False),
            ],
        ),
        noop,
    )

    tools = registry.get_anthropic_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "test_tool"
    assert "arg1" in tool["input_schema"]["properties"]
    assert "arg1" in tool["input_schema"]["required"]
    assert "arg2" not in tool["input_schema"]["required"]


def test_builtin_tools_registered(tool_registry):
    tools = tool_registry.list_tools()
    names = {t.name for t in tools}
    assert "file_read" in names
    assert "file_write" in names
    assert "shell_exec" in names
    assert "http_request" in names
    assert "python_exec" in names
