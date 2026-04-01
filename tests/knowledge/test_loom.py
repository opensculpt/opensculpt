"""Tests for The Loom (unified knowledge manager)."""

import pytest
import tempfile

from agos.knowledge.manager import TheLoom


@pytest.fixture
async def loom():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    lm = TheLoom(db_path)
    await lm.initialize()
    return lm


@pytest.mark.asyncio
async def test_remember_and_recall(loom):
    await loom.remember("Python was created by Guido van Rossum", tags=["python", "history"])
    await loom.remember("Rust was created by Graydon Hoare", tags=["rust", "history"])

    results = await loom.recall("Python creator")
    assert len(results) >= 1
    assert any("Python" in t.content for t in results)


@pytest.mark.asyncio
async def test_learner_records_interaction(loom):
    await loom.learner.record_interaction(
        agent_id="agent-1",
        agent_name="researcher",
        user_input="What is quantum computing?",
        agent_output="Quantum computing uses qubits...",
        tokens_used=500,
        tools_used=["http_request"],
    )

    # Should be findable in recall
    results = await loom.recall("quantum computing")
    assert len(results) >= 1

    # Should be in timeline
    events = await loom.timeline()
    assert len(events) >= 1

    # Tool usage should be in the graph
    edges = await loom.graph.connections("agent:researcher", relation="used_tool")
    assert len(edges) == 1
    assert edges[0].target == "tool:http_request"


@pytest.mark.asyncio
async def test_timeline(loom):
    await loom.learner.record_agent_lifecycle(
        agent_id="a1", agent_name="coder", event="spawned"
    )
    await loom.learner.record_agent_lifecycle(
        agent_id="a1", agent_name="coder", event="completed"
    )

    events = await loom.timeline()
    assert len(events) == 2
    assert "spawned" in events[0].content or "completed" in events[0].content
