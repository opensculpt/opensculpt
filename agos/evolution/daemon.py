"""Local evolution daemon — runs evolution on a schedule in the background.

Meant for users who want their agos to continuously self-improve locally.
Uses asyncio tasks for scheduling.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from agos.events.bus import EventBus
from agos.evolution.engine import EvolutionEngine, EvolutionReport
from agos.evolution.pipeline import EvolutionPipelineConfig, assess_risk

logger = structlog.get_logger()


class EvolutionDaemon:
    """Background daemon that runs evolution cycles on a schedule."""

    def __init__(
        self,
        engine: EvolutionEngine,
        event_bus: EventBus | None = None,
        config: EvolutionPipelineConfig | None = None,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._config = config or EvolutionPipelineConfig()
        self._running = False
        self._task: asyncio.Task | None = None
        self._history: list[EvolutionReport] = []

    async def start(self) -> None:
        """Start the daemon loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        await self._emit(
            "evolution.daemon_started",
            {"interval_hours": self._config.evolution_interval_hours},
        )

    async def stop(self) -> None:
        """Stop the daemon."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self._emit("evolution.daemon_stopped", {})

    async def run_once(self) -> EvolutionReport:
        """Run a single evolution cycle."""
        report = await self._engine.run_cycle(
            days=self._config.evolution_days_lookback,
            max_papers=self._config.evolution_max_papers,
        )
        self._history.append(report)

        # Assess risk for each new proposal
        proposals = await self._engine.get_proposals(status="proposed")
        for proposal in proposals:
            risk = assess_risk(proposal)
            await self._emit(
                "evolution.daemon_proposal_assessed",
                {
                    "proposal_id": proposal.id,
                    "risk_level": risk.risk_level,
                    "auto_mergeable": risk.auto_mergeable,
                },
            )

            # Auto-accept low-risk proposals if configured
            if (
                self._config.auto_merge_low_risk
                and risk.auto_mergeable
                and not self._config.require_human_review
            ):
                await self._engine.accept_proposal(
                    proposal.id,
                    notes=f"Auto-accepted by daemon (risk={risk.risk_level})",
                )

        return report

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def history(self) -> list[EvolutionReport]:
        return list(self._history)

    async def _run_loop(self) -> None:
        """Main daemon loop — runs evolution cycles on a schedule."""
        interval_seconds = self._config.evolution_interval_hours * 3600
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("evolution_daemon_cycle_failed", error=str(e))
                await self._emit("evolution.daemon_error", {"error": str(e)})

            # Wait for next cycle (or until stopped)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    async def _emit(self, topic: str, data: dict[str, Any]) -> None:
        """Emit an event on the bus."""
        if self._event_bus:
            await self._event_bus.emit(topic, data, source="evolution_daemon")
