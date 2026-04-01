"""Agent Runtime — the process table of agos."""

from __future__ import annotations

import asyncio

from agos.types import AgentId, AgentDefinition
from agos.kernel.agent import Agent
from agos.llm.base import BaseLLMProvider
from agos.exceptions import AgentNotFoundError


class AgentRuntime:
    """Central registry and manager for all agents — the kernel.

    This is the equivalent of the Linux process table. Every agent
    that exists in the system is tracked here.
    """

    def __init__(self, llm_provider: BaseLLMProvider, tool_registry: object | None = None):
        self._agents: dict[AgentId, Agent] = {}
        self._llm = llm_provider
        self._tool_registry = tool_registry
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        definition: AgentDefinition,
        user_message: str | None = None,
        agent_id: str | None = None,
    ) -> Agent:
        """Spawn a new agent and start its run loop."""
        agent = Agent(
            definition=definition,
            llm=self._llm,
            tool_executor=self._tool_registry,
            agent_id=agent_id,
        )
        await agent.initialize()
        async with self._lock:
            self._agents[agent.id] = agent
        await agent.start(user_message=user_message)
        return agent

    async def kill(self, agent_id: AgentId) -> None:
        agent = self._get(agent_id)
        await agent.kill()

    async def pause(self, agent_id: AgentId) -> None:
        agent = self._get(agent_id)
        await agent.pause()

    async def resume(self, agent_id: AgentId) -> None:
        agent = self._get(agent_id)
        await agent.resume()

    def get(self, agent_id: AgentId) -> Agent:
        return self._get(agent_id)

    def list_agents(self) -> list[dict]:
        return [
            {
                "id": a.id,
                "name": a.definition.name,
                "role": a.definition.role,
                "state": a.state.value,
                "tokens_used": a.context.tokens_used,
                "turns": a.context.turns,
            }
            for a in self._agents.values()
        ]

    def _get(self, agent_id: AgentId) -> Agent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"No agent with id {agent_id}")
        return agent
