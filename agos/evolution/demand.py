"""Demand-driven evolution — evolve what users actually need.

Instead of blindly scanning arxiv on a timer, this module collects
real signals from user activity, errors, and tool failures, then
directs the evolution engine to fix actual problems and build
missing capabilities.

Demand signals:
  - User command failures (os.error)
  - Tool execution failures (os.tool_result with ok=False)
  - Missing tool requests (tool_not_found)
  - Agent crashes (agent.error)
  - Repeated error patterns
  - Slow tool executions
  - User feedback ("this doesn't work", "I need X")
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agos.events.bus import Event

_logger = logging.getLogger(__name__)


@dataclass
class DemandSignal:
    """A single demand from the environment."""
    kind: str          # "error", "missing_tool", "slow_tool", "user_need", "agent_crash"
    source: str        # what component/tool triggered it
    description: str   # human-readable description of the need
    priority: float    # 0.0-1.0, higher = more urgent
    count: int = 1     # how many times this has occurred
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)
    # Lifecycle tracking — prevent infinite loops (G6)
    attempts: int = 0         # how many times evolution tried to resolve this
    last_attempt: float = 0   # when evolution last attempted resolution
    status: str = "active"    # active | attempting | escalated | resolved
    # Feedback loop — track what was tried so the LLM doesn't repeat failures
    failed_actions: list[str] = field(default_factory=list)  # ["create_tool", "patch_source"]
    last_failure: str = ""    # human-readable reason the last attempt failed

    @property
    def age_hours(self) -> float:
        return (time.time() - self.first_seen) / 3600

    @property
    def should_attempt(self) -> bool:
        """Whether evolution should attempt this demand again.

        Implements exponential backoff: wait 2^attempts minutes between retries.
        After 6 attempts, demand is auto-escalated to user.
        """
        if self.status in ("resolved", "escalated"):
            return False
        if self.attempts == 0:
            return True
        # Exponential backoff: 1min, 2min, 4min, 8min, 16min, 32min
        backoff_seconds = min(1920, 60 * (2 ** (self.attempts - 1)))
        return (time.time() - self.last_attempt) > backoff_seconds

    def mark_attempt(self) -> None:
        """Record an evolution attempt."""
        self.attempts += 1
        self.last_attempt = time.time()
        self.status = "attempting"
        if self.attempts >= 6:
            self.status = "escalated"

    def mark_resolved(self) -> None:
        """Mark demand as resolved — evolution succeeded."""
        self.status = "resolved"

    def merge(self, other: "DemandSignal") -> None:
        """Merge a duplicate signal — increases count and priority."""
        self.count += other.count
        self.last_seen = max(self.last_seen, other.last_seen)
        # Repeated problems become higher priority
        self.priority = min(1.0, self.priority + 0.05 * other.count)
        # If a resolved demand recurs, reactivate it
        if self.status == "resolved":
            self.status = "active"
            self.attempts = 0


class DemandCollector:
    """Collects demand signals from the event bus and ranks evolution priorities.

    Subscribes to failure/error events across the OS and builds a
    prioritized queue of what the evolution engine should work on next.
    """

    def __init__(self, max_signals: int = 200) -> None:
        self._signals: dict[str, DemandSignal] = {}  # keyed by dedup key
        self._max_signals = max_signals
        self._command_errors: dict[str, int] = defaultdict(int)  # command pattern -> count
        self._tool_failures: dict[str, int] = defaultdict(int)   # tool_name -> failure count
        self._tool_slow: dict[str, list[float]] = defaultdict(list)  # tool_name -> exec times
        self._missing_capabilities: list[str] = []
        self._user_frustrations: list[dict] = []  # commands that got error responses

    def _persist(self) -> None:
        """Persist demands to disk so they survive restarts."""
        try:
            import json
            from pathlib import Path
            from agos.config import settings
            path = Path(settings.workspace_dir) / "demand_signals.json"
            path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass  # Best-effort — don't crash on persistence failure

    def subscribe(self, bus) -> None:
        """Wire up to the event bus — listen for all failure signals."""
        bus.subscribe("os.error", self._on_os_error)
        bus.subscribe("os.tool_result", self._on_tool_result)
        bus.subscribe("os.complete", self._on_command_complete)
        bus.subscribe("os.capability_gap", self._on_capability_gap)
        bus.subscribe("agent.error", self._on_agent_error)
        bus.subscribe("evolution.sandbox_failed", self._on_sandbox_failed)
        bus.subscribe("evolution.codegen_failed", self._on_codegen_failed)
        bus.subscribe("process.error", self._on_process_error)
        bus.subscribe("phase_failed", self._on_phase_failed)
        bus.subscribe("phase_completed", self._on_phase_succeeded)
        bus.subscribe("principles_reinforced", self._on_principles_reinforced)
        bus.subscribe("evolution.impasse", self._on_impasse)
        bus.subscribe("os.memory_critical", self._on_memory_critical)
        bus.subscribe("os.memory_warning", self._on_memory_warning)
        bus.subscribe("os.phase_skipped", self._on_phase_skipped)
        _logger.info("DemandCollector subscribed to failure events")

    # ── Event handlers ──

    async def _on_os_error(self, event: Event) -> None:
        """User command failed entirely."""
        cmd = event.data.get("command", "")[:100]
        error = event.data.get("error", "")[:200]
        self._command_errors[cmd] += 1

        # Classify the error
        kind, desc, priority = self._classify_error(cmd, error)
        self._add_signal(
            key=f"os_error:{cmd[:50]}",
            kind=kind,
            source="os_agent",
            description=desc,
            priority=priority,
            context={"command": cmd, "error": error},
        )

    async def _on_tool_result(self, event: Event) -> None:
        """Tool succeeded or failed — track both for patterns."""
        tool = event.data.get("tool", "")
        ok = event.data.get("ok", True)
        preview = event.data.get("preview", "")

        if not ok:
            self._tool_failures[tool] += 1
            fail_count = self._tool_failures[tool]
            priority = min(0.9, 0.3 + 0.1 * fail_count)
            self._add_signal(
                key=f"tool_fail:{tool}",
                kind="error",
                source=tool,
                description=f"Tool '{tool}' failed {fail_count} times. Last error: {preview[:100]}",
                priority=priority,
                context={"tool": tool, "failures": fail_count, "last_error": preview[:100]},
            )

        # Even "successful" shell commands can indicate missing capabilities
        # e.g. shell runs `docker pull` but docker isn't installed → exit=1 in output
        if ok and tool == "shell" and preview:
            preview_lower = preview.lower()
            _missing_patterns = {
                "docker": ("docker", "container orchestration and deployment"),
                "kubectl": ("kubernetes", "Kubernetes cluster management"),
                "psql": ("database", "PostgreSQL database management"),
                "mysql": ("database", "MySQL database management"),
                "playwright": ("browser", "browser automation and web interaction"),
                "npm": ("nodejs", "Node.js package management"),
            }
            for cmd, (tool_name, desc) in _missing_patterns.items():
                if (f"'{cmd}' is not recognized" in preview_lower
                        or "command not found" in preview_lower
                        or "not found" in preview_lower
                        or "failed to connect" in preview_lower
                        or "cannot find" in preview_lower
                        or "is not installed" in preview_lower) and cmd in preview_lower:
                    self._add_signal(
                        key=f"missing_tool:{tool_name}",
                        kind="missing_tool",
                        source="shell",
                        description=f"Command '{cmd}' not available — need {desc}",
                        priority=0.8,
                        context={"tool": tool_name, "command": cmd, "error": preview[:150]},
                    )

    async def _on_command_complete(self, event: Event) -> None:
        """User command completed — check for quality signals."""
        tokens = event.data.get("tokens", 0)
        turns = event.data.get("turns", 0)
        _steps = event.data.get("steps", 0)

        # High token usage = LLM struggling with the task
        if tokens > 50_000:
            cmd = event.data.get("command", "")[:80]
            self._add_signal(
                key=f"expensive_cmd:{cmd[:40]}",
                kind="user_need",
                source="os_agent",
                description=f"Command used {tokens:,} tokens ({turns} turns) — OS agent struggled. Command: {cmd}",
                priority=0.4,
                context={"command": cmd, "tokens": tokens, "turns": turns},
            )

        # Many turns = task is hard, could benefit from a dedicated tool
        if turns > 15:
            cmd = event.data.get("command", "")[:80]
            self._add_signal(
                key=f"hard_task:{cmd[:40]}",
                kind="user_need",
                source="os_agent",
                description=f"Task took {turns} turns — might need a dedicated tool or agent. Command: {cmd}",
                priority=0.5,
                context={"command": cmd, "turns": turns},
            )

    async def _on_capability_gap(self, event: Event) -> None:
        """OS agent used shell workarounds — needs native tools."""
        workarounds = event.data.get("shell_workarounds", [])
        command = event.data.get("command", "")
        tokens = event.data.get("tokens", 0)

        _tool_descriptions = {
            "docker": "Container management — pull, run, stop, logs, compose for Docker",
            "docker-compose": "Multi-container orchestration with Docker Compose",
            "kubectl": "Kubernetes cluster and pod management",
            "helm": "Kubernetes package management with Helm charts",
            "psql": "PostgreSQL database queries and management",
            "mysql": "MySQL database queries and management",
            "mongo": "MongoDB database operations",
            "redis-cli": "Redis cache operations",
            "npm": "Node.js package management",
            "npx": "Node.js package execution",
            "playwright": "Browser automation for web interaction",
            "curl": "HTTP requests (already have http tool — may need upgrade)",
            "pip": "Python package management",
        }

        for cmd in workarounds:
            _desc = _tool_descriptions.get(cmd, f"Native {cmd} tool for the OS")
            self._add_signal(
                key=f"capability_gap:{cmd}",
                kind="missing_tool",
                source="os_agent",
                description=f"OS used shell('{cmd}') as workaround — needs native {cmd} tool. User task: {command[:60]}",
                priority=0.7,
                context={"tool": cmd, "command": command[:100], "tokens_used": tokens},
            )
            _logger.info("Capability gap: %s (from command: %s)", cmd, command[:50])

    async def _on_agent_error(self, event: Event) -> None:
        """System agent crashed."""
        agent = event.data.get("agent", "unknown")
        error = event.data.get("error", "")[:200]
        self._add_signal(
            key=f"agent_crash:{agent}",
            kind="agent_crash",
            source=agent,
            description=f"Agent '{agent}' crashed: {error}",
            priority=0.7,
            context={"agent": agent, "error": error},
        )

    async def _on_sandbox_failed(self, event: Event) -> None:
        """Evolved code failed sandbox — evolution itself needs help."""
        pattern = event.data.get("pattern", "")
        error = event.data.get("error", "")[:150]
        self._add_signal(
            key=f"sandbox_fail:{pattern[:30]}",
            kind="error",
            source="evolution",
            description=f"Evolved pattern '{pattern}' failed sandbox: {error}",
            priority=0.3,  # Lower priority — evolution internal issue
            context={"pattern": pattern, "error": error},
        )

    async def _on_codegen_failed(self, event: Event) -> None:
        """Code generation failed for a pattern."""
        pattern = event.data.get("pattern", "")
        self._add_signal(
            key=f"codegen_fail:{pattern[:30]}",
            kind="error",
            source="evolution",
            description=f"Code generation failed for '{pattern}'",
            priority=0.2,
            context={"pattern": pattern},
        )

    async def _on_process_error(self, event: Event) -> None:
        """OS process crashed."""
        name = event.data.get("name", "unknown")
        error = event.data.get("error", "")[:200]
        self._add_signal(
            key=f"process_crash:{name}",
            kind="agent_crash",
            source=name,
            description=f"Process '{name}' failed: {error}",
            priority=0.6,
            context={"process": name, "error": error},
        )

    async def _on_phase_failed(self, event: Event) -> None:
        """Goal phase failed — rich context for evolution to learn from."""
        phase = event.data.get("phase", "unknown")
        error = event.data.get("error", "")[:500]
        task = event.data.get("task", "")[:300]
        category = event.data.get("category", "")
        goal_desc = event.data.get("goal_description", "")[:200]
        attempt = event.data.get("attempt", 1)
        verify = event.data.get("verify", "")[:200]

        self._add_signal(
            key=f"phase_fail:{category}:{phase}",
            kind="phase_failure",
            source=f"goal_runner:{phase}",
            description=f"Phase '{phase}' failed (attempt {attempt}): {error[:200]}",
            priority=min(1.0, 0.5 + 0.1 * attempt),  # Higher priority with more retries
            context={
                "phase": phase,
                "error": error,
                "task": task,
                "verify": verify,
                "category": category,
                "goal": goal_desc,
                "attempt": attempt,
            },
        )

    async def _on_phase_skipped(self, event: Event) -> None:
        """User manually skipped a phase — the OS failed to do its job."""
        phase = event.data.get("phase", "unknown")
        reason = event.data.get("reason", "")
        goal_id = event.data.get("goal_id", "")
        original_error = event.data.get("original_error", "")[:200]
        self._add_signal(
            key=f"user_skip:{phase}",
            kind="user_skip",
            source="dashboard",
            description=f"User skipped phase '{phase}': {reason}. Original error: {original_error}",
            priority=0.8,  # High — user had to intervene
            context={
                "phase": phase,
                "reason": reason,
                "goal_id": goal_id,
                "original_error": original_error,
            },
        )

    async def _on_memory_critical(self, event: Event) -> None:
        """System memory critically low — like Windows low memory warning."""
        percent = event.data.get("percent", 0)
        available = event.data.get("available_mb", 0)
        self._add_signal(
            key="resource:memory_critical",
            kind="resource_pressure",
            source="gc",
            description=f"MEMORY CRITICAL: {percent}% used, {available}MB free. Orphaned resources consuming memory.",
            priority=1.0,
            context={"percent": percent, "available_mb": available},
        )

    async def _on_memory_warning(self, event: Event) -> None:
        """System memory under pressure."""
        percent = event.data.get("percent", 0)
        self._add_signal(
            key="resource:memory_warning",
            kind="resource_pressure",
            source="gc",
            description=f"MEMORY WARNING: {percent}% used. Consider cleaning up unused resources.",
            priority=0.6,
            context={"percent": percent},
        )

    async def _on_impasse(self, event: Event) -> None:
        """Goal hit an impasse — phase failed 2+ times. High priority for Evolution Agent."""
        phase = event.data.get("phase", "unknown")
        goal = event.data.get("goal", "")[:100]
        error = event.data.get("error", "")[:300]
        env = event.data.get("environment", "")[:200]
        attempts = event.data.get("attempts", 2)

        self._add_signal(
            key=f"impasse:{phase}",
            kind="impasse",
            source=f"goal_runner:{phase}",
            description=f"IMPASSE: Phase '{phase}' failed {attempts}x. Goal: {goal[:60]}. Error: {error[:150]}",
            priority=0.9,  # High priority — triggers Evolution Agent
            context={
                "phase": phase, "goal": goal, "error": error,
                "environment": env, "attempts": attempts,
                "category": event.data.get("category", ""),
            },
        )
        _logger.info("Impasse demand created: %s (priority 0.9)", phase)

    async def _on_phase_succeeded(self, event: Event) -> None:
        """Goal phase succeeded — verify if evolution helped."""
        if event.data.get("status") != "done":
            return
        phase = event.data.get("phase", "")
        _goal_id = event.data.get("goal_id", "")
        # Clear any phase_fail demands for this phase — evolution worked
        cleared = self.clear_resolved("phase_fail:")
        if cleared > 0:
            _logger.info("Phase '%s' succeeded — cleared %d evolution demands", phase, cleared)

    async def _on_principles_reinforced(self, event: Event) -> None:
        """Track which principles helped (or didn't) — adjust confidence."""
        principles = event.data.get("principles", [])
        outcome = event.data.get("outcome", "")
        if not principles:
            return
        # Update confidence in evolution state
        try:
            import json as _json
            from agos.config import settings
            evo_path = settings.workspace_dir / "evolution_state.json"
            if not evo_path.exists():
                return
            data = _json.loads(evo_path.read_text(errors="ignore"))
            evo_mem = data.get("evolution_memory", {})
            insights = evo_mem.get("insights", [])
            changed = False
            for ins in insights:
                p_key = (ins.get("principle") or ins.get("what_worked", ""))[:50]
                if p_key and p_key in principles:
                    old_conf = ins.get("confidence", 1.0)
                    if outcome == "success":
                        ins["confidence"] = min(2.0, old_conf + 0.2)
                    elif outcome == "failure":
                        ins["confidence"] = max(0.0, old_conf - 0.3)
                    changed = True
                    _logger.info("Principle confidence %s: %.1f → %.1f (%s)",
                                 p_key[:30], old_conf, ins["confidence"], outcome)
            if changed:
                evo_path.write_text(_json.dumps(data, default=str))
        except Exception:
            pass

    # ── Signal management ──

    def _add_signal(self, key: str, kind: str, source: str, description: str,
                    priority: float, context: dict | None = None) -> None:
        """Add or merge a demand signal."""
        new = DemandSignal(
            kind=kind, source=source, description=description,
            priority=priority, context=context or {},
        )
        if key in self._signals:
            self._signals[key].merge(new)
        else:
            self._signals[key] = new

        # Evict old, low-priority signals if over limit
        if len(self._signals) > self._max_signals:
            ranked = sorted(self._signals.items(), key=lambda x: x[1].priority)
            for k, _ in ranked[:len(self._signals) - self._max_signals]:
                del self._signals[k]

        # Persist to disk so demands survive restarts
        self._persist()

    def _classify_error(self, command: str, error: str) -> tuple[str, str, float]:
        """Classify an OS error into a demand signal type."""
        error_lower = error.lower()
        _cmd_lower = command.lower()

        # Missing capability signals
        missing_kw = ["not found", "no such", "doesn't exist", "cannot find",
                      "not installed", "not available", "no tool", "tool available",
                      "cannot interact", "no capability", "missing capability",
                      "not recognized", "command not found"]
        if any(kw in error_lower for kw in missing_kw):
            return (
                "missing_tool",
                f"Missing capability for: {command[:60]}. Error: {error[:80]}",
                0.8,
            )

        # Permission/auth issues
        auth_kw = ["permission denied", "unauthorized", "forbidden", "access denied", "auth"]
        if any(kw in error_lower for kw in auth_kw):
            return (
                "error",
                f"Permission issue: {error[:100]}",
                0.5,
            )

        # Timeout/performance
        if "timeout" in error_lower or "timed out" in error_lower:
            return (
                "slow_tool",
                f"Timeout during: {command[:60]}",
                0.6,
            )

        # Network/connectivity
        net_kw = ["connection refused", "unreachable", "dns", "network"]
        if any(kw in error_lower for kw in net_kw):
            return (
                "error",
                f"Network issue: {error[:100]}",
                0.4,
            )

        # Generic failure
        return (
            "error",
            f"Command failed: {command[:60]}. Error: {error[:80]}",
            0.5,
        )

    # ── Public API for evolution engine ──

    def top_demands(self, limit: int = 5, include_all: bool = False) -> list[DemandSignal]:
        """Return the top N demand signals, ranked by priority and frequency.

        By default, only returns demands that should be attempted (respects
        backoff and lifecycle status). Pass include_all=True for dashboard display.
        """
        if include_all:
            signals = list(self._signals.values())
        else:
            signals = [s for s in self._signals.values() if s.should_attempt]

        # Score = priority * (1 + log(count)) * recency_boost
        def score(s: DemandSignal) -> float:
            import math
            recency = 1.0 / (1.0 + s.age_hours)  # Recent signals matter more
            return s.priority * (1.0 + math.log1p(s.count)) * (0.5 + 0.5 * recency)

        signals.sort(key=score, reverse=True)
        return signals[:limit]

    def demand_topics(self, limit: int = 3) -> list[str]:
        """Convert top demands into arxiv search topics.

        Instead of rotating through fixed topics, search for papers
        that could actually help solve the user's problems.
        """
        top = self.top_demands(limit=limit)
        topics = []
        for signal in top:
            topic = self._signal_to_topic(signal)
            if topic and topic not in topics:
                topics.append(topic)
        return topics[:limit]

    def demand_context_for_codegen(self) -> str:
        """Build a context string for LLM code generation from top demands.

        This tells the LLM what real problems to solve instead of
        generating generic code from paper abstracts.
        """
        top = self.top_demands(limit=5)
        if not top:
            return ""

        lines = ["## Real problems to solve (from user activity):\n"]
        for i, sig in enumerate(top, 1):
            lines.append(f"{i}. [{sig.kind}] {sig.description}")
            if sig.count > 1:
                lines.append(f"   (occurred {sig.count} times)")
        lines.append(
            "\nPrioritize generating code that directly addresses "
            "these problems over generic implementations."
        )
        return "\n".join(lines)

    def has_demands(self) -> bool:
        """Any actionable demand signals (not resolved/backing-off)?"""
        return any(s.should_attempt for s in self._signals.values())

    def pending_count(self) -> int:
        """Count of active (non-resolved) signals."""
        return sum(1 for s in self._signals.values() if s.status != "resolved")

    @property
    def active_demands(self) -> list[DemandSignal]:
        """All signals that should be attempted now."""
        return [s for s in self._signals.values() if s.should_attempt]

    def clear_resolved(self, key_prefix: str) -> int:
        """Mark signals as resolved (instead of deleting — preserves history)."""
        count = 0
        for k, v in self._signals.items():
            if k.startswith(key_prefix) and v.status != "resolved":
                v.mark_resolved()
                count += 1
        return count

    def summary(self) -> dict[str, Any]:
        """Dashboard-friendly summary."""
        by_kind: dict[str, int] = defaultdict(int)
        by_status: dict[str, int] = defaultdict(int)
        for s in self._signals.values():
            by_kind[s.kind] += 1
            by_status[s.status] += 1
        top = self.top_demands(limit=3, include_all=True)
        return {
            "total_signals": len(self._signals),
            "by_kind": dict(by_kind),
            "by_status": dict(by_status),
            "top_demands": [
                {"kind": s.kind, "source": s.source,
                 "description": s.description[:100],
                 "priority": round(s.priority, 2),
                 "count": s.count,
                 "status": s.status,
                 "attempts": s.attempts}
                for s in top
            ],
            "tool_failure_counts": dict(self._tool_failures),
            "command_error_counts": dict(self._command_errors),
        }

    def _signal_to_topic(self, signal: DemandSignal) -> str | None:
        """Convert a demand signal into a relevant arxiv search topic."""
        desc = signal.description.lower()
        ctx = signal.context

        # Missing tool → search for papers about that capability
        if signal.kind == "missing_tool":
            tool_name = ctx.get("tool", ctx.get("command", ""))[:40]
            # Map common tool needs to research topics
            topic_map = {
                "image": "image understanding vision language models",
                "pdf": "document extraction information retrieval",
                "web": "web navigation browsing autonomous agents",
                "database": "database query optimization agents",
                "code": "automated code generation program synthesis",
                "test": "automated test generation software testing",
                "deploy": "automated deployment continuous delivery agents",
                "monitor": "system monitoring anomaly detection agents",
                "search": "information retrieval semantic search agents",
                "api": "API discovery integration autonomous agents",
                "email": "email automation natural language processing",
                "scrape": "web scraping information extraction agents",
                "translate": "machine translation multilingual agents",
                "summarize": "text summarization abstractive agents",
            }
            for keyword, topic in topic_map.items():
                if keyword in tool_name.lower() or keyword in desc:
                    return topic
            return f"agent tool {tool_name} automation"

        # Tool failures → search for better approaches
        if signal.kind == "error" and "tool" in signal.source.lower():
            tool = ctx.get("tool", signal.source)
            return f"robust {tool} error handling fault tolerance agents"

        # Slow/expensive tasks → search for efficiency improvements
        if signal.kind == "slow_tool" or signal.kind == "user_need":
            if "token" in desc:
                return "efficient LLM inference token reduction agents"
            if "turn" in desc:
                return "agent planning task decomposition efficiency"
            return "agent task optimization performance improvement"

        # Agent crashes → search for reliability
        if signal.kind == "agent_crash":
            return "fault tolerant agent systems self-healing recovery"

        return None

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "signals": {
                k: {
                    "kind": v.kind, "source": v.source,
                    "description": v.description, "priority": v.priority,
                    "count": v.count, "first_seen": v.first_seen,
                    "last_seen": v.last_seen, "context": v.context,
                    "attempts": v.attempts, "last_attempt": v.last_attempt,
                    "status": v.status,
                }
                for k, v in self._signals.items()
            },
            "tool_failures": dict(self._tool_failures),
            "command_errors": dict(self._command_errors),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DemandCollector":
        """Restore from persisted state."""
        dc = cls()
        for k, v in data.get("signals", {}).items():
            dc._signals[k] = DemandSignal(
                kind=v["kind"], source=v["source"],
                description=v["description"], priority=v["priority"],
                count=v.get("count", 1),
                first_seen=v.get("first_seen", time.time()),
                last_seen=v.get("last_seen", time.time()),
                context=v.get("context", {}),
                attempts=v.get("attempts", 0),
                last_attempt=v.get("last_attempt", 0),
                status=v.get("status", "active"),
            )
        dc._tool_failures = defaultdict(int, data.get("tool_failures", {}))
        dc._command_errors = defaultdict(int, data.get("command_errors", {}))
        return dc
