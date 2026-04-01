"""EvolutionEngine — the self-evolving core of agos.

Orchestrates the full evolution cycle:
  1. Scout arxiv for recent papers
  2. Filter out already-seen papers
  3. Analyze papers via LLM for actionable insights
  4. Find and fetch code from paper repositories
  5. Analyze repo code for implementable patterns
  6. Test code patterns in the sandbox
  7. Create evolution proposals (with code patterns)
  8. Store everything in the knowledge system
  9. Emit events + audit log
  10. Return report to user
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agos.evolution.integrator import EvolutionIntegrator, IntegrationResult

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.knowledge.base import Thread
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.evolution.scout import ArxivScout, Paper
from agos.evolution.analyzer import PaperAnalyzer, PaperInsight
from agos.evolution.repo_scout import RepoScout
from agos.evolution.code_analyzer import CodeAnalyzer, CodePattern
from agos.evolution.sandbox import Sandbox, SandboxResult


class EvolutionProposal(BaseModel):
    """A proposed improvement to agos based on research."""

    id: str = Field(default_factory=new_id)
    insight: PaperInsight
    code_patterns: list[CodePattern] = Field(default_factory=list)
    sandbox_results: list[SandboxResult] = Field(default_factory=list)
    repo_url: str = ""
    status: str = "proposed"  # proposed, accepted, rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None = None
    notes: str = ""


class EvolutionReport(BaseModel):
    """Summary of a single evolution cycle."""

    id: str = Field(default_factory=new_id)
    papers_found: int = 0
    papers_analyzed: int = 0
    proposals_created: int = 0
    repos_found: int = 0
    code_patterns_found: int = 0
    sandbox_tests_run: int = 0
    sandbox_tests_passed: int = 0
    topics_searched: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: float = 0.0
    papers: list[str] = Field(default_factory=list)  # titles of found papers
    proposal_ids: list[str] = Field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"EvolutionReport(papers={self.papers_found}, "
            f"analyzed={self.papers_analyzed}, "
            f"proposals={self.proposals_created}, "
            f"code_patterns={self.code_patterns_found})"
        )


class EvolutionEngine:
    """The self-evolving brain of agos.

    Scans the research frontier, extracts techniques, and proposes
    improvements. Proposals are stored for user review — the engine
    suggests, the user decides.
    """

    def __init__(
        self,
        scout: ArxivScout,
        analyzer: PaperAnalyzer,
        loom: TheLoom,
        event_bus: EventBus | None = None,
        audit_trail: AuditTrail | None = None,
        repo_scout: RepoScout | None = None,
        code_analyzer: CodeAnalyzer | None = None,
        sandbox: Sandbox | None = None,
        integrator: "EvolutionIntegrator | None" = None,
    ) -> None:
        self._scout = scout
        self._analyzer = analyzer
        self._loom = loom
        self._event_bus = event_bus
        self._audit = audit_trail
        self._repo_scout = repo_scout
        self._code_analyzer = code_analyzer
        self._sandbox = sandbox
        self._integrator = integrator
        self._proposals: dict[str, EvolutionProposal] = {}
        self._reports: list[EvolutionReport] = []

    async def run_cycle(
        self, days: int = 7, max_papers: int = 20
    ) -> EvolutionReport:
        """Run a full evolution cycle: scout → analyze → propose → store."""
        start = time.monotonic()
        report = EvolutionReport(topics_searched=len(ArxivScout.__init__.__defaults__ or []))

        await self._emit("evolution.cycle_started", {"days": days, "max_papers": max_papers})

        # Step 1: Scout arxiv
        papers = await self._scout.search_recent(days=days, max_results=max_papers)
        report.papers_found = len(papers)
        report.papers = [p.title for p in papers]

        if not papers:
            report.duration_ms = (time.monotonic() - start) * 1000
            self._reports.append(report)
            await self._emit("evolution.cycle_completed", {"papers_found": 0})
            return report

        # Step 2: Filter already-seen papers
        unseen = await self._filter_unseen(papers)
        report.papers_analyzed = len(unseen)

        for paper in unseen:
            await self._emit("evolution.paper_found", {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
            })

        # Step 3: Store papers in knowledge system
        for paper in unseen:
            await self._store_paper(paper)

        # Step 4: Analyze papers via LLM
        insights = await self._analyzer.analyze_batch(unseen)

        # Step 5: For each insight, find repo code and create enriched proposals
        # Build a map of paper abstracts for repo lookup
        paper_map = {p.arxiv_id: p for p in unseen}

        for insight in insights:
            # Try to find and analyze repo code for this paper
            code_patterns: list[CodePattern] = []
            sandbox_results: list[SandboxResult] = []
            repo_url = ""

            paper = paper_map.get(insight.paper_id)
            if paper and self._repo_scout and self._code_analyzer:
                repo_url, code_patterns, sandbox_results = await self._enrich_from_repo(
                    paper, report
                )

            proposal = EvolutionProposal(
                insight=insight,
                code_patterns=code_patterns,
                sandbox_results=sandbox_results,
                repo_url=repo_url,
            )
            self._proposals[proposal.id] = proposal
            report.proposal_ids.append(proposal.id)

            await self._store_proposal(proposal)
            await self._emit("evolution.proposal_created", {
                "proposal_id": proposal.id,
                "technique": insight.technique,
                "module": insight.agos_module,
                "priority": insight.priority,
                "paper": insight.paper_title,
                "code_patterns": len(code_patterns),
                "repo_url": repo_url,
            })

            if self._audit:
                await self._audit.record(
                    __import__("agos.policy.audit", fromlist=["AuditEntry"]).AuditEntry(
                        agent_name="evolution_engine",
                        action="proposal_created",
                        detail=f"Proposed: {insight.technique} (from: {insight.paper_title[:60]})",
                    )
                )

        report.proposals_created = len(insights)
        report.duration_ms = (time.monotonic() - start) * 1000
        self._reports.append(report)

        # Store the report itself
        await self._store_report(report)

        await self._emit("evolution.cycle_completed", {
            "papers_found": report.papers_found,
            "papers_analyzed": report.papers_analyzed,
            "proposals_created": report.proposals_created,
            "duration_ms": round(report.duration_ms),
        })

        return report

    async def get_proposals(
        self, status: str = "", limit: int = 20
    ) -> list[EvolutionProposal]:
        """Get proposals, optionally filtered by status."""
        proposals = list(self._proposals.values())
        if status:
            proposals = [p for p in proposals if p.status == status]
        proposals.sort(key=lambda p: p.created_at, reverse=True)
        return proposals[:limit]

    async def accept_proposal(self, proposal_id: str, notes: str = "") -> EvolutionProposal | None:
        """Accept a proposal for implementation."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return None
        proposal.status = "accepted"
        proposal.reviewed_at = datetime.utcnow()
        proposal.notes = notes

        await self._emit("evolution.proposal_accepted", {
            "proposal_id": proposal_id,
            "technique": proposal.insight.technique,
        })
        return proposal

    async def reject_proposal(self, proposal_id: str, notes: str = "") -> EvolutionProposal | None:
        """Reject a proposal."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return None
        proposal.status = "rejected"
        proposal.reviewed_at = datetime.utcnow()
        proposal.notes = notes

        await self._emit("evolution.proposal_rejected", {
            "proposal_id": proposal_id,
            "technique": proposal.insight.technique,
        })
        return proposal

    async def history(self, limit: int = 10) -> list[EvolutionReport]:
        """Get recent evolution cycle reports."""
        return list(reversed(self._reports[-limit:]))

    async def test_proposal_code(self, proposal_id: str) -> list[SandboxResult]:
        """Test code patterns from a proposal in the sandbox."""
        proposal = self._proposals.get(proposal_id)
        if not proposal or not proposal.code_patterns or not self._sandbox:
            return []

        results = []
        for pattern in proposal.code_patterns:
            if pattern.code_snippet:
                result = await self._sandbox.test_pattern(pattern.code_snippet)
                results.append(result)
                await self._emit("evolution.code_tested", {
                    "proposal_id": proposal_id,
                    "pattern": pattern.name,
                    "passed": result.passed,
                })

        proposal.sandbox_results = results
        return results

    async def integrate_proposal(self, proposal_id: str) -> "IntegrationResult | None":
        """Apply an accepted proposal via the integrator."""
        if not self._integrator:
            return None
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return None
        return await self._integrator.apply(proposal)

    async def rollback_integration(self, version_id: str) -> bool:
        """Rollback a previously applied integration."""
        if not self._integrator:
            return False
        return await self._integrator.rollback(version_id)

    # ── Internal helpers ──────────────────────────────────────────

    async def _enrich_from_repo(
        self, paper: Paper, report: EvolutionReport
    ) -> tuple[str, list[CodePattern], list[SandboxResult]]:
        """Find a paper's repo, analyze code, and optionally test patterns."""
        code_patterns: list[CodePattern] = []
        sandbox_results: list[SandboxResult] = []
        repo_url = ""

        try:
            # Find the repo
            url = await self._repo_scout.find_repo(paper.abstract, paper.title)
            if not url:
                return repo_url, code_patterns, sandbox_results
            repo_url = url
            report.repos_found += 1

            await self._emit("evolution.repo_found", {
                "paper": paper.title,
                "repo_url": repo_url,
            })

            # Fetch repo code
            snapshot = await self._repo_scout.fetch_repo(repo_url)
            if not snapshot or not snapshot.files:
                return repo_url, code_patterns, sandbox_results

            # Analyze code for patterns
            analysis = await self._code_analyzer.analyze_repo(snapshot)
            code_patterns = analysis.patterns
            report.code_patterns_found += len(code_patterns)

            for pattern in code_patterns:
                await self._emit("evolution.code_pattern_found", {
                    "pattern": pattern.name,
                    "module": pattern.agos_module,
                    "repo": repo_url,
                })

                # Store pattern in knowledge system
                await self._store_code_pattern(pattern, paper.arxiv_id)

            # Test patterns in sandbox if available
            if self._sandbox:
                for pattern in code_patterns:
                    if pattern.code_snippet:
                        result = await self._sandbox.test_pattern(pattern.code_snippet)
                        sandbox_results.append(result)
                        report.sandbox_tests_run += 1
                        if result.passed:
                            report.sandbox_tests_passed += 1

        except Exception:
            pass

        return repo_url, code_patterns, sandbox_results

    async def _filter_unseen(self, papers: list[Paper]) -> list[Paper]:
        """Filter out papers already stored in the knowledge system."""
        unseen = []
        for paper in papers:
            conns = await self._loom.graph.connections(f"paper:{paper.arxiv_id}")
            if not conns:
                unseen.append(paper)
        return unseen

    async def _store_paper(self, paper: Paper) -> None:
        """Store a paper in the knowledge system."""
        thread = Thread(
            content=f"{paper.title}\n\n{paper.abstract[:500]}",
            kind="paper",
            tags=paper.categories + ["arxiv", "evolution"],
            metadata={
                "arxiv_id": paper.arxiv_id,
                "authors": paper.authors[:5],
                "pdf_url": paper.pdf_url,
                "published": paper.published.isoformat(),
            },
            source=f"arxiv:{paper.arxiv_id}",
            confidence=0.8,
        )
        await self._loom.semantic.store(thread)
        await self._loom.graph.link(
            f"paper:{paper.arxiv_id}", "discovered_by", "agent:evolution_engine"
        )

    async def _store_proposal(self, proposal: EvolutionProposal) -> None:
        """Store a proposal in the knowledge system."""
        insight = proposal.insight
        content = (
            f"Evolution Proposal: {insight.technique}\n\n"
            f"Based on: {insight.paper_title}\n"
            f"Module: {insight.agos_module}\n"
            f"Priority: {insight.priority}\n\n"
            f"Description: {insight.description}\n\n"
            f"How to apply: {insight.applicability}\n\n"
            f"Implementation: {insight.implementation_hint}"
        )

        if proposal.code_patterns:
            content += f"\n\nCode Patterns Found: {len(proposal.code_patterns)}"
            for p in proposal.code_patterns:
                content += f"\n- {p.name}: {p.description[:100]}"

        if proposal.repo_url:
            content += f"\n\nSource Repository: {proposal.repo_url}"

        thread = Thread(
            content=content,
            kind="evolution_proposal",
            tags=["evolution", "proposal", insight.agos_module, insight.priority],
            metadata={
                "proposal_id": proposal.id,
                "paper_id": insight.paper_id,
                "technique": insight.technique,
                "status": proposal.status,
                "repo_url": proposal.repo_url,
                "code_patterns_count": len(proposal.code_patterns),
            },
            source=f"paper:{insight.paper_id}",
            confidence=0.7,
        )
        await self._loom.semantic.store(thread)

        # Link paper -> proposal -> module
        await self._loom.graph.link(
            f"paper:{insight.paper_id}", "inspired", f"proposal:{proposal.id}"
        )
        if insight.agos_module:
            await self._loom.graph.link(
                f"proposal:{proposal.id}", "improves", f"module:{insight.agos_module}"
            )

    async def _store_code_pattern(self, pattern: CodePattern, paper_id: str) -> None:
        """Store a code pattern in the knowledge system."""
        content = (
            f"Code Pattern: {pattern.name}\n\n"
            f"Description: {pattern.description}\n"
            f"Module: {pattern.agos_module}\n"
            f"Source: {pattern.source_repo} / {pattern.source_file}\n\n"
            f"```python\n{pattern.code_snippet}\n```\n\n"
            f"Integration: {pattern.integration_steps}"
        )
        thread = Thread(
            content=content,
            kind="code_pattern",
            tags=["evolution", "code", pattern.agos_module, pattern.priority],
            metadata={
                "pattern_id": pattern.id,
                "pattern_name": pattern.name,
                "source_repo": pattern.source_repo,
                "source_file": pattern.source_file,
            },
            source=f"paper:{paper_id}",
            confidence=0.6,
        )
        await self._loom.semantic.store(thread)
        await self._loom.graph.link(
            f"paper:{paper_id}", "has_code", f"pattern:{pattern.id}"
        )

    async def _store_report(self, report: EvolutionReport) -> None:
        """Store an evolution cycle report."""
        thread = Thread(
            content=(
                f"Evolution cycle completed: "
                f"{report.papers_found} papers found, "
                f"{report.papers_analyzed} analyzed, "
                f"{report.proposals_created} proposals created. "
                f"Duration: {report.duration_ms:.0f}ms."
            ),
            kind="evolution_cycle",
            tags=["evolution", "cycle", "report"],
            metadata={
                "report_id": report.id,
                "papers_found": report.papers_found,
                "proposals_created": report.proposals_created,
            },
            source="evolution_engine",
        )
        await self._loom.episodic.store(thread)

    async def _emit(self, topic: str, data: dict) -> None:
        """Emit an event if event bus is available."""
        if self._event_bus:
            await self._event_bus.emit(topic, data, source="evolution_engine")
