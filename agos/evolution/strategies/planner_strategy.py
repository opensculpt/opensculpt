"""PlannerStrategy — evolves coordination strategy selection.

Analyzes which coordination strategies (solo, pipeline, parallel,
debate) produce the best results for different task types, and
adjusts the intent prompt accordingly.
"""

from __future__ import annotations

import logging
from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal

_logger = logging.getLogger(__name__)

_LOW_SUCCESS_THRESHOLD = 0.6
_HIGH_TOKEN_THRESHOLD = 150_000
_MIN_OBSERVATIONS = 5


class PlannerStrategy(IntegrationStrategy):
    """Evolve the Planner's strategy selection heuristics."""

    name = "planner_strategy"
    target_module = "orchestration"

    def __init__(self, runtime=None, event_bus=None) -> None:
        self._runtime = runtime
        self._event_bus = event_bus
        self._strategy_scores: dict[str, list[float]] = {
            "solo": [],
            "pipeline": [],
            "parallel": [],
            "debate": [],
        }

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        import agos.intent.engine as _mod
        return {
            "strategy_scores": {k: list(v) for k, v in self._strategy_scores.items()},
            "system_prompt": _mod.INTENT_SYSTEM_PROMPT,
        }

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Analyze agent outcomes and adjust strategy selection."""
        changes: list[str] = []

        if not self._runtime:
            return ["No runtime available for strategy analysis"]

        agents = self._runtime.list_agents()
        if not agents:
            return ["No agents to analyze"]

        completed = [a for a in agents if a["state"] == "completed"]
        errored = [a for a in agents if a["state"] == "error"]
        total = len(completed) + len(errored)

        if total > 0:
            success_rate = len(completed) / total
            self._strategy_scores["solo"].append(success_rate)
            self._strategy_scores["solo"] = self._strategy_scores["solo"][-20:]

            avg_score = (
                sum(self._strategy_scores["solo"])
                / len(self._strategy_scores["solo"])
            )
            changes.append(
                f"Solo strategy success rate: {avg_score:.1%} "
                f"(over {len(self._strategy_scores['solo'])} observations)"
            )

        avg_tokens = 0.0
        if completed:
            avg_tokens = sum(a["tokens_used"] for a in completed) / len(completed)
            avg_turns = sum(a["turns"] for a in completed) / len(completed)
            changes.append(
                f"Avg tokens/agent: {avg_tokens:,.0f}, avg turns: {avg_turns:.1f}"
            )

        # Append strategy guidance when solo success is low
        solo_scores = self._strategy_scores["solo"]
        if len(solo_scores) >= _MIN_OBSERVATIONS:
            avg = sum(solo_scores) / len(solo_scores)
            if avg < _LOW_SUCCESS_THRESHOLD:
                import agos.intent.engine as _mod
                if "STRATEGY GUIDANCE" not in _mod.INTENT_SYSTEM_PROMPT:
                    guidance = (
                        "\n\nSTRATEGY GUIDANCE (learned from performance data):\n"
                        f"- Solo strategy success rate is {avg:.0%}. "
                        "For complex tasks, prefer 'pipeline' or 'parallel'.\n"
                        "- Use 'solo' only for simple, single-step 'answer' intents."
                    )
                    _mod.INTENT_SYSTEM_PROMPT += guidance
                    changes.append(
                        f"Appended strategy guidance (solo rate {avg:.0%})"
                    )
                    _logger.info("Appended strategy guidance to intent prompt")

        # Append efficiency note when token usage is high
        if completed and avg_tokens > _HIGH_TOKEN_THRESHOLD:
            import agos.intent.engine as _mod
            if "TOKEN EFFICIENCY" not in _mod.INTENT_SYSTEM_PROMPT:
                note = (
                    "\n\nTOKEN EFFICIENCY NOTE:\n"
                    "- Average token usage is high. Prefer 'solo' with "
                    "'orchestrator' for simple tasks to conserve tokens."
                )
                _mod.INTENT_SYSTEM_PROMPT += note
                changes.append("Appended token efficiency guidance")

        return changes or ["Strategy analysis pending more data"]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        if "strategy_scores" in snapshot_data:
            self._strategy_scores = snapshot_data["strategy_scores"]
        if "system_prompt" in snapshot_data:
            import agos.intent.engine as _mod
            _mod.INTENT_SYSTEM_PROMPT = snapshot_data["system_prompt"]

    async def health_check(self) -> bool:
        try:
            import agos.intent.engine as _mod
            return len(_mod.INTENT_SYSTEM_PROMPT) > 50
        except Exception:
            return False
