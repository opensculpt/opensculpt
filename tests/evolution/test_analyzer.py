"""Tests for the PaperAnalyzer."""

import pytest

from agos.llm.base import LLMResponse
from agos.evolution.scout import Paper
from agos.evolution.analyzer import PaperAnalyzer, PaperInsight, AGOS_ARCHITECTURE

from tests.conftest import MockLLMProvider


def _make_paper(**kwargs) -> Paper:
    defaults = dict(
        arxiv_id="2401.12345",
        title="Agentic Memory Systems for Self-Improving AI",
        authors=["Alice Smith"],
        abstract="We propose a novel approach to agentic memory.",
        categories=["cs.AI"],
    )
    defaults.update(kwargs)
    return Paper(**defaults)


@pytest.mark.asyncio
async def test_analyze_relevant_paper():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Adaptive Memory Indexing", '
                    '"description": "Dynamically adjusts memory retrieval strategies.", '
                    '"applicability": "Could improve agos semantic search.", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "Add a scoring layer to SemanticWeave."}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=100,
        ),
    ])
    analyzer = PaperAnalyzer(mock)
    paper = _make_paper()

    insight = await analyzer.analyze(paper)

    assert insight is not None
    assert insight.technique == "Adaptive Memory Indexing"
    assert insight.priority == "high"
    assert insight.agos_module == "knowledge"
    assert insight.paper_id == "2401.12345"


@pytest.mark.asyncio
async def test_analyze_irrelevant_paper():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"relevant": false}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=10,
        ),
    ])
    analyzer = PaperAnalyzer(mock)
    paper = _make_paper(title="Quantum Computing in Biology")

    insight = await analyzer.analyze(paper)
    assert insight is None


@pytest.mark.asyncio
async def test_analyze_handles_markdown_json():
    mock = MockLLMProvider([
        LLMResponse(
            content='```json\n{"relevant": true, "technique": "Graph RAG", '
                    '"description": "Uses knowledge graphs for retrieval.", '
                    '"applicability": "Enhances agos graph module.", '
                    '"priority": "medium", "agos_module": "knowledge", '
                    '"implementation_hint": "Add RAG layer."}\n```',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=100,
        ),
    ])
    analyzer = PaperAnalyzer(mock)
    insight = await analyzer.analyze(_make_paper())

    assert insight is not None
    assert insight.technique == "Graph RAG"


@pytest.mark.asyncio
async def test_analyze_handles_llm_error():
    mock = MockLLMProvider([])  # No responses = will raise

    analyzer = PaperAnalyzer(mock)
    insight = await analyzer.analyze(_make_paper())

    assert insight is None  # Graceful failure


@pytest.mark.asyncio
async def test_analyze_batch():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Tech A", "description": "Desc A", '
                    '"applicability": "App A", "priority": "high", "agos_module": "kernel", '
                    '"implementation_hint": "Hint A"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
        LLMResponse(
            content='{"relevant": false}',
            stop_reason="end_turn", input_tokens=100, output_tokens=10,
        ),
        LLMResponse(
            content='{"relevant": true, "technique": "Tech C", "description": "Desc C", '
                    '"applicability": "App C", "priority": "low", "agos_module": "tools", '
                    '"implementation_hint": "Hint C"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(mock)
    papers = [_make_paper(arxiv_id=f"id-{i}") for i in range(3)]

    insights = await analyzer.analyze_batch(papers)
    assert len(insights) == 2
    assert insights[0].technique == "Tech A"
    assert insights[1].technique == "Tech C"


def test_paper_insight_model():
    insight = PaperInsight(
        paper_id="2401.12345",
        paper_title="Test Paper",
        technique="Memory Consolidation",
        description="Compresses old memories.",
        applicability="Applies to agos consolidator.",
        priority="high",
        agos_module="knowledge",
        implementation_hint="Extend Consolidator class.",
    )
    assert insight.id
    assert insight.technique == "Memory Consolidation"
    assert insight.priority == "high"


def test_agos_architecture_description():
    assert "Intent Engine" in AGOS_ARCHITECTURE
    assert "Knowledge System" in AGOS_ARCHITECTURE
    assert "Multi-Agent Coordination" in AGOS_ARCHITECTURE


def test_parse_response_valid_json():
    analyzer = PaperAnalyzer.__new__(PaperAnalyzer)
    data = analyzer._parse_response('{"relevant": true, "technique": "test"}')
    assert data["relevant"] is True


def test_parse_response_embedded_json():
    analyzer = PaperAnalyzer.__new__(PaperAnalyzer)
    data = analyzer._parse_response('Some text before {"relevant": true} some after')
    assert data["relevant"] is True


def test_parse_response_invalid():
    analyzer = PaperAnalyzer.__new__(PaperAnalyzer)
    data = analyzer._parse_response("not json at all")
    assert data is None
