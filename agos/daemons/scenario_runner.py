"""Scenario auto-runner — keeps the OS visibly doing work.

In spectator mode, this daemon picks scenarios and sends them as goals
every INTERVAL seconds. Designed for live.opensculpt.ai so visitors
always see an active, working OS.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

_logger = logging.getLogger(__name__)

# Simple scenarios that work reliably inside a container (no Docker-in-Docker)
SCENARIOS = [
    "deploy a Flask hello-world app on port 5050 and verify it responds",
    "check system health — report disk usage, memory, CPU, and network connectivity",
    "create a Python script at /tmp/file_watcher.py that monitors /tmp for new files and logs changes",
    "set up a simple key-value store REST API with FastAPI on port 5060",
    "analyze installed Python packages and report any with known security issues",
    "create a simple SQLite-backed TODO API with FastAPI on port 5070",
    "write a system monitoring dashboard script that outputs HTML to /tmp/sysmon.html",
    "scan the filesystem for files larger than 10MB and report them",
    "create a background job that checks internet connectivity every 30 seconds and logs results",
    "build a simple URL shortener API with FastAPI on port 5080",
]

INTERVAL = 300  # 5 minutes between scenarios
MAX_ACTIVE_GOALS = 2  # Don't pile up goals
GOAL_TTL = 600  # Clean up goals older than 10 min


class ScenarioRunner:
    """Daemon that auto-runs scenarios to keep the OS active."""

    def __init__(self, os_agent) -> None:
        self._os_agent = os_agent
        self._running = False
        self._task: asyncio.Task | None = None
        self._scenario_idx = 0
        # Shuffle on init so repeat visitors see different order
        random.shuffle(SCENARIOS)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        _logger.info("ScenarioRunner started (interval=%ds, %d scenarios)", INTERVAL, len(SCENARIOS))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        # Wait 30s on boot before first scenario (let OS stabilize)
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._run_next()
            except Exception as e:
                _logger.warning("ScenarioRunner error: %s", e)
            await asyncio.sleep(INTERVAL)

    async def _run_next(self) -> None:
        # Check active goal count — don't pile up
        try:
            from agos.daemons.goal_runner import GoalRunner
            goals = GoalRunner._load_goals() if hasattr(GoalRunner, '_load_goals') else []
            active = [g for g in goals if g.get("status") in ("active", "pending")]
            if len(active) >= MAX_ACTIVE_GOALS:
                _logger.info("ScenarioRunner: %d active goals, skipping", len(active))
                return

            # Clean up old completed/stale goals
            now = time.time()
            for g in goals:
                if g.get("status") in ("complete", "stale", "failed"):
                    age = now - g.get("created_at", now)
                    if age > GOAL_TTL:
                        g["status"] = "stale"
        except Exception:
            pass

        # Pick next scenario (round-robin through shuffled list)
        scenario = SCENARIOS[self._scenario_idx % len(SCENARIOS)]
        self._scenario_idx += 1

        _logger.info("ScenarioRunner: firing scenario %d — %s", self._scenario_idx, scenario[:60])

        try:
            result = await self._os_agent.execute(scenario)
            _logger.info("ScenarioRunner: scenario complete — %s", (result.get("message", "") or "")[:100])
        except Exception as e:
            _logger.warning("ScenarioRunner: scenario failed — %s", e)
