"""Workspace — shared scratchpad for agent teams.

When agents collaborate, they need a place to share intermediate results,
notes, and artifacts. The workspace is that place — a key-value store
that any team member can read or write.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import AgentId, new_id


class Artifact(BaseModel):
    """A piece of data stored in the workspace."""

    id: str = Field(default_factory=new_id)
    key: str
    value: Any
    author: AgentId
    kind: str = "text"  # text, code, data, file
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Workspace:
    """Shared key-value store for a team of agents.

    Agents can put, get, and list artifacts. All operations are
    async-safe for concurrent access.

    Usage:
        ws = Workspace()
        await ws.put("findings", "The API is rate-limited at 100 req/s", author="researcher")
        result = await ws.get("findings")
    """

    def __init__(self, name: str = ""):
        self.name = name
        self._artifacts: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        key: str,
        value: Any,
        author: AgentId,
        kind: str = "text",
    ) -> Artifact:
        """Store or update an artifact."""
        async with self._lock:
            existing = self._artifacts.get(key)
            if existing:
                existing.value = value
                existing.author = author
                existing.kind = kind
                existing.updated_at = datetime.now()
                return existing

            artifact = Artifact(
                key=key,
                value=value,
                author=author,
                kind=kind,
            )
            self._artifacts[key] = artifact
            return artifact

    async def get(self, key: str) -> Artifact | None:
        """Retrieve an artifact by key."""
        return self._artifacts.get(key)

    async def get_value(self, key: str, default: Any = None) -> Any:
        """Retrieve just the value of an artifact."""
        artifact = self._artifacts.get(key)
        return artifact.value if artifact else default

    async def delete(self, key: str) -> bool:
        """Remove an artifact."""
        async with self._lock:
            return self._artifacts.pop(key, None) is not None

    async def list_artifacts(self) -> list[Artifact]:
        """List all artifacts, sorted by most recent update."""
        return sorted(
            self._artifacts.values(),
            key=lambda a: a.updated_at,
            reverse=True,
        )

    async def keys(self) -> list[str]:
        """List all artifact keys."""
        return list(self._artifacts.keys())

    async def clear(self) -> None:
        """Remove all artifacts."""
        async with self._lock:
            self._artifacts.clear()

    def summary(self) -> str:
        """Quick text summary of workspace contents for agent context."""
        if not self._artifacts:
            return "Workspace is empty."
        lines = []
        for key, art in self._artifacts.items():
            preview = str(art.value)[:80]
            lines.append(f"- {key} ({art.kind}, by {art.author}): {preview}")
        return "Workspace contents:\n" + "\n".join(lines)
