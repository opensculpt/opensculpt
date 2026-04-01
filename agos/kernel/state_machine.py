"""Agent state machine — enforces valid lifecycle transitions."""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

from agos.types import AgentId, AgentState
from agos.exceptions import AgentStateError

TransitionCallback = Callable[[AgentId, AgentState, AgentState], Awaitable[None]]

# Valid state transitions — the rules of agent life
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.CREATED: {AgentState.READY, AgentState.TERMINATED},
    AgentState.READY: {AgentState.RUNNING, AgentState.TERMINATED},
    AgentState.RUNNING: {
        AgentState.PAUSED,
        AgentState.COMPLETED,
        AgentState.TERMINATED,
        AgentState.ERROR,
        AgentState.READY,
    },
    AgentState.PAUSED: {AgentState.READY, AgentState.TERMINATED},
    AgentState.COMPLETED: set(),  # terminal
    AgentState.TERMINATED: set(),  # terminal
    AgentState.ERROR: {AgentState.READY, AgentState.TERMINATED},
}


class AgentStateMachine:
    """Manages the lifecycle state of a single agent.

    Enforces that only valid transitions occur and notifies listeners
    on every state change.
    """

    def __init__(self, agent_id: AgentId):
        self.agent_id = agent_id
        self._state = AgentState.CREATED
        self._listeners: list[TransitionCallback] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> AgentState:
        return self._state

    async def transition(self, target: AgentState) -> None:
        async with self._lock:
            valid = VALID_TRANSITIONS.get(self._state, set())
            if target not in valid:
                raise AgentStateError(
                    f"Cannot transition agent {self.agent_id} "
                    f"from {self._state.value} to {target.value}"
                )
            old = self._state
            self._state = target
        # Notify listeners outside the lock
        for listener in self._listeners:
            await listener(self.agent_id, old, target)

    def on_transition(self, callback: TransitionCallback) -> None:
        self._listeners.append(callback)
