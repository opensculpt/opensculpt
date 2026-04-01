"""AdaptiveConfidenceStrategy — access-based confidence evolution.

Instead of static confidence scores set once at creation, this enables
adaptive confidence: confidence increases when a thread is accessed
(reinforcement) and decays over time when unused (forgetting curve).

Based on ALMA's adaptive confidence pattern.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal
from agos.knowledge.semantic import SemanticWeave


class AdaptiveConfidenceStrategy(IntegrationStrategy):
    """Enable access-based confidence tracking in SemanticWeave."""

    name = "adaptive_confidence"
    target_module = "knowledge.semantic"

    def __init__(self, semantic: SemanticWeave) -> None:
        self._semantic = semantic

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._semantic:
            return False, "SemanticWeave not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        return {"track_access": self._semantic._track_access}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        self._semantic.enable_access_tracking(True)
        return [
            "Enabled access tracking on SemanticWeave",
            "Thread confidence now adapts based on usage patterns",
            "Decay can be triggered via decay_confidence() method",
        ]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        tracking = snapshot_data.get("track_access", False)
        self._semantic.enable_access_tracking(tracking)

    async def health_check(self) -> bool:
        return True
