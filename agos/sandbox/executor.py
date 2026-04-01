"""Sandboxed Tool Executor — per-agent isolation with resource limits.

Wraps a ToolRegistry to execute tools in isolated subprocesses instead of
in-process. Three levels:
  - NONE: current behavior, pass-through to inner registry
  - PROCESS: run tool in subprocess with memory/CPU limits
  - STRICT: subprocess + restricted file paths + optional network block

Usage:
    from agos.sandbox.executor import SandboxedToolExecutor, SandboxConfig, SandboxLevel

    config = SandboxConfig(level=SandboxLevel.PROCESS, memory_limit_mb=256)
    executor = SandboxedToolExecutor(inner_registry=registry, config=config)
    result = await executor.execute("shell_exec", {"command": "ls"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from agos.tools.registry import ToolRegistry, ToolExecutionResult

_logger = logging.getLogger(__name__)


class SandboxLevel(str, Enum):
    NONE = "none"
    PROCESS = "process"
    STRICT = "strict"


class SandboxConfig(BaseModel):
    """Configuration for sandboxed tool execution."""

    level: SandboxLevel = SandboxLevel.NONE
    memory_limit_mb: int = 512
    cpu_time_limit_s: int = 60
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    allow_network: bool = True


# Tools that involve file paths — checked in STRICT mode
_PATH_TOOLS = {"file_read", "file_write", "read_file", "write_file"}

# Tools that use network — blocked in STRICT mode when allow_network=False
_NETWORK_TOOLS = {"http_request", "http", "web_search"}


class SandboxedToolExecutor:
    """Drop-in replacement for ToolRegistry with per-agent isolation.

    Implements the same interface (get_anthropic_tools, execute) so it
    can be used wherever a ToolRegistry is expected (duck typing).
    """

    def __init__(self, inner_registry: ToolRegistry, config: SandboxConfig) -> None:
        self._inner = inner_registry
        self._config = config

    def get_anthropic_tools(self) -> list[dict]:
        """Delegate tool listing to the inner registry."""
        return self._inner.get_anthropic_tools()

    def list_tools(self):
        """Delegate tool listing to the inner registry."""
        return self._inner.list_tools()

    async def execute(self, tool_name: str, arguments: dict) -> ToolExecutionResult:
        """Execute a tool, optionally in a sandboxed subprocess."""
        if self._config.level == SandboxLevel.NONE:
            return await self._inner.execute(tool_name, arguments)

        # STRICT mode checks before execution
        if self._config.level == SandboxLevel.STRICT:
            # Block network tools if configured
            if not self._config.allow_network and tool_name in _NETWORK_TOOLS:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error="Network access blocked by sandbox policy",
                )

            # Validate file paths against allowed/blocked lists
            if tool_name in _PATH_TOOLS:
                path_arg = arguments.get("path", "")
                if not self._check_path(path_arg):
                    return ToolExecutionResult(
                        tool_name=tool_name,
                        success=False,
                        error=f"Path '{path_arg}' blocked by sandbox policy",
                    )

        return await self._execute_sandboxed(tool_name, arguments)

    def _check_path(self, path_str: str) -> bool:
        """Check if a path is allowed under STRICT sandbox policy."""
        if not path_str:
            return True

        path = Path(path_str).resolve()
        path_s = str(path)

        # Check blocked paths first
        for blocked in self._config.blocked_paths:
            if path_s.startswith(str(Path(blocked).resolve())):
                return False

        # If allowed_paths is empty, allow everything not blocked
        if not self._config.allowed_paths:
            return True

        # Check if path is under any allowed path
        for allowed in self._config.allowed_paths:
            if path_s.startswith(str(Path(allowed).resolve())):
                return True

        return False

    async def _execute_sandboxed(
        self, tool_name: str, arguments: dict
    ) -> ToolExecutionResult:
        """Run tool execution in a subprocess with resource limits."""
        import time as _time

        start = _time.monotonic()

        task_json = json.dumps({
            "tool_name": tool_name,
            "arguments": arguments,
        })

        config_json = json.dumps({
            "memory_limit_mb": self._config.memory_limit_mb,
            "cpu_time_limit_s": self._config.cpu_time_limit_s,
        })

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "agos.sandbox.runner", config_json,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=task_json.encode()),
                timeout=self._config.cpu_time_limit_s + 10,
            )

            elapsed = (_time.monotonic() - start) * 1000

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[:2000] if stderr else "Unknown error"
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Sandbox subprocess failed (exit={proc.returncode}): {err}",
                    execution_time_ms=elapsed,
                )

            try:
                result_data = json.loads(stdout.decode())
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Failed to parse sandbox output: {e}",
                    execution_time_ms=elapsed,
                )

            return ToolExecutionResult(
                tool_name=tool_name,
                success=result_data.get("success", False),
                result=result_data.get("result"),
                error=result_data.get("error"),
                execution_time_ms=elapsed,
            )

        except asyncio.TimeoutError:
            elapsed = (_time.monotonic() - start) * 1000
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Sandbox timeout after {self._config.cpu_time_limit_s}s",
                execution_time_ms=elapsed,
            )
        except Exception as e:
            elapsed = (_time.monotonic() - start) * 1000
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Sandbox error: {e}",
                execution_time_ms=elapsed,
            )
