"""The Loom — base interfaces for the knowledge substrate.

The Loom is not a database. It's a living fabric where knowledge is
woven, connected, and decayed. Every interaction leaves threads that
agents can pull on later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import AgentId, new_id


class Thread(BaseModel):
    """A single thread of knowledge in The Loom.

    Threads are the atomic unit of knowledge — a fact, an event,
    an observation, a connection. They can be woven together into
    richer understanding.
    """

    id: str = Field(default_factory=new_id)
    agent_id: AgentId | None = None  # None = system-level knowledge
    content: str
    kind: str = "general"  # "event", "fact", "observation", "decision", "entity"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    ttl_seconds: int | None = None  # None = lives forever
    source: str = ""  # where this knowledge came from
    confidence: float = 1.0  # 0.0 to 1.0
    access_count: int = 0  # how many times this thread has been accessed
    last_accessed: datetime | None = None  # last time this thread was accessed


class ThreadQuery(BaseModel):
    """How to search The Loom."""

    agent_id: AgentId | None = None
    text: str | None = None  # for semantic search
    kind: str | None = None
    tags: list[str] = Field(default_factory=list)
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 20
    min_confidence: float = 0.0


class BaseWeave(ABC):
    """Abstract base for a knowledge weave (a store within The Loom)."""

    @abstractmethod
    async def store(self, thread: Thread) -> str:
        """Store a thread, return its ID."""
        ...

    @abstractmethod
    async def query(self, q: ThreadQuery) -> list[Thread]:
        """Search for threads."""
        ...

    @abstractmethod
    async def delete(self, thread_id: str) -> bool:
        """Remove a thread. Returns True if it existed."""
        ...

    @abstractmethod
    async def prune(self) -> int:
        """Remove expired threads. Returns count pruned."""
        ...
