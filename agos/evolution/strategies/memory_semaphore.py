"""SemaphoreBatchStrategy — concurrent batch operations for Consolidator.

Instead of sequential delete/store operations during memory consolidation,
this enables concurrent batch operations limited by an asyncio.Semaphore.
Improves consolidation throughput while preventing database contention.

Based on ALMA's concurrent batch update pattern.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal
from agos.knowledge.consolidator import Consolidator


class SemaphoreBatchStrategy(IntegrationStrategy):
    """Enable concurrent batch operations in Consolidator."""

    name = "semaphore_batch"
    target_module = "knowledge.consolidator"

    def __init__(self, consolidator: Consolidator) -> None:
        self._consolidator = consolidator

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._consolidator:
            return False, "Consolidator not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        return {
            "max_concurrent_writes": self._consolidator._max_concurrent_writes,
        }

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        self._consolidator._max_concurrent_writes = 5
        # Reset semaphore so it gets recreated with new value
        self._consolidator._semaphore = None
        return [
            "Enabled semaphore-limited batch operations (max_concurrent=5)",
            "Consolidation now uses concurrent deletes and stores",
        ]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        self._consolidator._max_concurrent_writes = snapshot_data.get(
            "max_concurrent_writes", 5
        )
        self._consolidator._semaphore = None

    async def health_check(self) -> bool:
        return self._consolidator._max_concurrent_writes > 0
