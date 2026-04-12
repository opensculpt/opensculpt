"""OS Agent Sub-Agent Runner — spawns and manages sub-agent Claude loops.

Extracted from os_agent.py following the industry pattern of separating
sub-process management from the main agent loop (OpenHands agent delegation,
Unix fork() model).

Like Unix fork() — every sub-agent is a replica of the OS agent with FULL
capabilities (shell, docker, browser, http, python, etc.) but specialized
for its task. Sub-agents are powered by the same LLM and inherit all tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry

_logger = logging.getLogger(__name__)


class SubAgentRunner:
    """Manages sub-agent lifecycle: spawn, run, check.

    Receives references to shared infrastructure (LLM, tools, bus, etc.)
    from the parent OSAgent. Owns the _sub_agents dict.
    """

    def __init__(
        self,
        event_bus: EventBus,
        audit: AuditTrail,
        agent_registry: Any = None,
    ) -> None:
        self._bus = event_bus
        self._audit = audit
        self._registry = agent_registry
        self._sub_agents: dict[str, dict] = {}

        # These are set after init by the OSAgent (avoids circular deps)
        self._llm: Any = None
        self._tools: Any = None
        self._approval: Any = None
        self._loom: Any = None
        self._pattern_registry: Any = None
        self._resource_registry: Any = None
        self._capability_gate: Any = None
        self._daemon_manager: Any = None
        self._cheap_llm: Any = None

    def set_refs(
        self,
        llm: Any = None,
        tools: Any = None,
        approval: Any = None,
        loom: Any = None,
        pattern_registry: Any = None,
        resource_registry: Any = None,
        capability_gate: Any = None,
        daemon_manager: Any = None,
        cheap_llm: Any = None,
    ) -> None:
        """Set shared references from the parent OSAgent."""
        if llm is not None:
            self._llm = llm
        if tools is not None:
            self._tools = tools
        if approval is not None:
            self._approval = approval
        if loom is not None:
            self._loom = loom
        if pattern_registry is not None:
            self._pattern_registry = pattern_registry
        if resource_registry is not None:
            self._resource_registry = resource_registry
        if capability_gate is not None:
            self._capability_gate = capability_gate
        if daemon_manager is not None:
            self._daemon_manager = daemon_manager
        if cheap_llm is not None:
            self._cheap_llm = cheap_llm

    def select_design_pattern(self, task: str) -> tuple[str, str]:
        """Select the best agentic design pattern(s) for a task.

        Uses the PatternRegistry (evolvable, fitness-weighted) if available,
        falls back to simple keyword matching otherwise.
        """
        if self._pattern_registry:
            selected = self._pattern_registry.select_for_task(task, count=2)
            if selected:
                names = ", ".join(p.name for p in selected)
                instructions = "\n\n".join(p.instructions for p in selected)
                return names, instructions

        task_lower = task.lower()
        if any(w in task_lower for w in ["review", "analyze", "audit", "evaluate"]):
            return "reflection", "PATTERN: Reflection - draft, self-critique, revise. Minimum 2 passes."
        if any(w in task_lower for w in ["install", "set up", "deploy", "configure"]):
            return "planning", "PATTERN: Planning - plan steps, execute in order, verify each step."
        if any(w in task_lower for w in ["monitor", "watch", "alert", "track"]):
            return "goal_monitoring", "PATTERN: Goal Monitoring - define success, track progress, alert on deviation."
        return "tool_use", "PATTERN: Tool Use - use tools aggressively, verify results."

    async def spawn(self, name: str, task: str, persona: str = "",
                    goal_id: str = "") -> str:
        """Spawn a sub-agent that works on a task independently (fire-and-forget)."""
        if not self._llm:
            return "Error: No LLM available"

        pattern_name, pattern_instructions = self.select_design_pattern(task)

        agent_id = f"sub_{name}_{int(time.time()) % 10000}"
        self._sub_agents[name] = {
            "id": agent_id, "task": task, "status": "running",
            "result": None, "pattern": pattern_name, "goal_id": goal_id,
        }

        await self._bus.emit("os.sub_agent.spawned", {
            "name": name, "task": task[:200], "agent_id": agent_id,
        }, source="os_agent")

        if self._registry:
            try:
                self._registry.register_live_agent(agent_id, name, task, source="os_agent")
            except Exception:
                pass

        await self._bus.emit("agent.spawned", {
            "id": agent_id, "agent": name,
            "role": persona or task[:60],
        }, source="os_agent")
        await self._audit.record(AuditEntry(
            agent_id=agent_id, agent_name=name,
            action="state_change", detail="created -> running",
            success=True,
        ))

        asyncio.create_task(self.run(name, task, persona, pattern_instructions))
        return f"Sub-agent '{name}' spawned (pattern: {pattern_name}) working on: {task[:100]}"

    async def spawn_and_wait(self, name: str, task: str,
                             persona: str = "", timeout: int = 300,
                             goal_id: str = "") -> dict:
        """Spawn ONE sub-agent and WAIT for it to complete.

        Used by GoalRunner to enforce sequential phase execution.
        """
        await self.spawn(name, task, persona, goal_id=goal_id)

        start = time.time()
        while time.time() - start < timeout:
            agent = self._sub_agents.get(name, {})
            status = agent.get("status", "")
            if status in ("done", "error"):
                return {
                    "ok": status == "done",
                    "message": agent.get("result", "") or "",
                    "data": agent.get("data", {}),
                }
            await asyncio.sleep(3)

        return {"ok": False, "message": f"Agent '{name}' timed out after {timeout}s"}

    async def check(self, name: str) -> str:
        """Check a sub-agent's status."""
        if name not in self._sub_agents:
            return f"No sub-agent named '{name}'. Active: {list(self._sub_agents.keys())}"
        agent = self._sub_agents[name]
        if agent["status"] == "running":
            return f"Sub-agent '{name}' is still working on: {agent['task'][:100]}"
        result = agent.get("result", "(no result)")
        return f"Sub-agent '{name}' finished ({agent['status']}).\n\nResult:\n{result}"

    # ── The 563-line _run_sub_agent stays in os_agent.py for now ──
    # It's too tightly coupled to extract in one step (accesses 10+ self._
    # vars, builds LLM messages, processes tool calls). The spawn/wait/check
    # methods above are the clean public API. The run() method below is a
    # thin wrapper that delegates back to the OSAgent's _run_sub_agent.
    #
    # Phase 2 of the refactor: extract the LLM loop into a shared
    # ConversationLoop class that both execute() and run() can use.

    async def run(self, name: str, task: str, persona: str,
                  pattern_instructions: str = "") -> None:
        """Run a sub-agent's task. Delegates to OSAgent._run_sub_agent for now.

        This will be extracted fully in phase 2 of the refactor when the
        shared ConversationLoop class is created.
        """
        # This is set by OSAgent after init
        if hasattr(self, '_run_impl'):
            await self._run_impl(name, task, persona, pattern_instructions)
        else:
            _logger.error("SubAgentRunner.run() called but _run_impl not set")
            self._sub_agents[name]["status"] = "error"
            self._sub_agents[name]["result"] = "Error: SubAgentRunner not fully wired"
