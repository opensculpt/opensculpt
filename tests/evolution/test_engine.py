"""Tests for the EvolutionEngine."""

import tempfile
from datetime import datetime

import pytest
import pytest_asyncio

from agos.llm.base import LLMResponse
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus
from agos.evolution.scout import Paper
from agos.evolution.analyzer import PaperAnalyzer, PaperInsight
from agos.evolution.engine import EvolutionEngine, EvolutionProposal, EvolutionReport
from agos.evolution.repo_scout import RepoSnapshot, RepoFile
from agos.evolution.code_analyzer import CodePattern, CodeAnalysisResult
from agos.evolution.sandbox import Sandbox

from tests.conftest import MockLLMProvider


def _make_paper(arxiv_id="2401.001", title="Test Paper", abstract="A test.") -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=["Author"],
        abstract=abstract,
        categories=["cs.AI"],
        published=datetime.utcnow(),
    )


class FakeScout:
    """Scout that returns predetermined papers without hitting arxiv."""

    def __init__(self, papers: list[Paper] | None = None):
        self._papers = papers or []

    async def search_recent(self, days=7, max_results=20):
        return self._papers


@pytest_asyncio.fixture
async def loom():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    the_loom = TheLoom(db_path)
    await the_loom.initialize()
    return the_loom


@pytest.mark.asyncio
async def test_run_cycle_no_papers(loom):
    scout = FakeScout([])
    analyzer = PaperAnalyzer(MockLLMProvider([]))
    engine = EvolutionEngine(scout, analyzer, loom)

    report = await engine.run_cycle()

    assert report.papers_found == 0
    assert report.proposals_created == 0
    assert report.duration_ms >= 0


@pytest.mark.asyncio
async def test_run_cycle_with_papers(loom):
    papers = [
        _make_paper("id-1", "Paper One", "About agentic memory."),
        _make_paper("id-2", "Paper Two", "About multi-agent systems."),
    ]
    scout = FakeScout(papers)

    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Adaptive Recall", '
                    '"description": "Better retrieval.", '
                    '"applicability": "Improves agos search.", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "Modify SemanticWeave."}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
        LLMResponse(
            content='{"relevant": false}',
            stop_reason="end_turn", input_tokens=100, output_tokens=10,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    report = await engine.run_cycle()

    assert report.papers_found == 2
    assert report.papers_analyzed == 2
    assert report.proposals_created == 1
    assert len(report.proposal_ids) == 1


@pytest.mark.asyncio
async def test_run_cycle_emits_events(loom):
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("evolution.*", handler)

    papers = [_make_paper("id-1", "Paper", "Abstract")]
    scout = FakeScout(papers)
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Test Tech", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "medium", "agos_module": "kernel", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom, event_bus=bus)

    await engine.run_cycle()

    topics = [e.topic for e in received]
    assert "evolution.cycle_started" in topics
    assert "evolution.paper_found" in topics
    assert "evolution.proposal_created" in topics
    assert "evolution.cycle_completed" in topics


@pytest.mark.asyncio
async def test_filter_already_seen_papers(loom):
    papers = [
        _make_paper("seen-1", "Already Seen"),
        _make_paper("new-1", "Brand New"),
    ]

    # Pre-store "seen-1" in the graph
    await loom.graph.link("paper:seen-1", "discovered_by", "agent:evolution_engine")

    scout = FakeScout(papers)
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "New Tech", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "low", "agos_module": "tools", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    report = await engine.run_cycle()

    # Only "new-1" should be analyzed (seen-1 filtered out)
    assert report.papers_found == 2
    assert report.papers_analyzed == 1


@pytest.mark.asyncio
async def test_get_proposals(loom):
    scout = FakeScout([_make_paper()])
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Tech", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    await engine.run_cycle()

    all_proposals = await engine.get_proposals()
    assert len(all_proposals) == 1
    assert all_proposals[0].status == "proposed"

    proposed = await engine.get_proposals(status="proposed")
    assert len(proposed) == 1

    accepted = await engine.get_proposals(status="accepted")
    assert len(accepted) == 0


@pytest.mark.asyncio
async def test_accept_proposal(loom):
    scout = FakeScout([_make_paper()])
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Accept Me", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    await engine.run_cycle()
    proposals = await engine.get_proposals()
    pid = proposals[0].id

    result = await engine.accept_proposal(pid, notes="Looks good")
    assert result.status == "accepted"
    assert result.notes == "Looks good"
    assert result.reviewed_at is not None


@pytest.mark.asyncio
async def test_reject_proposal(loom):
    scout = FakeScout([_make_paper()])
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Reject Me", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "low", "agos_module": "tools", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    await engine.run_cycle()
    proposals = await engine.get_proposals()
    pid = proposals[0].id

    result = await engine.reject_proposal(pid, notes="Not relevant")
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_accept_nonexistent():
    engine = EvolutionEngine.__new__(EvolutionEngine)
    engine._proposals = {}
    engine._event_bus = None
    result = await engine.accept_proposal("nope")
    assert result is None


@pytest.mark.asyncio
async def test_history(loom):
    scout = FakeScout([])
    analyzer = PaperAnalyzer(MockLLMProvider([]))
    engine = EvolutionEngine(scout, analyzer, loom)

    await engine.run_cycle()
    await engine.run_cycle()

    reports = await engine.history()
    assert len(reports) == 2


@pytest.mark.asyncio
async def test_report_repr():
    report = EvolutionReport(papers_found=5, papers_analyzed=3, proposals_created=2)
    assert "papers=5" in repr(report)
    assert "proposals=2" in repr(report)


@pytest.mark.asyncio
async def test_proposal_model():
    insight = PaperInsight(
        paper_id="123",
        paper_title="Paper",
        technique="Test",
        description="Desc",
        applicability="App",
        priority="high",
        agos_module="knowledge",
        implementation_hint="Hint",
    )
    proposal = EvolutionProposal(insight=insight)
    assert proposal.id
    assert proposal.status == "proposed"
    assert proposal.insight.technique == "Test"


@pytest.mark.asyncio
async def test_stores_in_knowledge_system(loom):
    papers = [_make_paper("store-1", "Stored Paper", "This paper is about memory.")]
    scout = FakeScout(papers)
    mock_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Memory Store", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=50, output_tokens=30,
        ),
    ])
    analyzer = PaperAnalyzer(mock_llm)
    engine = EvolutionEngine(scout, analyzer, loom)

    await engine.run_cycle()

    # Paper should be in graph
    conns = await loom.graph.connections("paper:store-1")
    assert len(conns) >= 1

    # Proposal should be findable via semantic search
    results = await loom.recall("Memory Store")
    assert len(results) >= 1


# ── Code-Aware Evolution Tests ───────────────────────────────────


class FakeRepoScout:
    """RepoScout that returns predetermined results without hitting GitHub."""

    def __init__(self, url: str = "", snapshot: RepoSnapshot | None = None):
        self._url = url
        self._snapshot = snapshot

    async def find_repo(self, abstract: str, title: str = "") -> str | None:
        return self._url or None

    async def fetch_repo(self, repo_url: str, max_files: int = 15) -> RepoSnapshot | None:
        return self._snapshot


class FakeCodeAnalyzer:
    """CodeAnalyzer that returns predetermined patterns."""

    def __init__(self, patterns: list[CodePattern] | None = None):
        self._patterns = patterns or []

    async def analyze_repo(self, snapshot: RepoSnapshot) -> CodeAnalysisResult:
        return CodeAnalysisResult(
            repo_url=snapshot.repo_url,
            repo_name=snapshot.repo_name,
            patterns=self._patterns,
            files_analyzed=len(snapshot.files),
            total_code_size=snapshot.total_code_size,
        )


@pytest.mark.asyncio
async def test_run_cycle_with_code_analysis(loom):
    """Full cycle with paper analysis + repo code analysis."""
    papers = [_make_paper("code-1", "Code Paper", "See https://github.com/test/repo")]
    scout = FakeScout(papers)

    # Paper analyzer LLM responses
    paper_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Adaptive Retrieval", '
                    '"description": "Better search.", "applicability": "Improves recall.", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "Modify SemanticWeave."}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(paper_llm)

    # Repo scout returns a snapshot
    snapshot = RepoSnapshot(
        repo_url="https://github.com/test/repo",
        owner="test",
        repo_name="repo",
        files=[RepoFile(path="main.py", content="class Agent: pass", size=18, language="python")],
    )
    repo_scout = FakeRepoScout(url="https://github.com/test/repo", snapshot=snapshot)

    # Code analyzer returns patterns
    patterns = [
        CodePattern(
            name="Adaptive Index",
            description="Dynamic scoring",
            source_file="main.py",
            source_repo="https://github.com/test/repo",
            code_snippet="x = 1 + 2\nprint(x)",
            agos_module="knowledge",
            priority="high",
        ),
    ]
    code_analyzer = FakeCodeAnalyzer(patterns)

    engine = EvolutionEngine(
        scout, analyzer, loom,
        repo_scout=repo_scout,
        code_analyzer=code_analyzer,
    )

    report = await engine.run_cycle()

    assert report.papers_found == 1
    assert report.proposals_created == 1
    assert report.repos_found == 1
    assert report.code_patterns_found == 1

    # Proposal should carry code patterns
    proposals = await engine.get_proposals()
    assert len(proposals) == 1
    assert len(proposals[0].code_patterns) == 1
    assert proposals[0].code_patterns[0].name == "Adaptive Index"
    assert proposals[0].repo_url == "https://github.com/test/repo"


@pytest.mark.asyncio
async def test_run_cycle_with_sandbox(loom):
    """Cycle with sandbox testing of code patterns."""
    papers = [_make_paper("sand-1", "Sandbox Paper", "Abstract")]
    scout = FakeScout(papers)

    paper_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Safe Pattern", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "medium", "agos_module": "kernel", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(paper_llm)

    snapshot = RepoSnapshot(
        repo_url="https://github.com/test/safe",
        owner="test", repo_name="safe",
        files=[RepoFile(path="core.py", content="pass", size=4, language="python")],
    )
    repo_scout = FakeRepoScout(url="https://github.com/test/safe", snapshot=snapshot)

    patterns = [
        CodePattern(
            name="Safe Pattern",
            code_snippet="print('hello from sandbox')",
            agos_module="kernel",
        ),
    ]
    code_analyzer = FakeCodeAnalyzer(patterns)
    sandbox = Sandbox(timeout=5)

    engine = EvolutionEngine(
        scout, analyzer, loom,
        repo_scout=repo_scout,
        code_analyzer=code_analyzer,
        sandbox=sandbox,
    )

    report = await engine.run_cycle()

    assert report.sandbox_tests_run == 1
    assert report.sandbox_tests_passed == 1


@pytest.mark.asyncio
async def test_run_cycle_no_repo_found(loom):
    """When no repo is found, proposals still work (just no code patterns)."""
    papers = [_make_paper("norepo-1", "No Repo Paper", "No URLs here.")]
    scout = FakeScout(papers)

    paper_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Technique X", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "low", "agos_module": "tools", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(paper_llm)
    repo_scout = FakeRepoScout(url="", snapshot=None)
    code_analyzer = FakeCodeAnalyzer([])

    engine = EvolutionEngine(
        scout, analyzer, loom,
        repo_scout=repo_scout,
        code_analyzer=code_analyzer,
    )

    report = await engine.run_cycle()

    assert report.proposals_created == 1
    assert report.repos_found == 0
    assert report.code_patterns_found == 0

    proposals = await engine.get_proposals()
    assert proposals[0].code_patterns == []
    assert proposals[0].repo_url == ""


@pytest.mark.asyncio
async def test_test_proposal_code(loom):
    """Test code patterns from a proposal in the sandbox."""
    papers = [_make_paper("test-code-1", "Testable Paper", "Abstract")]
    scout = FakeScout(papers)

    paper_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Testable Tech", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(paper_llm)

    snapshot = RepoSnapshot(
        repo_url="https://github.com/test/testable",
        owner="test", repo_name="testable",
        files=[RepoFile(path="x.py", content="pass", size=4, language="python")],
    )
    repo_scout = FakeRepoScout(url="https://github.com/test/testable", snapshot=snapshot)

    patterns = [
        CodePattern(name="P1", code_snippet="print(2 + 2)", agos_module="knowledge"),
        CodePattern(name="P2", code_snippet="import math\nprint(math.sqrt(16))", agos_module="tools"),
    ]
    code_analyzer = FakeCodeAnalyzer(patterns)
    sandbox = Sandbox(timeout=5)

    engine = EvolutionEngine(
        scout, analyzer, loom,
        repo_scout=repo_scout,
        code_analyzer=code_analyzer,
        sandbox=sandbox,
    )

    await engine.run_cycle()
    proposals = await engine.get_proposals()
    pid = proposals[0].id

    results = await engine.test_proposal_code(pid)
    assert len(results) == 2
    assert all(r.passed for r in results)


@pytest.mark.asyncio
async def test_test_proposal_code_no_sandbox():
    """test_proposal_code returns empty when no sandbox configured."""
    engine = EvolutionEngine.__new__(EvolutionEngine)
    engine._proposals = {}
    engine._event_bus = None
    engine._sandbox = None

    results = await engine.test_proposal_code("nope")
    assert results == []


@pytest.mark.asyncio
async def test_proposal_with_code_patterns_model():
    """Proposals with code_patterns and sandbox_results work."""
    insight = PaperInsight(
        paper_id="123",
        paper_title="Paper",
        technique="Test",
        description="Desc",
        applicability="App",
        priority="high",
        agos_module="knowledge",
        implementation_hint="Hint",
    )
    patterns = [CodePattern(name="P", code_snippet="pass", agos_module="kernel")]
    proposal = EvolutionProposal(
        insight=insight,
        code_patterns=patterns,
        repo_url="https://github.com/test/repo",
    )
    assert len(proposal.code_patterns) == 1
    assert proposal.repo_url == "https://github.com/test/repo"
    assert proposal.sandbox_results == []


@pytest.mark.asyncio
async def test_report_with_code_stats():
    """EvolutionReport includes code-aware stats."""
    report = EvolutionReport(
        papers_found=5,
        papers_analyzed=3,
        proposals_created=2,
        repos_found=2,
        code_patterns_found=4,
        sandbox_tests_run=4,
        sandbox_tests_passed=3,
    )
    assert "papers=5" in repr(report)
    assert "code_patterns=4" in repr(report)
    assert report.repos_found == 2
    assert report.sandbox_tests_passed == 3


@pytest.mark.asyncio
async def test_code_pattern_stored_in_knowledge(loom):
    """Code patterns should be stored in the knowledge system."""
    papers = [_make_paper("store-code-1", "Stored Code Paper", "Abstract")]
    scout = FakeScout(papers)

    paper_llm = MockLLMProvider([
        LLMResponse(
            content='{"relevant": true, "technique": "Stored Tech", '
                    '"description": "D", "applicability": "A", '
                    '"priority": "high", "agos_module": "knowledge", '
                    '"implementation_hint": "H"}',
            stop_reason="end_turn", input_tokens=100, output_tokens=50,
        ),
    ])
    analyzer = PaperAnalyzer(paper_llm)

    snapshot = RepoSnapshot(
        repo_url="https://github.com/test/storable",
        owner="test", repo_name="storable",
        files=[RepoFile(path="core.py", content="pass", size=4, language="python")],
    )
    repo_scout = FakeRepoScout(url="https://github.com/test/storable", snapshot=snapshot)
    patterns = [
        CodePattern(
            name="Storable Pattern",
            description="A pattern to store",
            code_snippet="print('stored')",
            source_file="core.py",
            source_repo="https://github.com/test/storable",
            agos_module="knowledge",
        ),
    ]
    code_analyzer = FakeCodeAnalyzer(patterns)

    engine = EvolutionEngine(
        scout, analyzer, loom,
        repo_scout=repo_scout,
        code_analyzer=code_analyzer,
    )

    await engine.run_cycle()

    # Code pattern should be findable in knowledge
    results = await loom.recall("Storable Pattern")
    assert len(results) >= 1

    # Graph should link paper to pattern
    conns = await loom.graph.connections("paper:store-code-1")
    rels = [c.relation for c in conns]
    assert "has_code" in rels
