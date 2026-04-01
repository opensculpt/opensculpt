"""DomainDaemon — LLM-powered domain worker.

Spawned by GoalRunner after goals complete. Each daemon does domain-specific
work on a schedule (check stale leads, monitor tickets, verify services).

Two-tier tick design:
  fast_check() — cheap Python (HTTP ping, API query, docker inspect)
  smart_tick() — LLM-powered reasoning (only when fast_check says "needs attention")

This is what fills TheLoom with domain knowledge over weeks, making
Day 30 fundamentally smarter than Day 1.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)

# Max LLM budget per tick — keeps daemons frugal
_MAX_TURNS = 5
_MAX_TOKENS = 10_000


class DomainDaemon(Daemon):
    """LLM-powered domain worker with fast-path gating.

    Config keys:
        task: str           — NL description ("Check CRM for stale leads")
        category: str       — sales, support, devops, knowledge
        skill_paths: list   — paths to .opensculpt/skills/*.md
        check_type: str     — "http", "api_query", "docker", "always"
        check_target: str   — URL or container name for fast check
        role: str           — CapabilityGate role name
    """

    name = "domain"
    description = "Domain-specific background worker"
    icon = "🔧"
    one_shot = False
    default_interval = 3600

    def __init__(self) -> None:
        super().__init__()
        self._loom: Any = None
        self._llm: Any = None
        self._tools: Any = None  # ToolRegistry for mini agent loop

    def set_loom(self, loom: Any) -> None:
        self._loom = loom

    def set_llm(self, llm: Any) -> None:
        self._llm = llm

    async def setup(self) -> None:
        """Build a lightweight tool registry for the mini agent loop."""
        from agos.tools.registry import ToolRegistry
        from agos.tools.schema import ToolSchema, ToolParameter

        self._tools = ToolRegistry()
        T, P = ToolSchema, ToolParameter

        # Import the same handlers the OS agent uses
        from agos.os_agent import _shell, _http, _read_file

        self._tools.register(T(
            name="shell",
            description="Run a shell command.",
            parameters=[P(name="command", description="Shell command")],
        ), _shell)

        self._tools.register(T(
            name="http",
            description="HTTP request to an API.",
            parameters=[
                P(name="url", description="URL"),
                P(name="method", description="GET/POST/PUT/DELETE", required=False),
                P(name="body", description="Request body", required=False),
                P(name="headers", description="JSON headers string", required=False),
            ],
        ), _http)

        self._tools.register(T(
            name="read_file",
            description="Read a file.",
            parameters=[P(name="path", description="File path")],
        ), _read_file)

    async def tick(self) -> None:
        """Two-tier tick: fast_check gates smart_tick. Backs off after repeated empties."""
        task = self.config.get("task", "")
        if not task:
            return

        # 1. Fast check (no LLM)
        needs_attention, check_data = await self._fast_check()

        if not needs_attention:
            # Backoff: stop wasting tokens on hopeless checks
            empty_count = getattr(self, "_consecutive_empty", 0) + 1
            self._consecutive_empty = empty_count
            if empty_count >= 10:
                from agos.daemons.base import DaemonStatus
                self.status = DaemonStatus.PAUSED
                await self.emit("paused", {
                    "reason": f"Nothing found after {empty_count} checks",
                    "task": task[:80],
                })
                _logger.info("DomainDaemon '%s' paused after %d empty checks", self.name, empty_count)
            return

        self._consecutive_empty = 0  # Reset on finding

        # 2. Smart tick (LLM, capped)
        if not self._llm:
            _logger.debug("DomainDaemon '%s' has no LLM — skipping smart tick", self.name)
            return

        result = await self._smart_tick(check_data)
        if not result:
            return

        # 3. Write findings to TheLoom
        summary = result.get("summary", "")
        if self._loom and summary:
            try:
                category = self.config.get("category", "general")
                await self._loom.remember(
                    summary,
                    kind="daemon_finding",
                    tags=["daemon", self.name, category],
                    agent_id=f"daemon:{self.name}",
                )
            except Exception as e:
                _logger.debug("TheLoom write failed for '%s': %s", self.name, e)

        # 4. Emit event + store result
        await self.emit("finding", {
            "task": task[:100],
            "summary": summary[:300],
            "actions": result.get("actions", []),
        })

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=True,
            summary=summary[:200],
            data=result,
        ))

    # ── Fast check (no LLM) ──────────────────────────────────────────────

    async def _fast_check(self) -> tuple[bool, dict]:
        """Cheap gate — only invoke LLM when something needs attention."""
        check_type = self.config.get("check_type", "always")
        target = self.config.get("check_target", "")

        if check_type == "always":
            return True, {}

        if check_type == "http":
            return await self._check_http(target)

        if check_type == "api_query":
            return await self._check_api_query(target)

        if check_type == "docker":
            return await self._check_docker(target)

        return True, {}

    async def _check_http(self, url: str) -> tuple[bool, dict]:
        """Ping URL — needs attention if non-200 or down."""
        if not url:
            return True, {}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    return False, {"status": 200}
                return True, {"status": r.status_code, "body": r.text[:500]}
        except Exception as e:
            return True, {"error": str(e)[:200]}

    async def _check_api_query(self, url: str) -> tuple[bool, dict]:
        """Hit API endpoint — needs attention if results found."""
        if not url:
            return True, {}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.get(url)
                data = r.json()
                # Support common API response shapes
                items = data if isinstance(data, list) else (
                    data.get("list", data.get("data", data.get("results", [])))
                )
                if not items:
                    return False, {"count": 0}
                return True, {
                    "count": len(items),
                    "preview": json.dumps(items[:3], default=str)[:500],
                }
        except Exception as e:
            return True, {"error": str(e)[:200]}

    async def _check_docker(self, container: str) -> tuple[bool, dict]:
        """Check container status — needs attention if not running."""
        if not container:
            return True, {}
        try:
            proc = await asyncio.create_subprocess_shell(
                f"docker inspect --format '{{{{.State.Status}}}}' {container}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            status = out.decode().strip().strip("'\"")
            if status == "running":
                return False, {"container": container, "status": "running"}
            return True, {"container": container, "status": status}
        except Exception as e:
            return True, {"container": container, "error": str(e)[:200]}

    # ── Smart tick (LLM-powered) ─────────────────────────────────────────

    async def _smart_tick(self, check_data: dict) -> dict | None:
        """LLM-powered reasoning. Max 5 turns, 10K tokens."""
        from agos.llm.base import LLMMessage

        skills_context = self._load_skills()
        loom_context = await self._recall_context()
        task = self.config.get("task", "")
        category = self.config.get("category", "general")

        system_prompt = (
            f"You are a domain daemon for {category}.\n"
            f"Your recurring task: {task}\n\n"
            f"SKILL DOCS:\n{skills_context}\n\n"
            f"PREVIOUS KNOWLEDGE:\n{loom_context}\n\n"
            f"CURRENT CHECK DATA:\n{json.dumps(check_data, default=str)}\n\n"
            "Execute your task using tools. Be concise — report findings in 2-3 sentences.\n"
            "Max 5 tool calls. Focus on actionable findings."
        )

        messages: list[LLMMessage] = [
            LLMMessage(role="user", content=f"Run your task now: {task}"),
        ]

        tools = self._tools.get_anthropic_tools() if self._tools else None
        actions: list[dict] = []
        tokens = 0
        final_text = ""

        for turn in range(_MAX_TURNS):
            if tokens >= _MAX_TOKENS:
                break

            try:
                resp = await self._llm.complete(
                    messages=messages,
                    system=system_prompt,
                    tools=tools,
                    max_tokens=2048,
                )
            except Exception as e:
                _logger.warning("DomainDaemon '%s' LLM error: %s", self.name, e)
                break

            tokens += resp.input_tokens + resp.output_tokens

            # No tool calls — done
            if not resp.tool_calls:
                final_text = resp.content or ""
                break

            # Build assistant message
            asst: list[dict] = []
            if resp.content:
                asst.append({"type": "text", "text": resp.content})
            for tc in resp.tool_calls:
                asst.append({
                    "type": "tool_use", "id": tc.id,
                    "name": tc.name, "input": tc.arguments,
                })
            messages.append(LLMMessage(role="assistant", content=asst))

            # Execute tools
            results: list[dict] = []
            for tc in resp.tool_calls:
                if not self._tools:
                    break
                try:
                    res = await self._tools.execute(tc.name, tc.arguments)
                    out = str(res.result) if res.success else str(res.error)
                    if len(out) > 2000:
                        out = out[:1500] + "\n...[truncated]...\n" + out[-300:]
                    results.append({
                        "type": "tool_result", "tool_use_id": tc.id,
                        "content": out, "is_error": not res.success,
                    })
                    actions.append({
                        "tool": tc.name,
                        "args": _trunc(tc.arguments),
                        "ok": res.success,
                        "preview": out[:150],
                    })
                except Exception as e:
                    results.append({
                        "type": "tool_result", "tool_use_id": tc.id,
                        "content": str(e)[:500], "is_error": True,
                    })

            messages.append(LLMMessage(role="user", content=results))

            # Capture final text from last response if it had content
            if resp.content:
                final_text = resp.content

        if not final_text and not actions:
            return None

        return {
            "summary": final_text[:500],
            "actions": actions,
            "tokens_used": tokens,
            "timestamp": time.time(),
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load_skills(self) -> str:
        """Load skill docs from configured paths."""
        skill_paths = self.config.get("skill_paths", [])
        parts = []
        for sp in skill_paths:
            p = Path(sp)
            if p.exists():
                try:
                    content = p.read_text(errors="ignore")[:1000]
                    parts.append(f"--- {p.name} ---\n{content}")
                except Exception:
                    pass
        return "\n\n".join(parts) if parts else "(no skill docs yet)"

    async def _recall_context(self) -> str:
        """Recall relevant knowledge from TheLoom."""
        if not self._loom:
            return "(no knowledge system)"
        try:
            task = self.config.get("task", "")
            recalled = await self._loom.recall(task, limit=5)
            if recalled:
                return "\n".join(f"- {r}" for r in recalled[:5])
        except Exception:
            pass
        return "(no prior knowledge)"

    def to_dict(self) -> dict:
        """Override to include domain-specific info."""
        d = super().to_dict()
        d["task"] = self.config.get("task", "")[:100]
        d["category"] = self.config.get("category", "")
        d["check_type"] = self.config.get("check_type", "always")
        return d


def _trunc(d: dict, max_len: int = 100) -> dict:
    """Truncate dict values for logging."""
    out = {}
    for k, v in d.items():
        s = str(v)
        out[k] = s[:max_len] if len(s) > max_len else s
    return out
