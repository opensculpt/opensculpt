"""IntentPromptStrategy — evolves the IntentEngine's classification prompt.

Monitors task completion rates per intent type and adjusts the system
prompt to improve classification accuracy over time.
"""

from __future__ import annotations

import logging
from typing import Any

from agos.evolution.integrator import IntegrationStrategy, EvolutionProposal

_logger = logging.getLogger(__name__)

_INTENT_KEYWORDS = [
    "research", "code", "review", "analyze",
    "monitor", "automate", "answer", "create",
]


class IntentPromptStrategy(IntegrationStrategy):
    """Evolve the IntentEngine's system prompt based on audit data."""

    name = "intent_prompt"
    target_module = "intent"

    def __init__(self, intent_engine=None, audit_trail=None) -> None:
        self._engine = intent_engine
        self._audit = audit_trail

    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        if not self._audit:
            return False, "AuditTrail not available"
        return True, ""

    async def snapshot(self) -> dict[str, Any]:
        import agos.intent.engine as _mod
        return {"system_prompt": _mod.INTENT_SYSTEM_PROMPT}

    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        if not self._audit:
            return ["No audit trail available for intent analysis"]

        entries = await self._audit.query(action="execute", limit=200)
        if not entries:
            return ["No execution data yet for intent analysis"]

        failed = [e for e in entries if not e.success]
        succeeded = [e for e in entries if e.success]

        new_rules: list[str] = []
        changes: list[str] = []

        for kw in _INTENT_KEYWORDS:
            fail_count = sum(1 for e in failed if kw in e.detail.lower())
            ok_count = sum(1 for e in succeeded if kw in e.detail.lower())
            total = fail_count + ok_count
            if total >= 3 and fail_count > ok_count:
                rate = ok_count / total
                new_rules.append(
                    f"- '{kw}' tasks have a {rate:.0%} success rate. "
                    f"Prefer 'pipeline' strategy to break them into steps."
                )
                changes.append(
                    f"Added rule for '{kw}' ({fail_count} failures / {total} total)"
                )

        if new_rules:
            import agos.intent.engine as _mod
            addendum = (
                "\n\nLEARNED RULES (from observed outcomes):\n"
                + "\n".join(new_rules)
            )
            _mod.INTENT_SYSTEM_PROMPT += addendum
            _logger.info("Appended %d intent rules to prompt", len(new_rules))

        return changes or ["Intent classification reviewed, no adjustments needed"]

    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        import agos.intent.engine as _mod
        _mod.INTENT_SYSTEM_PROMPT = snapshot_data["system_prompt"]

    async def health_check(self) -> bool:
        try:
            import agos.intent.engine as _mod
            return len(_mod.INTENT_SYSTEM_PROMPT) > 100
        except Exception:
            return False
