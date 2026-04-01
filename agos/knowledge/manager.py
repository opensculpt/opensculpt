"""The Loom Manager — unified access to the entire knowledge substrate.

This is the single entry point for all knowledge operations. The
agent runtime, CLI, and future dashboard all interact with knowledge
through this facade.
"""

from __future__ import annotations

from dataclasses import dataclass

from agos.knowledge.episodic import EpisodicWeave
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph
from agos.knowledge.learner import Learner
from agos.knowledge.note import NoteStore
from agos.knowledge.base import BaseWeave, Thread, ThreadQuery


@dataclass
class MemoryLayer:
    """A named memory layer with a priority and backing weave."""

    name: str
    weave: BaseWeave
    priority: int = 0
    enabled: bool = True


class TheLoom:
    """The Loom — agos's knowledge substrate.

    Three weaves, one learner, unified interface:
    - Episodic: what happened (timeline)
    - Semantic: what we understand (searchable knowledge)
    - Graph: how things connect (entity relationships)
    - Learner: auto-extracts knowledge from interactions
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self.episodic = EpisodicWeave(db_path)
        self.semantic = SemanticWeave(db_path)
        self.graph = KnowledgeGraph(db_path)
        self.learner = Learner(self.episodic, self.semantic, self.graph)
        self.notes = NoteStore(self.semantic, self.graph)
        self._layers: list[MemoryLayer] = []
        self._use_layered_recall: bool = False

    async def initialize(self) -> None:
        """Initialize all weaves — creates tables if needed."""
        await self.episodic.initialize()
        await self.semantic.initialize()
        await self.graph.initialize()

        # Run any pending database migrations
        from agos.migrations.runner import apply_migrations
        await apply_migrations(self._db_path)

    def add_layer(
        self, name: str, weave: BaseWeave, priority: int = 0
    ) -> None:
        """Add a memory layer with a priority (higher = checked first)."""
        self._layers.append(MemoryLayer(name=name, weave=weave, priority=priority))
        self._layers.sort(key=lambda ly: ly.priority, reverse=True)

    def enable_layered_recall(self, enabled: bool = True) -> None:
        """Enable or disable layered recall mode."""
        self._use_layered_recall = enabled

    async def recall(self, query: str, limit: int = 10) -> list[Thread]:
        """Universal recall — searches knowledge weaves.

        If layered recall is enabled and layers are configured, checks
        layers in priority order. Otherwise, searches episodic + semantic
        in parallel (the default flat merge).
        """
        if self._use_layered_recall and self._layers:
            return await self._layered_recall(query, limit)

        q = ThreadQuery(text=query, limit=limit)

        # Search both weaves in parallel
        import asyncio
        episodic_results, semantic_results = await asyncio.gather(
            self.episodic.query(q),
            self.semantic.query(q),
        )

        # Also search MemoryNotes (linked knowledge network)
        note_results = []
        try:
            note_results = await self.notes.search(query, limit=limit // 2 or 3)
        except Exception:
            pass

        # Merge and deduplicate by ID
        seen = set()
        merged = []
        for thread in semantic_results + episodic_results + note_results:
            if thread.id not in seen:
                seen.add(thread.id)
                merged.append(thread)

        return merged[:limit]

    async def _layered_recall(self, query: str, limit: int) -> list[Thread]:
        """Check layers in priority order, stop when limit is reached."""
        seen: set[str] = set()
        results: list[Thread] = []

        for layer in self._layers:
            if not layer.enabled:
                continue
            if len(results) >= limit:
                break
            remaining = limit - len(results)
            q_layer = ThreadQuery(text=query, limit=remaining)
            threads = await layer.weave.query(q_layer)
            for t in threads:
                if t.id not in seen:
                    seen.add(t.id)
                    results.append(t)
                    if len(results) >= limit:
                        break

        return results

    async def remember(self, content: str, kind: str = "fact",
                       tags: list[str] | None = None,
                       agent_id: str | None = None) -> str:
        """Explicitly store a piece of knowledge.

        Use this for facts, observations, or decisions that should
        persist in the semantic weave.
        """
        thread = Thread(
            content=content,
            kind=kind,
            tags=tags or [],
            agent_id=agent_id,
            source="explicit",
        )
        return await self.semantic.store(thread)

    async def timeline(self, agent_id: str | None = None,
                       limit: int = 20) -> list[Thread]:
        """Get recent events from the episodic weave."""
        q = ThreadQuery(agent_id=agent_id, limit=limit)
        return await self.episodic.query(q)

    async def prune_all(self) -> dict[str, int]:
        """Prune expired threads from all weaves."""
        ep = await self.episodic.prune()
        sem = await self.semantic.prune()
        return {"episodic": ep, "semantic": sem}
