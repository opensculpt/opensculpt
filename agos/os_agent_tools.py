"""OS Agent Tools — tool registration, execution handlers, and evolution hooks.

Extracted from os_agent.py following the industry-standard brain/body separation
(OpenHands Agent/Tools, OpenClaw Brain/Body pattern).

The ToolManager owns the ToolRegistry and handles:
- Base tool registration (shell, read_file, write_file, http, python, think)
- Docker and browser tool packs (dormant until evolution activates)
- Evolution event handlers (builtin_activated, tool_deployed)
- Dynamic evolved tool loading from .opensculpt/evolved/tools/
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import re as _re
from typing import Any

from agos.tools.schema import ToolSchema, ToolParameter
from agos.tools.registry import ToolRegistry
from agos.events.bus import EventBus

_logger = logging.getLogger(__name__)


# ── Tool Manager ──────────────────────────────────────────────────

class ToolManager:
    """Manages tool registration, activation, and evolution hooks.

    Separation of concerns:
    - OSAgent (brain) decides WHAT tool to call
    - ToolManager (body) knows HOW to register and load tools
    """

    def __init__(
        self,
        event_bus: EventBus,
        agent_registry: Any = None,
    ) -> None:
        self.registry = ToolRegistry()
        self._bus = event_bus
        self._agent_registry = agent_registry
        self._dormant_tools: dict[str, bool] = {"docker": False, "browser": False}

    def register_base_tools(self) -> None:
        """Register the core tools available at boot."""
        T, P = ToolSchema, ToolParameter

        self.registry.register(T(
            name="shell",
            description="Run any shell command. You have root. Use for: apt-get, pip, npm, git, ls, ps, curl, make, gcc, etc.",
            parameters=[
                P(name="command", description="Shell command to execute"),
                P(name="timeout", type="integer", description="Timeout seconds (default 60)", required=False),
            ],
        ), shell)

        self.registry.register(T(
            name="read_file",
            description="Read a file or list a directory.",
            parameters=[P(name="path", description="File or directory path")],
        ), read_file)

        self.registry.register(T(
            name="write_file",
            description="Write content to a file. Creates parent dirs.",
            parameters=[
                P(name="path", description="File path"),
                P(name="content", description="Content to write"),
            ],
        ), write_file)

        self.registry.register(T(
            name="http",
            description="HTTP request. Use for APIs, web scraping, downloads.",
            parameters=[
                P(name="url", description="URL"),
                P(name="method", description="GET/POST/PUT/DELETE", required=False),
                P(name="body", description="Request body", required=False),
                P(name="headers", description="JSON headers string", required=False),
            ],
        ), http)

        self.registry.register(T(
            name="python",
            description="Run Python code. Use print() for output.",
            parameters=[P(name="code", description="Python code")],
        ), python)

        # Think tool (OpenHands pattern) — agent reasons without executing.
        async def _think(thought: str) -> str:
            return f"[Thought recorded: {thought[:200]}]"

        self.registry.register(T(
            name="think",
            description="Reason about your approach WITHOUT executing anything. Use this to plan multi-step work, debug why something failed, or decide between approaches. Counts as progress.",
            parameters=[P(name="thought", description="Your reasoning or plan")],
        ), _think)

        # Agent management if registry available
        if self._agent_registry:
            self.registry.register(T(
                name="list_agents",
                description="List installed agents on this system.",
                parameters=[],
            ), make_list_agents(self._agent_registry))

            self.registry.register(T(
                name="manage_agent",
                description="Manage installed agents: setup/start/stop/restart/uninstall/status.",
                parameters=[
                    P(name="action", description="setup|start|stop|restart|uninstall|status"),
                    P(name="name", description="Agent name"),
                    P(name="github_url", description="GitHub URL (for setup)", required=False),
                ],
            ), make_manage_agent(self._agent_registry))

    def subscribe_evolution_events(self) -> None:
        """Wire up EventBus subscriptions for evolution tool deployment."""
        self._bus.subscribe("evolution.builtin_activated", self._on_tool_activated)
        self._bus.subscribe("evolution.tool_deployed", self._on_evolved_tool_deployed)

    async def _on_tool_activated(self, event) -> None:
        """Evolution activated a builtin tool — register it now."""
        module = event.data.get("module", "")
        if "docker" in module and not self._dormant_tools.get("docker"):
            self.register_docker_pack()
            self._dormant_tools["docker"] = True
            _logger.info("Evolution activated docker tools")
        if "browser" in module and not self._dormant_tools.get("browser"):
            self.register_browser_pack()
            self._dormant_tools["browser"] = True
            _logger.info("Evolution activated browser tools")

    async def _on_evolved_tool_deployed(self, event) -> None:
        """Evolution deployed a new tool — load and register it."""
        tool_name = event.data.get("tool", "")
        if not tool_name or self.registry.get_tool(tool_name):
            return
        from agos.config import settings as _s
        tool_file = _s.workspace_dir / "evolved" / "tools" / f"{tool_name}.py"
        if not tool_file.exists():
            return
        try:
            spec = importlib.util.spec_from_file_location(f"evolved_{tool_name}", tool_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            handler = getattr(module, "handler", None)
            if not handler:
                return
            params_raw = getattr(module, "TOOL_PARAMETERS", [])
            parameters = [
                ToolParameter(
                    name=p["name"], type=p.get("type", "string"),
                    description=p.get("description", ""), required=p.get("required", True),
                )
                for p in params_raw if isinstance(p, dict)
            ]
            schema = ToolSchema(
                name=getattr(module, "TOOL_NAME", tool_name),
                description=getattr(module, "TOOL_DESCRIPTION", event.data.get("description", "Evolved tool")),
                parameters=parameters,
            )
            self.registry.register(schema, handler)
            _logger.info("Registered evolved tool: %s", tool_name)
        except Exception as e:
            _logger.warning("Failed to load evolved tool %s: %s", tool_name, e)

    def activate_pack(self, pack: str) -> bool:
        """Manually activate a tool pack (for testing or first-run setup)."""
        if pack == "docker" and not self._dormant_tools.get("docker"):
            self.register_docker_pack()
            self._dormant_tools["docker"] = True
            return True
        if pack == "browser" and not self._dormant_tools.get("browser"):
            self.register_browser_pack()
            self._dormant_tools["browser"] = True
            return True
        return False

    def register_docker_pack(self) -> None:
        """Register Docker tools — called when evolution activates them."""
        T, P = ToolSchema, ToolParameter
        from agos.tools.docker_tool import (
            docker_run, docker_ps, docker_stop, docker_rm,
            docker_logs, docker_pull, docker_network, docker_exec,
        )
        kw = ["docker", "container", "deploy", "postgres", "mysql", "redis", "nginx", "install", "crm", "database"]
        self.registry.register(T(name="docker_run", description="Run a Docker container. Use for installing software (CRM, databases, etc).", parameters=[
            P(name="image", description="Docker image (e.g. 'espocrm/espocrm:latest')"),
            P(name="name", description="Container name", required=False),
            P(name="ports", description="Port mapping (e.g. '8081:80')", required=False),
            P(name="env", description="Env vars as JSON: {\"KEY\": \"value\"}", required=False),
            P(name="network", description="Docker network name", required=False),
            P(name="extra", description="Additional docker flags", required=False),
        ], deferred=True, keywords=kw), docker_run)
        self.registry.register(T(name="docker_ps", description="List running Docker containers.", parameters=[
            P(name="all_containers", type="boolean", description="Show all (including stopped)", required=False),
        ], deferred=True, keywords=kw), docker_ps)
        self.registry.register(T(name="docker_stop", description="Stop a Docker container.", parameters=[
            P(name="name", description="Container name or ID"),
        ], deferred=True, keywords=kw), docker_stop)
        self.registry.register(T(name="docker_rm", description="Remove a Docker container.", parameters=[
            P(name="name", description="Container name or ID"),
            P(name="force", type="boolean", description="Force remove", required=False),
        ], deferred=True, keywords=kw), docker_rm)
        self.registry.register(T(name="docker_logs", description="Get logs from a Docker container.", parameters=[
            P(name="name", description="Container name"),
            P(name="tail", type="integer", description="Number of lines (default 50)", required=False),
        ], deferred=True, keywords=kw), docker_logs)
        self.registry.register(T(name="docker_pull", description="Pull a Docker image.", parameters=[
            P(name="image", description="Image to pull (e.g. 'mysql:8.0')"),
        ], deferred=True, keywords=kw), docker_pull)
        self.registry.register(T(name="docker_network", description="Manage Docker networks (create, rm, ls).", parameters=[
            P(name="action", description="create, rm, or ls"),
            P(name="name", description="Network name"),
        ], deferred=True, keywords=kw), docker_network)
        self.registry.register(T(name="docker_exec", description="Run a command inside a Docker container.", parameters=[
            P(name="container", description="Container name"),
            P(name="command", description="Command to run"),
        ], deferred=True, keywords=kw), docker_exec)

    def register_browser_pack(self) -> None:
        """Register Browser tools — called when evolution activates them."""
        T, P = ToolSchema, ToolParameter
        from agos.tools.browser_tool import (
            browse, browser_fill, browser_click, browser_screenshot, browser_content,
        )
        kw = ["browse", "scrape", "website", "navigate", "click", "screenshot", "browser", "page", "form", "login", "dashboard", "ui"]
        self.registry.register(T(name="browse", description="Open a URL in a headless browser and return the page text. Use for web UIs, CRM dashboards, setup wizards.", parameters=[
            P(name="url", description="URL to navigate to"),
        ], deferred=True, keywords=kw), browse)
        self.registry.register(T(name="browser_fill", description="Fill a form field on the current page.", parameters=[
            P(name="selector", description="CSS selector (e.g. '#username', 'input[name=email]')"),
            P(name="value", description="Text to type"),
        ], deferred=True, keywords=kw), browser_fill)
        self.registry.register(T(name="browser_click", description="Click a button or link on the current page.", parameters=[
            P(name="selector", description="CSS selector or text selector (e.g. 'text=Sign In')"),
        ], deferred=True, keywords=kw), browser_click)
        self.registry.register(T(name="browser_screenshot", description="Take a screenshot of the current browser page.", parameters=[
            P(name="path", description="File path to save screenshot", required=False),
        ], deferred=True, keywords=kw), browser_screenshot)
        self.registry.register(T(name="browser_content", description="Get text content of a page element.", parameters=[
            P(name="selector", description="CSS selector (default 'body')", required=False),
        ], deferred=True, keywords=kw), browser_content)


# ── Tool Handler Functions (pure I/O, no agent state) ─────────────

async def shell(command: str, timeout: int = 60) -> str:
    import subprocess as _sp
    import os as _os
    try:
        cwd = "/app" if _os.path.isdir("/app") else _os.getcwd()
        proc = await asyncio.create_subprocess_shell(
            command, stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        parts = [f"exit={proc.returncode}"]
        if stdout:
            parts.append(stdout.decode(errors="replace")[:6000])
        if stderr:
            parts.append(f"stderr: {stderr.decode(errors='replace')[:3000]}")
        return "\n".join(parts)
    except asyncio.TimeoutError:
        return f"Timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


async def read_file(path: str) -> str:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return f"Not found: {path}"
    if p.is_dir():
        entries = sorted(p.iterdir())
        lines = []
        for e in entries[:100]:
            kind = "DIR " if e.is_dir() else "FILE"
            sz = e.stat().st_size if e.is_file() else 0
            lines.append(f"  {kind} {e.name:40s} {sz:>10,}b")
        return f"{path} ({len(entries)} entries)\n" + "\n".join(lines)
    try:
        c = p.read_text(encoding="utf-8", errors="replace")
        if len(c) > 10000:
            return c[:5000] + f"\n...[{len(c)} chars total]...\n" + c[-3000:]
        return c
    except Exception as e:
        return f"Error: {e}"


async def write_file(path: str, content: str) -> str:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


async def http(url: str, method: str = "GET", body: str = "", headers: str = "") -> str:
    import httpx
    import json
    try:
        hdrs = json.loads(headers) if headers else {}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.request(method, url, content=body or None, headers=hdrs)
            return f"HTTP {r.status_code}\n{r.text[:8000]}"
    except Exception as e:
        return f"Error: {e}"


async def python(code: str) -> str:
    import subprocess as _sp
    import os as _os
    import sys as _sys
    try:
        cwd = "/app" if _os.path.isdir("/app") else _os.getcwd()
        python_cmd = _sys.executable or "python3"
        proc = await asyncio.create_subprocess_exec(
            python_cmd, "-c", code, stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = ""
        if stdout:
            out += stdout.decode(errors="replace")[:6000]
        if stderr:
            out += f"\nstderr: {stderr.decode(errors='replace')[:3000]}"
        return out or "(no output)"
    except asyncio.TimeoutError:
        return "Timed out after 60s"
    except Exception as e:
        return f"Error: {e}"


# ── Factory Closures (capture registry, not self) ─────────────────

def make_list_agents(registry):
    async def _fn() -> str:
        agents = registry.list_agents()
        if not agents:
            return "No agents installed."
        lines = [f"  {a['name']} [{a['runtime']}] {a['status']}" for a in agents]
        return "\n".join(lines)
    return _fn


def make_manage_agent(registry):
    async def _fn(action: str, name: str, github_url: str = "") -> str:
        try:
            if action == "setup":
                a = await registry.setup(name, github_url=github_url)
                return f"Setup {a.display_name}: {a.status.value}"
            agent = registry.get_agent_by_name(name)
            if not agent:
                return f"Agent '{name}' not found."
            if action == "start":
                a = await registry.start(agent.id)
                return f"Started {a.display_name}: {a.status.value}"
            elif action == "stop":
                a = await registry.stop(agent.id)
                return f"Stopped {a.display_name}."
            elif action == "restart":
                if agent.status.value == "running":
                    await registry.stop(agent.id)
                a = await registry.start(agent.id)
                return f"Restarted {a.display_name}."
            elif action == "uninstall":
                await registry.uninstall(agent.id)
                return f"Uninstalled {name}."
            elif action == "status":
                return f"{agent.display_name} [{agent.runtime}] {agent.status.value} mem={agent.memory_limit_mb}MB"
            return f"Unknown action: {action}"
        except Exception as e:
            return f"Error: {e}"
    return _fn


# ── Response Utilities (pure functions) ───────────────────────────

_DANGEROUS_PATTERNS = [
    (r"(?i)DROP\s+TABLE\s+\w+", "[SQL_REDACTED]"),
    (r"(?i)DELETE\s+FROM\s+\w+", "[SQL_REDACTED]"),
    (r"(?i)INSERT\s+INTO\s+\w+", "[SQL_REDACTED]"),
    (r"(?i)UPDATE\s+\w+\s+SET\s+", "[SQL_REDACTED]"),
    (r"(?i);\s*--", "[SQL_REDACTED]"),
    (r"(?i)xp_cmdshell", "[REDACTED]"),
    (r"rm\s+-rf\s+/(?:\s|$)", "[REDACTED]"),
    (r"(?i)password\s*[:=]\s*\S+", "password: [REDACTED]"),
    (r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*\S+", r"\1: [REDACTED]"),
]
_DANGEROUS_RE = [(_re.compile(p), r) for p, r in _DANGEROUS_PATTERNS]


def sanitize_response(text: str) -> str:
    """Strip dangerous patterns from user-facing responses."""
    for pattern, replacement in _DANGEROUS_RE:
        text = pattern.sub(replacement, text)
    return text


def reply(ok: bool, action: str, message: str, data: dict | None = None) -> dict:
    return {"ok": ok, "action": action, "message": message, "data": data or {}}


def trunc_args(args: dict) -> dict:
    return {k: (str(v)[:100] + "..." if len(str(v)) > 100 else str(v)) for k, v in args.items()}
