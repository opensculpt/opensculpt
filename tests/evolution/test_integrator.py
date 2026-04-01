"""Tests for the EvolutionIntegrator."""

import pytest

from agos.evolution.integrator import (
    EvolutionIntegrator,
    IntegrationStrategy,
    IntegrationVersion,
    IntegrationResult,
)
from agos.evolution.engine import EvolutionProposal
from agos.evolution.analyzer import PaperInsight
from agos.exceptions import IntegrationRollbackError


# ── Fake strategy for testing ────────────────────────────────────

class FakeStrategy(IntegrationStrategy):
    """A test strategy that records calls."""

    name = "fake_strategy"
    target_module = "fake.module"

    def __init__(self, *, fail_apply=False, fail_health=False, fail_rollback=False):
        self._fail_apply = fail_apply
        self._fail_health = fail_health
        self._fail_rollback = fail_rollback
        self.applied = False
        self.rolled_back = False

    def validate(self, proposal):
        return True, ""

    async def snapshot(self):
        return {"state": "original"}

    async def apply(self, proposal):
        if self._fail_apply:
            raise RuntimeError("apply failed")
        self.applied = True
        return ["Changed X", "Changed Y"]

    async def rollback(self, snapshot_data):
        if self._fail_rollback:
            raise RuntimeError("rollback failed")
        self.rolled_back = True

    async def health_check(self):
        return not self._fail_health


class FailValidateStrategy(IntegrationStrategy):
    name = "fail_validate"
    target_module = "fake.module"

    def validate(self, proposal):
        return False, "not valid"

    async def snapshot(self):
        return {}

    async def apply(self, proposal):
        return []

    async def rollback(self, snapshot_data):
        pass

    async def health_check(self):
        return True


# ── Helpers ──────────────────────────────────────────────────────

def _make_proposal(status="accepted", module="fake.module"):
    insight = PaperInsight(
        paper_id="test-paper",
        paper_title="Test Paper",
        technique="Test Technique",
        description="A test",
        applicability="Very applicable",
        priority="high",
        agos_module=module,
        implementation_hint="Just do it",
    )
    return EvolutionProposal(insight=insight, status=status)


# ── Tests ────────────────────────────────────────────────────────

def test_integration_version_model():
    v = IntegrationVersion(
        proposal_id="p1",
        strategy_name="softmax",
        target_module="knowledge.semantic",
        changes=["enabled softmax"],
    )
    assert v.id
    assert v.status == "applied"
    assert v.proposal_id == "p1"
    assert len(v.changes) == 1


def test_integration_result_model():
    r = IntegrationResult(success=True, version_id="v1", changes=["a", "b"])
    assert r.success
    assert r.version_id == "v1"
    assert len(r.changes) == 2

    r2 = IntegrationResult(error="something broke")
    assert not r2.success
    assert r2.error == "something broke"


@pytest.mark.asyncio
async def test_register_strategy():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    integrator.register_strategy(strategy)
    assert len(integrator.get_strategies()) == 1
    assert integrator.get_strategies()[0].name == "fake_strategy"


@pytest.mark.asyncio
async def test_apply_success():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    integrator.register_strategy(strategy)

    proposal = _make_proposal()
    result = await integrator.apply(proposal)

    assert result.success
    assert result.version_id
    assert len(result.changes) == 2
    assert strategy.applied
    assert proposal.status == "integrated"


@pytest.mark.asyncio
async def test_apply_wrong_status():
    integrator = EvolutionIntegrator()
    integrator.register_strategy(FakeStrategy())

    proposal = _make_proposal(status="proposed")
    result = await integrator.apply(proposal)

    assert not result.success
    assert "must be 'accepted'" in result.error


@pytest.mark.asyncio
async def test_apply_no_strategy():
    integrator = EvolutionIntegrator()

    proposal = _make_proposal(module="nonexistent.module")
    result = await integrator.apply(proposal)

    assert not result.success
    assert "No strategy found" in result.error


@pytest.mark.asyncio
async def test_apply_validation_fails():
    integrator = EvolutionIntegrator()
    integrator.register_strategy(FailValidateStrategy())

    proposal = _make_proposal()
    result = await integrator.apply(proposal)

    assert not result.success
    assert "Validation failed" in result.error


@pytest.mark.asyncio
async def test_apply_exception():
    integrator = EvolutionIntegrator()
    integrator.register_strategy(FakeStrategy(fail_apply=True))

    proposal = _make_proposal()
    result = await integrator.apply(proposal)

    assert not result.success
    assert "Apply failed" in result.error


@pytest.mark.asyncio
async def test_apply_health_check_fails():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy(fail_health=True)
    integrator.register_strategy(strategy)

    proposal = _make_proposal()
    result = await integrator.apply(proposal)

    assert not result.success
    assert "Health check failed" in result.error
    assert strategy.rolled_back


@pytest.mark.asyncio
async def test_rollback_success():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    integrator.register_strategy(strategy)

    proposal = _make_proposal()
    result = await integrator.apply(proposal)
    assert result.success

    rolled = await integrator.rollback(result.version_id)
    assert rolled
    assert strategy.rolled_back


@pytest.mark.asyncio
async def test_rollback_not_found():
    integrator = EvolutionIntegrator()
    assert not await integrator.rollback("nonexistent")


@pytest.mark.asyncio
async def test_rollback_already_rolled_back():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    integrator.register_strategy(strategy)

    proposal = _make_proposal()
    result = await integrator.apply(proposal)
    await integrator.rollback(result.version_id)

    # Second rollback should return False
    assert not await integrator.rollback(result.version_id)


@pytest.mark.asyncio
async def test_rollback_raises_on_failure():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy(fail_rollback=True)
    integrator.register_strategy(strategy)

    proposal = _make_proposal()
    result = await integrator.apply(proposal)

    with pytest.raises(IntegrationRollbackError):
        await integrator.rollback(result.version_id)


@pytest.mark.asyncio
async def test_list_integrations():
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    integrator.register_strategy(strategy)

    p1 = _make_proposal()
    p2 = _make_proposal()
    await integrator.apply(p1)
    await integrator.apply(p2)

    versions = await integrator.list_integrations()
    assert len(versions) == 2

    # Filter by status
    applied = await integrator.list_integrations(status="applied")
    assert len(applied) == 2


@pytest.mark.asyncio
async def test_list_integrations_after_rollback():
    integrator = EvolutionIntegrator()
    integrator.register_strategy(FakeStrategy())

    p = _make_proposal()
    result = await integrator.apply(p)
    await integrator.rollback(result.version_id)

    applied = await integrator.list_integrations(status="applied")
    assert len(applied) == 0

    rolled = await integrator.list_integrations(status="rolled_back")
    assert len(rolled) == 1


@pytest.mark.asyncio
async def test_find_strategy_partial_match():
    """Strategy target_module 'knowledge.semantic' should match proposal module 'knowledge'."""
    integrator = EvolutionIntegrator()
    strategy = FakeStrategy()
    strategy.target_module = "knowledge.semantic"
    integrator.register_strategy(strategy)

    proposal = _make_proposal(module="knowledge")
    result = await integrator.apply(proposal)
    assert result.success
