"""Tests for working memory."""

import pytest

from agos.knowledge.working import WorkingMemory
from agos.knowledge.base import Thread


@pytest.mark.asyncio
async def test_add_and_retrieve():
    wm = WorkingMemory(capacity=10)
    item = wm.add("The auth module has a bug", source="user")

    assert item.content == "The auth module has a bug"
    assert item.source == "user"
    assert wm.size == 1


@pytest.mark.asyncio
async def test_capacity_eviction():
    wm = WorkingMemory(capacity=3)

    wm.add("Low relevance", source="system", relevance=0.1)
    wm.add("Medium relevance", source="recall", relevance=0.5)
    wm.add("High relevance", source="user", relevance=1.0)
    wm.add("Another high", source="user", relevance=0.9)

    # Should have evicted the lowest relevance item
    assert wm.size == 3
    contents = [i.content for i in wm.items]
    assert "Low relevance" not in contents


@pytest.mark.asyncio
async def test_set_task():
    wm = WorkingMemory()
    wm.set_task("Fix the login bug")

    assert wm.task == "Fix the login bug"
    assert wm.size == 1
    assert "Current task: Fix the login bug" in wm.items[0].content


@pytest.mark.asyncio
async def test_focus():
    wm = WorkingMemory()
    wm.add("auth module is in src/auth.py", source="recall")
    wm.add("database is PostgreSQL", source="recall")
    wm.add("auth uses JWT tokens", source="recall")

    focused = wm.focus("auth")
    assert len(focused) == 2


@pytest.mark.asyncio
async def test_decay():
    wm = WorkingMemory()
    wm.add("test item", source="user", relevance=1.0)

    wm.decay(factor=0.5)
    assert wm.items[0].relevance == pytest.approx(0.5)

    wm.decay(factor=0.5)
    assert wm.items[0].relevance == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_clear():
    wm = WorkingMemory()
    wm.set_task("Some task")
    wm.add("context", source="recall")

    wm.clear()
    assert wm.size == 0
    assert wm.task == ""


@pytest.mark.asyncio
async def test_to_context_string():
    wm = WorkingMemory()
    wm.add("The server is running on port 8080", source="recall", relevance=0.8)
    wm.add("User wants to debug the API", source="user", relevance=1.0)

    ctx = wm.to_context_string()
    assert "Relevant context from memory:" in ctx
    assert "port 8080" in ctx
    assert "debug the API" in ctx


@pytest.mark.asyncio
async def test_to_context_string_empty():
    wm = WorkingMemory()
    assert wm.to_context_string() == ""


@pytest.mark.asyncio
async def test_to_context_string_respects_max():
    wm = WorkingMemory()
    for i in range(20):
        wm.add(f"Item {i}", source="system", relevance=float(i) / 20)

    ctx = wm.to_context_string(max_items=3)
    # Should only include top 3 by relevance
    lines = ctx.strip().split("\n")
    assert len(lines) == 4  # header + 3 items


@pytest.mark.asyncio
async def test_add_from_recall():
    wm = WorkingMemory()

    threads = [
        Thread(content="Fact about Python GIL", kind="fact", confidence=0.9),
        Thread(content="Event: user asked about GIL", kind="event", confidence=0.7),
    ]

    added = wm.add_from_recall(threads)
    assert added == 2
    assert wm.size == 2
    assert wm.items[0].source == "recall"


@pytest.mark.asyncio
async def test_stats():
    wm = WorkingMemory(capacity=10)
    wm.set_task("Fix bugs")
    wm.add("recall data", source="recall", relevance=0.8)

    stats = wm.stats()
    assert stats["size"] == 2
    assert stats["capacity"] == 10
    assert stats["task"] == "Fix bugs"
    assert "user" in stats["sources"]
    assert "recall" in stats["sources"]


@pytest.mark.asyncio
async def test_stats_empty():
    wm = WorkingMemory()
    stats = wm.stats()
    assert stats["size"] == 0
    assert stats["avg_relevance"] == 0
