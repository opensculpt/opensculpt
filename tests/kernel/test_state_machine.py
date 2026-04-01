"""Tests for the agent state machine."""

import pytest

from agos.types import AgentState
from agos.kernel.state_machine import AgentStateMachine
from agos.exceptions import AgentStateError


@pytest.mark.asyncio
async def test_initial_state():
    sm = AgentStateMachine("test-agent")
    assert sm.state == AgentState.CREATED


@pytest.mark.asyncio
async def test_valid_transition_created_to_ready():
    sm = AgentStateMachine("test-agent")
    await sm.transition(AgentState.READY)
    assert sm.state == AgentState.READY


@pytest.mark.asyncio
async def test_valid_lifecycle():
    sm = AgentStateMachine("test-agent")
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)
    await sm.transition(AgentState.COMPLETED)
    assert sm.state == AgentState.COMPLETED


@pytest.mark.asyncio
async def test_pause_resume_cycle():
    sm = AgentStateMachine("test-agent")
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)
    await sm.transition(AgentState.PAUSED)
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)
    assert sm.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_invalid_transition_raises():
    sm = AgentStateMachine("test-agent")
    with pytest.raises(AgentStateError):
        await sm.transition(AgentState.RUNNING)  # Can't go CREATED -> RUNNING


@pytest.mark.asyncio
async def test_terminal_state_blocks_transitions():
    sm = AgentStateMachine("test-agent")
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)
    await sm.transition(AgentState.COMPLETED)
    with pytest.raises(AgentStateError):
        await sm.transition(AgentState.RUNNING)


@pytest.mark.asyncio
async def test_error_recovery():
    sm = AgentStateMachine("test-agent")
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)
    await sm.transition(AgentState.ERROR)
    await sm.transition(AgentState.READY)  # Recover from error
    assert sm.state == AgentState.READY


@pytest.mark.asyncio
async def test_transition_listener():
    sm = AgentStateMachine("test-agent")
    transitions = []

    async def listener(agent_id, old, new):
        transitions.append((agent_id, old.value, new.value))

    sm.on_transition(listener)
    await sm.transition(AgentState.READY)
    await sm.transition(AgentState.RUNNING)

    assert len(transitions) == 2
    assert transitions[0] == ("test-agent", "created", "ready")
    assert transitions[1] == ("test-agent", "ready", "running")
