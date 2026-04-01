"""Intent Engine — the soul of agos.

Takes natural language, understands what the user wants, and produces
an execution plan. This is what makes agos an OS, not a library.
"""

from __future__ import annotations

from agos.types import (
    CoordinationStrategy,
    ExecutionPlan,
    IntentType,
)
from agos.llm.base import BaseLLMProvider, LLMMessage
from agos.intent.personas import PERSONAS, ORCHESTRATOR

INTENT_SYSTEM_PROMPT = """\
You are the Intent Engine of agos, an Agentic Operating System.

Your job: understand what the user wants and create an execution plan.

Given the user's natural language request, respond with EXACTLY this JSON format:
{
    "intent_type": "<one of: research, code, review, analyze, monitor, automate, answer, create>",
    "description": "<one sentence describing the plan>",
    "agents": ["<agent persona names to use, from: researcher, coder, reviewer, analyst, orchestrator>"],
    "strategy": "<one of: solo, pipeline, parallel, debate>"
}

Guidelines:
- "research": user wants to find information, investigate something
- "code": user wants to write, modify, or fix code
- "review": user wants to review or critique code/documents
- "analyze": user wants to understand, examine, or break down something
- "monitor": user wants to watch/track something over time
- "automate": user wants to set up recurring tasks
- "answer": user has a simple question that needs a direct answer
- "create": user wants to create files, projects, or artifacts

- Use "solo" when one agent can handle it (most common)
- Use "pipeline" when tasks must happen in sequence (e.g., design → code → test)
- Use "parallel" when independent subtasks can run simultaneously
- Use "debate" when the user wants pros/cons or comparison

For solo tasks, prefer "orchestrator" — it's the most versatile.
Only use specialized agents when the task clearly matches their role.

Respond with ONLY the JSON. No markdown, no explanation."""


class IntentEngine:
    """Understands user intent and produces execution plans.

    This is the core differentiator of agos. Instead of requiring
    users to manually configure agents, the Intent Engine figures
    out what to do from natural language.
    """

    def __init__(self, llm: BaseLLMProvider):
        self._llm = llm

    async def understand(self, user_input: str) -> ExecutionPlan:
        """Take natural language and produce an execution plan."""
        try:
            response = await self._llm.complete(
                messages=[LLMMessage(role="user", content=user_input)],
                system=INTENT_SYSTEM_PROMPT,
                max_tokens=500,
            )

            if not response.content:
                return self._fallback_plan(user_input)

            return self._parse_plan(response.content, user_input)

        except Exception:
            # If intent parsing fails, fall back to orchestrator
            return self._fallback_plan(user_input)

    def _parse_plan(self, raw: str, user_input: str) -> ExecutionPlan:
        """Parse the LLM's JSON response into an ExecutionPlan."""
        import orjson

        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = orjson.loads(text)
        except Exception:
            return self._fallback_plan(user_input)

        # Build agent definitions from persona names
        agent_names = data.get("agents", ["orchestrator"])
        agents = []
        for name in agent_names:
            persona = PERSONAS.get(name, ORCHESTRATOR)
            agents.append(persona)

        return ExecutionPlan(
            intent_type=IntentType(data.get("intent_type", "answer")),
            description=data.get("description", user_input),
            agents=agents,
            strategy=CoordinationStrategy(data.get("strategy", "solo")),
        )

    def _fallback_plan(self, user_input: str) -> ExecutionPlan:
        """When in doubt, use the orchestrator."""
        return ExecutionPlan(
            intent_type=IntentType.ANSWER,
            description=user_input,
            agents=[ORCHESTRATOR],
            strategy=CoordinationStrategy.SOLO,
        )
