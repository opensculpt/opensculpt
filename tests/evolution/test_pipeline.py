"""Tests for the evolution pipeline."""


from agos.evolution.engine import EvolutionProposal, EvolutionReport
from agos.evolution.analyzer import PaperInsight
from agos.evolution.code_analyzer import CodePattern
from agos.evolution.sandbox import SandboxResult
from agos.evolution.pipeline import (
    EvolutionPipelineConfig,
    RiskAssessment,
    assess_risk,
    format_proposal_markdown,
    format_summary_markdown,
)


def _make_insight(module="knowledge", priority="medium") -> PaperInsight:
    return PaperInsight(
        paper_id="2401.001",
        paper_title="Test Paper",
        technique="test technique",
        description="A test description.",
        applicability="Can be applied to agos.",
        priority=priority,
        agos_module=module,
        implementation_hint="Implement it.",
    )


def _make_proposal(
    module="knowledge",
    priority="medium",
    code_patterns=None,
    sandbox_results=None,
) -> EvolutionProposal:
    return EvolutionProposal(
        insight=_make_insight(module, priority),
        code_patterns=code_patterns or [],
        sandbox_results=sandbox_results or [],
    )


# ── EvolutionPipelineConfig tests ───────────────────────────────

def test_pipeline_config_defaults():
    config = EvolutionPipelineConfig()
    assert config.auto_merge_low_risk is False
    assert config.require_human_review is True
    assert config.risk_threshold == "low"
    assert config.max_proposals_per_cycle == 10
    assert config.evolution_interval_hours == 168


# ── assess_risk tests ───────────────────────────────────────────

def test_risk_low_knowledge_no_code():
    proposal = _make_proposal(module="knowledge")
    risk = assess_risk(proposal)
    assert risk.risk_level == "low"
    assert risk.auto_mergeable is True


def test_risk_low_tools_no_code():
    proposal = _make_proposal(module="tools")
    risk = assess_risk(proposal)
    assert risk.risk_level == "low"
    assert risk.auto_mergeable is True


def test_risk_high_kernel_module():
    proposal = _make_proposal(module="kernel")
    risk = assess_risk(proposal)
    assert risk.risk_level == "high"
    assert risk.auto_mergeable is False


def test_risk_high_policy_module():
    proposal = _make_proposal(module="policy")
    risk = assess_risk(proposal)
    assert risk.risk_level == "high"
    assert risk.auto_mergeable is False


def test_risk_high_failed_sandbox():
    patterns = [CodePattern(
        name="test", description="test", source_file="a.py",
        source_repo="r", code_snippet="x=1", agos_module="knowledge",
        integration_steps="step", priority="medium",
    )]
    sandbox_results = [SandboxResult(passed=False, output="", error="failed")]
    proposal = _make_proposal(
        module="knowledge",
        code_patterns=patterns,
        sandbox_results=sandbox_results,
    )
    risk = assess_risk(proposal)
    assert risk.risk_level == "high"


def test_risk_medium_many_patterns():
    patterns = [
        CodePattern(
            name=f"p{i}", description="d", source_file="a.py",
            source_repo="r", code_snippet="x=1", agos_module="knowledge",
            integration_steps="s", priority="medium",
        )
        for i in range(4)
    ]
    proposal = _make_proposal(module="intent", code_patterns=patterns)
    risk = assess_risk(proposal)
    assert risk.risk_level == "medium"


# ── format_proposal_markdown tests ──────────────────────────────

def test_format_proposal_basic():
    proposal = _make_proposal()
    risk = RiskAssessment(proposal_id=proposal.id, risk_level="low")
    md = format_proposal_markdown(proposal, risk)
    assert "## test technique" in md
    assert "**Priority:** medium" in md
    assert "**Module:** knowledge" in md
    assert "**Risk:** low" in md
    assert "Test Paper" in md


def test_format_proposal_with_code():
    patterns = [CodePattern(
        name="softmax", description="Better retrieval", source_file="a.py",
        source_repo="r", code_snippet="def softmax(): pass",
        agos_module="knowledge", integration_steps="Apply it", priority="high",
    )]
    proposal = _make_proposal(code_patterns=patterns)
    risk = RiskAssessment(proposal_id=proposal.id, risk_level="medium")
    md = format_proposal_markdown(proposal, risk)
    assert "### Code Patterns Found" in md
    assert "softmax" in md
    assert "def softmax()" in md


def test_format_proposal_with_repo():
    proposal = _make_proposal()
    proposal.repo_url = "https://github.com/example/repo"
    risk = RiskAssessment(proposal_id=proposal.id, risk_level="low")
    md = format_proposal_markdown(proposal, risk)
    assert "github.com/example/repo" in md


# ── format_summary_markdown tests ───────────────────────────────

def test_format_summary_basic():
    report = EvolutionReport(
        papers_found=5,
        papers_analyzed=3,
        proposals_created=2,
        repos_found=1,
        code_patterns_found=4,
    )
    proposals = [_make_proposal(), _make_proposal(module="tools")]
    risks = [
        RiskAssessment(proposal_id=proposals[0].id, risk_level="low", auto_mergeable=True),
        RiskAssessment(proposal_id=proposals[1].id, risk_level="medium"),
    ]
    md = format_summary_markdown(report, proposals, risks)
    assert "# Evolution Report" in md
    assert "Papers found:** 5" in md
    assert "[AUTO-MERGEABLE]" in md


def test_format_summary_empty():
    report = EvolutionReport()
    md = format_summary_markdown(report, [], [])
    assert "# Evolution Report" in md
    assert "Papers found:** 0" in md
