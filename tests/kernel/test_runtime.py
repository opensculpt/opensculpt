"""Tests for the AgentRuntime."""

import pytest

from agos.types import AgentDefinition
from agos.exceptions import AgentNotFoundError


@pytest.mark.asyncio
async def test_spawn_and_list(runtime):
    defn = AgentDefinition(name="test", system_prompt="Test agent")
    agent = await runtime.spawn(defn, user_message="Hello")
    await agent.wait()

    agents = runtime.list_agents()
    assert len(agents) == 1
    assert agents[0]["name"] == "test"


@pytest.mark.asyncio
async def test_spawn_multiple(runtime):
    for i in range(3):
        defn = AgentDefinition(name=f"agent-{i}", system_prompt="Test")
        agent = await runtime.spawn(defn, user_message="Go")
        await agent.wait()

    agents = runtime.list_agents()
    assert len(agents) == 3


@pytest.mark.asyncio
async def test_get_nonexistent_raises(runtime):
    with pytest.raises(AgentNotFoundError):
        runtime.get("nonexistent-id")


@pytest.mark.asyncio
async def test_kill_agent(runtime):
    defn = AgentDefinition(name="test", system_prompt="Test")
    agent = await runtime.spawn(defn, user_message="Hello")
    await runtime.kill(agent.id)
    assert agent.state.value in ("terminated", "completed")
