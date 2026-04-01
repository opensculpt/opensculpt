"""Tests for PlannerStrategy — actionable strategy guidance."""

import pytest

import agos.intent.engine as intent_mod
from agos.evolution.integrator import EvolutionProposal
from agos.evolution.analyzer import PaperInsight
from agos.evolution.strategies.planner_strategy import PlannerStrategy


def _make_proposal():
    insight = PaperInsight(
        paper_id="ps-001",
        paper_title="Strategy Selection Paper",
        technique="Coordination Strategy Tuning",
        description="A test",
        applicability="Very applicable",
        priority="high",
        agos_module="orchestration",
        implementation_hint="Tune strategies",
    )
    return EvolutionProposal(insight=insight, status="accepted")


class _MockRuntime:
    def __init__(self, agents):
        self._agents = agents

    def list_agents(self):
        return self._agents


@pytest.fixture
def original_prompt():
    """Capture and restore INTENT_SYSTEM_PROMPT around each test."""
    original = intent_mod.INTENT_SYSTEM_PROMPT
    yield original
    intent_mod.INTENT_SYSTEM_PROMPT = original


class TestPlannerStrategy:
    @pytest.mark.asyncio
    async def test_apply_no_runtime(self, original_prompt):
        s = PlannerStrategy()
        changes = await s.apply(_make_proposal())
        assert changes == ["No runtime available for strategy analysis"]

    @pytest.mark.asyncio
    async def test_apply_no_agents(self, original_prompt):
        s = PlannerStrategy(runtime=_MockRuntime([]))
        changes = await s.apply(_make_proposal())
        assert changes == ["No agents to analyze"]

    @pytest.mark.asyncio
    async def test_apply_tracks_scores(self, original_prompt):
        agents = [
            {"state": "completed", "tokens_used": 5000, "turns": 3},
            {"state": "completed", "tokens_used": 8000, "turns": 5},
            {"state": "error", "tokens_used": 2000, "turns": 2},
        ]
        s = PlannerStrategy(runtime=_MockRuntime(agents))
        changes = await s.apply(_make_proposal())
        assert any("Solo strategy success rate" in c for c in changes)
        assert any("Avg tokens/agent" in c for c in changes)
        assert len(s._strategy_scores["solo"]) == 1
        assert s._strategy_scores["solo"][0] == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_apply_appends_guidance_when_low_success(self, original_prompt):
        agents = [
            {"state": "error", "tokens_used": 1000, "turns": 1},
        ]
        s = PlannerStrategy(runtime=_MockRuntime(agents))
        # Preload low scores to pass the min-observations threshold
        s._strategy_scores["solo"] = [0.3, 0.4, 0.2, 0.5, 0.3]

        await s.apply(_make_proposal())
        assert "STRATEGY GUIDANCE" in intent_mod.INTENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_apply_no_guidance_when_high_success(self, original_prompt):
        agents = [
            {"state": "completed", "tokens_used": 5000, "turns": 3},
        ]
        s = PlannerStrategy(runtime=_MockRuntime(agents))
        s._strategy_scores["solo"] = [0.9, 0.95, 0.85, 0.9, 0.92]

        await s.apply(_make_proposal())
        assert "STRATEGY GUIDANCE" not in intent_mod.INTENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_no_duplicate_guidance(self, original_prompt):
        agents = [
            {"state": "error", "tokens_used": 1000, "turns": 1},
        ]
        s = PlannerStrategy(runtime=_MockRuntime(agents))
        s._strategy_scores["solo"] = [0.3, 0.4, 0.2, 0.5, 0.3]

        await s.apply(_make_proposal())
        prompt_after_first = intent_mod.INTENT_SYSTEM_PROMPT

        await s.apply(_make_proposal())
        assert intent_mod.INTENT_SYSTEM_PROMPT == prompt_after_first

    @pytest.mark.asyncio
    async def test_snapshot_captures_prompt(self, original_prompt):
        s = PlannerStrategy()
        snap = await s.snapshot()
        assert "system_prompt" in snap
        assert "strategy_scores" in snap
        assert snap["system_prompt"] == original_prompt

    @pytest.mark.asyncio
    async def test_rollback_restores_prompt_and_scores(self, original_prompt):
        agents = [
            {"state": "error", "tokens_used": 1000, "turns": 1},
        ]
        s = PlannerStrategy(runtime=_MockRuntime(agents))
        s._strategy_scores["solo"] = [0.3, 0.4, 0.2, 0.5, 0.3]

        snap = await s.snapshot()
        await s.apply(_make_proposal())
        assert intent_mod.INTENT_SYSTEM_PROMPT != original_prompt

        await s.rollback(snap)
        assert intent_mod.INTENT_SYSTEM_PROMPT == original_prompt
        assert s._strategy_scores["solo"] == [0.3, 0.4, 0.2, 0.5, 0.3]

    @pytest.mark.asyncio
    async def test_health_check(self, original_prompt):
        s = PlannerStrategy()
        assert await s.health_check()
