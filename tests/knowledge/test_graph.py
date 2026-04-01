"""Tests for the Knowledge Graph."""

import pytest
import tempfile

from agos.knowledge.graph import KnowledgeGraph


@pytest.fixture
async def graph():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    g = KnowledgeGraph(db_path)
    await g.initialize()
    return g


@pytest.mark.asyncio
async def test_link_and_connections(graph):
    await graph.link("user:abhis", "works_on", "project:agos")
    await graph.link("user:abhis", "uses", "tool:claude")

    conns = await graph.connections("user:abhis")
    assert len(conns) == 2
    targets = {c.target for c in conns}
    assert "project:agos" in targets
    assert "tool:claude" in targets


@pytest.mark.asyncio
async def test_connections_by_relation(graph):
    await graph.link("agent:coder", "used_tool", "tool:file_write")
    await graph.link("agent:coder", "used_tool", "tool:shell_exec")
    await graph.link("agent:coder", "handled", "task:build-api")

    conns = await graph.connections("agent:coder", relation="used_tool")
    assert len(conns) == 2


@pytest.mark.asyncio
async def test_incoming_connections(graph):
    await graph.link("agent:a", "depends_on", "service:db")
    await graph.link("agent:b", "depends_on", "service:db")

    conns = await graph.connections("service:db", direction="incoming")
    assert len(conns) == 2


@pytest.mark.asyncio
async def test_neighbors(graph):
    await graph.link("A", "knows", "B")
    await graph.link("B", "knows", "C")
    await graph.link("C", "knows", "D")

    # 1 hop from A
    n1 = await graph.neighbors("A", depth=1)
    assert "B" in n1
    assert "C" not in n1

    # 2 hops from A
    n2 = await graph.neighbors("A", depth=2)
    assert "B" in n2
    assert "C" in n2
    assert "D" not in n2


@pytest.mark.asyncio
async def test_entities(graph):
    await graph.link("X", "r", "Y")
    await graph.link("Y", "r", "Z")

    ents = await graph.entities()
    assert ents == {"X", "Y", "Z"}


@pytest.mark.asyncio
async def test_unlink(graph):
    edge = await graph.link("A", "r", "B")
    removed = await graph.unlink(edge.id)
    assert removed

    conns = await graph.connections("A")
    assert len(conns) == 0
