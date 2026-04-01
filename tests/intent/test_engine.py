"""Tests for the Intent Engine."""

import pytest

from agos.types import IntentType, CoordinationStrategy
from agos.intent.engine import IntentEngine
from agos.llm.base import LLMResponse

from tests.conftest import MockLLMProvider


@pytest.mark.asyncio
async def test_understand_research_intent():
    llm = MockLLMProvider(responses=[
        LLMResponse(
            content='{"intent_type": "research", "description": "Research AI trends", "agents": ["researcher"], "strategy": "solo"}',
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=30,
        )
    ])
    engine = IntentEngine(llm)
    plan = await engine.understand("research the latest AI trends")

    assert plan.intent_type == IntentType.RESEARCH
    assert plan.strategy == CoordinationStrategy.SOLO
    assert len(plan.agents) == 1
    assert plan.agents[0].name == "researcher"


@pytest.mark.asyncio
async def test_understand_code_intent():
    llm = MockLLMProvider(responses=[
        LLMResponse(
            content='{"intent_type": "code", "description": "Write a REST API", "agents": ["coder"], "strategy": "solo"}',
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=30,
        )
    ])
    engine = IntentEngine(llm)
    plan = await engine.understand("write a REST API for user management")

    assert plan.intent_type == IntentType.CODE
    assert plan.agents[0].name == "coder"


@pytest.mark.asyncio
async def test_understand_pipeline():
    llm = MockLLMProvider(responses=[
        LLMResponse(
            content='{"intent_type": "create", "description": "Build API with tests", "agents": ["coder", "reviewer"], "strategy": "pipeline"}',
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=30,
        )
    ])
    engine = IntentEngine(llm)
    plan = await engine.understand("build an API and review it")

    assert plan.strategy == CoordinationStrategy.PIPELINE
    assert len(plan.agents) == 2


@pytest.mark.asyncio
async def test_fallback_on_bad_response():
    llm = MockLLMProvider(responses=[
        LLMResponse(
            content="I don't understand JSON",
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=30,
        )
    ])
    engine = IntentEngine(llm)
    plan = await engine.understand("do something")

    # Should fall back to orchestrator
    assert plan.intent_type == IntentType.ANSWER
    assert plan.agents[0].name == "orchestrator"


@pytest.mark.asyncio
async def test_fallback_on_exception():
    class FailingLLM(MockLLMProvider):
        async def complete(self, *args, **kwargs):
            raise RuntimeError("API down")

    engine = IntentEngine(FailingLLM())
    plan = await engine.understand("anything")

    assert plan.agents[0].name == "orchestrator"
