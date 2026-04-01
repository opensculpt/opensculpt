"""The Learner — auto-extracts knowledge after every interaction.

After every agent run, the Learner processes what happened and
weaves new threads into The Loom:
- Events → Episodic Weave (what happened)
- Facts → Semantic Weave (what we learned)
- Relationships → Knowledge Graph (how things connect)

This is what makes the OS get smarter over time. It's not just
logging — it's comprehension.
"""

from __future__ import annotations

from datetime import datetime

from agos.types import AgentId
from agos.knowledge.base import Thread
from agos.knowledge.episodic import EpisodicWeave
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph


class Learner:
    """Extracts knowledge from agent interactions and weaves it into The Loom."""

    def __init__(
        self,
        episodic: EpisodicWeave,
        semantic: SemanticWeave,
        graph: KnowledgeGraph,
    ):
        self._episodic = episodic
        self._semantic = semantic
        self._graph = graph

    async def record_interaction(
        self,
        agent_id: AgentId,
        agent_name: str,
        user_input: str,
        agent_output: str,
        tokens_used: int,
        tools_used: list[str] | None = None,
    ) -> None:
        """Record a complete interaction — user asked, agent responded."""
        now = datetime.now()

        # 1. Episodic: log the event
        await self._episodic.store(Thread(
            agent_id=agent_id,
            content=f"User asked: {user_input[:500]}",
            kind="event",
            tags=["interaction", "user_input"],
            metadata={"agent_name": agent_name},
            created_at=now,
            source="learner",
        ))

        await self._episodic.store(Thread(
            agent_id=agent_id,
            content=f"Agent {agent_name} responded: {agent_output[:500]}",
            kind="event",
            tags=["interaction", "agent_output"],
            metadata={
                "agent_name": agent_name,
                "tokens_used": tokens_used,
                "tools_used": tools_used or [],
            },
            created_at=now,
            source="learner",
        ))

        # 2. Semantic: store the exchange as searchable knowledge
        combined = f"Q: {user_input}\nA: {agent_output[:1000]}"
        await self._semantic.store(Thread(
            agent_id=agent_id,
            content=combined,
            kind="interaction",
            tags=["qa", agent_name],
            metadata={"tokens_used": tokens_used},
            created_at=now,
            source="learner",
        ))

        # 3. Graph: link agent to this interaction
        await self._graph.link(
            source=f"agent:{agent_name}",
            relation="handled",
            target=f"interaction:{now.isoformat()[:19]}",
            metadata={"tokens": tokens_used},
        )

        # Link tools used
        for tool in (tools_used or []):
            await self._graph.link(
                source=f"agent:{agent_name}",
                relation="used_tool",
                target=f"tool:{tool}",
            )

    async def record_tool_call(
        self,
        agent_id: AgentId,
        agent_name: str,
        tool_name: str,
        arguments: dict,
        result: str,
        success: bool,
    ) -> None:
        """Record a single tool call."""
        await self._episodic.store(Thread(
            agent_id=agent_id,
            content=f"Tool call: {tool_name}({arguments}) → {'success' if success else 'failed'}: {result[:300]}",
            kind="tool_call",
            tags=["tool", tool_name],
            metadata={
                "agent_name": agent_name,
                "tool_name": tool_name,
                "arguments": arguments,
                "success": success,
            },
            source="learner",
        ))

    async def record_agent_lifecycle(
        self,
        agent_id: AgentId,
        agent_name: str,
        event: str,  # "spawned", "completed", "killed", "error"
        details: str = "",
    ) -> None:
        """Record agent lifecycle events."""
        await self._episodic.store(Thread(
            agent_id=agent_id,
            content=f"Agent {agent_name} {event}. {details}",
            kind="lifecycle",
            tags=["lifecycle", event],
            metadata={"agent_name": agent_name, "event": event},
            source="learner",
        ))
