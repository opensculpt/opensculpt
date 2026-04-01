"""Tests for the memory consolidation engine."""

import tempfile
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from agos.knowledge.base import Thread
from agos.knowledge.episodic import EpisodicWeave
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph
from agos.knowledge.consolidator import Consolidator


@pytest_asyncio.fixture
async def stores():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    episodic = EpisodicWeave(db_path)
    semantic = SemanticWeave(db_path)
    graph = KnowledgeGraph(db_path)
    await episodic.initialize()
    await semantic.initialize()
    await graph.initialize()
    return episodic, semantic, graph


@pytest.mark.asyncio
async def test_consolidate_old_events(stores):
    episodic, semantic, graph = stores
    consolidator = Consolidator(episodic, semantic, graph)

    # Add old events (> 24h ago)
    old_time = datetime.now() - timedelta(hours=48)
    for i in range(5):
        await episodic.store(Thread(
            content=f"Old event {i}",
            kind="event",
            created_at=old_time,
        ))

    report = await consolidator.consolidate(older_than_hours=24, min_cluster_size=3)

    assert report.summaries_created >= 1
    assert report.events_pruned >= 3


@pytest.mark.asyncio
async def test_consolidate_skips_recent(stores):
    episodic, semantic, graph = stores
    consolidator = Consolidator(episodic, semantic, graph)

    # Add recent events
    for i in range(5):
        await episodic.store(Thread(
            content=f"Recent event {i}",
            kind="event",
        ))

    report = await consolidator.consolidate(older_than_hours=24)

    assert report.summaries_created == 0
    assert report.events_pruned == 0


@pytest.mark.asyncio
async def test_consolidate_needs_min_cluster(stores):
    episodic, semantic, graph = stores
    consolidator = Consolidator(episodic, semantic, graph)

    # Add only 2 old events (below min_cluster_size of 3)
    old_time = datetime.now() - timedelta(hours=48)
    for i in range(2):
        await episodic.store(Thread(
            content=f"Old event {i}",
            kind="event",
            created_at=old_time,
        ))

    report = await consolidator.consolidate(min_cluster_size=3)
    assert report.summaries_created == 0


@pytest.mark.asyncio
async def test_extract_patterns(stores):
    episodic, semantic, graph = stores
    consolidator = Consolidator(episodic, semantic, graph)

    # Create tool usage patterns in the graph
    for i in range(5):
        await graph.link(f"agent:coder-{i}", "used_tool", "tool:shell_exec")

    patterns = await consolidator.extract_patterns()

    assert len(patterns) >= 1
    assert any("shell_exec" in p.content for p in patterns)


@pytest.mark.asyncio
async def test_consolidation_report(stores):
    episodic, semantic, graph = stores
    consolidator = Consolidator(episodic, semantic, graph)

    report = await consolidator.consolidate()
    assert "ConsolidationReport" in repr(report)
