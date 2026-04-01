"""LayeredRetrievalStrategy — priority-ordered memory layers for TheLoom.

Instead of querying all weaves in parallel and merging, this enables
a layered approach: check high-priority layers first (e.g., working
memory), then episodic, then semantic. Stops once the requested limit
is reached, reducing unnecessary queries.

Based on ALMA's layered memory architecture.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal
from agos.knowledge.manager import TheLoom


class LayeredRetrievalStrategy(IntegrationStrategy):
    """Enable priority-ordered layered retrieval in TheLoom."""

    name = "layered_retrieval"
    target_module = "knowledge.manager"

    def __init__(self, loom: TheLoom) -> None:
        self._loom = loom

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._loom:
            return False, "TheLoom not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        return {
            "use_layered_recall": self._loom._use_layered_recall,
            "layers": [
                {"name": ly.name, "priority": ly.priority, "enabled": ly.enabled}
                for ly in self._loom._layers
            ],
        }

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        changes = []

        # Add default layers if none exist
        if not self._loom._layers:
            self._loom.add_layer("semantic", self._loom.semantic, priority=0)
            self._loom.add_layer("episodic", self._loom.episodic, priority=10)
            changes.append("Added semantic layer (priority=0)")
            changes.append("Added episodic layer (priority=10)")

        self._loom.enable_layered_recall(True)
        changes.append("Enabled layered recall mode")

        return changes

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        self._loom._use_layered_recall = snapshot_data.get("use_layered_recall", False)
        # Restore layers state
        layer_data = snapshot_data.get("layers", [])
        if not layer_data:
            self._loom._layers.clear()
        else:
            for ld in layer_data:
                for layer in self._loom._layers:
                    if layer.name == ld["name"]:
                        layer.priority = ld["priority"]
                        layer.enabled = ld["enabled"]

    async def health_check(self) -> bool:
        # Verify recall still works
        try:
            await self._loom.recall("test health check", limit=1)
            return True
        except Exception:
            return False
