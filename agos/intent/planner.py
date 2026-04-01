"""Planner — executes an ExecutionPlan by spawning agents and coordinating them."""

from __future__ import annotations

import asyncio

from agos.types import CoordinationStrategy, ExecutionPlan
from agos.kernel.runtime import AgentRuntime
from agos.kernel.agent import Agent


class Planner:
    """Takes an ExecutionPlan and executes it.

    For Sprint 1, supports:
    - SOLO: single agent handles everything
    - PIPELINE: agents run in sequence, each gets the previous output
    - PARALLEL: agents run simultaneously, results merged
    """

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime

    async def execute(self, plan: ExecutionPlan, user_message: str) -> str:
        """Execute a plan and return the final output."""
        strategy = plan.strategy

        if strategy == CoordinationStrategy.SOLO:
            return await self._execute_solo(plan, user_message)
        elif strategy == CoordinationStrategy.PIPELINE:
            return await self._execute_pipeline(plan, user_message)
        elif strategy == CoordinationStrategy.PARALLEL:
            return await self._execute_parallel(plan, user_message)
        elif strategy == CoordinationStrategy.DEBATE:
            return await self._execute_debate(plan, user_message)
        else:
            return await self._execute_solo(plan, user_message)

    async def _execute_solo(self, plan: ExecutionPlan, user_message: str) -> str:
        """Single agent handles the entire task."""
        definition = plan.agents[0]
        agent = await self._runtime.spawn(definition, user_message=user_message)
        result = await agent.wait()
        return result or "(no output)"

    async def _execute_pipeline(self, plan: ExecutionPlan, user_message: str) -> str:
        """Agents run in sequence. Each gets the previous agent's output."""
        current_input = user_message
        for definition in plan.agents:
            agent = await self._runtime.spawn(definition, user_message=current_input)
            result = await agent.wait()
            current_input = result or current_input
        return current_input

    async def _execute_parallel(self, plan: ExecutionPlan, user_message: str) -> str:
        """All agents run simultaneously. Results are merged."""
        agents: list[Agent] = []
        for definition in plan.agents:
            agent = await self._runtime.spawn(definition, user_message=user_message)
            agents.append(agent)

        results = await asyncio.gather(*[a.wait() for a in agents])
        merged = "\n\n---\n\n".join(
            f"**{a.definition.name}:**\n{r or '(no output)'}"
            for a, r in zip(agents, results)
        )
        return merged

    async def _execute_debate(self, plan: ExecutionPlan, user_message: str) -> str:
        """Two agents debate, a judge (orchestrator) decides."""
        if len(plan.agents) < 2:
            return await self._execute_solo(plan, user_message)

        # Run first two agents in parallel with opposing prompts
        agents: list[Agent] = []
        for i, definition in enumerate(plan.agents[:2]):
            prompt = (
                f"Argue {'FOR' if i == 0 else 'AGAINST'} the following:\n\n{user_message}\n\n"
                "Be specific, cite evidence, and make your strongest case."
            )
            agent = await self._runtime.spawn(definition, user_message=prompt)
            agents.append(agent)

        results = await asyncio.gather(*[a.wait() for a in agents])

        # Judge summarizes
        from agos.intent.personas import ORCHESTRATOR

        judge_prompt = (
            f"Two agents debated the following question:\n\n{user_message}\n\n"
            f"**Argument FOR:**\n{results[0]}\n\n"
            f"**Argument AGAINST:**\n{results[1]}\n\n"
            "Provide a balanced verdict. Who made the stronger case and why? "
            "Give your recommendation."
        )
        judge = await self._runtime.spawn(ORCHESTRATOR, user_message=judge_prompt)
        verdict = await judge.wait()
        return verdict or "(no verdict)"
