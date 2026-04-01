"""ConsolidationTuningStrategy — evolves memory lifecycle parameters.

Monitors memory growth, retrieval patterns, and storage efficiency
to tune consolidation frequency, pruning aggressiveness, and
event TTL defaults.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal


class ConsolidationTuningStrategy(IntegrationStrategy):
    """Evolve memory consolidation and lifecycle parameters."""

    name = "consolidation_tuning"
    target_module = "knowledge.consolidator"

    def __init__(self, loom=None) -> None:
        self._loom = loom
        self._consolidation_history: list[dict] = []

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._loom:
            return False, "TheLoom not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        return {
            "use_layered_recall": self._loom._use_layered_recall,
            "layers_count": len(self._loom._layers),
            "history": list(self._consolidation_history[-10:]),
        }

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Analyze memory health and tune consolidation."""
        changes = []

        # Check memory sizes
        from agos.knowledge.base import ThreadQuery
        recent_semantic = await self._loom.semantic.query(
            ThreadQuery(limit=100)
        )
        recent_episodic = await self._loom.episodic.query(
            ThreadQuery(limit=100)
        )
        graph_entities = await self._loom.graph.entities()

        semantic_count = len(recent_semantic)
        episodic_count = len(recent_episodic)
        entity_count = len(graph_entities)

        self._consolidation_history.append({
            "semantic": semantic_count,
            "episodic": episodic_count,
            "entities": entity_count,
        })

        # If episodic is growing fast, enable more aggressive consolidation
        if episodic_count > 80:
            changes.append(
                f"High episodic volume ({episodic_count} recent events) "
                "— recommend shorter consolidation window"
            )

        # If semantic is sparse, lower confidence threshold
        if semantic_count < 10:
            changes.append(
                f"Low semantic density ({semantic_count} threads) "
                "— recommend lower relevance threshold"
            )

        # If graph is rich, enable layered recall
        if entity_count > 50 and not self._loom._use_layered_recall:
            self._loom.enable_layered_recall(True)
            changes.append(
                f"Rich graph ({entity_count} entities) "
                "— enabled layered recall"
            )

        # Run pruning
        pruned = await self._loom.prune_all()
        if pruned["episodic"] > 0 or pruned["semantic"] > 0:
            changes.append(
                f"Pruned {pruned['episodic']} episodic + "
                f"{pruned['semantic']} semantic expired threads"
            )

        return changes or ["Memory health nominal"]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        if self._loom:
            self._loom.enable_layered_recall(
                snapshot_data.get("use_layered_recall", False)
            )

    async def health_check(self) -> bool:
        if not self._loom:
            return False
        # Verify we can still query
        try:
            from agos.knowledge.base import ThreadQuery
            await self._loom.semantic.query(ThreadQuery(text="test", limit=1))
            return True
        except Exception:
            return False
