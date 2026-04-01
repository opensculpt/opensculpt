"""Tests for the Agent class."""

import pytest

from agos.types import AgentState, AgentDefinition
from agos.kernel.agent import Agent
from agos.llm.base import LLMResponse


@pytest.mark.asyncio
async def test_agent_lifecycle(mock_llm):
    defn = AgentDefinition(
        name="test", system_prompt="You are a test agent.", token_budget=1000
    )
    agent = Agent(definition=defn, llm=mock_llm)
    await agent.initialize()
    assert agent.state == AgentState.READY

    await agent.start(user_message="Hello")
    result = await agent.wait()

    assert agent.state == AgentState.COMPLETED
    assert result == "Done."
    assert agent.context.turns == 1
    assert agent.context.tokens_used == 15  # 10 input + 5 output


@pytest.mark.asyncio
async def test_agent_multi_turn(mock_llm_with_responses):
    responses = [
        LLMResponse(content="Thinking...", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ]
    llm = mock_llm_with_responses(responses)

    defn = AgentDefinition(
        name="test", system_prompt="Test", token_budget=10000, max_turns=5
    )
    agent = Agent(definition=defn, llm=llm)
    await agent.initialize()
    await agent.start(user_message="Go")
    result = await agent.wait()

    assert result == "Thinking..."
    assert agent.context.turns == 1


@pytest.mark.asyncio
async def test_agent_kill(mock_llm):
    defn = AgentDefinition(
        name="test", system_prompt="Test", token_budget=1000
    )
    agent = Agent(definition=defn, llm=mock_llm)
    await agent.initialize()
    await agent.start(user_message="Hello")
    await agent.kill()

    assert agent.state in (AgentState.TERMINATED, AgentState.COMPLETED)


@pytest.mark.asyncio
async def test_agent_token_budget(mock_llm_with_responses):
    responses = [
        LLMResponse(content="1", stop_reason="not_done", input_tokens=500, output_tokens=500),
        LLMResponse(content="2", stop_reason="not_done", input_tokens=500, output_tokens=500),
    ]
    llm = mock_llm_with_responses(responses)

    defn = AgentDefinition(
        name="test", system_prompt="Test", token_budget=100, max_turns=10
    )
    agent = Agent(definition=defn, llm=llm)
    await agent.initialize()
    await agent.start(user_message="Go")

    # Should error due to budget
    try:
        await agent.wait()
    except Exception:
        pass

    assert agent.state == AgentState.ERROR
