"""Team — groups agents with a shared channel and workspace.

A team is the unit of multi-agent coordination. It wraps the planner's
strategies with real-time communication and shared state.

Usage:
    team = Team("code-review", runtime, llm)
    team.add_member(researcher_def)
    team.add_member(coder_def)
    result = await team.run("refactor the auth module")
"""

from __future__ import annotations

import asyncio
from typing import Any

from agos.types import (
    AgentDefinition,
    CoordinationStrategy,
    new_id,
)
from agos.kernel.runtime import AgentRuntime
from agos.kernel.agent import Agent
from agos.coordination.channel import Channel, Message
from agos.coordination.workspace import Workspace


class Team:
    """A coordinated group of agents that can communicate and share work."""

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        strategy: CoordinationStrategy = CoordinationStrategy.PARALLEL,
    ):
        self.id = new_id()
        self.name = name
        self._runtime = runtime
        self.strategy = strategy
        self.channel = Channel(name=f"team-{name}")
        self.workspace = Workspace(name=f"team-{name}")
        self._member_defs: list[AgentDefinition] = []
        self._agents: list[Agent] = []

    def add_member(self, definition: AgentDefinition) -> None:
        """Add an agent definition to the team."""
        self._member_defs.append(definition)

    @property
    def members(self) -> list[AgentDefinition]:
        return list(self._member_defs)

    @property
    def agents(self) -> list[Agent]:
        return list(self._agents)

    async def run(self, task: str) -> str:
        """Execute the team task using the configured strategy."""
        if self.strategy == CoordinationStrategy.SOLO:
            return await self._run_solo(task)
        elif self.strategy == CoordinationStrategy.PIPELINE:
            return await self._run_pipeline(task)
        elif self.strategy == CoordinationStrategy.PARALLEL:
            return await self._run_parallel(task)
        elif self.strategy == CoordinationStrategy.DEBATE:
            return await self._run_debate(task)
        return await self._run_parallel(task)

    async def _run_solo(self, task: str) -> str:
        """Single agent handles the task with workspace context."""
        if not self._member_defs:
            return "(no team members)"

        defn = self._member_defs[0]
        prompt = self._build_prompt(task, defn.name)
        agent = await self._runtime.spawn(defn, user_message=prompt)
        self._agents.append(agent)
        self.channel.subscribe(agent.id, self._noop_handler)

        result = await agent.wait()
        output = result or "(no output)"
        await self.workspace.put("result", output, author=agent.id)
        return output

    async def _run_pipeline(self, task: str) -> str:
        """Sequential execution — each agent builds on the previous one's work."""
        current_input = task

        for defn in self._member_defs:
            prompt = self._build_prompt(current_input, defn.name)
            agent = await self._runtime.spawn(defn, user_message=prompt)
            self._agents.append(agent)
            self.channel.subscribe(agent.id, self._noop_handler)

            result = await agent.wait()
            output = result or current_input

            # Store in workspace and announce on channel
            await self.workspace.put(
                f"output-{defn.name}", output, author=agent.id,
            )
            await self.channel.post(
                agent.id,
                f"Completed my part. Key findings stored as 'output-{defn.name}'.",
                kind="result",
            )
            current_input = output

        return current_input

    async def _run_parallel(self, task: str) -> str:
        """All agents work simultaneously with shared workspace."""
        if not self._member_defs:
            return "(no team members)"

        # Spawn all agents
        for defn in self._member_defs:
            prompt = self._build_prompt(task, defn.name)
            agent = await self._runtime.spawn(defn, user_message=prompt)
            self._agents.append(agent)
            self.channel.subscribe(agent.id, self._noop_handler)

        # Wait for all to complete
        results = await asyncio.gather(*[a.wait() for a in self._agents])

        # Store each result in workspace
        for agent, result in zip(self._agents, results):
            output = result or "(no output)"
            await self.workspace.put(
                f"output-{agent.definition.name}",
                output,
                author=agent.id,
            )

        # Merge results
        merged = "\n\n---\n\n".join(
            f"**{a.definition.name}:**\n{r or '(no output)'}"
            for a, r in zip(self._agents, results)
        )
        await self.workspace.put("merged-result", merged, author="system")
        return merged

    async def _run_debate(self, task: str) -> str:
        """Two agents debate, then a judge decides."""
        if len(self._member_defs) < 2:
            return await self._run_solo(task)

        # Spawn debaters
        debaters = []
        positions = ["FOR", "AGAINST"]
        for i, defn in enumerate(self._member_defs[:2]):
            position = positions[i]
            prompt = (
                f"You are arguing {position} the following:\n\n{task}\n\n"
                f"Be specific, cite evidence, and make your strongest case."
            )
            agent = await self._runtime.spawn(defn, user_message=prompt)
            self._agents.append(agent)
            debaters.append(agent)

        # Wait for both arguments
        arguments = await asyncio.gather(*[a.wait() for a in debaters])

        for agent, arg, pos in zip(debaters, arguments, positions):
            await self.workspace.put(
                f"argument-{pos.lower()}", arg or "(no argument)", author=agent.id,
            )
            await self.channel.post(
                agent.id,
                f"Submitted {pos} argument.",
                kind="result",
            )

        # Judge — use the third member or fallback to first
        from agos.intent.personas import ORCHESTRATOR
        judge_def = (
            self._member_defs[2] if len(self._member_defs) > 2 else ORCHESTRATOR
        )

        judge_prompt = (
            f"Two agents debated:\n\n{task}\n\n"
            f"**FOR:**\n{arguments[0]}\n\n"
            f"**AGAINST:**\n{arguments[1]}\n\n"
            "Provide a balanced verdict with your recommendation."
        )
        judge = await self._runtime.spawn(judge_def, user_message=judge_prompt)
        self._agents.append(judge)

        verdict = await judge.wait()
        output = verdict or "(no verdict)"
        await self.workspace.put("verdict", output, author=judge.id)
        return output

    def _build_prompt(self, task: str, agent_name: str) -> str:
        """Build a prompt that includes workspace context."""
        ws_summary = self.workspace.summary()
        team_members = ", ".join(d.name for d in self._member_defs)
        prompt = (
            f"You are {agent_name} on team '{self.name}' "
            f"(members: {team_members}).\n\n"
        )
        if ws_summary != "Workspace is empty.":
            prompt += f"Shared workspace:\n{ws_summary}\n\n"
        prompt += f"Task: {task}"
        return prompt

    def status(self) -> dict[str, Any]:
        """Get team status summary."""
        return {
            "id": self.id,
            "name": self.name,
            "strategy": self.strategy.value,
            "members": [d.name for d in self._member_defs],
            "agents_spawned": len(self._agents),
            "channel_messages": len(self.channel.history),
            "workspace_items": len(self.workspace._artifacts),
        }

    @staticmethod
    async def _noop_handler(msg: Message) -> None:
        """Default message handler — agents don't process messages yet."""
        pass
