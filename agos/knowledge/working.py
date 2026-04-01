"""Working Memory — active context for the current task.

The missing piece from the survey taxonomy. Working memory is what
the agent is "thinking about right now" — the active context that
gets assembled from long-term memory, the current task, and recent
interactions.

Unlike episodic (permanent log) or semantic (searchable facts),
working memory is ephemeral and task-scoped. It's rebuilt for each
interaction but informed by everything the OS knows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.knowledge.base import Thread


class WorkingMemoryItem(BaseModel):
    """A single item in working memory."""

    id: str = Field(default_factory=new_id)
    content: str
    source: str  # "user", "recall", "agent", "system"
    relevance: float = 1.0  # decays over the session
    added_at: datetime = Field(default_factory=datetime.now)


class WorkingMemory:
    """Task-scoped active context.

    Assembles relevant context from long-term memory for the
    current task. Has a capacity limit — least relevant items
    get evicted when full (like real working memory).

    Usage:
        wm = WorkingMemory(capacity=20)
        wm.add("The user wants to fix auth bugs", source="user")
        wm.add("Auth module is in src/auth.py", source="recall")
        context = wm.to_context_string()
    """

    def __init__(self, capacity: int = 20):
        self._items: list[WorkingMemoryItem] = []
        self.capacity = capacity
        self.task: str = ""
        self.created_at: datetime = datetime.now()

    def add(
        self,
        content: str,
        source: str = "system",
        relevance: float = 1.0,
    ) -> WorkingMemoryItem:
        """Add an item to working memory. Evicts lowest relevance if full."""
        item = WorkingMemoryItem(
            content=content,
            source=source,
            relevance=relevance,
        )
        self._items.append(item)

        # Evict if over capacity
        if len(self._items) > self.capacity:
            self._items.sort(key=lambda x: x.relevance, reverse=True)
            self._items = self._items[:self.capacity]

        return item

    def add_from_recall(self, threads: list[Thread], max_items: int = 5) -> int:
        """Load relevant long-term memories into working memory."""
        added = 0
        for thread in threads[:max_items]:
            self.add(
                content=thread.content,
                source="recall",
                relevance=thread.confidence * 0.8,  # slightly lower than fresh input
            )
            added += 1
        return added

    def set_task(self, task: str) -> None:
        """Set the current task — highest priority context."""
        self.task = task
        self.add(f"Current task: {task}", source="user", relevance=1.0)

    def focus(self, keyword: str) -> list[WorkingMemoryItem]:
        """Get items related to a keyword (simple text match)."""
        keyword_lower = keyword.lower()
        return [
            item for item in self._items
            if keyword_lower in item.content.lower()
        ]

    def decay(self, factor: float = 0.9) -> None:
        """Decay relevance of all items (attention fading)."""
        for item in self._items:
            item.relevance *= factor

    def clear(self) -> None:
        """Clear working memory for a new task."""
        self._items.clear()
        self.task = ""

    def to_context_string(self, max_items: int = 10) -> str:
        """Build a context string for the LLM from working memory.

        This is what gets injected into the agent's system prompt
        to give it awareness of what the OS knows.
        """
        if not self._items:
            return ""

        # Sort by relevance, take top items
        sorted_items = sorted(
            self._items,
            key=lambda x: x.relevance,
            reverse=True,
        )[:max_items]

        lines = ["Relevant context from memory:"]
        for item in sorted_items:
            lines.append(f"- [{item.source}] {item.content}")

        return "\n".join(lines)

    @property
    def items(self) -> list[WorkingMemoryItem]:
        return list(self._items)

    @property
    def size(self) -> int:
        return len(self._items)

    def stats(self) -> dict[str, Any]:
        """Working memory statistics."""
        if not self._items:
            return {"size": 0, "capacity": self.capacity, "avg_relevance": 0}
        avg_rel = sum(i.relevance for i in self._items) / len(self._items)
        sources = {}
        for item in self._items:
            sources[item.source] = sources.get(item.source, 0) + 1
        return {
            "size": len(self._items),
            "capacity": self.capacity,
            "avg_relevance": round(avg_rel, 3),
            "sources": sources,
            "task": self.task,
        }
