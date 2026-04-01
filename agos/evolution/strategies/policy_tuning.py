"""PolicyTuningStrategy — evolves PolicyEngine defaults.

Analyzes violation patterns from the audit trail and adjusts rate
limits, token budgets, and tool permissions to reduce violations
while maximizing agent capability.
"""

from __future__ import annotations

from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal


class PolicyTuningStrategy(IntegrationStrategy):
    """Evolve policy parameters based on violation patterns."""

    name = "policy_tuning"
    target_module = "policy"

    def __init__(self, policy_engine=None, audit_trail=None) -> None:
        self._engine = policy_engine
        self._audit = audit_trail

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._engine:
            return False, "PolicyEngine not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        default = self._engine._default
        return {
            "max_tokens": default.max_tokens,
            "max_turns": default.max_turns,
            "max_tool_calls_per_minute": default.max_tool_calls_per_minute,
            "read_only": default.read_only,
            "allow_network": default.allow_network,
            "allow_shell": default.allow_shell,
        }

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Tune policy based on violation analysis."""
        changes = []

        if not self._audit:
            return ["No audit trail for policy analysis"]

        violations = await self._audit.violations(limit=100)

        if not violations:
            return ["No violations — policy is well-calibrated"]

        # Analyze violation types
        rate_violations = sum(
            1 for v in violations if "rate limit" in v.policy_violation.lower()
        )
        budget_violations = sum(
            1 for v in violations if "budget" in v.policy_violation.lower()
        )
        default = self._engine._default

        # If many rate limit violations, increase limit by 20%
        if rate_violations > 5:
            old_limit = default.max_tool_calls_per_minute
            new_limit = min(int(old_limit * 1.2), 200)
            default.max_tool_calls_per_minute = new_limit
            changes.append(
                f"Increased rate limit: {old_limit} -> {new_limit}/min "
                f"({rate_violations} violations)"
            )

        # If many budget violations, increase budget by 20%
        if budget_violations > 3:
            old_budget = default.max_tokens
            new_budget = min(int(old_budget * 1.2), 1_000_000)
            default.max_tokens = new_budget
            changes.append(
                f"Increased token budget: {old_budget:,} -> {new_budget:,} "
                f"({budget_violations} violations)"
            )

        return changes or ["Policy reviewed, no adjustments needed"]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        default = self._engine._default
        default.max_tokens = snapshot_data["max_tokens"]
        default.max_turns = snapshot_data["max_turns"]
        default.max_tool_calls_per_minute = snapshot_data["max_tool_calls_per_minute"]
        default.read_only = snapshot_data["read_only"]

    async def health_check(self) -> bool:
        return self._engine is not None and self._engine._default.max_tokens > 0
