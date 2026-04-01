"""Tests for the Semantic Weave."""

import pytest
import tempfile

from agos.knowledge.semantic import SemanticWeave, _tokenize, _compute_tf
from agos.knowledge.base import Thread, ThreadQuery


@pytest.fixture
async def semantic():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    weave = SemanticWeave(db_path)
    await weave.initialize()
    return weave


def test_tokenize():
    tokens = _tokenize("Hello, World! Python 3.11 is great.")
    assert "hello" in tokens
    assert "python" in tokens
    assert "3" in tokens
    assert "11" in tokens


def test_compute_tf():
    tf = _compute_tf(["hello", "world", "hello"])
    assert tf["hello"] == pytest.approx(2 / 3)
    assert tf["world"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_store_and_semantic_query(semantic):
    await semantic.store(Thread(
        content="Python is an excellent language for machine learning and AI development",
        kind="fact",
    ))
    await semantic.store(Thread(
        content="Rust provides memory safety without garbage collection",
        kind="fact",
    ))
    await semantic.store(Thread(
        content="Machine learning models need large datasets for training",
        kind="fact",
    ))

    # Query for "machine learning" should return the two ML-related threads
    results = await semantic.query(ThreadQuery(text="machine learning"))
    assert len(results) >= 1
    # The most relevant should mention machine learning
    assert "machine learning" in results[0].content.lower() or "machine" in results[0].content.lower()


@pytest.mark.asyncio
async def test_semantic_relevance_ranking(semantic):
    await semantic.store(Thread(content="cats are cute furry animals"))
    await semantic.store(Thread(content="dogs are loyal companions"))
    await semantic.store(Thread(content="cats and dogs can be friends"))

    results = await semantic.query(ThreadQuery(text="cats"))
    assert len(results) >= 1
    # First result should be most relevant to "cats"
    assert "cats" in results[0].content.lower()


@pytest.mark.asyncio
async def test_filtered_query_no_text(semantic):
    await semantic.store(Thread(content="fact 1", kind="fact"))
    await semantic.store(Thread(content="observation 1", kind="observation"))

    results = await semantic.query(ThreadQuery(kind="fact"))
    assert len(results) == 1
    assert results[0].kind == "fact"
