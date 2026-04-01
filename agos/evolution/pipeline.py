"""Autonomous R&D pipeline — wraps EvolutionEngine for CI/CD and daemon use.

Can be invoked by:
  - GitHub Actions (weekly): python -m agos.evolution.pipeline --output proposals/
  - Local daemon: programmatically via EvolutionDaemon
  - CLI: agos evolve (existing command)

Outputs structured proposals as markdown and JSON for PR creation.
"""

from __future__ import annotations

import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from agos.evolution.engine import EvolutionProposal, EvolutionReport


class EvolutionPipelineConfig(BaseSettings):
    """Configuration for autonomous evolution."""

    auto_merge_low_risk: bool = False
    require_human_review: bool = True
    risk_threshold: str = "low"  # low, medium, high
    max_proposals_per_cycle: int = 10
    evolution_interval_hours: int = 168  # weekly
    evolution_days_lookback: int = 7
    evolution_max_papers: int = 20

    model_config = {"env_prefix": "AGOS_EVOLUTION_"}


class RiskAssessment(BaseModel):
    """Risk assessment for an evolution proposal."""

    proposal_id: str
    risk_level: str = "medium"  # low, medium, high
    reasons: list[str] = Field(default_factory=list)
    auto_mergeable: bool = False


def assess_risk(proposal: EvolutionProposal) -> RiskAssessment:
    """Assess the risk of a proposal for auto-merge decisions."""
    risk = RiskAssessment(proposal_id=proposal.id)
    reasons: list[str] = []

    module = proposal.insight.agos_module

    # High-risk modules
    high_risk_modules = {"kernel", "policy", "llm"}
    if module in high_risk_modules:
        risk.risk_level = "high"
        reasons.append(f"Targets critical module: {module}")

    # Code patterns increase risk
    if proposal.code_patterns:
        if any(not r.passed for r in proposal.sandbox_results):
            risk.risk_level = "high"
            reasons.append("Sandbox tests failed for some patterns")
        elif len(proposal.code_patterns) > 3:
            risk.risk_level = "medium"
            reasons.append(f"Large change: {len(proposal.code_patterns)} code patterns")

    # Low-risk: knowledge/tools module, no code changes
    if module in {"knowledge", "tools", "evolution"} and not proposal.code_patterns:
        risk.risk_level = "low"
        reasons.append("Non-critical module, no code changes")

    risk.reasons = reasons
    risk.auto_mergeable = risk.risk_level == "low"
    return risk


def format_proposal_markdown(
    proposal: EvolutionProposal,
    risk: RiskAssessment,
) -> str:
    """Format a single proposal as markdown for PR body."""
    insight = proposal.insight
    lines = [
        f"## {insight.technique}",
        "",
        f"**Priority:** {insight.priority}",
        f"**Module:** {insight.agos_module}",
        f"**Risk:** {risk.risk_level}",
        f"**Paper:** {insight.paper_title}",
        "",
        "### Description",
        insight.description,
        "",
        "### How to Apply",
        insight.applicability,
        "",
        "### Implementation Hint",
        insight.implementation_hint,
    ]

    if proposal.code_patterns:
        lines.extend(["", "### Code Patterns Found"])
        for p in proposal.code_patterns:
            lines.extend([
                "",
                f"#### {p.name}",
                p.description,
                "```python",
                p.code_snippet,
                "```",
                f"**Integration steps:** {p.integration_steps}",
            ])

    if proposal.repo_url:
        lines.extend(["", f"**Source repository:** {proposal.repo_url}"])

    return "\n".join(lines)


def format_summary_markdown(
    report: EvolutionReport,
    proposals: list[EvolutionProposal],
    risks: list[RiskAssessment],
) -> str:
    """Format a summary of all proposals for the PR body."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# Evolution Report -- {date}",
        "",
        f"- **Papers found:** {report.papers_found}",
        f"- **Papers analyzed:** {report.papers_analyzed}",
        f"- **Proposals created:** {report.proposals_created}",
        f"- **Repos found:** {report.repos_found}",
        f"- **Code patterns:** {report.code_patterns_found}",
        f"- **Duration:** {report.duration_ms:.0f}ms",
        "",
        "## Proposals",
        "",
    ]

    for proposal, risk in zip(proposals, risks):
        auto_tag = " [AUTO-MERGEABLE]" if risk.auto_mergeable else ""
        lines.append(
            f"- **{proposal.insight.technique}** "
            f"({proposal.insight.priority}, {risk.risk_level} risk){auto_tag}"
        )

    return "\n".join(lines)


async def run_pipeline(
    output_dir: Path,
    config: EvolutionPipelineConfig | None = None,
) -> EvolutionReport:
    """Run the full evolution pipeline and write proposals to disk.

    Bootstraps an EvolutionEngine, runs a cycle, assesses risk for
    each proposal, and writes markdown + JSON files to output_dir.
    """
    config = config or EvolutionPipelineConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Bootstrap the engine
    from agos.config import settings
    from agos.llm.anthropic import AnthropicProvider
    from agos.knowledge.manager import TheLoom
    from agos.events.bus import EventBus
    from agos.evolution.scout import ArxivScout
    from agos.evolution.analyzer import PaperAnalyzer
    from agos.evolution.engine import EvolutionEngine
    from agos.evolution.repo_scout import RepoScout
    from agos.evolution.code_analyzer import CodeAnalyzer
    from agos.evolution.sandbox import Sandbox

    llm = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.default_model,
    )
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    loom = TheLoom(str(settings.workspace_dir / "agos.db"))
    await loom.initialize()

    engine = EvolutionEngine(
        scout=ArxivScout(),
        analyzer=PaperAnalyzer(llm),
        loom=loom,
        event_bus=EventBus(),
        repo_scout=RepoScout(),
        code_analyzer=CodeAnalyzer(llm),
        sandbox=Sandbox(),
    )

    # Run the cycle
    report = await engine.run_cycle(
        days=config.evolution_days_lookback,
        max_papers=config.evolution_max_papers,
    )

    # Get proposals and assess risk
    proposals = await engine.get_proposals(status="proposed")
    proposals = proposals[: config.max_proposals_per_cycle]
    risks = [assess_risk(p) for p in proposals]

    # Write individual proposal files
    for proposal, risk in zip(proposals, risks):
        filename = f"{proposal.id}.md"
        content = format_proposal_markdown(proposal, risk)
        (output_dir / filename).write_text(content, encoding="utf-8")

    # Write JSON for machine consumption
    proposals_json = [
        {
            "id": p.id,
            "technique": p.insight.technique,
            "module": p.insight.agos_module,
            "priority": p.insight.priority,
            "risk_level": r.risk_level,
            "auto_mergeable": r.auto_mergeable,
            "paper_title": p.insight.paper_title,
            "code_patterns_count": len(p.code_patterns),
            "repo_url": p.repo_url,
        }
        for p, r in zip(proposals, risks)
    ]
    (output_dir / "proposals.json").write_text(
        json.dumps(proposals_json, indent=2), encoding="utf-8"
    )

    # Write summary
    summary = format_summary_markdown(report, proposals, risks)
    (output_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")

    return report


# CLI entry point for GitHub Actions
if __name__ == "__main__":
    import asyncio
    import os

    parser = argparse.ArgumentParser(description="agos evolution pipeline")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("proposals"),
        help="Output directory for proposals",
    )
    args = parser.parse_args()

    report = asyncio.run(run_pipeline(args.output))

    # Set GitHub Actions output
    has_proposals = "true" if report.proposals_created > 0 else "false"
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_proposals={has_proposals}\n")

    print(f"Evolution complete: {report.proposals_created} proposals created")
