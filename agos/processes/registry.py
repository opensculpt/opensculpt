"""Agent Registry — the app store / package manager of AGOS.

Users install agents on the OS. Each installed agent gets:
- A unique identity (agent ID, name)
- Resource quotas (memory, tokens, max restarts)
- Permissions (file access, network, tool usage)
- Lifecycle management (install → start → stop → uninstall)

Agents can be installed from:
- Bundled workloads (shipped with OS in /workloads/)
- GitHub repos (cloned on demand)
- Local paths

This is the user-facing layer. The ProcessManager underneath handles
the actual subprocess supervision.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

from agos.types import new_id
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.processes.manager import ProcessManager
from agos.processes.workload import WorkloadDiscovery


class AgentStatus(str, Enum):
    AVAILABLE = "available"      # Discovered but not installed
    INSTALLING = "installing"    # Dependencies being installed
    INSTALLED = "installed"      # Ready to run
    RUNNING = "running"          # Active process
    STOPPED = "stopped"          # Installed but not running
    CRASHED = "crashed"          # Exited with error
    UNINSTALLING = "uninstalling"
    ERROR = "error"              # Install or runtime error


@dataclass
class AgentIdentity:
    """Every user-installed agent gets a unique identity."""

    id: str                          # Unique agent ID
    name: str                        # Human-readable name (e.g. "openclaw")
    display_name: str = ""           # Pretty name (e.g. "OpenClaw AI Assistant")
    status: AgentStatus = AgentStatus.AVAILABLE
    source: str = ""                 # Where it came from (bundled, github URL, local path)
    runtime: str = "unknown"         # nodejs, go, python, rust
    path: str = ""                   # Install path on disk
    description: str = ""

    # Resource quotas (OS-enforced limits)
    memory_limit_mb: float = 256.0
    token_limit: int = 100_000
    max_restarts: int = 3

    # Permissions
    allowed_paths: list[str] = field(default_factory=lambda: ["/tmp"])
    network_access: bool = True
    can_spawn_children: bool = False

    # Lifecycle
    installed_at: float | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    process_pid: str | None = None   # AGOS process ID (links to ProcessManager)
    install_error: str = ""

    # Stats
    total_runs: int = 0
    total_uptime_s: int = 0
    total_crashes: int = 0
    total_tokens_used: int = 0


class AgentRegistry:
    """Manages the lifecycle of user-installed agents.

    This is what users interact with — install, start, stop, uninstall agents.
    Under the hood it delegates to WorkloadDiscovery and ProcessManager.
    """

    def __init__(
        self,
        event_bus: EventBus,
        audit_trail: AuditTrail,
        process_manager: ProcessManager,
        workload_discovery: WorkloadDiscovery,
        state_path: Path | None = None,
    ) -> None:
        self._bus = event_bus
        self._audit = audit_trail
        self._pm = process_manager
        self._wd = workload_discovery
        self._agents: dict[str, AgentIdentity] = {}
        self._state_path = state_path or Path(".opensculpt/agent_registry.json")

    async def discover_available(self) -> list[AgentIdentity]:
        """Scan for bundled workloads and register them as available."""
        manifests = await self._wd.scan()
        discovered = []

        for m in manifests:
            if any(a.name == m.name for a in self._agents.values()):
                continue  # Already registered

            agent = AgentIdentity(
                id=new_id(),
                name=m.name,
                display_name=_pretty_name(m.name),
                status=AgentStatus.AVAILABLE,
                source="bundled",
                runtime=m.runtime,
                path=m.path,
                description=m.description,
                memory_limit_mb=m.memory_limit_mb,
            )
            self._agents[agent.id] = agent
            discovered.append(agent)

            await self._bus.emit("agent.available", {
                "agent_id": agent.id,
                "name": agent.name,
                "runtime": agent.runtime,
                "source": "bundled",
            }, source="agent_registry")

        self._save_state()
        return discovered

    def register_live_agent(self, agent_id: str, name: str, task: str = "",
                            source: str = "os_agent") -> AgentIdentity:
        """Register an OS sub-agent as a live running agent.

        Called by the OS agent when it spawns sub-agents so they appear
        in the Agents tab dashboard.
        """
        # Don't duplicate
        if any(a.name == name and a.status == AgentStatus.RUNNING
               for a in self._agents.values()):
            existing = next(a for a in self._agents.values()
                           if a.name == name and a.status == AgentStatus.RUNNING)
            return existing

        agent = AgentIdentity(
            id=agent_id,
            name=name,
            display_name=_pretty_name(name),
            status=AgentStatus.RUNNING,
            source=source,
            runtime="python",
            description=task[:200] if task else f"Sub-agent: {name}",
        )
        agent.installed_at = time.time()
        self._agents[agent_id] = agent
        self._save_state()
        return agent

    def mark_agent_completed(self, agent_id: str, success: bool = True) -> None:
        """Mark a live agent as completed/crashed."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.status = AgentStatus.STOPPED if success else AgentStatus.CRASHED
            agent.total_runs += 1
            self._save_state()

    async def install_from_github(self, github_url: str, name: str | None = None) -> AgentIdentity:
        """Install an agent from a GitHub repository.

        The OS clones the repo, detects runtime, installs deps, creates identity.
        """
        agent_id = new_id()
        repo_name = name or github_url.rstrip("/").split("/")[-1].replace(".git", "")
        install_path = str(Path(self._wd._workload_dir) / repo_name)

        agent = AgentIdentity(
            id=agent_id,
            name=repo_name,
            display_name=_pretty_name(repo_name),
            status=AgentStatus.INSTALLING,
            source=github_url,
            path=install_path,
        )
        self._agents[agent_id] = agent

        await self._bus.emit("agent.installing", {
            "agent_id": agent_id,
            "name": repo_name,
            "source": github_url,
        }, source="agent_registry")

        try:
            # Clone the repo
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", github_url, install_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                agent.status = AgentStatus.ERROR
                agent.install_error = stderr.decode("utf-8", errors="replace")[:500]
                await self._bus.emit("agent.install_failed", {
                    "agent_id": agent_id,
                    "name": repo_name,
                    "error": agent.install_error[:200],
                }, source="agent_registry")
                return agent

            # Detect runtime and install deps
            manifests = await self._wd.scan()
            manifest = next((m for m in manifests if m.name == repo_name), None)
            if manifest:
                agent.runtime = manifest.runtime
                agent.memory_limit_mb = manifest.memory_limit_mb
                ok = await self._wd.install(repo_name)
                if not ok:
                    agent.install_error = "Dependency installation failed"

            agent.status = AgentStatus.INSTALLED
            agent.installed_at = time.time()

            await self._bus.emit("agent.installed", {
                "agent_id": agent_id,
                "name": repo_name,
                "runtime": agent.runtime,
                "source": github_url,
            }, source="agent_registry")

            await self._audit.log_state_change(
                agent_id, repo_name, "installing", "installed"
            )

        except asyncio.TimeoutError:
            agent.status = AgentStatus.ERROR
            agent.install_error = "Clone timed out after 120s"
        except Exception as e:
            agent.status = AgentStatus.ERROR
            agent.install_error = str(e)[:300]

        self._save_state()
        return agent

    async def install(self, agent_id: str) -> AgentIdentity:
        """Install a discovered (available) agent — install its dependencies."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        if agent.status not in (AgentStatus.AVAILABLE, AgentStatus.ERROR):
            raise ValueError(f"Agent {agent.name} is {agent.status.value}, cannot install")

        agent.status = AgentStatus.INSTALLING
        await self._bus.emit("agent.installing", {
            "agent_id": agent_id,
            "name": agent.name,
        }, source="agent_registry")

        ok = await self._wd.install(agent.name)
        if ok:
            agent.status = AgentStatus.INSTALLED
            agent.installed_at = time.time()
            agent.install_error = ""
            await self._bus.emit("agent.installed", {
                "agent_id": agent_id,
                "name": agent.name,
                "runtime": agent.runtime,
            }, source="agent_registry")
            await self._audit.log_state_change(
                agent_id, agent.name, "available", "installed"
            )
        else:
            agent.status = AgentStatus.ERROR
            manifest = self._wd.get_manifest(agent.name)
            agent.install_error = manifest.install_error if manifest else "Install failed"
            await self._bus.emit("agent.install_failed", {
                "agent_id": agent_id,
                "name": agent.name,
                "error": agent.install_error[:200],
            }, source="agent_registry")

        self._save_state()
        return agent

    async def start(self, agent_id: str) -> AgentIdentity:
        """Start a user-installed agent — spawn it as a supervised process."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        if agent.status == AgentStatus.RUNNING:
            raise ValueError(f"Agent {agent.name} is already running")
        if agent.status not in (AgentStatus.INSTALLED, AgentStatus.STOPPED, AgentStatus.CRASHED):
            raise ValueError(
                f"Agent {agent.name} is {agent.status.value} — install it first"
            )

        manifest = self._wd.get_manifest(agent.name)
        if not manifest:
            raise ValueError(f"No workload manifest for {agent.name}")

        # Spawn via ProcessManager with the agent's quotas
        proc_info = await self._pm.spawn(
            name=agent.name,
            command=manifest.entry_point,
            workdir=manifest.path,
            memory_limit_mb=agent.memory_limit_mb,
            token_limit=agent.token_limit,
            kind="user_agent",
            tags={
                "runtime": agent.runtime,
                "agent_id": agent.id,
                "source": agent.source,
            },
            auto_restart=agent.max_restarts > 0,
        )

        agent.status = AgentStatus.RUNNING
        agent.started_at = time.time()
        agent.process_pid = proc_info.pid
        agent.total_runs += 1

        await self._bus.emit("agent.started", {
            "agent_id": agent_id,
            "name": agent.name,
            "process_pid": proc_info.pid,
            "os_pid": proc_info.os_pid,
        }, source="agent_registry")

        await self._audit.log_state_change(
            agent_id, agent.name, "installed", "running"
        )

        # Monitor for crashes in background
        asyncio.create_task(self._watch_process(agent_id))

        self._save_state()
        return agent

    async def stop(self, agent_id: str) -> AgentIdentity:
        """Stop a running agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        if agent.status != AgentStatus.RUNNING:
            raise ValueError(f"Agent {agent.name} is not running")

        if agent.process_pid:
            await self._pm.kill(agent.process_pid, reason=f"user stopped {agent.name}")

        if agent.started_at:
            agent.total_uptime_s += int(time.time() - agent.started_at)

        agent.status = AgentStatus.STOPPED
        agent.stopped_at = time.time()

        await self._bus.emit("agent.stopped", {
            "agent_id": agent_id,
            "name": agent.name,
        }, source="agent_registry")

        await self._audit.log_state_change(
            agent_id, agent.name, "running", "stopped"
        )

        self._save_state()
        return agent

    async def uninstall(self, agent_id: str) -> None:
        """Uninstall an agent — stop it and remove files."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        if agent.status == AgentStatus.RUNNING:
            await self.stop(agent_id)

        await self._bus.emit("agent.uninstalled", {
            "agent_id": agent_id,
            "name": agent.name,
        }, source="agent_registry")

        # Only remove files if installed from GitHub (not bundled)
        if agent.source != "bundled" and agent.path:
            import shutil
            try:
                shutil.rmtree(agent.path, ignore_errors=True)
            except Exception:
                pass

        del self._agents[agent_id]
        self._save_state()

    async def set_quota(
        self,
        agent_id: str,
        memory_limit_mb: float | None = None,
        token_limit: int | None = None,
        max_restarts: int | None = None,
    ) -> AgentIdentity:
        """Update resource quotas for an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        if memory_limit_mb is not None:
            agent.memory_limit_mb = memory_limit_mb
        if token_limit is not None:
            agent.token_limit = token_limit
        if max_restarts is not None:
            agent.max_restarts = max_restarts

        await self._bus.emit("agent.quota_updated", {
            "agent_id": agent_id,
            "name": agent.name,
            "memory_limit_mb": agent.memory_limit_mb,
            "token_limit": agent.token_limit,
            "max_restarts": agent.max_restarts,
        }, source="agent_registry")

        self._save_state()
        return agent

    async def _watch_process(self, agent_id: str) -> None:
        """Background watcher — detects when a process exits and updates agent status."""
        agent = self._agents.get(agent_id)
        if not agent or not agent.process_pid:
            return

        while agent.status == AgentStatus.RUNNING:
            await asyncio.sleep(5)
            proc_info = self._pm.get_process(agent.process_pid)
            if not proc_info:
                break
            if proc_info.state.value in ("crashed", "killed", "stopped"):
                if agent.started_at:
                    agent.total_uptime_s += int(time.time() - agent.started_at)
                if proc_info.state.value == "crashed":
                    agent.status = AgentStatus.CRASHED
                    agent.total_crashes += 1
                else:
                    agent.status = AgentStatus.STOPPED
                agent.stopped_at = time.time()
                # Sync token usage
                agent.total_tokens_used += proc_info.token_count
                self._save_state()
                break

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents (available, installed, running, etc.)."""
        result = []
        for agent in self._agents.values():
            # Sync live process stats if running
            proc_stats = {}
            if agent.process_pid and agent.status == AgentStatus.RUNNING:
                proc_info = self._pm.get_process(agent.process_pid)
                if proc_info:
                    proc_stats = {
                        "os_pid": proc_info.os_pid,
                        "memory_mb": round(proc_info.memory_mb, 1),
                        "token_count": proc_info.token_count,
                        "uptime_s": int(time.time() - (proc_info.started_at or time.time())),
                        "restart_count": proc_info.restart_count,
                    }

            result.append({
                "id": agent.id,
                "name": agent.name,
                "display_name": agent.display_name,
                "status": agent.status.value,
                "source": agent.source,
                "runtime": agent.runtime,
                "description": agent.description,
                "memory_limit_mb": agent.memory_limit_mb,
                "token_limit": agent.token_limit,
                "max_restarts": agent.max_restarts,
                "installed_at": agent.installed_at,
                "total_runs": agent.total_runs,
                "total_uptime_s": agent.total_uptime_s,
                "total_crashes": agent.total_crashes,
                "total_tokens_used": agent.total_tokens_used,
                "install_error": agent.install_error,
                **proc_stats,
            })
        return result

    async def setup(self, name: str, github_url: str = "") -> AgentIdentity:
        """One-shot: user says 'set up X' and the OS handles everything.

        Discovers, installs dependencies, creates identity, starts the process.
        Works with bundled agents (by name) or GitHub repos (by URL).
        """
        agent = self.get_agent_by_name(name)

        # If not found and a GitHub URL was given, clone it
        if not agent and github_url:
            agent = await self.install_from_github(github_url, name=name)
            if agent.status == AgentStatus.ERROR:
                return agent

        # If still not found, try discovering bundled workloads
        if not agent:
            await self.discover_available()
            agent = self.get_agent_by_name(name)

        if not agent:
            raise ValueError(
                f"Agent '{name}' not found. Available: "
                + ", ".join(a.name for a in self._agents.values())
            )

        # Install if needed
        if agent.status == AgentStatus.AVAILABLE:
            agent = await self.install(agent.id)
            if agent.status == AgentStatus.ERROR:
                return agent

        # Start if not already running
        if agent.status in (AgentStatus.INSTALLED, AgentStatus.STOPPED, AgentStatus.CRASHED):
            agent = await self.start(agent.id)

        return agent

    def get_agent(self, agent_id: str) -> AgentIdentity | None:
        return self._agents.get(agent_id)

    def get_agent_by_name(self, name: str) -> AgentIdentity | None:
        return next((a for a in self._agents.values() if a.name == name), None)

    def _save_state(self) -> None:
        """Persist agent registry to disk."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for aid, agent in self._agents.items():
                d = asdict(agent)
                d["status"] = agent.status.value
                data[aid] = d
            self._state_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_state(self) -> None:
        """Load persisted agent registry from disk."""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text())
                for aid, d in data.items():
                    d["status"] = AgentStatus(d["status"])
                    # Reset running agents to stopped (they aren't running after restart)
                    if d["status"] == AgentStatus.RUNNING:
                        d["status"] = AgentStatus.STOPPED
                    self._agents[aid] = AgentIdentity(**d)
        except Exception:
            pass


def _pretty_name(name: str) -> str:
    """Convert slug to display name: 'openclaw' → 'OpenClaw'."""
    parts = name.replace("-", " ").replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)
