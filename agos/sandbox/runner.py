"""Subprocess runner for sandboxed tool execution.

This script is executed as a subprocess by SandboxedToolExecutor:
    python -m agos.sandbox.runner '{"memory_limit_mb": 512, "cpu_time_limit_s": 60}'

It reads a JSON task from stdin, executes the tool, and writes the
result as JSON to stdout.

Input (stdin): {"tool_name": "shell_exec", "arguments": {"command": "ls"}}
Output (stdout): {"success": true, "result": "...", "error": null, "execution_time_ms": 42}
"""

from __future__ import annotations

import asyncio
import json
import platform
import sys
import time


def _apply_resource_limits(config: dict) -> None:
    """Apply OS-level resource limits (Linux only)."""
    if platform.system() != "Linux":
        return  # Windows/macOS: graceful degradation to timeout-only

    try:
        import resource

        mem_bytes = config.get("memory_limit_mb", 512) * 1024 * 1024
        cpu_secs = config.get("cpu_time_limit_s", 60)

        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_secs, cpu_secs))
    except (ImportError, ValueError, OSError):
        pass  # Best effort


async def _run(task: dict) -> dict:
    """Execute the tool and return the result."""
    from agos.tools.registry import ToolRegistry
    from agos.tools.builtins import register_builtin_tools
    from agos.tools.extended import register_extended_tools

    tool_name = task["tool_name"]
    arguments = task["arguments"]

    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_extended_tools(registry)

    start = time.monotonic()
    result = await registry.execute(tool_name, arguments)
    elapsed = (time.monotonic() - start) * 1000

    return {
        "success": result.success,
        "result": str(result.result) if result.success else None,
        "error": result.error,
        "execution_time_ms": round(elapsed, 1),
    }


def main() -> None:
    # Parse config from argv
    config = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    _apply_resource_limits(config)

    # Read task from stdin
    task_json = sys.stdin.read()
    task = json.loads(task_json)

    # Execute
    result = asyncio.run(_run(task))

    # Write result to stdout
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
