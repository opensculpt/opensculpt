"""PersonaTuningStrategy — evolves agent persona definitions.

Adjusts token budgets, max turns, and tool sets based on actual agent
performance metrics from the audit trail and runtime.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal


class PersonaTuningStrategy(IntegrationStrategy):
    """Evolve agent persona parameters based on usage patterns."""

    name = "persona_tuning"
    target_module = "intent.personas"

    def __init__(self, runtime=None, audit_trail=None) -> None:
        self._runtime = runtime
        self._audit = audit_trail
        self._snapshot_data: dict[str, Any] = {}

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        from agos.intent.personas import PERSONAS
        snap = {}
        for name, persona in PERSONAS.items():
            snap[name] = {
                "token_budget": persona.token_budget,
                "max_turns": persona.max_turns,
                "tools": list(persona.tools),
            }
        return snap

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Tune persona budgets based on actual usage."""
        changes = []

        if not self._runtime:
            return ["No runtime available for persona analysis"]

        agents = self._runtime.list_agents()
        completed = [a for a in agents if a["state"] == "completed"]

        if not completed:
            return ["No completed agents to analyze"]

        # Analyze token usage by role
        role_stats: dict[str, list[int]] = {}
        for a in completed:
            role = a.get("role", "unknown")
            if role not in role_stats:
                role_stats[role] = []
            role_stats[role].append(a.get("tokens_used", 0))

        # Adjust budgets: set to 150% of actual max usage
        from agos.intent.personas import PERSONAS
        for role, token_list in role_stats.items():
            if role in PERSONAS and token_list:
                max_used = max(token_list)
                avg_used = sum(token_list) // len(token_list)
                persona = PERSONAS[role]

                # If agents consistently use <50% of budget, reduce it
                if max_used < persona.token_budget * 0.5 and max_used > 0:
                    new_budget = min(int(max_used * 1.5), persona.token_budget)
                    new_budget = max(new_budget, 50_000)  # floor
                    persona.token_budget = new_budget
                    changes.append(
                        f"Reduced {role} budget to {new_budget:,} "
                        f"(avg used: {avg_used:,})"
                    )

        return changes or ["Persona budgets already well-calibrated"]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        from agos.intent.personas import PERSONAS
        for name, data in snapshot_data.items():
            if name in PERSONAS:
                PERSONAS[name].token_budget = data["token_budget"]
                PERSONAS[name].max_turns = data["max_turns"]

    async def health_check(self) -> bool:
        from agos.intent.personas import PERSONAS
        return len(PERSONAS) > 0
