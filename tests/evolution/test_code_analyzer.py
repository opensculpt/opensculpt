"""Tests for the CodeAnalyzer."""

import pytest

from agos.llm.base import LLMResponse
from agos.evolution.repo_scout import RepoSnapshot, RepoFile
from agos.evolution.code_analyzer import (
    CodeAnalyzer, CodePattern, CodeAnalysisResult, CODE_ANALYSIS_PROMPT,
)

from tests.conftest import MockLLMProvider


def _make_snapshot(**kwargs) -> RepoSnapshot:
    defaults = dict(
        repo_url="https://github.com/test/repo",
        owner="test",
        repo_name="repo",
        description="Test repo",
        stars=10,
        language="Python",
        readme="# Test Repo\nA research project.",
        file_tree=["main.py", "agent.py", "README.md"],
        files=[
            RepoFile(
                path="main.py",
                content="class Agent:\n    def think(self):\n        pass\n",
                size=45,
                language="python",
            ),
            RepoFile(
                path="agent.py",
                content="async def run():\n    return 'done'\n",
                size=35,
                language="python",
            ),
        ],
    )
    defaults.update(kwargs)
    return RepoSnapshot(**defaults)


@pytest.mark.asyncio
async def test_analyze_repo_with_patterns():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"patterns": [{"name": "Adaptive Memory Index", '
                    '"description": "Dynamic scoring for memory retrieval.", '
                    '"source_file": "main.py", '
                    '"code_snippet": "class AdaptiveIndex:\\n    pass", '
                    '"agos_module": "knowledge", '
                    '"integration_steps": "Add to SemanticWeave", '
                    '"priority": "high"}]}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=200,
        ),
    ])
    analyzer = CodeAnalyzer(mock)
    snapshot = _make_snapshot()

    result = await analyzer.analyze_repo(snapshot)

    assert isinstance(result, CodeAnalysisResult)
    assert result.repo_url == "https://github.com/test/repo"
    assert result.repo_name == "repo"
    assert result.files_analyzed == 2
    assert len(result.patterns) == 1
    assert result.patterns[0].name == "Adaptive Memory Index"
    assert result.patterns[0].priority == "high"
    assert result.patterns[0].source_repo == "https://github.com/test/repo"


@pytest.mark.asyncio
async def test_analyze_repo_no_patterns():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"patterns": []}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=10,
        ),
    ])
    analyzer = CodeAnalyzer(mock)
    result = await analyzer.analyze_repo(_make_snapshot())

    assert len(result.patterns) == 0
    assert result.files_analyzed == 2


@pytest.mark.asyncio
async def test_analyze_repo_empty_snapshot():
    mock = MockLLMProvider([])
    analyzer = CodeAnalyzer(mock)
    snapshot = RepoSnapshot(
        repo_url="https://github.com/test/empty",
        owner="test",
        repo_name="empty",
        files=[],
    )
    result = await analyzer.analyze_repo(snapshot)

    assert len(result.patterns) == 0
    assert result.files_analyzed == 0
    # LLM should NOT have been called
    assert mock._call_count == 0


@pytest.mark.asyncio
async def test_analyze_repo_multiple_patterns():
    mock = MockLLMProvider([
        LLMResponse(
            content='{"patterns": ['
                    '{"name": "Pattern A", "description": "Desc A", '
                    '"source_file": "a.py", "code_snippet": "code_a()", '
                    '"agos_module": "kernel", "integration_steps": "Step A", '
                    '"priority": "high"}, '
                    '{"name": "Pattern B", "description": "Desc B", '
                    '"source_file": "b.py", "code_snippet": "code_b()", '
                    '"agos_module": "tools", "integration_steps": "Step B", '
                    '"priority": "low"}'
                    ']}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=300,
        ),
    ])
    analyzer = CodeAnalyzer(mock)
    result = await analyzer.analyze_repo(_make_snapshot())

    assert len(result.patterns) == 2
    assert result.patterns[0].name == "Pattern A"
    assert result.patterns[1].name == "Pattern B"


@pytest.mark.asyncio
async def test_analyze_repo_handles_markdown_json():
    mock = MockLLMProvider([
        LLMResponse(
            content='```json\n{"patterns": [{"name": "Test", "description": "D", '
                    '"source_file": "x.py", "code_snippet": "pass", '
                    '"agos_module": "knowledge", "integration_steps": "S", '
                    '"priority": "medium"}]}\n```',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=100,
        ),
    ])
    analyzer = CodeAnalyzer(mock)
    result = await analyzer.analyze_repo(_make_snapshot())
    assert len(result.patterns) == 1
    assert result.patterns[0].name == "Test"


@pytest.mark.asyncio
async def test_analyze_repo_handles_llm_error():
    mock = MockLLMProvider([])  # No responses = will use default "Done."
    analyzer = CodeAnalyzer(mock)
    result = await analyzer.analyze_repo(_make_snapshot())

    # Should return empty result, not crash
    assert len(result.patterns) == 0


@pytest.mark.asyncio
async def test_analyze_repo_filters_empty_patterns():
    """Patterns without name or code_snippet should be filtered out."""
    mock = MockLLMProvider([
        LLMResponse(
            content='{"patterns": ['
                    '{"name": "", "description": "No name", '
                    '"source_file": "x.py", "code_snippet": "code()", '
                    '"agos_module": "kernel", "integration_steps": "S", '
                    '"priority": "low"}, '
                    '{"name": "Valid", "description": "Has name and code", '
                    '"source_file": "y.py", "code_snippet": "valid()", '
                    '"agos_module": "tools", "integration_steps": "S", '
                    '"priority": "high"}'
                    ']}',
            stop_reason="end_turn",
            input_tokens=500,
            output_tokens=200,
        ),
    ])
    analyzer = CodeAnalyzer(mock)
    result = await analyzer.analyze_repo(_make_snapshot())
    assert len(result.patterns) == 1
    assert result.patterns[0].name == "Valid"


def test_code_pattern_model():
    pattern = CodePattern(
        name="Test Pattern",
        description="A test",
        source_file="main.py",
        source_repo="https://github.com/test/repo",
        code_snippet="class Foo: pass",
        agos_module="knowledge",
        integration_steps="Add to knowledge module",
        priority="high",
    )
    assert pattern.id  # auto-generated
    assert pattern.name == "Test Pattern"
    assert pattern.priority == "high"


def test_code_analysis_result_model():
    result = CodeAnalysisResult(
        repo_url="https://github.com/test/repo",
        repo_name="repo",
        patterns=[CodePattern(name="P", code_snippet="x")],
        files_analyzed=3,
        total_code_size=1000,
    )
    assert result.id
    assert len(result.patterns) == 1
    assert result.files_analyzed == 3


def test_build_code_context():
    analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
    snapshot = _make_snapshot()
    context = analyzer._build_code_context(snapshot)

    assert "test/repo" in context
    assert "Test repo" in context
    assert "README (excerpt)" in context
    assert "main.py" in context
    assert "class Agent" in context
    assert "agos Architecture" in context


def test_parse_response_valid():
    analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
    data = analyzer._parse_response('{"patterns": []}')
    assert data is not None
    assert data["patterns"] == []


def test_parse_response_embedded():
    analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
    data = analyzer._parse_response('Some text {"patterns": [{"name": "x"}]} after')
    assert data is not None
    assert len(data["patterns"]) == 1


def test_parse_response_invalid():
    analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
    data = analyzer._parse_response("not json at all")
    assert data is None


def test_code_analysis_prompt_exists():
    assert "patterns" in CODE_ANALYSIS_PROMPT
    assert "agos" in CODE_ANALYSIS_PROMPT
    assert "code_snippet" in CODE_ANALYSIS_PROMPT
