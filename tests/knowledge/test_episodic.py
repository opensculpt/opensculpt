"""Tests for the Episodic Weave."""

import pytest
import tempfile

from agos.knowledge.episodic import EpisodicWeave
from agos.knowledge.base import Thread, ThreadQuery


@pytest.fixture
async def episodic():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    weave = EpisodicWeave(db_path)
    await weave.initialize()
    return weave


@pytest.mark.asyncio
async def test_store_and_query(episodic):
    thread = Thread(
        agent_id="agent-1",
        content="User asked about Python performance",
        kind="event",
        tags=["interaction", "python"],
    )
    tid = await episodic.store(thread)
    assert tid == thread.id

    results = await episodic.query(ThreadQuery(agent_id="agent-1"))
    assert len(results) == 1
    assert results[0].content == "User asked about Python performance"


@pytest.mark.asyncio
async def test_query_by_kind(episodic):
    await episodic.store(Thread(content="event 1", kind="event"))
    await episodic.store(Thread(content="tool call", kind="tool_call"))
    await episodic.store(Thread(content="event 2", kind="event"))

    results = await episodic.query(ThreadQuery(kind="event"))
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_by_text(episodic):
    await episodic.store(Thread(content="Python is great for AI"))
    await episodic.store(Thread(content="Rust is fast"))
    await episodic.store(Thread(content="Python has many libraries"))

    results = await episodic.query(ThreadQuery(text="Python"))
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_by_tags(episodic):
    await episodic.store(Thread(content="tagged", tags=["important", "ai"]))
    await episodic.store(Thread(content="not tagged", tags=["other"]))

    results = await episodic.query(ThreadQuery(tags=["important"]))
    assert len(results) == 1
    assert results[0].content == "tagged"


@pytest.mark.asyncio
async def test_delete(episodic):
    thread = Thread(content="to be deleted")
    tid = await episodic.store(thread)

    deleted = await episodic.delete(tid)
    assert deleted

    results = await episodic.query(ThreadQuery(limit=100))
    assert len(results) == 0


@pytest.mark.asyncio
async def test_query_limit(episodic):
    for i in range(10):
        await episodic.store(Thread(content=f"event {i}"))

    results = await episodic.query(ThreadQuery(limit=3))
    assert len(results) == 3
