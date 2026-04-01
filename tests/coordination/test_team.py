"""Tests for the Team coordinator."""


import pytest

from agos.types import AgentDefinition, CoordinationStrategy
from agos.llm.base import LLMResponse
from agos.kernel.runtime import AgentRuntime
from agos.tools.registry import ToolRegistry
from agos.tools.builtins import register_builtin_tools
from agos.coordination.team import Team

from tests.conftest import MockLLMProvider


@pytest.fixture
def tools():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def _agent(name: str) -> AgentDefinition:
    return AgentDefinition(name=name, system_prompt=f"You are {name}.", tools=[])


@pytest.mark.asyncio
async def test_team_solo(tools):
    mock = MockLLMProvider([
        LLMResponse(content="Solo result.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("solo-team", runtime, strategy=CoordinationStrategy.SOLO)
    t.add_member(_agent("analyst"))

    result = await t.run("analyze this")
    assert result == "Solo result."
    assert len(t.agents) == 1

    # Workspace should have the result
    stored = await t.workspace.get_value("result")
    assert stored == "Solo result."


@pytest.mark.asyncio
async def test_team_pipeline(tools):
    mock = MockLLMProvider([
        LLMResponse(content="Researched findings.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="Code based on findings.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("pipe-team", runtime, strategy=CoordinationStrategy.PIPELINE)
    t.add_member(_agent("researcher"))
    t.add_member(_agent("coder"))

    result = await t.run("build a feature")
    assert result == "Code based on findings."
    assert len(t.agents) == 2

    # Workspace should have both outputs
    r1 = await t.workspace.get_value("output-researcher")
    r2 = await t.workspace.get_value("output-coder")
    assert r1 == "Researched findings."
    assert r2 == "Code based on findings."

    # Channel should have messages
    assert len(t.channel.history) >= 2


@pytest.mark.asyncio
async def test_team_parallel(tools):
    mock = MockLLMProvider([
        LLMResponse(content="Research done.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="Code written.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="Review complete.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("par-team", runtime, strategy=CoordinationStrategy.PARALLEL)
    t.add_member(_agent("researcher"))
    t.add_member(_agent("coder"))
    t.add_member(_agent("reviewer"))

    result = await t.run("implement and review auth")

    assert "researcher" in result.lower()
    assert "coder" in result.lower()
    assert "reviewer" in result.lower()
    assert len(t.agents) == 3

    # Workspace has individual outputs + merged
    merged = await t.workspace.get_value("merged-result")
    assert merged is not None


@pytest.mark.asyncio
async def test_team_debate(tools):
    mock = MockLLMProvider([
        LLMResponse(content="I argue FOR.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="I argue AGAINST.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="The FOR side wins.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("debate-team", runtime, strategy=CoordinationStrategy.DEBATE)
    t.add_member(_agent("analyst"))
    t.add_member(_agent("reviewer"))

    result = await t.run("should we use microservices?")
    assert "FOR" in result
    assert len(t.agents) == 3  # 2 debaters + 1 judge

    # Workspace should have arguments and verdict
    verdict = await t.workspace.get_value("verdict")
    assert verdict is not None


@pytest.mark.asyncio
async def test_team_status(tools):
    mock = MockLLMProvider([
        LLMResponse(content="Done.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("status-team", runtime, strategy=CoordinationStrategy.SOLO)
    t.add_member(_agent("analyst"))

    await t.run("test")

    status = t.status()
    assert status["name"] == "status-team"
    assert status["strategy"] == "solo"
    assert status["members"] == ["analyst"]
    assert status["agents_spawned"] == 1


@pytest.mark.asyncio
async def test_team_prompt_includes_context(tools):
    mock = MockLLMProvider([
        LLMResponse(content="Got workspace context.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])
    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    t = Team("ctx-team", runtime, strategy=CoordinationStrategy.SOLO)
    t.add_member(_agent("analyst"))

    # Pre-populate workspace
    await t.workspace.put("prior-work", "Auth module has a SQL injection bug", author="system")

    await t.run("fix the security issue")

    # The LLM should have received workspace context in the prompt
    call = mock.calls[0]
    user_msg = call["messages"][0].content
    assert "prior-work" in user_msg or "SQL injection" in user_msg


@pytest.mark.asyncio
async def test_team_no_members():
    mock = MockLLMProvider()
    runtime = AgentRuntime(llm_provider=mock, tool_registry=ToolRegistry())

    t = Team("empty-team", runtime, strategy=CoordinationStrategy.PARALLEL)
    result = await t.run("do something")
    assert result == "(no team members)"


@pytest.mark.asyncio
async def test_team_members_property(tools):
    t = Team("prop-team", AgentRuntime(llm_provider=MockLLMProvider(), tool_registry=tools))
    t.add_member(_agent("researcher"))
    t.add_member(_agent("coder"))

    assert len(t.members) == 2
    assert t.members[0].name == "researcher"
