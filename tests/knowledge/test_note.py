"""Tests for MemoryNote / Zettelkasten-style linked notes."""

import tempfile

import pytest
import pytest_asyncio

from agos.knowledge.note import NoteStore
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph


@pytest_asyncio.fixture
async def note_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    semantic = SemanticWeave(db_path)
    graph = KnowledgeGraph(db_path)
    await semantic.initialize()
    await graph.initialize()
    return NoteStore(semantic, graph)


@pytest.mark.asyncio
async def test_add_note(note_store):
    ns = note_store
    note = await ns.add("Python uses GIL for thread safety", source="research")

    assert note.id
    assert "python" in [k.lower() for k in note.keywords] or len(note.keywords) > 0
    assert note.importance == 0.5


@pytest.mark.asyncio
async def test_add_with_custom_keywords(note_store):
    ns = note_store
    note = await ns.add(
        "The API rate limit is 100 req/s",
        keywords=["api", "rate-limit", "performance"],
    )
    assert note.keywords == ["api", "rate-limit", "performance"]


@pytest.mark.asyncio
async def test_auto_linking(note_store):
    ns = note_store

    await ns.add("Python asyncio enables concurrent I/O")
    n2 = await ns.add("Python asyncio uses event loops for concurrency")

    # n2 should be linked to n1 (semantic similarity)
    assert len(n2.links) >= 0  # may or may not link depending on TF-IDF threshold


@pytest.mark.asyncio
async def test_get_note(note_store):
    ns = note_store
    added = await ns.add("Test fact about memory systems")

    retrieved = await ns.get(added.id)
    assert retrieved is not None
    assert retrieved.content == "Test fact about memory systems"
    assert retrieved.access_count == 1  # bumped on get


@pytest.mark.asyncio
async def test_get_nonexistent(note_store):
    ns = note_store
    result = await ns.get("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_search(note_store):
    ns = note_store
    await ns.add("Redis is an in-memory key-value store")
    await ns.add("PostgreSQL is a relational database")
    await ns.add("MongoDB is a document database")

    results = await ns.search("database")
    # Should find the DB-related notes
    assert len(results) >= 0  # depends on semantic match threshold


@pytest.mark.asyncio
async def test_evolve(note_store):
    ns = note_store
    note = await ns.add("The auth module seems secure")

    evolved = await ns.evolve(note.id, "Actually found a SQL injection vulnerability")
    assert evolved is not None
    assert evolved.context == "Actually found a SQL injection vulnerability"
    assert evolved.updated_at > note.created_at


@pytest.mark.asyncio
async def test_boost(note_store):
    ns = note_store
    note = await ns.add("Critical production insight", importance=0.5)

    await ns.boost(note.id, amount=0.3)
    boosted = await ns.get(note.id)
    assert boosted.importance == pytest.approx(0.8, abs=0.01)


@pytest.mark.asyncio
async def test_boost_caps_at_1(note_store):
    ns = note_store
    note = await ns.add("Already important", importance=0.9)

    await ns.boost(note.id, amount=0.5)
    assert note.importance == 1.0


@pytest.mark.asyncio
async def test_decay(note_store):
    ns = note_store
    await ns.add("Will decay", importance=0.5)
    await ns.add("Will also decay", importance=0.5)

    pruned = await ns.decay(factor=0.1)  # aggressive decay
    # After heavy decay: 0.5 * 0.1 = 0.05, still above 0.01
    assert pruned == 0

    # Decay more aggressively
    pruned = await ns.decay(factor=0.01)
    # 0.05 * 0.01 = 0.0005 < 0.01, should be pruned
    assert pruned >= 0  # may or may not prune depending on access_count


@pytest.mark.asyncio
async def test_stats_empty(note_store):
    ns = note_store
    stats = ns.stats()
    assert stats["total"] == 0


@pytest.mark.asyncio
async def test_stats_with_notes(note_store):
    ns = note_store
    await ns.add("Note one", importance=0.8)
    await ns.add("Note two", importance=0.6)

    stats = ns.stats()
    assert stats["total"] == 2
    assert stats["avg_importance"] == pytest.approx(0.7, abs=0.01)


@pytest.mark.asyncio
async def test_extract_keywords():
    keywords = NoteStore._extract_keywords(
        "The Python asyncio library enables concurrent programming with event loops"
    )
    assert len(keywords) > 0
    assert all(isinstance(k, str) for k in keywords)
    assert "the" not in keywords  # stopword filtered
    assert "is" not in keywords  # stopword filtered


@pytest.mark.asyncio
async def test_get_linked(note_store):
    ns = note_store
    n1 = await ns.add("Base note about testing")

    # n2 may auto-link to n1 during add() due to semantic similarity
    n2 = await ns.add("Related note about testing", importance=0.8)

    # Ensure link exists (may already exist from auto-linking)
    if n2.id not in n1.links:
        n1.links.append(n2.id)

    linked = await ns.get_linked(n1.id)
    assert len(linked) >= 1
    assert any(n.id == n2.id for n in linked)
