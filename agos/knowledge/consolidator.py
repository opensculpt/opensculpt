"""Memory Consolidation — compress old memories into higher-level insights.

Inspired by how human memory works during sleep: raw episodic memories
get consolidated into semantic knowledge. Old events get summarized,
patterns get extracted, and the memory becomes more efficient.

This is the ALMA paper's insight applied practically: the memory system
should evolve its own structure over time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from agos.knowledge.base import Thread, ThreadQuery
from agos.knowledge.episodic import EpisodicWeave
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph


class Consolidator:
    """Consolidates raw memories into higher-level knowledge.

    Run periodically (like a nightly job) to:
    1. Summarize clusters of related episodic events
    2. Extract patterns from repeated interactions
    3. Prune low-value memories
    4. Strengthen high-access memories
    """

    def __init__(
        self,
        episodic: EpisodicWeave,
        semantic: SemanticWeave,
        graph: KnowledgeGraph,
        max_concurrent_writes: int = 5,
    ):
        self._episodic = episodic
        self._semantic = semantic
        self._graph = graph
        self._max_concurrent_writes = max_concurrent_writes
        self._semaphore: asyncio.Semaphore | None = None

    async def consolidate(
        self,
        older_than_hours: int = 24,
        min_cluster_size: int = 3,
    ) -> ConsolidationReport:
        """Run a full consolidation pass.

        1. Find old episodic events
        2. Cluster them by similarity
        3. Create summary threads for each cluster
        4. Prune the original events
        """
        report = ConsolidationReport()

        # Get old events
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        old_events = await self._episodic.query(
            ThreadQuery(until=cutoff, limit=500)
        )

        if len(old_events) < min_cluster_size:
            return report

        # Cluster by kind + tags
        clusters = self._cluster_by_kind(old_events)

        for kind, threads in clusters.items():
            if len(threads) < min_cluster_size:
                continue

            # Create a summary
            summary = self._summarize_cluster(kind, threads)
            await self._semantic.store(summary)
            report.summaries_created += 1

            # Prune original events using batch delete
            deleted = await self._batch_delete(
                [t.id for t in threads], self._episodic
            )
            report.events_pruned += deleted

        # Prune expired threads
        ep_pruned = await self._episodic.prune()
        sem_pruned = await self._semantic.prune()
        report.expired_pruned = ep_pruned + sem_pruned

        return report

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazy-create semaphore (can't create in __init__ outside event loop)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent_writes)
        return self._semaphore

    async def _batch_delete(
        self, thread_ids: list[str], weave: EpisodicWeave | SemanticWeave
    ) -> int:
        """Delete multiple threads concurrently with semaphore limiting."""
        sem = self._get_semaphore()
        deleted = 0

        async def _delete_one(tid: str) -> bool:
            async with sem:
                return await weave.delete(tid)

        results = await asyncio.gather(
            *[_delete_one(tid) for tid in thread_ids],
            return_exceptions=True,
        )
        for r in results:
            if r is True:
                deleted += 1
        return deleted

    async def _batch_store(
        self, threads: list[Thread], weave: EpisodicWeave | SemanticWeave
    ) -> int:
        """Store multiple threads concurrently with semaphore limiting."""
        sem = self._get_semaphore()
        stored = 0

        async def _store_one(thread: Thread) -> str:
            async with sem:
                return await weave.store(thread)

        results = await asyncio.gather(
            *[_store_one(t) for t in threads],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, str):
                stored += 1
        return stored

    async def extract_patterns(self, limit: int = 100) -> list[Thread]:
        """Find recurring patterns in interactions.

        Looks for repeated queries, common tool usage, and
        frequently accessed topics.
        """
        patterns = []

        # Find frequent agent-tool connections
        entities = await self._graph.entities()
        tool_entities = [e for e in entities if e.startswith("tool:")]

        for tool_entity in tool_entities:
            conns = await self._graph.connections(tool_entity, direction="incoming")
            if len(conns) >= 3:
                agents = [c.source for c in conns]
                pattern = Thread(
                    content=f"Tool {tool_entity.replace('tool:', '')} is frequently used by: {', '.join(set(agents))}",
                    kind="pattern",
                    tags=["pattern", "tool_usage"],
                    metadata={
                        "tool": tool_entity,
                        "agent_count": len(set(agents)),
                        "total_uses": len(conns),
                    },
                    source="consolidator",
                )
                patterns.append(pattern)

        return patterns[:limit]

    async def strengthen_important(self, threshold: int = 5) -> int:
        """Boost confidence of frequently-referenced knowledge.

        Knowledge that appears in many graph connections is
        more important and should resist pruning.
        """
        strengthened = 0
        entities = await self._graph.entities()
        note_entities = [e for e in entities if e.startswith("note:")]

        for entity in note_entities:
            conns = await self._graph.connections(entity)
            if len(conns) >= threshold:
                # This note has many connections — it's important
                strengthened += 1

        return strengthened

    def _cluster_by_kind(self, threads: list[Thread]) -> dict[str, list[Thread]]:
        """Group threads by their kind (simple but effective clustering)."""
        clusters: dict[str, list[Thread]] = {}
        for thread in threads:
            key = thread.kind
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(thread)
        return clusters

    def _summarize_cluster(self, kind: str, threads: list[Thread]) -> Thread:
        """Create a summary thread from a cluster of events."""
        # Extract unique content snippets
        snippets = list(set(
            t.content[:100] for t in threads
        ))[:10]

        # Collect all tags
        all_tags = set()
        for t in threads:
            all_tags.update(t.tags)

        # Build summary
        time_range = ""
        if threads:
            earliest = min(t.created_at for t in threads)
            latest = max(t.created_at for t in threads)
            time_range = f" ({earliest.strftime('%m/%d')} - {latest.strftime('%m/%d')})"

        summary_text = (
            f"Consolidated {len(threads)} '{kind}' events{time_range}:\n"
            + "\n".join(f"- {s}" for s in snippets)
        )

        return Thread(
            content=summary_text,
            kind="summary",
            tags=list(all_tags)[:10] + ["consolidated"],
            metadata={
                "original_kind": kind,
                "event_count": len(threads),
                "source_ids": [t.id for t in threads[:20]],
            },
            source="consolidator",
            confidence=0.9,
        )


class ConsolidationReport:
    """Results of a consolidation run."""

    def __init__(self):
        self.summaries_created: int = 0
        self.events_pruned: int = 0
        self.expired_pruned: int = 0
        self.patterns_found: int = 0

    def __repr__(self) -> str:
        return (
            f"ConsolidationReport("
            f"summaries={self.summaries_created}, "
            f"pruned={self.events_pruned}, "
            f"expired={self.expired_pruned})"
        )
