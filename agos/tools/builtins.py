"""Built-in tools — the capabilities agents are born with."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import httpx

from agos.tools.schema import ToolSchema, ToolParameter
from agos.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools with the registry."""

    # ── file_read ─────────────────────────────────────────────────────────
    registry.register(
        ToolSchema(
            name="file_read",
            description="Read the contents of a file. Returns the file's text content.",
            parameters=[
                ToolParameter(name="path", description="Path to the file to read"),
            ],
        ),
        _file_read,
    )

    # ── file_write ────────────────────────────────────────────────────────
    registry.register(
        ToolSchema(
            name="file_write",
            description="Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
            parameters=[
                ToolParameter(name="path", description="Path to write to"),
                ToolParameter(name="content", description="Content to write"),
            ],
        ),
        _file_write,
    )

    # ── shell_exec ────────────────────────────────────────────────────────
    registry.register(
        ToolSchema(
            name="shell_exec",
            description=(
                "Execute a shell command and return its output. "
                "Use this for running programs, listing directories, git commands, etc."
            ),
            parameters=[
                ToolParameter(name="command", description="The shell command to execute"),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Timeout in seconds (default 30)",
                    required=False,
                ),
            ],
        ),
        _shell_exec,
    )

    # ── http_request ──────────────────────────────────────────────────────
    registry.register(
        ToolSchema(
            name="http_request",
            description="Make an HTTP request and return the response.",
            parameters=[
                ToolParameter(name="url", description="The URL to request"),
                ToolParameter(
                    name="method",
                    description="HTTP method (GET, POST, etc.). Defaults to GET.",
                    required=False,
                ),
                ToolParameter(
                    name="body",
                    description="Request body for POST/PUT requests",
                    required=False,
                ),
            ],
        ),
        _http_request,
    )

    # ── python_exec ───────────────────────────────────────────────────────
    registry.register(
        ToolSchema(
            name="python_exec",
            description="Execute Python code and return the output. Use print() to produce output.",
            parameters=[
                ToolParameter(name="code", description="Python code to execute"),
            ],
        ),
        _python_exec,
    )


# ── Tool Implementations ─────────────────────────────────────────────────────


async def _file_read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"


async def _file_write(path: str, content: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"


async def _shell_exec(command: str, timeout: int = 30) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = f"exit_code={proc.returncode}\n"
        if stdout:
            output += f"stdout:\n{stdout.decode(errors='replace')[:5000]}\n"
        if stderr:
            output += f"stderr:\n{stderr.decode(errors='replace')[:2000]}\n"
        return output
    except asyncio.TimeoutError:
        # Kill the hung process so it doesn't leak
        try:
            proc.kill()
        except Exception:
            pass
        return f"Error: Command timed out after {timeout}s (process killed). Command: {command[:100]}"
    except Exception as e:
        return f"Error: {e}"


async def _http_request(url: str, method: str = "GET", body: str = "") -> str:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(method, url, content=body if body else None)
            return f"HTTP {resp.status_code}\n{resp.text[:5000]}"
    except Exception as e:
        return f"Error: {e}"


async def _python_exec(code: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", code,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = ""
        if stdout:
            output += stdout.decode(errors="replace")[:5000]
        if stderr:
            output += f"\nstderr: {stderr.decode(errors='replace')[:2000]}"
        return output or "(no output)"
    except asyncio.TimeoutError:
        return "Error: Execution timed out after 30s"
    except Exception as e:
        return f"Error: {e}"
