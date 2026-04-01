"""SoftmaxScoringStrategy — enables probabilistic retrieval in SemanticWeave.

Instead of always returning the deterministic top-k results, softmax
scoring adds controlled randomness to retrieval. This increases diversity
in recalled knowledge, preventing the system from always returning the
same threads and encouraging exploration of less-accessed knowledge.

Based on ALMA's softmax scoring approach.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal
from agos.knowledge.semantic import SemanticWeave


class SoftmaxScoringStrategy(IntegrationStrategy):
    """Enable softmax-based probabilistic retrieval in SemanticWeave."""

    name = "softmax_scoring"
    target_module = "knowledge.semantic"

    def __init__(self, semantic: SemanticWeave) -> None:
        self._semantic = semantic

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._semantic:
            return False, "SemanticWeave not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        return {"temperature": self._semantic._temperature}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        self._semantic.set_temperature(0.3)
        return [
            "Enabled softmax scoring with temperature=0.3",
            "Retrieval now uses probabilistic sampling for diversity",
        ]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        temp = snapshot_data.get("temperature", 0.0)
        self._semantic.set_temperature(temp)

    async def health_check(self) -> bool:
        return self._semantic._temperature >= 0.0
