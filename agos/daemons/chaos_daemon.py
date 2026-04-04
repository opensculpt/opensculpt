"""Chaos Monkey Daemon — two-layer resilience testing.

Layer 1: Infrastructure chaos (kill containers, corrupt files) — chaos.py
Layer 2: User chaos (synthetic commands, invariant checks) — user_chaos.py

Both layers alternate on each tick and feed the same resilience score.
Disabled by default (SCULPT_CHAOS_ENABLED=false). Opt-in for testing/development.
"""
from __future__ import annotations

import logging
import time

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)

# Severity-aware cooldown (seconds between experiments)
_COOLDOWN_BY_SEVERITY = {
    "low": 300,     # 5 min
    "medium": 900,  # 15 min
    "high": 1800,   # 30 min
}
_DEFAULT_COOLDOWN = 900


class ChaosMonkeyDaemon(Daemon):
    """Periodic chaos injection — alternates infra and user chaos."""

    name = "chaos_monkey"
    description = "Two-layer resilience testing: infrastructure + user chaos"
    icon = "🐒"
    one_shot = False
    default_interval = 300  # Check every 5 minutes (actual experiments gated by cooldown)

    def __init__(self) -> None:
        super().__init__()
        self._monkey = None        # Infrastructure chaos (ChaosMonkey)
        self._user_monkey = None   # User-level chaos (UserChaosMonkey)
        self._last_experiment_at: float = 0
        self._last_severity: str = "medium"
        self._tick_count: int = 0
        self._goal_runner = None
        self._demand_collector = None
        self._evo_memory = None
        self._os_agent = None
        # Feedback loop: track experiment → demand → re-run result
        self._feedback_log: list[dict] = []

    def set_goal_runner(self, gr) -> None:
        self._goal_runner = gr

    def set_demand_collector(self, dc) -> None:
        self._demand_collector = dc

    def set_evo_memory(self, mem) -> None:
        self._evo_memory = mem

    def set_os_agent(self, agent) -> None:
        self._os_agent = agent

    async def setup(self) -> None:
        from agos.evolution.chaos import ChaosMonkey
        self._monkey = ChaosMonkey(
            event_bus=self._event_bus,
            demand_collector=self._demand_collector,
            evo_memory=self._evo_memory,
            os_agent=self._os_agent,
        )

        from agos.evolution.user_chaos import UserChaosMonkey
        # Internal mode: pass OS agent directly for full observability (no HTTP round-trip)
        self._user_monkey = UserChaosMonkey(os_agent=self._os_agent)

        infra_count = len(self._monkey.list_experiments())
        user_count = len(self._user_monkey.list_experiments())
        _logger.info(
            "Chaos Monkey daemon initialized: %d infra + %d user experiments",
            infra_count, user_count,
        )

    async def tick(self) -> None:
        if not self._monkey:
            return

        # Only run infra chaos when there's an active goal
        # User chaos can run anytime (tests how OS handles cold-start commands)
        has_active_goal = False
        if self._goal_runner:
            active = [g for g in self._goal_runner.get_goals()
                      if g.get("status") == "active"]
            has_active_goal = bool(active)

        # Adaptive cooldown: fast at start (assessment), slow as score stabilizes
        base_cooldown = _COOLDOWN_BY_SEVERITY.get(self._last_severity, _DEFAULT_COOLDOWN)
        if self._tick_count < 6:
            # First 6 ticks: run fast (60s) for initial assessment
            cooldown = 60
        elif len(self._feedback_log) > 10:
            # After enough data: slow down if score is stable
            recent = self._feedback_log[-10:]
            pass_rate = sum(1 for f in recent if f.get("passed")) / 10
            if pass_rate > 0.8:
                cooldown = base_cooldown * 2  # Things are good, slow down
            else:
                cooldown = base_cooldown  # Still finding issues, keep pace
        else:
            cooldown = base_cooldown

        elapsed = time.time() - self._last_experiment_at
        if elapsed < cooldown:
            return

        self._tick_count += 1

        # Alternate: even ticks = infra, odd ticks = user
        if self._tick_count % 2 == 0 and has_active_goal and self._monkey:
            await self._run_infra_chaos()
        elif self._user_monkey:
            await self._run_user_chaos()

    async def _run_infra_chaos(self) -> None:
        """Run an infrastructure chaos experiment."""
        category = self.config.get("category", "")
        result = await self._monkey.run_random(category=category)
        self._last_experiment_at = time.time()

        # Get severity of the experiment that ran
        exp = next((e for e in self._monkey.list_experiments()
                    if e.name == result.experiment), None)
        self._last_severity = exp.severity if exp else "medium"

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=result.recovered,
            summary=(
                f"[infra] {result.experiment}: "
                f"injected={result.injected} "
                f"detected={result.detected} "
                f"recovered={result.recovered}"
            ),
            data={
                "layer": "infrastructure",
                "experiment": result.experiment,
                "injected": result.injected,
                "detected": result.detected,
                "recovered": result.recovered,
                "evolution_triggered": result.evolution_triggered,
                "time_to_detect_s": result.time_to_detect_s,
                "time_to_recover_s": result.time_to_recover_s,
            },
        ))

        self._write_trace("infra", result.experiment, result.recovered, {
            "injected": result.injected,
            "detected": result.detected,
            "evolution_triggered": result.evolution_triggered,
            "time_to_detect_s": result.time_to_detect_s,
            "time_to_recover_s": result.time_to_recover_s,
            "details": result.details or "",
        })

        # Inject demand for unrecovered failures
        if result.injected and result.detected and not result.recovered:
            self._inject_demand(
                f"chaos:infra:{result.experiment}",
                f"OS failed to recover from infra chaos: {result.experiment}. "
                f"Detected in {result.time_to_detect_s:.0f}s but not recovered. "
                f"{result.details[:200]}",
            )

        # Feedback loop: check if previously-unrecovered experiment now recovers
        prev_failures = [f for f in self._feedback_log
                         if f["experiment"] == result.experiment and not f.get("passed")]
        if result.recovered and prev_failures:
            _logger.info(
                "Chaos FEEDBACK: '%s' now RECOVERS (was failing). Evolution fixed it!",
                result.experiment,
            )
            self._write_trace("feedback", result.experiment, True, {
                "event": "regression_fixed",
                "layer": "infrastructure",
                "previous_failures": len(prev_failures),
            })
        self._feedback_log.append({
            "experiment": result.experiment,
            "passed": result.recovered,
            "tick": self._tick_count,
        })

        self._update_resilience_score()
        _logger.info("Chaos [infra]: %s — recovered=%s", result.experiment, result.recovered)

    async def _run_user_chaos(self) -> None:
        """Run a user-level chaos experiment."""
        category = self.config.get("user_category", "")
        result = await self._user_monkey.run_random(category=category)
        self._last_experiment_at = time.time()
        self._last_severity = "medium"  # User chaos is always medium severity

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=result.passed,
            summary=(
                f"[user] {result.experiment}: "
                f"passed={result.passed} "
                f"violations={result.violations}"
            ),
            data={
                "layer": "user",
                "experiment": result.experiment,
                "category": result.category,
                "command": result.command,
                "passed": result.passed,
                "violations": result.violations,
                "goal_status": result.goal_status,
                "duration_s": result.duration_s,
            },
        ))

        self._write_trace("user", result.experiment, result.passed, {
            "command": result.command,
            "category": result.category,
            "violations": result.violations,
            "goal_status": result.goal_status,
            "response_preview": result.response_text[:200],
        })

        # Inject demand for invariant violations
        if not result.passed:
            self._inject_demand(
                f"chaos:user:{result.experiment}",
                f"User chaos '{result.experiment}' failed invariants: "
                f"{', '.join(result.violations)}. "
                f"Command: '{result.command}'. {result.details[:200]}",
            )

        # Feedback loop: check if a previously-failed experiment now passes
        prev_failures = [f for f in self._feedback_log
                         if f["experiment"] == result.experiment and not f["passed"]]
        if result.passed and prev_failures:
            _logger.info(
                "Chaos FEEDBACK: '%s' now PASSES (was failing). Evolution fixed it!",
                result.experiment,
            )
            self._write_trace("feedback", result.experiment, True, {
                "event": "regression_fixed",
                "previous_failures": len(prev_failures),
                "fixed_after_attempts": prev_failures[-1].get("attempt", 0),
            })
        self._feedback_log.append({
            "experiment": result.experiment,
            "passed": result.passed,
            "violations": result.violations,
            "tick": self._tick_count,
            "attempt": len([f for f in self._feedback_log if f["experiment"] == result.experiment]) + 1,
        })

        self._update_resilience_score()
        _logger.info("Chaos [user]: %s — passed=%s violations=%s",
                      result.experiment, result.passed, result.violations)

    def _write_trace(self, layer: str, experiment: str, ok: bool, data: dict) -> None:
        """Write experiment result to trace store."""
        try:
            from agos.evolution.trace_store import TraceStore
            TraceStore().write_evo_trace(0, {
                "kind": "chaos_experiment",
                "tool": experiment,
                "ok": ok,
                "args": data,
                "output": f"layer={layer} ok={ok}",
                "context": f"chaos:{layer}:{experiment}",
                "source": "chaos_monkey",
            })
        except Exception:
            pass

    def _update_resilience_score(self) -> None:
        """Persist resilience score to EvolutionState.chaos_stats."""
        if not self._evo_memory:
            return
        user_results = [f for f in self._feedback_log]
        user_passed = sum(1 for f in user_results if f.get("passed"))
        total = len(self._feedback_log)
        user_rate = user_passed / max(len(user_results), 1)
        score = user_rate * 0.6  # infra handled separately

        stats = {
            "experiments_run": total,
            "resilience_score": round(score, 3),
            "user_passed": user_passed,
            "user_total": len(user_results),
            "feedback_fixes": sum(1 for f in self._feedback_log
                                  if f.get("experiment") in
                                  {g["experiment"] for g in self._feedback_log if not g.get("passed")}
                                  and f.get("passed")),
        }
        try:
            self._evo_memory.chaos_stats = stats
            self._evo_memory.save_evolution_memory()
        except Exception:
            pass

    def _inject_demand(self, key: str, description: str) -> None:
        """Inject a demand signal for an unrecovered failure."""
        if self._demand_collector:
            self._demand_collector._add_signal(
                key=key,
                kind="chaos_unrecovered",
                source="chaos_monkey",
                description=description,
                priority=0.9,
            )
            _logger.info("Chaos: injected demand '%s'", key)
