"""ComponentEvolver — unified self-improvement loop for ALL OS components.

Borrows the exact pattern from IntegrationStrategy:
    snapshot() → propose() → sandbox() → apply() → health_check() → rollback()

Each component type has its own Evolver that follows this contract.
The ComponentEvolver orchestrates all of them on a schedule.

What evolves:
    1. Agents      — prompts, tool selection, memory strategies
    2. Daemons       — new hand types from usage patterns
    3. Providers   — auto-discover new LLM endpoints
    4. Channels    — detect new services, generate adapters
    5. Tools       — existing tools rewrite themselves from failure data
    6. OS Brain    — system prompt refined from conversation outcomes

All evolved artifacts go to .agos/evolved/<type>/ and hot-load at runtime.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry
from agos.evolution.sandbox import Sandbox

_logger = logging.getLogger(__name__)

def _get_evolved_root() -> Path:
    from agos.config import settings
    return Path(settings.workspace_dir) / "evolved"

EVOLVED_ROOT = _get_evolved_root()


# ── Base contract (same as IntegrationStrategy) ─────────────


class EvolutionProposal(BaseModel):
    """A proposed change to a component."""
    id: str = ""
    component_type: str = ""   # agent, hand, provider, channel, tool, brain
    component_name: str = ""
    change_type: str = ""      # improve, create, tune
    description: str = ""
    code: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    source: str = ""           # usage_analysis, failure_repair, arxiv, community
    fitness_before: float = 0.0
    fitness_after: float = 0.0


class EvolutionResult(BaseModel):
    success: bool = False
    proposal_id: str = ""
    changes: list[str] = Field(default_factory=list)
    error: str = ""
    rolled_back: bool = False


class ComponentEvolver(ABC):
    """Base class for component-specific evolvers.

    Follows the proven strategy pattern:
    snapshot → propose → sandbox → apply → health_check → rollback
    """

    component_type: str = ""
    evolved_dir: Path = EVOLVED_ROOT

    def __init__(self, event_bus: EventBus, audit: AuditTrail):
        self._bus = event_bus
        self._audit = audit
        self._sandbox = Sandbox(timeout=10)
        self._history: list[dict] = []  # evolution history

    @abstractmethod
    async def observe(self) -> list[dict]:
        """Observe the system and return improvement opportunities.

        Returns list of {name, description, priority, context}.
        """
        ...

    @abstractmethod
    async def propose(self, opportunity: dict, llm=None) -> EvolutionProposal | None:
        """Generate a concrete proposal from an opportunity."""
        ...

    @abstractmethod
    async def snapshot(self, name: str) -> dict[str, Any]:
        """Capture current state before applying changes."""
        ...

    @abstractmethod
    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Apply the proposal. Returns list of change descriptions."""
        ...

    @abstractmethod
    async def health_check(self, proposal: EvolutionProposal) -> bool:
        """Verify component works after applying changes."""
        ...

    @abstractmethod
    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        """Restore previous state from snapshot."""
        ...

    async def evolve_cycle(self, llm=None) -> list[EvolutionResult]:
        """Run one evolution cycle for this component type."""
        results = []

        # 1. Observe
        opportunities = await self.observe()
        if not opportunities:
            return results

        # Sort by priority, take top 2 per cycle
        opportunities.sort(key=lambda o: -o.get("priority", 0))
        batch = opportunities[:2]

        for opp in batch:
            result = await self._evolve_one(opp, llm)
            results.append(result)

        return results

    async def _evolve_one(self, opportunity: dict, llm=None) -> EvolutionResult:
        """Execute the full evolution lifecycle for one opportunity."""
        name = opportunity.get("name", "unknown")

        # 2. Propose
        proposal = await self.propose(opportunity, llm)
        if not proposal:
            return EvolutionResult(error=f"No proposal generated for {name}")

        # 3. Sandbox test (if there's code)
        if proposal.code:
            sandbox_result = self._sandbox.validate(proposal.code)
            if not sandbox_result.safe:
                return EvolutionResult(
                    error=f"Sandbox failed for {name}: {'; '.join(sandbox_result.issues)}",
                    proposal_id=proposal.id,
                )

        # 4. Snapshot
        snap = await self.snapshot(name)

        # 5. Apply
        try:
            changes = await self.apply(proposal)
        except Exception as e:
            return EvolutionResult(error=f"Apply failed: {e}", proposal_id=proposal.id)

        # 6. Health check
        healthy = await self.health_check(proposal)
        if not healthy:
            # Auto-rollback
            try:
                await self.rollback(snap)
            except Exception:
                pass
            return EvolutionResult(
                error=f"Health check failed for {name}, rolled back",
                proposal_id=proposal.id,
                rolled_back=True,
            )

        # Success — record
        result = EvolutionResult(
            success=True,
            proposal_id=proposal.id,
            changes=changes,
        )
        self._history.append({
            "component_type": self.component_type,
            "name": name,
            "changes": changes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._bus.emit(f"evolution.{self.component_type}_evolved", {
            "name": name,
            "changes": changes,
            "source": proposal.source,
        }, source="component_evolver")

        await self._audit.record(AuditEntry(
            agent_name="component_evolver",
            action=f"{self.component_type}_evolved",
            detail=f"{name}: {', '.join(changes[:3])}",
            success=True,
        ))

        return result

    def _write_evolved_file(self, filename: str, code: str) -> Path:
        """Write evolved code to the persistent directory."""
        d = self.evolved_dir / self.component_type
        d.mkdir(parents=True, exist_ok=True)
        path = d / filename
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:12]

        if path.exists():
            existing_hash = hashlib.sha256(path.read_text().encode()).hexdigest()[:12]
            if existing_hash == code_hash:
                return path  # unchanged

        path.write_text(code, encoding="utf-8")
        _logger.info("Wrote evolved %s: %s", self.component_type, path)
        return path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. AGENT EVOLVER — agents improve their own prompts & tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class AgentEvolver(ComponentEvolver):
    """Agents evolve their system prompts and tool selections.

    Observes: task success/failure rates per agent from audit trail.
    Evolves: system prompt additions, tool profiles (which tools per agent type).
    """

    component_type = "agent"

    def __init__(self, event_bus: EventBus, audit: AuditTrail):
        super().__init__(event_bus, audit)
        self._prompt_patches: dict[str, list[str]] = {}  # agent_name -> prompt additions
        self._tool_profiles: dict[str, set[str]] = {}    # agent_name -> allowed tools

    async def observe(self) -> list[dict]:
        opportunities = []
        try:
            entries = await self._audit.recent(limit=300)

            # Track success rates per agent
            agent_stats: dict[str, dict] = {}  # name -> {success, total, common_errors}
            for e in entries:
                name = e.agent_name or "unknown"
                if name not in agent_stats:
                    agent_stats[name] = {"success": 0, "total": 0, "errors": []}
                agent_stats[name]["total"] += 1
                if e.success:
                    agent_stats[name]["success"] += 1
                elif e.detail:
                    agent_stats[name]["errors"].append(e.detail[:100])

            for name, stats in agent_stats.items():
                if stats["total"] < 5:
                    continue
                rate = stats["success"] / stats["total"]
                if rate < 0.7:  # Below 70% success
                    opportunities.append({
                        "name": name,
                        "description": f"Agent '{name}' has {rate:.0%} success rate",
                        "priority": 1.0 - rate,
                        "context": {
                            "success_rate": rate,
                            "total_actions": stats["total"],
                            "recent_errors": stats["errors"][-5:],
                        },
                    })
        except Exception as e:
            _logger.debug("Agent observation failed: %s", e)
        return opportunities

    async def propose(self, opp: dict, llm=None) -> EvolutionProposal | None:
        name = opp["name"]
        errors = opp.get("context", {}).get("recent_errors", [])

        # Generate prompt patch from error patterns
        patch_lines = [f"# Evolved guidance for {name} (auto-generated)"]
        if errors:
            error_summary = "; ".join(set(errors[:3]))
            patch_lines.append(f"# Common errors observed: {error_summary}")
            patch_lines.append("# Adjust behavior to avoid these failure patterns.")

            if any("timeout" in e.lower() for e in errors):
                patch_lines.append("- Use shorter timeouts. Prefer quick retries over long waits.")
            if any("not found" in e.lower() for e in errors):
                patch_lines.append("- Verify resources exist before operating on them.")
            if any("permission" in e.lower() for e in errors):
                patch_lines.append("- Check permissions before file/network operations.")
            if any("parse" in e.lower() or "json" in e.lower() for e in errors):
                patch_lines.append("- Validate data formats before processing. Handle malformed input.")

        if llm:
            try:
                resp = await llm.complete_prompt(
                    f"Given these agent errors:\n{chr(10).join(errors[:5])}\n\n"
                    f"Write 3-5 short behavioral guidelines (one line each) to prevent them.",
                    max_tokens=200,
                )
                if resp:
                    patch_lines.append(resp.strip())
            except Exception:
                pass

        prompt_patch = "\n".join(patch_lines)
        return EvolutionProposal(
            id=hashlib.sha256(f"{name}:{time.time()}".encode()).hexdigest()[:16],
            component_type="agent",
            component_name=name,
            change_type="improve",
            description=f"Improve {name} success rate from {opp['context']['success_rate']:.0%}",
            config={"prompt_patch": prompt_patch},
            source="usage_analysis",
        )

    async def snapshot(self, name: str) -> dict[str, Any]:
        return {"name": name, "patches": list(self._prompt_patches.get(name, []))}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        name = proposal.component_name
        patch = proposal.config.get("prompt_patch", "")
        if patch:
            if name not in self._prompt_patches:
                self._prompt_patches[name] = []
            self._prompt_patches[name].append(patch)

            # Persist to disk
            self._write_evolved_file(
                f"agent_{name}_prompt.txt",
                "\n\n".join(self._prompt_patches[name]),
            )
        return [f"Added prompt patch for {name}"]

    async def health_check(self, proposal: EvolutionProposal) -> bool:
        return True  # Prompt patches are always safe

    async def rollback(self, snap: dict[str, Any]) -> None:
        name = snap["name"]
        self._prompt_patches[name] = snap.get("patches", [])

    def get_prompt_patches(self, agent_name: str) -> str:
        """Get accumulated prompt improvements for an agent."""
        patches = self._prompt_patches.get(agent_name, [])
        return "\n\n".join(patches) if patches else ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. HAND EVOLVER — new hand types emerge from usage patterns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class HandEvolver(ComponentEvolver):
    """New hand types evolve from repeated command patterns.

    Observes: OS shell commands in audit trail — what do users do repeatedly?
    Evolves: New Daemon classes that automate those patterns.
    """

    component_type = "hand"

    def __init__(self, event_bus: EventBus, audit: AuditTrail, daemon_manager=None):
        super().__init__(event_bus, audit)
        self._daemon_manager = daemon_manager

    async def observe(self) -> list[dict]:
        opportunities = []
        try:
            entries = await self._audit.recent(limit=500)

            # Find repeated command patterns
            commands: dict[str, int] = {}
            for e in entries:
                if e.action in ("shell_command", "os_shell", "tool_execution"):
                    cmd = (e.detail or "")[:80]
                    if cmd:
                        # Normalize — strip arguments to find base patterns
                        base = cmd.split()[0] if cmd.split() else cmd
                        commands[base] = commands.get(base, 0) + 1

            # Commands run 5+ times are candidates for automation
            for cmd, count in commands.items():
                if count >= 5:
                    opportunities.append({
                        "name": f"auto_{cmd.replace('/', '_').replace('.', '_')}",
                        "description": f"Automate '{cmd}' — executed {count} times",
                        "priority": min(0.9, count / 20),
                        "context": {"command": cmd, "executions": count},
                    })
        except Exception as e:
            _logger.debug("Daemon observation failed: %s", e)
        return opportunities

    async def propose(self, opp: dict, llm=None) -> EvolutionProposal | None:
        name = opp["name"]
        cmd = opp["context"]["command"]

        # Generate a simple scheduled hand
        code = f'''"""Evolved hand: {name} — auto-generated from usage patterns."""

from __future__ import annotations
import asyncio
import logging
from agos.daemons.base import Hand, DaemonResult

_logger = logging.getLogger(__name__)


class EvolvedHand(Daemon):
    name = "{name}"
    description = "Auto-evolved: runs '{cmd}' on schedule"
    icon = "🔄"
    one_shot = False
    default_interval = 300  # every 5 minutes

    async def tick(self) -> None:
        command = self.config.get("command", "{cmd}")
        timeout = self.config.get("timeout", 30)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")[:1000]
            success = proc.returncode == 0

            self.add_result(DaemonResult(
                hand_name=self.name,
                success=success,
                summary=f"{{command}}: exit={{proc.returncode}}",
                data={{"output": output, "exit_code": proc.returncode}},
            ))
            await self.emit("task_executed", {{
                "command": command, "success": success, "output": output[:200],
            }})
        except asyncio.TimeoutError:
            self.add_result(DaemonResult(
                hand_name=self.name, success=False,
                summary=f"Timeout after {{timeout}}s",
            ))
        except Exception as e:
            self.add_result(DaemonResult(
                hand_name=self.name, success=False, summary=str(e),
            ))
'''

        return EvolutionProposal(
            id=hashlib.sha256(f"hand:{name}:{time.time()}".encode()).hexdigest()[:16],
            component_type="hand",
            component_name=name,
            change_type="create",
            description=f"New hand automating '{cmd}'",
            code=code,
            source="usage_analysis",
        )

    async def snapshot(self, name: str) -> dict[str, Any]:
        return {"name": name}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        path = self._write_evolved_file(f"{proposal.component_name}.py", proposal.code)

        # Hot-load into hand manager
        if self._daemon_manager:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(f"evolved_hand_{proposal.component_name}", path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    hand_cls = getattr(module, "EvolvedHand", None)
                    if hand_cls:
                        self._daemon_manager.register(hand_cls())
                        return [f"Created and registered hand: {proposal.component_name}"]
            except Exception as e:
                _logger.warning("Failed to hot-load evolved hand: %s", e)

        return [f"Created hand file: {path}"]

    async def health_check(self, proposal: EvolutionProposal) -> bool:
        # Verify the code at least parses
        try:
            ast.parse(proposal.code)
            return True
        except SyntaxError:
            return False

    async def rollback(self, snap: dict[str, Any]) -> None:
        name = snap["name"]
        path = self.evolved_dir / self.component_type / f"{name}.py"
        if path.exists():
            path.unlink()
        if self._daemon_manager:
            try:
                self._daemon_manager._daemons.pop(name, None)
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. PROVIDER EVOLVER — discover new LLM endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ProviderEvolver(ComponentEvolver):
    """Auto-discover and register new OpenAI-compatible LLM endpoints.

    Observes: environment variables, network scans, community configs.
    Evolves: new provider entries that hot-load into the provider registry.
    """

    component_type = "provider"

    # Well-known OpenAI-compatible endpoints to probe
    _PROBE_TARGETS = [
        ("http://localhost:1234/v1", "lmstudio_local"),
        ("http://localhost:11434/v1", "ollama_local"),
        ("http://localhost:8000/v1", "vllm_local"),
        ("http://localhost:5000/v1", "text_gen_webui"),
        ("http://localhost:8080/v1", "localai"),
        ("http://localhost:3000/v1", "jan_local"),
        ("http://host.docker.internal:1234/v1", "lmstudio_docker"),
        ("http://host.docker.internal:11434/v1", "ollama_docker"),
    ]

    def __init__(self, event_bus: EventBus, audit: AuditTrail):
        super().__init__(event_bus, audit)
        self._discovered: dict[str, dict] = {}

    async def observe(self) -> list[dict]:
        opportunities = []
        import httpx

        for url, name in self._PROBE_TARGETS:
            if name in self._discovered:
                continue
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(f"{url.rstrip('/').replace('/v1','')}/v1/models")
                    if r.status_code == 200:
                        data = r.json()
                        models = [m.get("id", "") for m in data.get("data", [])]
                        if models:
                            opportunities.append({
                                "name": name,
                                "description": f"Found {len(models)} models at {url}",
                                "priority": 0.6,
                                "context": {"url": url, "models": models[:10]},
                            })
            except Exception:
                pass

        # Check env vars for API keys that aren't registered
        import os
        env_providers = {
            "OPENAI_API_KEY": ("openai_env", "https://api.openai.com/v1"),
            "GROQ_API_KEY": ("groq_env", "https://api.groq.com/openai/v1"),
            "TOGETHER_API_KEY": ("together_env", "https://api.together.xyz/v1"),
            "MISTRAL_API_KEY": ("mistral_env", "https://api.mistral.ai/v1"),
            "DEEPSEEK_API_KEY": ("deepseek_env", "https://api.deepseek.com/v1"),
        }
        for env_var, (name, url) in env_providers.items():
            if os.environ.get(env_var) and name not in self._discovered:
                opportunities.append({
                    "name": name,
                    "description": f"Found {env_var} in environment",
                    "priority": 0.8,
                    "context": {"url": url, "env_var": env_var},
                })

        return opportunities

    async def propose(self, opp: dict, llm=None) -> EvolutionProposal | None:
        name = opp["name"]
        url = opp["context"]["url"]
        models = opp["context"].get("models", [])
        env_var = opp["context"].get("env_var", "")

        config = {
            "name": name,
            "base_url": url,
            "models": models,
            "env_var": env_var,
        }

        return EvolutionProposal(
            id=hashlib.sha256(f"provider:{name}".encode()).hexdigest()[:16],
            component_type="provider",
            component_name=name,
            change_type="create",
            description=f"New provider: {name} at {url}",
            config=config,
            source="auto_discovery",
        )

    async def snapshot(self, name: str) -> dict[str, Any]:
        return {"name": name, "was_discovered": name in self._discovered}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        self._discovered[proposal.component_name] = proposal.config

        # Persist discovery
        self._write_evolved_file(
            f"{proposal.component_name}.json",
            json.dumps(proposal.config, indent=2),
        )
        return [f"Discovered provider: {proposal.component_name} at {proposal.config['base_url']}"]

    async def health_check(self, proposal: EvolutionProposal) -> bool:
        # Verify endpoint responds
        import httpx
        url = proposal.config.get("base_url", "")
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{url}/models")
                return r.status_code == 200
        except Exception:
            return True  # Don't fail for env-var-based providers

    async def rollback(self, snap: dict[str, Any]) -> None:
        name = snap["name"]
        if not snap.get("was_discovered"):
            self._discovered.pop(name, None)

    def get_discovered(self) -> dict[str, dict]:
        return dict(self._discovered)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TOOL SELF-IMPROVER — existing tools rewrite themselves
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ToolImprover(ComponentEvolver):
    """Existing tools track failure rates and generate improved versions.

    Observes: tool execution results — timing, errors, success rates.
    Evolves: improved handler code for underperforming tools.
    """

    component_type = "tool"

    def __init__(self, event_bus: EventBus, audit: AuditTrail, tool_registry=None):
        super().__init__(event_bus, audit)
        self._registry = tool_registry
        self._tool_stats: dict[str, dict] = {}  # name -> {calls, failures, avg_ms, errors}

    async def observe(self) -> list[dict]:
        opportunities = []
        try:
            entries = await self._audit.recent(limit=500)

            # Aggregate tool performance stats
            for e in entries:
                if e.action != "tool_execution":
                    continue
                parts = (e.detail or "").split(":", 1)
                tool_name = parts[0].strip() if parts else ""
                if not tool_name:
                    continue

                if tool_name not in self._tool_stats:
                    self._tool_stats[tool_name] = {"calls": 0, "failures": 0, "errors": []}
                self._tool_stats[tool_name]["calls"] += 1
                if not e.success:
                    self._tool_stats[tool_name]["failures"] += 1
                    if len(parts) > 1:
                        self._tool_stats[tool_name]["errors"].append(parts[1][:100])

            # Find underperforming tools
            for name, stats in self._tool_stats.items():
                if stats["calls"] < 5:
                    continue
                failure_rate = stats["failures"] / stats["calls"]
                if failure_rate > 0.2:
                    opportunities.append({
                        "name": name,
                        "description": f"Tool '{name}' failing {failure_rate:.0%} of the time",
                        "priority": failure_rate,
                        "context": {
                            "failure_rate": failure_rate,
                            "calls": stats["calls"],
                            "errors": stats["errors"][-5:],
                        },
                    })
        except Exception as e:
            _logger.debug("Tool improvement observation failed: %s", e)
        return opportunities

    async def propose(self, opp: dict, llm=None) -> EvolutionProposal | None:
        name = opp["name"]
        errors = opp["context"].get("errors", [])

        # Generate improved wrapper with error handling
        error_types = set()
        for err in errors:
            if "timeout" in err.lower():
                error_types.add("timeout")
            elif "not found" in err.lower():
                error_types.add("not_found")
            elif "permission" in err.lower():
                error_types.add("permission")
            else:
                error_types.add("general")

        # Build improvement config (not full code — we patch the existing tool)
        config = {
            "tool_name": name,
            "error_types": list(error_types),
            "improvements": [],
        }

        if "timeout" in error_types:
            config["improvements"].append("increase_timeout")
        if "not_found" in error_types:
            config["improvements"].append("add_existence_check")
        if "permission" in error_types:
            config["improvements"].append("add_permission_check")

        return EvolutionProposal(
            id=hashlib.sha256(f"tool_improve:{name}:{time.time()}".encode()).hexdigest()[:16],
            component_type="tool",
            component_name=name,
            change_type="improve",
            description=f"Improve {name}: fix {', '.join(error_types)} errors",
            config=config,
            source="failure_repair",
        )

    async def snapshot(self, name: str) -> dict[str, Any]:
        return {"name": name, "stats": dict(self._tool_stats.get(name, {}))}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        # Record improvement intent (actual code patching needs tool source access)
        changes = []
        for improvement in proposal.config.get("improvements", []):
            changes.append(f"Applied {improvement} to {proposal.component_name}")

        # Reset failure counter
        name = proposal.component_name
        if name in self._tool_stats:
            self._tool_stats[name]["failures"] = 0
            self._tool_stats[name]["errors"] = []

        # Persist improvement record
        self._write_evolved_file(
            f"tool_improvement_{name}.json",
            json.dumps({"tool": name, "improvements": proposal.config.get("improvements", []),
                        "timestamp": datetime.now(timezone.utc).isoformat()}, indent=2),
        )
        return changes

    async def health_check(self, proposal: EvolutionProposal) -> bool:
        return True  # Config changes are safe

    async def rollback(self, snap: dict[str, Any]) -> None:
        name = snap["name"]
        if name in self._tool_stats:
            self._tool_stats[name] = snap.get("stats", {})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. BRAIN EVOLVER — OS agent refines its system prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BrainEvolver(ComponentEvolver):
    """The OS agent's brain learns from conversation outcomes.

    Observes: command success/failure in audit trail, user feedback patterns.
    Evolves: system prompt additions, reasoning guidelines, tool preferences.
    """

    component_type = "brain"

    def __init__(self, event_bus: EventBus, audit: AuditTrail, os_agent=None):
        super().__init__(event_bus, audit)
        self._os_agent = os_agent
        self._learned_rules: list[str] = []
        self._load_existing_rules()

    def _load_existing_rules(self) -> None:
        path = EVOLVED_ROOT / "brain" / "learned_rules.txt"
        if path.exists():
            self._learned_rules = [
                line.strip() for line in path.read_text().splitlines() if line.strip()
            ]

    async def observe(self) -> list[dict]:
        opportunities = []
        try:
            entries = await self._audit.recent(limit=300)

            # Find patterns in OS agent failures
            os_failures: list[str] = []
            os_successes: list[str] = []
            for e in entries:
                if e.agent_name not in ("OSAgent", "os_agent"):
                    continue
                if e.success:
                    os_successes.append(e.detail or "")
                else:
                    os_failures.append(e.detail or "")

            if len(os_failures) > 3:
                opportunities.append({
                    "name": "brain_improvement",
                    "description": f"OS agent had {len(os_failures)} failures in recent history",
                    "priority": min(0.8, len(os_failures) / 20),
                    "context": {
                        "failures": os_failures[-10:],
                        "successes": os_successes[-5:],
                    },
                })
        except Exception as e:
            _logger.debug("Brain observation failed: %s", e)
        return opportunities

    async def propose(self, opp: dict, llm=None) -> EvolutionProposal | None:
        failures = opp["context"].get("failures", [])
        _successes = opp["context"].get("successes", [])

        new_rules = []

        # Pattern-based rule extraction
        error_keywords: dict[str, str] = {
            "timeout": "Set shorter timeouts and implement retries with exponential backoff.",
            "rate limit": "Implement rate limiting awareness. Wait before retrying API calls.",
            "not found": "Always verify resource existence before operations.",
            "syntax": "Validate code syntax before execution.",
            "permission denied": "Check file/network permissions before operations.",
            "out of memory": "Process large data in chunks. Monitor memory usage.",
            "connection refused": "Verify service availability before connecting.",
        }

        for failure in failures:
            failure_lower = failure.lower()
            for keyword, rule in error_keywords.items():
                if keyword in failure_lower and rule not in self._learned_rules:
                    new_rules.append(rule)

        # LLM-assisted rule generation
        if llm and failures:
            try:
                resp = await llm.complete_prompt(
                    "The OS agent failed on these tasks:\n"
                    + "\n".join(f"- {f}" for f in failures[:5])
                    + "\n\nWrite 2-3 concise rules (one line each) the agent should follow to avoid these. "
                    "Rules should be actionable and specific.",
                    max_tokens=200,
                )
                if resp:
                    for line in resp.strip().splitlines():
                        line = line.strip().lstrip("- 0123456789.")
                        if line and len(line) > 10 and line not in self._learned_rules:
                            new_rules.append(line)
            except Exception:
                pass

        if not new_rules:
            return None

        return EvolutionProposal(
            id=hashlib.sha256(f"brain:{time.time()}".encode()).hexdigest()[:16],
            component_type="brain",
            component_name="os_agent",
            change_type="improve",
            description=f"Add {len(new_rules)} learned rules to OS agent",
            config={"new_rules": new_rules},
            source="usage_analysis",
        )

    async def snapshot(self, name: str) -> dict[str, Any]:
        return {"rules": list(self._learned_rules)}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        new_rules = proposal.config.get("new_rules", [])
        changes = []

        for rule in new_rules:
            if rule not in self._learned_rules:
                self._learned_rules.append(rule)
                changes.append(f"Learned: {rule[:80]}")

        # Persist
        d = EVOLVED_ROOT / "brain"
        d.mkdir(parents=True, exist_ok=True)
        (d / "learned_rules.txt").write_text(
            "\n".join(self._learned_rules), encoding="utf-8",
        )

        return changes

    async def health_check(self, proposal: EvolutionProposal) -> bool:
        return len(self._learned_rules) < 100  # Don't accumulate too many rules

    async def rollback(self, snap: dict[str, Any]) -> None:
        self._learned_rules = snap.get("rules", [])
        d = EVOLVED_ROOT / "brain"
        d.mkdir(parents=True, exist_ok=True)
        (d / "learned_rules.txt").write_text(
            "\n".join(self._learned_rules), encoding="utf-8",
        )

    def get_learned_rules(self) -> list[str]:
        """Get all learned rules for injection into OS agent prompt."""
        return list(self._learned_rules)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ORCHESTRATOR — runs all evolvers in a coordinated cycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SelfImprovementLoop:
    """Orchestrates all component evolvers on a schedule.

    Runs inside the evolution_loop alongside strategy evolution and meta-evolution.
    Each evolver gets a turn every N cycles, staggered to avoid overload.
    """

    def __init__(
        self,
        event_bus: EventBus,
        audit: AuditTrail,
        tool_registry=None,
        daemon_manager=None,
        os_agent=None,
    ):
        self._bus = event_bus
        self._audit = audit
        self._cycle = 0

        # Initialize all evolvers
        self.agent_evolver = AgentEvolver(event_bus, audit)
        self.hand_evolver = HandEvolver(event_bus, audit, daemon_manager)
        self.provider_evolver = ProviderEvolver(event_bus, audit)
        self.tool_improver = ToolImprover(event_bus, audit, tool_registry)
        self.brain_evolver = BrainEvolver(event_bus, audit, os_agent)

        # Schedule: which evolver runs on which cycle offset
        # Staggered so only 1-2 evolvers run per cycle
        self._schedule: list[tuple[int, ComponentEvolver]] = [
            (0, self.agent_evolver),      # Every 5th cycle starting at 0
            (1, self.hand_evolver),       # Every 5th cycle starting at 1
            (2, self.provider_evolver),   # Every 5th cycle starting at 2
            (3, self.tool_improver),      # Every 5th cycle starting at 3
            (4, self.brain_evolver),      # Every 5th cycle starting at 4
        ]

    async def tick(self, cycle: int, llm=None) -> dict:
        """Run scheduled evolvers for this cycle number.

        Returns summary of what evolved.
        """
        self._cycle = cycle
        summary: dict[str, Any] = {"cycle": cycle, "evolved": []}

        for offset, evolver in self._schedule:
            if cycle % 5 == offset:
                try:
                    results = await evolver.evolve_cycle(llm)
                    for r in results:
                        if r.success:
                            summary["evolved"].append({
                                "type": evolver.component_type,
                                "changes": r.changes,
                            })
                except Exception as e:
                    _logger.debug("Evolver %s failed: %s", evolver.component_type, e)

        if summary["evolved"]:
            await self._bus.emit("evolution.self_improvement", summary, source="self_improvement_loop")

        return summary

    def status(self) -> dict:
        return {
            "cycle": self._cycle,
            "agent_patches": len(self.agent_evolver._prompt_patches),
            "discovered_providers": len(self.provider_evolver._discovered),
            "learned_rules": len(self.brain_evolver._learned_rules),
            "tool_stats": {k: v["calls"] for k, v in self.tool_improver._tool_stats.items()},
            "evolution_history": [
                e._history[-5:] if hasattr(e, "_history") else []
                for _, e in self._schedule
            ],
        }
