"""Tool Registry — central registry of all tools agents can use."""

from __future__ import annotations

import time
from typing import Any, Callable, Awaitable

from pydantic import BaseModel

from agos.tools.schema import ToolSchema
from agos.exceptions import ToolNotFoundError

ToolHandler = Callable[..., Awaitable[Any]]


class ToolExecutionResult(BaseModel):
    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0


class ToolRegistry:
    """Central registry of all tools available in the system.

    Tools are registered with a schema and an async handler.
    The registry provides discovery (list/search) and execution.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSchema, ToolHandler]] = {}

    def register(self, schema: ToolSchema, handler: ToolHandler) -> None:
        self._tools[schema.name] = (schema, handler)

    def get_tool(self, tool_name: str) -> tuple[ToolSchema, ToolHandler] | None:
        return self._tools.get(tool_name)

    def unregister(self, tool_name: str) -> None:
        self._tools.pop(tool_name, None)

    def list_tools(self) -> list[ToolSchema]:
        return [schema for schema, _ in self._tools.values()]

    def get_anthropic_tools(self, command: str | None = None) -> list[dict]:
        """Get tools in Anthropic API format.

        If command is provided, deferred tools are only included when their
        keywords match the command (Claude Code deferred loading pattern).
        This saves ~70% tool schema tokens on most turns.
        """
        if command is None:
            return [schema.to_anthropic_tool() for schema, _ in self._tools.values()]

        cmd_lower = command.lower()
        cmd_words = set(cmd_lower.split())
        result = []
        for schema, _ in self._tools.values():
            if not schema.deferred:
                # Always-load tools
                result.append(schema.to_anthropic_tool())
            elif schema.keywords and (cmd_words & set(schema.keywords)):
                # Deferred tool with keyword match
                result.append(schema.to_anthropic_tool())
        return result

    async def execute(self, tool_name: str, arguments: dict) -> ToolExecutionResult:
        """Execute a tool by name with the given arguments."""
        entry = self._tools.get(tool_name)
        if entry is None:
            raise ToolNotFoundError(f"Tool '{tool_name}' not found")

        schema, handler = entry
        start = time.monotonic()

        try:
            result = await handler(**arguments)
            elapsed = (time.monotonic() - start) * 1000
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                result=result,
                execution_time_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"{type(e).__name__}: {e}",
                execution_time_ms=elapsed,
            )
