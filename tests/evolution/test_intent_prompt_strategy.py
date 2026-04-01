"""Tests for IntentPromptStrategy — audit-trail-based prompt evolution."""

import pytest

import agos.intent.engine as intent_mod
from agos.policy.audit import AuditTrail, AuditEntry
from agos.evolution.integrator import EvolutionProposal
from agos.evolution.analyzer import PaperInsight
from agos.evolution.strategies.intent_prompt import IntentPromptStrategy


def _make_proposal():
    insight = PaperInsight(
        paper_id="ip-001",
        paper_title="Intent Classification Paper",
        technique="Intent Classification Improvement",
        description="A test",
        applicability="Very applicable",
        priority="high",
        agos_module="intent",
        implementation_hint="Improve classification",
    )
    return EvolutionProposal(insight=insight, status="accepted")


@pytest.fixture
def original_prompt():
    """Capture and restore INTENT_SYSTEM_PROMPT around each test."""
    original = intent_mod.INTENT_SYSTEM_PROMPT
    yield original
    intent_mod.INTENT_SYSTEM_PROMPT = original


@pytest.fixture
def audit():
    return AuditTrail(":memory:")


class TestIntentPromptStrategy:
    def test_validate_without_audit(self):
        s = IntentPromptStrategy()
        valid, reason = s.validate(_make_proposal())
        assert not valid
        assert "AuditTrail" in reason

    def test_validate_with_audit(self, audit):
        s = IntentPromptStrategy(audit_trail=audit)
        valid, reason = s.validate(_make_proposal())
        assert valid
        assert reason == ""

    @pytest.mark.asyncio
    async def test_snapshot_captures_prompt(self, original_prompt):
        s = IntentPromptStrategy()
        snap = await s.snapshot()
        assert snap["system_prompt"] == original_prompt
        assert len(snap["system_prompt"]) > 100

    @pytest.mark.asyncio
    async def test_apply_no_audit(self, original_prompt):
        s = IntentPromptStrategy()
        changes = await s.apply(_make_proposal())
        assert changes == ["No audit trail available for intent analysis"]

    @pytest.mark.asyncio
    async def test_apply_no_data(self, audit, original_prompt):
        await audit.initialize()
        s = IntentPromptStrategy(audit_trail=audit)
        changes = await s.apply(_make_proposal())
        assert changes == ["No execution data yet for intent analysis"]

    @pytest.mark.asyncio
    async def test_apply_with_failures(self, audit, original_prompt):
        await audit.initialize()
        s = IntentPromptStrategy(audit_trail=audit)

        # Record failures for "code" tasks
        for i in range(4):
            await audit.record(AuditEntry(
                agent_id="os_agent", agent_name="OSAgent",
                action="execute",
                detail=f"code build project {i}",
                success=False,
            ))
        # Record 1 success
        await audit.record(AuditEntry(
            agent_id="os_agent", agent_name="OSAgent",
            action="execute",
            detail="code fix typo",
            success=True,
        ))

        changes = await s.apply(_make_proposal())
        assert any("code" in c for c in changes)
        assert "LEARNED RULES" in intent_mod.INTENT_SYSTEM_PROMPT
        assert "'code'" in intent_mod.INTENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_apply_no_changes_when_healthy(self, audit, original_prompt):
        await audit.initialize()
        s = IntentPromptStrategy(audit_trail=audit)

        # Record all successes
        for i in range(5):
            await audit.record(AuditEntry(
                agent_id="os_agent", agent_name="OSAgent",
                action="execute",
                detail=f"code task {i}",
                success=True,
            ))

        changes = await s.apply(_make_proposal())
        assert changes == ["Intent classification reviewed, no adjustments needed"]
        assert "LEARNED RULES" not in intent_mod.INTENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_rollback_restores_prompt(self, audit, original_prompt):
        await audit.initialize()
        s = IntentPromptStrategy(audit_trail=audit)

        snap = await s.snapshot()

        # Record failures to trigger prompt change
        for i in range(4):
            await audit.record(AuditEntry(
                agent_id="os_agent", agent_name="OSAgent",
                action="execute",
                detail=f"research investigate topic {i}",
                success=False,
            ))

        await s.apply(_make_proposal())
        assert intent_mod.INTENT_SYSTEM_PROMPT != original_prompt

        await s.rollback(snap)
        assert intent_mod.INTENT_SYSTEM_PROMPT == original_prompt

    @pytest.mark.asyncio
    async def test_health_check(self, original_prompt):
        s = IntentPromptStrategy()
        assert await s.health_check()
