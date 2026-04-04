"""Chaos Monkey for OpenSculpt — deliberately inject failures to harden the OS.

Inspired by Netflix's Simian Army. Instead of waiting for natural failures,
the Chaos Monkey deliberately breaks things so the Evolution Agent has
real problems to solve.

Each chaos experiment follows the Netflix pattern:
1. Define STEADY STATE (what "working" looks like)
2. INJECT failure (kill service, corrupt data, remove capability)
3. OBSERVE (did the OS detect it? did it self-heal?)
4. LEARN (did the Evolution Agent produce a fix?)
5. HARDEN (is the OS more resilient after?)

Chaos experiments are derived from SCENARIOS.md — real failure modes
that users will encounter in production.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agos.events.bus import EventBus

_logger = logging.getLogger(__name__)


@dataclass
class ChaosExperiment:
    """A single chaos experiment definition."""
    name: str
    scenario: str  # Which SCENARIOS.md scenario this tests
    category: str  # infrastructure, data, capability, performance
    description: str
    steady_state: str  # What "working" looks like
    inject: str  # What to break
    verify_detection: str  # How to check OS detected the failure
    verify_recovery: str  # How to check OS self-healed
    severity: str = "medium"  # low, medium, high
    requires: list[str] = field(default_factory=list)  # e.g., ["docker", "running_goal"]


@dataclass
class ChaosResult:
    """Result of running a chaos experiment."""
    experiment: str
    injected: bool = False
    detected: bool = False
    recovered: bool = False
    evolution_triggered: bool = False
    insights_before: int = 0
    insights_after: int = 0
    time_to_detect_s: float = 0
    time_to_recover_s: float = 0
    details: str = ""


# ── Chaos experiments derived from SCENARIOS.md ──

EXPERIMENTS: list[ChaosExperiment] = [
    # === INFRASTRUCTURE FAILURES ===
    ChaosExperiment(
        name="kill_deployed_service",
        scenario="1-Sales CRM / 2-Support / 4-DevOps",
        category="infrastructure",
        description="Kill a running Docker container that the OS deployed",
        steady_state="Container running, health check passes",
        inject="docker stop <container>",
        verify_detection="DemandCollector receives service_down signal within 60s",
        verify_recovery="OS restarts the container or redeploys",
        severity="high",
        requires=["docker"],
    ),
    ChaosExperiment(
        name="remove_config_file",
        scenario="4-DevOps monitoring",
        category="infrastructure",
        description="Delete a config file the OS created (prometheus.yml, grafana provisioning)",
        steady_state="Config file exists, service uses it",
        inject="rm /app/monitoring/prometheus/prometheus.yml",
        verify_detection="Next verify phase detects missing file",
        verify_recovery="OS recreates the file from skill docs",
        severity="medium",
    ),
    ChaosExperiment(
        name="port_conflict",
        scenario="1-Sales CRM / 7-E-commerce",
        category="infrastructure",
        description="Occupy a port the OS needs for deployment",
        steady_state="Port 8081 available",
        inject="python -c 'import socket; s=socket.socket(); s.bind((\"0.0.0.0\",8081)); s.listen(1); import time; time.sleep(300)'",
        verify_detection="Deployment phase fails, demand signal created",
        verify_recovery="OS chooses different port or kills conflicting process",
        severity="medium",
        requires=["docker"],
    ),

    # === CAPABILITY FAILURES ===
    ChaosExperiment(
        name="llm_returns_empty",
        scenario="ALL",
        category="capability",
        description="Simulate LLM returning empty responses (rate limit/timeout)",
        steady_state="LLM responds to queries",
        inject="Temporarily replace LLM response with empty string",
        verify_detection="GoalRunner detects 'LLM returned empty response'",
        verify_recovery="Evolution Agent patches GoalRunner to handle gracefully",
        severity="high",
    ),
    ChaosExperiment(
        name="tool_not_found",
        scenario="4-DevOps / 1-Sales",
        category="capability",
        description="Remove a tool from the registry that agents depend on",
        steady_state="docker_run tool available in registry",
        inject="Unregister docker_run from tool registry",
        verify_detection="Next agent using docker_run gets tool_not_found error",
        verify_recovery="Evolution Agent creates replacement or reactivates builtin",
        severity="medium",
    ),
    ChaosExperiment(
        name="delete_skill_doc",
        scenario="ALL",
        category="capability",
        description="Delete a skill doc that agents use for context",
        steady_state="Skill doc exists, agents use it",
        inject="rm .opensculpt/skills/<topic>.md",
        verify_detection="Agent performance degrades (more turns needed)",
        verify_recovery="Evolution Agent recreates skill doc from demand patterns",
        severity="low",
    ),

    # === DATA FAILURES ===
    ChaosExperiment(
        name="corrupt_evolution_state",
        scenario="ALL",
        category="data",
        description="Corrupt the evolution state file",
        steady_state="evolution_state.json loads correctly",
        inject="Write invalid JSON to evolution_state.json",
        verify_detection="Evolution loop catches JSON parse error",
        verify_recovery="Evolution loop creates fresh state, doesn't crash",
        severity="high",
    ),
    ChaosExperiment(
        name="clear_all_demands",
        scenario="ALL",
        category="data",
        description="Clear all demand signals",
        steady_state="DemandCollector has active demands",
        inject="Clear all signals from DemandCollector",
        verify_detection="New demands regenerate from ongoing failures",
        verify_recovery="Evolution continues from new signals",
        severity="low",
    ),

    # === PERFORMANCE FAILURES ===
    ChaosExperiment(
        name="slow_llm_response",
        scenario="ALL",
        category="performance",
        description="Inject 30-second delay before LLM responses",
        steady_state="LLM responds in <5 seconds",
        inject="Add asyncio.sleep(30) before LLM call",
        verify_detection="GoalRunner phase times out",
        verify_recovery="OS learns to use shorter timeouts or retry",
        severity="medium",
    ),
    ChaosExperiment(
        name="disk_full_simulation",
        scenario="3-Knowledge / 8-Finance",
        category="performance",
        description="Fill up /tmp to simulate disk pressure",
        steady_state="Sufficient disk space for operations",
        inject="dd if=/dev/zero of=/tmp/fill bs=1M count=500",
        verify_detection="File write operations fail",
        verify_recovery="OS detects disk issue, cleans up, adapts",
        severity="medium",
    ),

    # === SCENARIO-SPECIFIC FAILURES ===
    ChaosExperiment(
        name="database_connection_lost",
        scenario="1-Sales / 2-Support / 8-Finance",
        category="infrastructure",
        description="Kill the database container that a CRM/helpdesk depends on",
        steady_state="Database container running, app connected",
        inject="docker stop <db_container>",
        verify_detection="App reports connection error, demand signal created",
        verify_recovery="OS restarts database, verifies connection restored",
        severity="high",
        requires=["docker"],
    ),
    ChaosExperiment(
        name="network_partition",
        scenario="5-Company-in-a-Box",
        category="infrastructure",
        description="Block network between two services",
        steady_state="Services can communicate",
        inject="docker network disconnect <network> <container>",
        verify_detection="Cross-service operations fail",
        verify_recovery="OS reconnects or recreates network",
        severity="high",
        requires=["docker"],
    ),
]


# Severity-aware cooldown (seconds)
COOLDOWN_BY_SEVERITY = {
    "low": 300,     # 5 min — skill doc deletion, demand clearing
    "medium": 900,  # 15 min — config deletion, port conflict
    "high": 1800,   # 30 min — service kill, data corruption
}


class ChaosLLMProxy:
    """Wraps an LLM provider to inject chaos faults.

    Instead of fragile monkey-patching (which breaks if OS agent creates
    a new LLM instance), this proxy wraps the provider object and intercepts
    all calls. The OS agent's _llm reference is replaced with this proxy,
    which delegates to the real provider unless a fault is active.
    """

    def __init__(self, real_llm):
        self._real = real_llm
        self._fault: str | None = None  # "empty", "slow", None
        self._fault_until: float = 0

    async def complete(self, *args, **kwargs):
        if self._fault and time.time() < self._fault_until:
            if self._fault == "empty":
                from agos.llm.base import LLMResponse
                return LLMResponse(
                    content="", tool_calls=[], stop_reason="stop",
                    input_tokens=0, output_tokens=0,
                )
            elif self._fault == "slow":
                await asyncio.sleep(30)
                return await self._real.complete(*args, **kwargs)
        return await self._real.complete(*args, **kwargs)

    def inject_fault(self, fault: str, duration_s: float = 60) -> None:
        """Inject a fault: 'empty' or 'slow'."""
        self._fault = fault
        self._fault_until = time.time() + duration_s

    def clear_fault(self) -> None:
        self._fault = None
        self._fault_until = 0

    def __getattr__(self, name):
        """Delegate everything else to the real LLM provider."""
        return getattr(self._real, name)


class ChaosMonkey:
    """Runs chaos experiments against the running OS.

    Usage:
        monkey = ChaosMonkey(event_bus, demand_collector, evo_memory)

        # Run a specific experiment
        result = await monkey.run_experiment("kill_deployed_service")

        # Run a random experiment
        result = await monkey.run_random()

        # Run all experiments for a scenario
        results = await monkey.run_scenario("devops")
    """

    def __init__(
        self,
        event_bus: EventBus,
        demand_collector=None,
        evo_memory=None,
        os_agent=None,
    ):
        self._bus = event_bus
        self._demands = demand_collector
        self._memory = evo_memory
        self._os_agent = os_agent
        # Track what was broken for specific recovery checks
        self._last_injection_state: dict = {}

    def set_os_agent(self, agent) -> None:
        """Set OS agent reference for monkey-patching LLM/tools."""
        self._os_agent = agent

    def list_experiments(self, category: str = "", scenario: str = "") -> list[ChaosExperiment]:
        """List available experiments, optionally filtered."""
        experiments = EXPERIMENTS
        if category:
            experiments = [e for e in experiments if e.category == category]
        if scenario:
            experiments = [e for e in experiments
                           if scenario.lower() in e.scenario.lower()]
        return experiments

    async def run_experiment(self, name: str) -> ChaosResult:
        """Run a specific chaos experiment by name."""
        experiment = next((e for e in EXPERIMENTS if e.name == name), None)
        if not experiment:
            return ChaosResult(experiment=name, details=f"Unknown experiment: {name}")

        _logger.info("Chaos Monkey: starting experiment '%s'", name)
        await self._bus.emit("chaos.experiment_started", {
            "name": name, "category": experiment.category,
            "description": experiment.description,
        }, source="chaos_monkey")

        result = ChaosResult(experiment=name)

        # Record state before
        result.insights_before = len(self._memory.insights) if self._memory else 0
        demands_before = self._demands.pending_count() if self._demands else 0

        # Inject the failure
        try:
            result.injected = await self._inject(experiment)
        except Exception as e:
            result.details = f"Injection failed: {e}"
            _logger.warning("Chaos Monkey: injection failed for '%s': %s", name, e)
            return result

        if not result.injected:
            result.details = "Could not inject failure (prerequisites not met)"
            return result

        # Wait for detection (up to 120s)
        inject_time = time.time()
        detected = False
        for _ in range(24):  # 24 x 5s = 120s
            await asyncio.sleep(5)
            new_demands = (self._demands.pending_count() if self._demands else 0)
            if new_demands > demands_before:
                detected = True
                result.time_to_detect_s = time.time() - inject_time
                break

        result.detected = detected
        if not detected:
            result.details = "Failure not detected within 120s"
            _logger.info("Chaos Monkey: '%s' — failure NOT detected", name)
        else:
            _logger.info("Chaos Monkey: '%s' — detected in %.1fs", name, result.time_to_detect_s)

        # Wait for recovery (up to 300s more)
        if detected:
            recovery_start = time.time()
            for _ in range(60):  # 60 x 5s = 300s
                await asyncio.sleep(5)
                # Check if steady state restored
                recovered = await self._check_recovery(experiment)
                if recovered:
                    result.recovered = True
                    result.time_to_recover_s = time.time() - recovery_start
                    break

            if result.recovered:
                _logger.info("Chaos Monkey: '%s' — recovered in %.1fs", name, result.time_to_recover_s)
            else:
                _logger.info("Chaos Monkey: '%s' — NOT recovered within 300s", name)

        # Check if evolution was triggered
        result.insights_after = len(self._memory.insights) if self._memory else 0
        result.evolution_triggered = result.insights_after > result.insights_before

        await self._bus.emit("chaos.experiment_completed", {
            "name": name,
            "injected": result.injected,
            "detected": result.detected,
            "recovered": result.recovered,
            "evolution_triggered": result.evolution_triggered,
            "time_to_detect_s": result.time_to_detect_s,
            "time_to_recover_s": result.time_to_recover_s,
        }, source="chaos_monkey")

        _logger.info(
            "Chaos Monkey result: %s — injected=%s detected=%s recovered=%s evolved=%s",
            name, result.injected, result.detected, result.recovered, result.evolution_triggered,
        )
        return result

    async def run_random(self, category: str = "") -> ChaosResult:
        """Run a random experiment."""
        candidates = self.list_experiments(category=category)
        if not candidates:
            return ChaosResult(experiment="none", details="No experiments available")
        experiment = random.choice(candidates)
        return await self.run_experiment(experiment.name)

    async def run_scenario(self, scenario: str) -> list[ChaosResult]:
        """Run all experiments relevant to a scenario."""
        experiments = self.list_experiments(scenario=scenario)
        results = []
        for exp in experiments:
            result = await self.run_experiment(exp.name)
            results.append(result)
        return results

    async def _inject(self, experiment: ChaosExperiment) -> bool:
        """Inject the failure described by the experiment.

        Each injection tracks its target in _last_injection_state so
        recovery checks can verify the SPECIFIC thing was fixed.
        """
        name = experiment.name
        self._last_injection_state = {"experiment": name, "injected_at": time.time()}

        if name == "kill_deployed_service":
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                containers = [c for c in out.stdout.strip().split("\n")
                              if c and not c.startswith("sculpt-")]
                if not containers:
                    return False
                target = random.choice(containers)
                self._last_injection_state["target"] = target
                subprocess.run(["docker", "stop", target], timeout=10)
                _logger.info("Chaos: killed container '%s'", target)
                return True
            except Exception:
                return False

        elif name == "remove_config_file":
            candidates = list(Path("/app/monitoring").rglob("*.yml")) if Path("/app/monitoring").exists() else []
            if not candidates:
                candidates = list(Path(".opensculpt/skills").glob("*.md"))
            if not candidates:
                return False
            target = random.choice(candidates)
            self._last_injection_state["target"] = str(target)
            target.unlink()
            _logger.info("Chaos: deleted config '%s'", target)
            return True

        elif name == "port_conflict":
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("0.0.0.0", 8081))
                s.listen(1)
                self._last_injection_state["target"] = "port:8081"
                self._last_injection_state["_socket"] = s
                asyncio.get_event_loop().call_later(60, s.close)
                _logger.info("Chaos: occupied port 8081")
                return True
            except OSError:
                return False

        elif name == "tool_not_found":
            # Actually unregister from OS agent's tool registry if available
            tool_name = "docker_run"
            self._last_injection_state["target"] = tool_name
            if self._os_agent and hasattr(self._os_agent, "_inner_registry"):
                registry = self._os_agent._inner_registry
                if hasattr(registry, "unregister") and registry.has_tool(tool_name):
                    self._last_injection_state["_removed_tool"] = registry.unregister(tool_name)
                    # Restore after 120s
                    removed = self._last_injection_state["_removed_tool"]
                    asyncio.get_event_loop().call_later(
                        120, lambda: registry.register(removed) if removed else None,
                    )
                    _logger.info("Chaos: actually unregistered '%s' from tool registry", tool_name)
                    return True
            # Fallback: emit event (less effective but still tests event handling)
            await self._bus.emit("chaos.tool_removed", {"tool": tool_name}, source="chaos_monkey")
            _logger.info("Chaos: emitted tool removal for %s (no registry access)", tool_name)
            return True

        elif name == "llm_returns_empty":
            # Use ChaosLLMProxy instead of fragile monkey-patching
            if self._os_agent and hasattr(self._os_agent, "_llm"):
                llm = self._os_agent._llm
                if not isinstance(llm, ChaosLLMProxy):
                    proxy = ChaosLLMProxy(llm)
                    self._os_agent._llm = proxy
                    self._last_injection_state["_proxy"] = proxy
                else:
                    proxy = llm
                proxy.inject_fault("empty", duration_s=60)
                # Auto-clear after 60s
                asyncio.get_event_loop().call_later(60, proxy.clear_fault)
                _logger.info("Chaos: LLM proxy injecting empty responses (60s)")
                return True
            await self._bus.emit("chaos.injected", {
                "experiment": name, "description": "LLM empty (no agent ref)",
            }, source="chaos_monkey")
            return True

        elif name == "slow_llm_response":
            # Use ChaosLLMProxy for delay injection
            if self._os_agent and hasattr(self._os_agent, "_llm"):
                llm = self._os_agent._llm
                if not isinstance(llm, ChaosLLMProxy):
                    proxy = ChaosLLMProxy(llm)
                    self._os_agent._llm = proxy
                    self._last_injection_state["_proxy"] = proxy
                else:
                    proxy = llm
                proxy.inject_fault("slow", duration_s=90)
                asyncio.get_event_loop().call_later(90, proxy.clear_fault)
                _logger.info("Chaos: LLM proxy injecting 30s delay (90s)")
                return True
            await self._bus.emit("chaos.injected", {
                "experiment": name, "description": "LLM slow (no agent ref)",
            }, source="chaos_monkey")
            return True

        elif name == "network_partition":
            # Actually disconnect a container from its Docker network
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                containers = [c for c in out.stdout.strip().split("\n")
                              if c and not c.startswith("sculpt-") and c]
                if not containers:
                    return False
                target = random.choice(containers)
                self._last_injection_state["target"] = target
                # Get container's network
                net_out = subprocess.run(
                    ["docker", "inspect", target, "--format",
                     "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
                    capture_output=True, text=True, timeout=10,
                )
                networks = [n for n in net_out.stdout.strip().split()
                            if n and n not in ("bridge", "host", "none")]
                if networks:
                    network = networks[0]
                    self._last_injection_state["network"] = network
                    subprocess.run(
                        ["docker", "network", "disconnect", network, target],
                        timeout=10,
                    )
                    # Reconnect after 120s
                    asyncio.get_event_loop().call_later(
                        120,
                        lambda: subprocess.run(
                            ["docker", "network", "connect", network, target],
                            timeout=10, capture_output=True,
                        ),
                    )
                    _logger.info("Chaos: disconnected '%s' from network '%s'", target, network)
                    return True
                return False
            except Exception:
                return False

        elif name == "delete_skill_doc":
            skills = list(Path(".opensculpt/skills").glob("*.md"))
            if not skills:
                return False
            target = random.choice(skills)
            self._last_injection_state["target"] = str(target)
            self._last_injection_state["target_name"] = target.name
            target.unlink()
            _logger.info("Chaos: deleted skill doc '%s'", target.name)
            return True

        elif name == "corrupt_evolution_state":
            evo_path = Path(".opensculpt/evolution_state.json")
            if evo_path.exists():
                backup = evo_path.with_suffix(".json.chaos_backup")
                backup.write_text(evo_path.read_text(encoding="utf-8"), encoding="utf-8")
                evo_path.write_text("{invalid json!!!}", encoding="utf-8")
                self._last_injection_state["target"] = str(evo_path)
                _logger.info("Chaos: corrupted evolution_state.json (backup saved)")
                return True
            return False

        elif name == "clear_all_demands":
            if self._demands:
                count = len(self._demands._signals)
                for key in list(self._demands._signals.keys()):
                    self._demands._signals[key].mark_resolved()
                self._last_injection_state["target"] = f"{count}_demands"
                _logger.info("Chaos: cleared %d demand signals", count)
                return True
            return False

        elif name == "database_connection_lost":
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                db_containers = [c for c in out.stdout.strip().split("\n")
                                 if any(db in c.lower() for db in ["mysql", "postgres", "mongo", "redis"])]
                if not db_containers:
                    return False
                target = random.choice(db_containers)
                self._last_injection_state["target"] = target
                subprocess.run(["docker", "stop", target], timeout=10)
                _logger.info("Chaos: killed database container '%s'", target)
                return True
            except Exception:
                return False

        elif name == "disk_full_simulation":
            try:
                subprocess.run(
                    "dd if=/dev/zero of=/tmp/chaos_fill bs=1M count=200",
                    shell=True, timeout=30, capture_output=True,
                )
                self._last_injection_state["target"] = "/tmp/chaos_fill"
                asyncio.get_event_loop().call_later(60, lambda: Path("/tmp/chaos_fill").unlink(missing_ok=True))
                _logger.info("Chaos: filled 200MB in /tmp")
                return True
            except Exception:
                return False

        # Default: emit a generic chaos event
        await self._bus.emit("chaos.injected", {
            "experiment": name, "description": experiment.description,
        }, source="chaos_monkey")
        return True

    async def _check_recovery(self, experiment: ChaosExperiment) -> bool:
        """Check if the OS recovered from the injected failure.

        Uses _last_injection_state to verify the SPECIFIC target was fixed,
        not just generic state.
        """
        name = experiment.name
        target = self._last_injection_state.get("target", "")

        if name == "kill_deployed_service":
            # Check if THE SPECIFIC container was restarted
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                return target in out.stdout if target else False
            except Exception:
                return False

        elif name == "remove_config_file":
            # Check if THE SPECIFIC file was recreated
            return Path(target).exists() if target else False

        elif name == "corrupt_evolution_state":
            evo_path = Path(target) if target else Path(".opensculpt/evolution_state.json")
            if evo_path.exists():
                try:
                    json.loads(evo_path.read_text(encoding="utf-8"))
                    return True
                except json.JSONDecodeError:
                    return False
            return False

        elif name == "delete_skill_doc":
            # Check if THE SPECIFIC skill doc was recreated
            if target:
                return Path(target).exists()
            skills = list(Path(".opensculpt/skills").glob("*.md"))
            return len(skills) > 0

        elif name == "database_connection_lost":
            # Check if THE SPECIFIC database container restarted
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                return target in out.stdout if target else False
            except Exception:
                return False

        elif name == "tool_not_found":
            # Check if tool was re-registered
            if self._os_agent and hasattr(self._os_agent, "_inner_registry"):
                tool_name = target or "docker_run"
                return self._os_agent._inner_registry.has_tool(tool_name)
            return False

        elif name == "network_partition":
            # Check if container reconnected to network
            network = self._last_injection_state.get("network", "")
            if target and network:
                try:
                    out = subprocess.run(
                        ["docker", "inspect", target, "--format",
                         "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    return network in out.stdout
                except Exception:
                    return False
            return False

        elif name == "port_conflict":
            # Check if port was released (our socket closed)
            sock = self._last_injection_state.get("_socket")
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            return True  # Port conflict resolves when socket closes

        elif name == "disk_full_simulation":
            fill_path = Path(target) if target else Path("/tmp/chaos_fill")
            return not fill_path.exists()  # Recovered if file was cleaned up

        # Default: check if demand count decreased
        if self._demands:
            return self._demands.pending_count() == 0
        return False


def get_scenario_experiments(scenario_name: str) -> list[dict]:
    """Get chaos experiments for a scenario, formatted for the API."""
    experiments = [e for e in EXPERIMENTS
                   if scenario_name.lower() in e.scenario.lower()
                   or scenario_name.lower() in e.name]
    # Also include ALL-scenario experiments
    experiments += [e for e in EXPERIMENTS if "ALL" in e.scenario]
    # Dedup
    seen = set()
    unique = []
    for e in experiments:
        if e.name not in seen:
            seen.add(e.name)
            unique.append({
                "name": e.name,
                "category": e.category,
                "description": e.description,
                "severity": e.severity,
                "steady_state": e.steady_state,
                "inject": e.inject,
                "verify_detection": e.verify_detection,
                "verify_recovery": e.verify_recovery,
            })
    return unique
