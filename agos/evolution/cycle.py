"""Evolution cycle orchestrator — arxiv research, sandbox testing, integration.

Runs the full evolution pipeline: scout papers, analyze insights,
find repos, sandbox-test code, evolve strategies, integrate proposals.
Also contains the continuous evolution_loop that drives repeated cycles.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from agos.types import new_id
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry
from agos.evolution.scout import ArxivScout, Paper
from agos.evolution.analyzer import PaperAnalyzer, PaperInsight
from agos.evolution.repo_scout import RepoScout
from agos.evolution.code_analyzer import CodePattern
from agos.evolution.sandbox import Sandbox
from agos.evolution.test_gate import RegressionGate
from agos.evolution.engine import EvolutionProposal
from agos.evolution.state import (
    EvolutionState, DesignArchive, DesignEntry,
    PerformanceTracker, CycleMetrics,
    EvolutionMemory, EvolutionInsight,
)
from agos.evolution.meta import MetaEvolver
from agos.evolution.codegen import evolve_code, iterate_strategy, load_evolved_strategies
from agos.evolution.component_evolver import SelfImprovementLoop
from agos.evolution.demand import DemandCollector
from agos.config import settings as _settings
from agos.knowledge.base import Thread

from agos.evolution.heuristics import (
    heuristic_analyze,
    extract_ast_patterns,
    _select_topics,
    _get_testable_snippet,
)

_logger = logging.getLogger(__name__)


async def _maybe_alma_iterate(
    cycle_num: int, bus: EventBus, audit: AuditTrail,
    design_archive: DesignArchive | None, llm_provider,
    evolution_state: EvolutionState | None, test_gate: RegressionGate | None,
    aid: str, name: str,
) -> None:
    """Run ALMA iterate-on-strategy every Nth cycle (independent of paper discovery)."""
    iterate_interval = _settings.evolution_alma_iterate_interval
    if not (design_archive is not None and llm_provider is not None
            and cycle_num % iterate_interval == 0 and design_archive.entries):
        return

    await bus.emit("evolution.alma_iterate_start", {
        "cycle": cycle_num, "archive_size": len(design_archive.entries),
    }, source=name)
    await audit.record(AuditEntry(
        agent_id=aid, agent_name=name, action="alma_iterate_start",
        detail=f"ALMA iterate cycle {cycle_num}, {len(design_archive.entries)} designs",
        success=True,
    ))

    # Sample 2 designs from archive via softmax
    candidates = design_archive.sample(min(2, len(design_archive.entries)))
    iterate_sandbox = Sandbox(timeout=10)
    for candidate in candidates:
        signals_str = f"fitness={candidate.current_fitness:.2f}"
        improved = await iterate_strategy(
            existing_code=candidate.code_snippet,
            fitness=candidate.current_fitness,
            signals=signals_str,
            module=candidate.module,
            sandbox=iterate_sandbox,
            llm_provider=llm_provider,
        )
        if improved:
            from agos.evolution.codegen import _hash_pattern
            child = DesignEntry(
                strategy_name=f"{candidate.strategy_name}_gen{candidate.generation + 1}",
                module=candidate.module,
                code_hash=_hash_pattern(improved),
                code_snippet=improved[:3000],
                current_fitness=candidate.current_fitness,
                generation=candidate.generation + 1,
                parent_id=candidate.id,
                source_paper=candidate.source_paper,
            )
            design_archive.add(child)

            try:
                evo_result = await evolve_code(
                    pattern_name=child.strategy_name,
                    pattern_code=improved,
                    source_paper=candidate.source_paper,
                    agos_module=candidate.module,
                    sandbox=iterate_sandbox,
                )
                if evo_result["success"]:
                    if test_gate is not None:
                        gate_result = await test_gate.check(evo_result["file_path"])
                        if not gate_result.passed:
                            try:
                                os.unlink(evo_result["file_path"])
                            except OSError:
                                pass
                            await bus.emit("evolution.test_gate_failed", {
                                "pattern": child.strategy_name,
                                "file": evo_result["file_path"],
                                "error": gate_result.error[:200],
                                "source": "alma_iterate",
                            }, source=name)
                            continue

                    await bus.emit("evolution.alma_iterated", {
                        "parent": candidate.strategy_name,
                        "child": child.strategy_name,
                        "generation": child.generation,
                        "file": evo_result["file_path"],
                    }, source=name)
                    await audit.record(AuditEntry(
                        agent_id=aid, agent_name=name, action="alma_iterated",
                        detail=f"{candidate.strategy_name} -> gen{child.generation}",
                        success=True,
                    ))
            except Exception as e:
                _logger.warning("ALMA iterate codegen failed: %s", e)
        await asyncio.sleep(0.5)

    # Persist archive
    if evolution_state is not None:
        evolution_state.save_design_archive(design_archive)


async def _maybe_generate_from_pending(
    evolution_state: EvolutionState | None, llm_provider,
    bus: EventBus, audit: AuditTrail,
    test_gate: RegressionGate | None, aid: str, name: str,
    evo_memory: EvolutionMemory | None = None,
    demand_context: str = "",
) -> None:
    """Try generating code from a queued paper insight (no unseen papers needed)."""
    if evolution_state is None or llm_provider is None:
        return

    insight_dict = evolution_state.pop_pending_insight()
    if insight_dict is None:
        return

    paper_id = insight_dict.get("paper_id", "unknown")
    technique = insight_dict.get("technique", "")[:60]
    module = insight_dict.get("agos_module", "knowledge")

    await bus.emit("evolution.pending_codegen_start", {
        "paper_id": paper_id, "technique": technique, "module": module,
    }, source=name)

    # Reconstruct PaperInsight from stored dict
    insight = PaperInsight(**{k: v for k, v in insight_dict.items()
                              if k in PaperInsight.model_fields})

    sandbox = Sandbox(timeout=10)
    from agos.evolution.codegen import generate_from_insight
    _mem_ctx = evo_memory.context_prompt(module) if evo_memory else ""
    _full_ctx = f"{_mem_ctx}\n\n{demand_context}".strip() if demand_context else _mem_ctx
    gen_code = await generate_from_insight(
        insight=insight, module=module,
        sandbox=sandbox, llm_provider=llm_provider,
        evo_memory_context=_full_ctx,
    )

    if gen_code:
        evolution_state.mark_codegen_done(paper_id)
        safe_name = technique[:30].replace(" ", "_").replace("/", "_").lower()
        pattern_name = f"paper_{safe_name}"

        # Write evolved file
        evo_result = await evolve_code(
            pattern_name=pattern_name,
            pattern_code=gen_code,
            source_paper=paper_id,
            agos_module=module,
            sandbox=sandbox,
        )
        if evo_result["success"]:
            if test_gate is not None:
                gate_result = await test_gate.check(evo_result["file_path"])
                if not gate_result.passed:
                    try:
                        os.unlink(evo_result["file_path"])
                    except OSError:
                        pass
                    await bus.emit("evolution.pending_codegen_gate_failed", {
                        "paper_id": paper_id, "pattern": pattern_name,
                    }, source=name)
                    return

            await bus.emit("evolution.pending_codegen_success", {
                "paper_id": paper_id, "pattern": pattern_name,
                "file": evo_result["file_path"],
            }, source=name)
            await audit.record(AuditEntry(
                agent_id=aid, agent_name=name, action="paper_codegen",
                detail=f"Generated code from paper {paper_id}: {pattern_name}",
                success=True,
            ))
        else:
            await bus.emit("evolution.pending_codegen_failed", {
                "paper_id": paper_id, "reason": "evolve_code failed",
            }, source=name)
    else:
        retries = insight_dict.get("_retries", 0)
        if retries < 3:
            # Re-queue for retry with incremented counter
            insight_dict["_retries"] = retries + 1
            evolution_state.data.pending_insights.append(insight_dict)
            await bus.emit("evolution.pending_codegen_retry", {
                "paper_id": paper_id, "retry": retries + 1,
            }, source=name)
        else:
            # Exhausted retries — mark done permanently
            evolution_state.mark_codegen_done(paper_id)
            await bus.emit("evolution.pending_codegen_failed", {
                "paper_id": paper_id, "reason": "exhausted 3 retries",
            }, source=name)


async def run_evolution_cycle(cycle_num: int, bus: EventBus, audit: AuditTrail, loom,
                              evolution_state: EvolutionState | None = None,
                              design_archive: DesignArchive | None = None,
                              llm_provider=None,
                              perf_tracker: PerformanceTracker | None = None,
                              evo_memory: EvolutionMemory | None = None,
                              demand_collector: DemandCollector | None = None) -> None:
    """Run one full evolution cycle with real research and real integration."""
    aid = new_id()
    name = "EvolutionEngine"
    await bus.emit("agent.spawned", {"id": aid, "agent": name, "role": "self-evolution"}, source="kernel")
    await audit.log_state_change(aid, name, "created", "running")
    start_time = time.time()

    await bus.emit("evolution.cycle_started", {"cycle": cycle_num}, source=name)

    # HyperAgents: track per-cycle metrics for performance analysis
    _cm = CycleMetrics(cycle=cycle_num)

    # ── Phase 0: Check demand signals (user-driven evolution) ──
    demand_topics: list[str] = []
    demand_context = ""
    if demand_collector and demand_collector.has_demands():
        demand_topics = demand_collector.demand_topics(limit=2)
        demand_context = demand_collector.demand_context_for_codegen()
        await bus.emit("evolution.demand_driven", {
            "signals": demand_collector.pending_count(),
            "topics": demand_topics,
            "top_demands": [
                {"kind": d.kind, "source": d.source, "priority": round(d.priority, 2)}
                for d in demand_collector.top_demands(limit=3)
            ],
        }, source=name)
        await audit.record(AuditEntry(
            agent_id=aid, agent_name=name, action="demand_check",
            detail=f"{demand_collector.pending_count()} demand signals, topics: {demand_topics}",
            success=True,
        ))

    # ── Phase 1: Scout arxiv (demand-driven + role-biased topics) ──
    scout = ArxivScout(timeout=25)
    # Demand topics first, then fill remaining slots with scheduled topics
    topics = demand_topics + [
        t for t in _select_topics(cycle_num)
        if t not in demand_topics
    ]
    topics = topics[:3]  # Max 3 topics per cycle

    all_papers: dict[str, Paper] = {}
    for topic in topics:
        await bus.emit("evolution.arxiv_searching", {"topic": topic}, source=name)
        await audit.record(AuditEntry(
            agent_id=aid, agent_name=name, action="arxiv_search",
            detail=f"Searching: {topic}", success=True,
        ))
        try:
            papers = await scout.search(topic, max_results=5)
            for p in papers:
                if p.arxiv_id not in all_papers:
                    all_papers[p.arxiv_id] = p
                    await bus.emit("evolution.paper_found", {
                        "title": p.title[:80],
                        "arxiv_id": p.arxiv_id,
                        "authors": ", ".join(p.authors[:2]),
                    }, source=name)
        except Exception as e:
            await bus.emit("evolution.arxiv_error", {"topic": topic, "error": str(e)[:120]}, source=name)
        await asyncio.sleep(1.5)

    papers = list(all_papers.values())
    await bus.emit("evolution.papers_discovered", {"total": len(papers)}, source=name)

    # Pre-create test gate (used by ALMA iterate on early returns too)
    test_gate = RegressionGate() if _settings.evolution_test_gate else None

    if not papers:
        # Try generating code from a queued paper insight
        await _maybe_generate_from_pending(
            evolution_state, llm_provider, bus, audit,
            test_gate, aid, name, evo_memory=evo_memory,
            demand_context=demand_context,
        )
        # Still iterate on existing designs + increment cycle + auto-share
        await _maybe_alma_iterate(
            cycle_num, bus, audit, design_archive, llm_provider,
            evolution_state, test_gate, aid, name,
        )
        if evolution_state is not None:
            evolution_state.increment_cycle()
            evolution_state.save(loom)
            pass  # auto-share removed — users share via git PRs
        await bus.emit("evolution.cycle_completed", {"papers": 0}, source=name)
        await audit.log_state_change(aid, name, "running", "completed")
        return

    # ── Phase 2: Filter already-seen ──
    unseen = []
    for paper in papers:
        conns = await loom.graph.connections(f"paper:{paper.arxiv_id}")
        if not conns:
            unseen.append(paper)

    # Re-seed: seen papers that still need codegen get re-queued for analysis
    if not unseen and evolution_state is not None:
        done = set(evolution_state.data.codegen_done_paper_ids)
        pending_ids = {d.get("paper_id") for d in evolution_state.data.pending_insights}
        needs_codegen = [
            p for p in papers
            if p.arxiv_id not in done and p.arxiv_id not in pending_ids
        ]
        if needs_codegen:
            unseen = needs_codegen[:3]  # re-analyze up to 3 per cycle
            await bus.emit("evolution.reseed_from_seen", {
                "count": len(unseen),
            }, source=name)

    await bus.emit("evolution.filtering_done", {
        "total": len(papers), "new": len(unseen), "seen": len(papers) - len(unseen),
    }, source=name)

    if not unseen:
        # Try generating code from a queued paper insight
        await _maybe_generate_from_pending(
            evolution_state, llm_provider, bus, audit,
            test_gate, aid, name, evo_memory=evo_memory,
            demand_context=demand_context,
        )
        # Still iterate on existing designs + increment cycle + auto-share
        await _maybe_alma_iterate(
            cycle_num, bus, audit, design_archive, llm_provider,
            evolution_state, test_gate, aid, name,
        )
        if evolution_state is not None:
            evolution_state.increment_cycle()
            evolution_state.save(loom)
            pass  # auto-share removed — users share via git PRs
        await bus.emit("evolution.cycle_completed", {"papers": len(papers), "new": 0}, source=name)
        await audit.log_state_change(aid, name, "running", "completed")
        return

    # ── Phase 3: Analyze papers ──
    # Use LLM analyzer when API key is available (reads the abstract and decides
    # relevance intelligently). Falls back to heuristic keyword matching.
    paper_analyzer = None
    if llm_provider is not None:
        paper_analyzer = PaperAnalyzer(llm_provider)

    insights: list[tuple[Paper, PaperInsight]] = []
    for paper in unseen[:6]:
        await bus.emit("evolution.analyzing_paper", {"title": paper.title[:80]}, source=name)

        await loom.semantic.store(Thread(
            content=f"{paper.title}\n\n{paper.abstract[:500]}",
            kind="paper",
            tags=paper.categories[:5] + ["arxiv", "evolution"],
            metadata={"arxiv_id": paper.arxiv_id, "authors": paper.authors[:5]},
            source=f"arxiv:{paper.arxiv_id}",
            confidence=0.8,
        ))
        await loom.graph.link(f"paper:{paper.arxiv_id}", "discovered_by", "agent:evolution_engine")

        # LLM analysis first (understands methodology), heuristic fallback
        insight = None
        if paper_analyzer is not None:
            try:
                insight = await paper_analyzer.analyze(paper)
                if insight:
                    await bus.emit("evolution.llm_analyzed", {
                        "paper": paper.title[:60], "relevant": True,
                    }, source=name)
                    await audit.record(AuditEntry(
                        agent_id=aid, agent_name=name, action="llm_analyzed",
                        detail=f"LLM accepted: {paper.title[:60]}", success=True,
                    ))
                else:
                    # Local LLMs may misjudge — heuristic gets second opinion
                    insight = heuristic_analyze(paper)
                    if insight:
                        await bus.emit("evolution.heuristic_rescued", {
                            "paper": paper.title[:60],
                        }, source=name)
                    else:
                        await bus.emit("evolution.llm_rejected", {
                            "paper": paper.title[:60], "reason": "LLM + heuristic both rejected",
                        }, source=name)
                        await audit.record(AuditEntry(
                            agent_id=aid, agent_name=name, action="llm_rejected",
                            detail=f"LLM rejected: {paper.title[:60]}", success=True,
                        ))
            except Exception as e:
                _logger.debug("LLM analysis failed for %s, using heuristic: %s", paper.arxiv_id, e)
                insight = heuristic_analyze(paper)
        else:
            insight = heuristic_analyze(paper)

        if insight:
            insights.append((paper, insight))
            await bus.emit("evolution.insight_extracted", {
                "technique": insight.technique[:65],
                "module": insight.agos_module,
                "priority": insight.priority,
            }, source=name)
            await audit.record(AuditEntry(
                agent_id=aid, agent_name=name, action="insight",
                detail=f"{insight.agos_module}: {insight.technique[:60]}", success=True,
            ))
            # Queue insight for code generation (survives across cycles)
            if evolution_state is not None:
                evolution_state.store_insight(insight.model_dump())
        else:
            await bus.emit("evolution.paper_filtered", {"title": paper.title[:60]}, source=name)
        await asyncio.sleep(0.5)

    await bus.emit("evolution.analysis_complete", {
        "analyzed": min(len(unseen), 6), "insights": len(insights),
    }, source=name)

    if not insights:
        await _maybe_generate_from_pending(
            evolution_state, llm_provider, bus, audit,
            test_gate, aid, name, evo_memory=evo_memory,
            demand_context=demand_context,
        )
        await _maybe_alma_iterate(
            cycle_num, bus, audit, design_archive, llm_provider,
            evolution_state, test_gate, aid, name,
        )
        if evolution_state is not None:
            evolution_state.increment_cycle()
            evolution_state.save(loom)
            pass  # auto-share removed — users share via git PRs
        await bus.emit("evolution.cycle_completed", {"papers": len(papers), "insights": 0}, source=name)
        await audit.log_state_change(aid, name, "running", "completed")
        return

    # ── Phase 4: Find repos + code + sandbox ──
    repo_scout = RepoScout(timeout=20)
    sandbox = Sandbox(timeout=10)
    proposals: list[EvolutionProposal] = []

    for paper, insight in insights[:3]:
        code_patterns: list[CodePattern] = []
        sandbox_results = []
        repo_url = ""

        await bus.emit("evolution.searching_repo", {"paper": paper.title[:60]}, source=name)
        try:
            url = await repo_scout.find_repo(paper.abstract, paper.title)
            if url:
                repo_url = url
                await bus.emit("evolution.repo_found", {"repo": url}, source=name)

                await bus.emit("evolution.fetching_code", {"repo": url}, source=name)
                snapshot = await repo_scout.fetch_repo(url, max_files=8)

                if snapshot and snapshot.files:
                    await bus.emit("evolution.code_fetched", {
                        "files": len(snapshot.files),
                        "kb": round(snapshot.total_code_size / 1024, 1),
                        "stars": snapshot.stars,
                    }, source=name)

                    ast_patterns = extract_ast_patterns(snapshot)
                    for pat in ast_patterns:
                        await bus.emit("evolution.code_pattern_found", {
                            "name": pat.name, "file": pat.source_file,
                        }, source=name)
                    code_patterns.extend(ast_patterns)
            else:
                await bus.emit("evolution.no_repo", {"paper": paper.title[:50]}, source=name)
        except Exception as e:
            await bus.emit("evolution.repo_error", {"error": str(e)[:120]}, source=name)

        # Generate code inspired by the paper insight (LLM) or fall back to seed
        llm_generated = False
        if llm_provider is not None:
            from agos.evolution.codegen import generate_from_insight
            # Combine evolution memory + demand context for targeted codegen
            _mem_ctx = evo_memory.context_prompt(insight.agos_module) if evo_memory else ""
            _full_ctx = f"{_mem_ctx}\n\n{demand_context}".strip() if demand_context else _mem_ctx
            gen_code = await generate_from_insight(
                insight=insight, module=insight.agos_module,
                sandbox=sandbox, llm_provider=llm_provider,
                evo_memory_context=_full_ctx,
            )
            if gen_code:
                safe_name = insight.technique[:30].replace(" ", "_").lower()
                code_patterns.append(CodePattern(
                    name=f"paper_{safe_name}",
                    description=f"Inspired by: {insight.technique}",
                    source_file="llm_generated",
                    source_repo=f"arxiv:{insight.paper_id}",
                    code_snippet=gen_code,
                    agos_module=insight.agos_module,
                    priority=insight.priority,
                ))
                llm_generated = True
                # Mark this paper as done so the pending queue skips it
                if evolution_state is not None:
                    evolution_state.mark_codegen_done(insight.paper_id)
        if not llm_generated:
            testable = _get_testable_snippet(insight.agos_module, cycle_num)
            if testable:
                code_patterns.append(testable)

        # Sandbox test
        for pattern in code_patterns:
            if not pattern.code_snippet:
                continue
            await bus.emit("evolution.sandbox_testing", {"pattern": pattern.name}, source=name)
            result = await sandbox.test_pattern(pattern.code_snippet)
            sandbox_results.append(result)

            if result.passed:
                await bus.emit("evolution.sandbox_passed", {
                    "pattern": pattern.name,
                    "ms": round(result.execution_time_ms),
                    "output": result.output.strip()[:100],
                }, source=name)

                # ── CODE EVOLUTION: write sandbox-passed pattern as real .py ──
                try:
                    evo_result = await evolve_code(
                        pattern_name=pattern.name,
                        pattern_code=pattern.code_snippet,
                        source_paper=paper.arxiv_id,
                        agos_module=insight.agos_module,
                        sandbox=sandbox,
                        sandbox_result=result,
                    )
                    if evo_result["success"]:
                        # ── REGRESSION GATE: verify tests still pass ──
                        if test_gate is not None:
                            gate_result = await test_gate.check(evo_result["file_path"])
                            if not gate_result.passed:
                                try:
                                    os.unlink(evo_result["file_path"])
                                except OSError:
                                    pass
                                await bus.emit("evolution.test_gate_failed", {
                                    "pattern": pattern.name,
                                    "file": evo_result["file_path"],
                                    "error": gate_result.error[:200],
                                    "test_summary": gate_result.test_count,
                                    "duration_ms": round(gate_result.execution_time_ms),
                                }, source=name)
                                await audit.record(AuditEntry(
                                    agent_id=aid, agent_name=name,
                                    action="TEST_GATE_REJECTED",
                                    detail=f"Deleted {evo_result['file_path']} — tests failed",
                                    success=False,
                                ))
                                _logger.warning(
                                    "Test gate rejected %s: tests failed",
                                    evo_result["file_path"],
                                )
                                # HyperAgents: record test gate failure
                                if evo_memory is not None:
                                    evo_memory.record(EvolutionInsight(
                                        cycle=cycle_num,
                                        what_tried=pattern.name,
                                        module=insight.agos_module,
                                        outcome="test_gate_failed",
                                        reason=gate_result.error[:200],
                                        source_paper=paper.arxiv_id,
                                    ))
                                continue

                            await bus.emit("evolution.test_gate_passed", {
                                "pattern": pattern.name,
                                "file": evo_result["file_path"],
                                "test_summary": gate_result.test_count,
                                "duration_ms": round(gate_result.execution_time_ms),
                            }, source=name)

                        await bus.emit("evolution.code_evolved", {
                            "pattern": pattern.name,
                            "file": evo_result["file_path"],
                            "module": evo_result["module_name"],
                            "class": evo_result["class_name"],
                        }, source=name)
                        await audit.record(AuditEntry(
                            agent_id=aid, agent_name=name,
                            action="CODE_EVOLVED",
                            detail=f"Wrote {evo_result['file_path']}",
                            success=True,
                        ))
                    else:
                        await bus.emit("evolution.codegen_failed", {
                            "pattern": pattern.name,
                            "error": evo_result["error"][:120],
                        }, source=name)
                except Exception as e:
                    _logger.warning("Code evolution failed for %s: %s", pattern.name, e)
            else:
                await bus.emit("evolution.sandbox_failed", {
                    "pattern": pattern.name,
                    "error": result.error[:100],
                }, source=name)
                # HyperAgents: record sandbox failure for cross-cycle learning
                if evo_memory is not None:
                    evo_memory.record(EvolutionInsight(
                        cycle=cycle_num,
                        what_tried=pattern.name,
                        module=insight.agos_module,
                        outcome="sandbox_failed",
                        reason=result.error[:200] if result.error else "unknown",
                        source_paper=paper.arxiv_id,
                    ))
            await asyncio.sleep(0.3)

        # Create proposal
        proposal = EvolutionProposal(
            insight=insight,
            code_patterns=code_patterns,
            sandbox_results=sandbox_results,
            repo_url=repo_url,
        )
        proposals.append(proposal)

        passed = sum(1 for r in sandbox_results if r.passed)
        await bus.emit("evolution.proposal_created", {
            "id": proposal.id[:10],
            "technique": insight.technique[:60],
            "module": insight.agos_module,
            "priority": insight.priority,
            "patterns": len(code_patterns),
            "sandbox": f"{passed}/{len(sandbox_results)}",
        }, source=name)
        await audit.record(AuditEntry(
            agent_id=aid, agent_name=name, action="proposal",
            detail=f"Proposed: {insight.technique[:60]}", success=True,
        ))

        await loom.semantic.store(Thread(
            content=f"Proposal: {insight.technique}\nModule: {insight.agos_module}\n{insight.description[:200]}",
            kind="evolution_proposal",
            tags=["evolution", "proposal", insight.agos_module],
            metadata={"proposal_id": proposal.id},
            source=f"paper:{insight.paper_id}",
        ))
        await loom.graph.link(f"paper:{insight.paper_id}", "inspired", f"proposal:{proposal.id}")
        await asyncio.sleep(1)

    # ── Phase 5: Auto-accept and integrate ──
    from agos.evolution.integrator import EvolutionIntegrator
    from agos.evolution.strategies.memory_softmax import SoftmaxScoringStrategy
    from agos.evolution.strategies.memory_confidence import AdaptiveConfidenceStrategy
    from agos.evolution.strategies.memory_layered import LayeredRetrievalStrategy
    from agos.evolution.strategies.memory_semaphore import SemaphoreBatchStrategy
    from agos.evolution.strategies.consolidation_tuning import ConsolidationTuningStrategy
    from agos.evolution.strategies.persona_tuning import PersonaTuningStrategy
    from agos.evolution.strategies.policy_tuning import PolicyTuningStrategy
    from agos.evolution.strategies.planner_strategy import PlannerStrategy
    from agos.evolution.strategies.intent_prompt import IntentPromptStrategy

    integrator = EvolutionIntegrator(loom=loom, event_bus=bus, audit_trail=audit, sandbox=sandbox)
    integrator.register_strategy(SoftmaxScoringStrategy(loom.semantic))
    integrator.register_strategy(AdaptiveConfidenceStrategy(loom.semantic))
    integrator.register_strategy(LayeredRetrievalStrategy(loom))
    integrator.register_strategy(SemaphoreBatchStrategy(loom.semantic))
    integrator.register_strategy(ConsolidationTuningStrategy(loom))
    integrator.register_strategy(PersonaTuningStrategy(runtime=None, audit_trail=audit))
    integrator.register_strategy(PolicyTuningStrategy(policy_engine=None, audit_trail=audit))
    integrator.register_strategy(PlannerStrategy(runtime=None, event_bus=bus))
    integrator.register_strategy(IntentPromptStrategy(audit_trail=audit))

    # ── Load evolved strategies from .agos/evolved/ with live components ──
    live_components = {
        "loom": loom,
        "event_bus": bus,
        "audit_trail": audit,
        "sandbox": sandbox,
    }
    for path, strategy in load_evolved_strategies(components=live_components):
        try:
            integrator.register_strategy(strategy)
            await bus.emit("evolution.evolved_strategy_loaded", {
                "name": strategy.name, "file": path.split("/")[-1],
            }, source=name)
        except Exception as e:
            _logger.warning("Failed to register evolved strategy from %s: %s", path, e)

    integrated = 0
    for proposal in proposals:
        proposal.status = "accepted"
        await bus.emit("evolution.proposal_accepted", {
            "id": proposal.id[:10], "technique": proposal.insight.technique[:60],
        }, source=name)

        result = await integrator.apply(proposal)
        if result.success:
            integrated += 1
            await bus.emit("evolution.os_evolved", {
                "module": proposal.insight.agos_module,
                "technique": proposal.insight.technique[:55],
                "changes": result.changes,
            }, source=name)
            await audit.record(AuditEntry(
                agent_id=aid, agent_name=name, action="EVOLVED",
                detail=f"Integrated: {', '.join(result.changes)}", success=True,
            ))
            # HyperAgents: record success insight for cross-cycle learning
            if evo_memory is not None:
                evo_memory.record(EvolutionInsight(
                    cycle=cycle_num,
                    what_tried=proposal.insight.technique[:80],
                    module=proposal.insight.agos_module,
                    outcome="success",
                    reason=f"Integrated: {', '.join(result.changes[:3])}",
                    source_paper=proposal.insight.paper_id,
                ))
        else:
            await bus.emit("evolution.integration_skipped", {
                "module": proposal.insight.agos_module,
                "reason": (result.error or "no strategy")[:80],
            }, source=name)
            # HyperAgents: record failure insight
            if evo_memory is not None:
                evo_memory.record(EvolutionInsight(
                    cycle=cycle_num,
                    what_tried=proposal.insight.technique[:80],
                    module=proposal.insight.agos_module,
                    outcome="rejected",
                    reason=(result.error or "no matching strategy")[:200],
                    source_paper=proposal.insight.paper_id,
                ))
        await asyncio.sleep(0.5)

    # ── Persist evolution state ──
    if evolution_state is not None:
        for proposal in proposals:
            sandbox_passed = any(r.passed for r in proposal.sandbox_results)
            source_papers = [{"arxiv_id": proposal.insight.paper_id,
                              "title": proposal.insight.paper_title}]
            evolution_state.record_integration(
                strategy_name=proposal.insight.technique[:80],
                module=proposal.insight.agos_module,
                parameters={},
                source_papers=source_papers,
                sandbox_passed=sandbox_passed,
            )
            for pat in proposal.code_patterns:
                if pat.code_snippet:
                    sb_out = ""
                    for r in proposal.sandbox_results:
                        if r.passed:
                            sb_out = r.output[:200]
                            break
                    evolution_state.record_pattern(
                        name=pat.name, module=pat.agos_module,
                        code_snippet=pat.code_snippet[:500],
                        sandbox_output=sb_out,
                        source_paper=proposal.insight.paper_id,
                    )
        evolution_state.increment_cycle()
        evolution_state.save(loom)
        await bus.emit("evolution.state_saved", {
            "cycles": evolution_state.data.cycles_completed,
            "strategies": len(evolution_state.data.strategies_applied),
            "patterns": len(evolution_state.data.discovered_patterns),
        }, source=name)

    # ── Phase 4.5: ALMA iterate-on-strategy (every Nth cycle) ──
    await _maybe_alma_iterate(
        cycle_num, bus, audit, design_archive, llm_provider,
        evolution_state, test_gate, aid, name,
    )

    # ── Add sandbox-passed patterns to design archive ──
    if design_archive is not None:
        for proposal in proposals:
            for i, pat in enumerate(proposal.code_patterns):
                if pat.code_snippet and i < len(proposal.sandbox_results):
                    sr = proposal.sandbox_results[i]
                    if sr.passed:
                        from agos.evolution.codegen import _hash_pattern
                        entry = DesignEntry(
                            strategy_name=pat.name,
                            module=pat.agos_module,
                            code_hash=_hash_pattern(pat.code_snippet),
                            code_snippet=pat.code_snippet[:3000],
                            current_fitness=0.5,  # initial neutral fitness
                            source_paper=proposal.insight.paper_id,
                        )
                        design_archive.add(entry)

    # ── Final report ──
    dur = round(time.time() - start_time, 1)

    # HyperAgents: record cycle metrics for stagnation detection
    _cm.papers_found = len(papers)
    _cm.insights_extracted = len(insights)
    _cm.strategies_loaded = integrated
    _cm.archive_size = len(design_archive.entries) if design_archive else 0
    # Count sandbox pass/fail across all proposals
    for prop in proposals:
        for sr in prop.sandbox_results:
            if sr.passed:
                _cm.sandbox_passed += 1
            else:
                _cm.sandbox_failed += 1
    # Compute archive fitness stats
    if design_archive and design_archive.entries:
        fitnesses = [e.current_fitness for e in design_archive.entries]
        _cm.fitness_avg = round(sum(fitnesses) / len(fitnesses), 4)
        _cm.fitness_best = round(max(fitnesses), 4)
    if perf_tracker is not None:
        perf_tracker.record(_cm)
        if evolution_state is not None:
            evolution_state.save_performance_tracker(perf_tracker)
    if evo_memory is not None and evolution_state is not None:
        evolution_state.save_evolution_memory(evo_memory)
        # Emit stagnation warning if detected
        if perf_tracker.is_stagnating():
            await bus.emit("evolution.stagnation_detected", {
                "cycle": cycle_num,
                "velocity": perf_tracker.improvement_velocity(),
                "window": perf_tracker._stagnation_window,
            }, source=name)

    await bus.emit("evolution.cycle_completed", {
        "cycle": cycle_num, "papers": len(papers), "new": len(unseen),
        "insights": len(insights), "proposals": len(proposals),
        "integrated": integrated, "duration_s": dur,
        "archive_size": len(design_archive.entries) if design_archive else 0,
        "perf_velocity": perf_tracker.improvement_velocity() if perf_tracker else None,
        "stagnating": perf_tracker.is_stagnating() if perf_tracker else False,
    }, source=name)

    await loom.episodic.store(Thread(
        content=f"Evolution cycle {cycle_num}: {len(papers)} papers, {len(insights)} insights, {integrated} integrated ({dur}s)",
        kind="evolution_cycle", tags=["evolution", "cycle"], source="evolution_engine",
    ))
    await audit.log_state_change(aid, name, "running", "completed")
    await bus.emit("agent.completed", {
        "agent": name, "findings": len(proposals),
        "summary": f"Cycle {cycle_num}: {len(papers)} papers, {integrated} integrated",
    }, source="kernel")


async def evolution_loop(bus: EventBus, audit: AuditTrail, loom,
                         evolution_state: EvolutionState | None = None,
                         meta_evolver: MetaEvolver | None = None,
                         policy_engine=None, tracer=None, runtime=None,
                         design_archive: DesignArchive | None = None,
                         llm_provider=None,
                         tool_registry=None,
                         daemon_manager=None,
                         os_agent=None,
                         demand_collector: DemandCollector | None = None) -> None:
    """Continuously run evolution cycles + meta-evolution + tool evolution + self-improvement."""
    await asyncio.sleep(10)  # Wait for boot + initial agents
    # Stagger delay for multi-node fleet (avoid simultaneous arxiv hits)
    initial_delay = _settings.evolution_initial_delay
    if initial_delay > 0:
        await asyncio.sleep(initial_delay)
    cycle = evolution_state.data.cycles_completed if evolution_state else 0

    # HyperAgents: restore or create performance tracker
    perf_tracker = (
        evolution_state.restore_performance_tracker()
        if evolution_state else PerformanceTracker()
    )
    # HyperAgents: restore or create evolution memory
    evo_memory = (
        evolution_state.restore_evolution_memory()
        if evolution_state else EvolutionMemory()
    )

    # Demand-driven evolution: use the shared DemandCollector from serve.py
    # This is the SAME instance that the dashboard uses — one source of truth.
    if demand_collector is None:
        demand_collector = DemandCollector()
        demand_collector.subscribe(bus)
    # Restore persisted demand state (merge into existing collector)
    if evolution_state is not None:
        _demand_data = evolution_state.load_json("demand_signals")
        if _demand_data:
            _restored = DemandCollector.from_dict(_demand_data)
            # Merge restored signals into the live collector
            for key, signal in _restored._signals.items():
                if key not in demand_collector._signals:
                    demand_collector._signals[key] = signal
            _logger.info("Merged %d persisted demand signals", len(_restored._signals))

    await bus.emit("evolution.perf_tracker_init", {
        "cycles_recorded": perf_tracker.cycles_recorded,
        "stagnating": perf_tracker.is_stagnating(),
        "memory_insights": len(evo_memory.insights),
        "demand_signals": demand_collector.pending_count(),
    }, source="evolution_loop")

    # Create sandbox for meta-evolution eval tasks
    _meta_sandbox = Sandbox(timeout=10)

    # Initialize ToolEvolver — the OS evolves its own capabilities
    from agos.evolution.tool_evolver import ToolEvolver
    tool_evolver = ToolEvolver(event_bus=bus, audit=audit, tool_registry=tool_registry)
    loaded = await tool_evolver.load_existing()
    if loaded:
        await bus.emit("evolution.tools_restored", {"count": loaded}, source="tool_evolver")

    # Initialize DemandSolver — persistent across cycles (tracks attempts/escalation)
    # Knowledge stored as .md files (LLM-native), not SQLite.
    from agos.evolution.demand_solver import DemandSolver
    demand_solver = DemandSolver(bus, audit, demand_collector, evo_memory)

    # Initialize SourcePatcher — self-modifying code engine
    # When the OS detects infrastructure problems (database locks, tool bugs),
    # it reads its own source code, asks the LLM to propose a fix, tests it,
    # applies it, and verifies the error goes away.
    source_patcher = None
    if llm_provider and demand_collector:
        try:
            from agos.evolution.source_patcher import SourcePatcher
            source_patcher = SourcePatcher(
                event_bus=bus, audit=audit,
                demand_collector=demand_collector,
                llm=llm_provider,
            )
            _logger.info("SourcePatcher initialized — self-modifying code engine active")
        except Exception as e:
            _logger.debug("SourcePatcher init failed: %s", e)

    # Initialize SelfImprovementLoop — unified evolution for all components
    _self_improvement = SelfImprovementLoop(
        event_bus=bus,
        audit=audit,
        tool_registry=tool_registry,
        daemon_manager=daemon_manager,
        os_agent=os_agent,
    )
    await bus.emit("evolution.self_improvement_init", {
        "evolvers": ["agent", "hand", "provider", "tool", "brain"],
    }, source="self_improvement_loop")

    _consecutive_llm_failures = 0
    _llm_backoff_until = 0

    while True:
        cycle += 1

        # ── Token conservation: circuit breaker for LLM quota exhaustion ──
        import time as _time
        if _time.time() < _llm_backoff_until:
            remaining = int(_llm_backoff_until - _time.time())
            if cycle % 20 == 0:  # Log occasionally, not every cycle
                _logger.info("LLM circuit breaker active — %ds until retry (saving tokens)", remaining)
            await asyncio.sleep(60)
            continue

        # Re-check LLM provider every 10 cycles (user may configure after boot)
        if cycle % 10 == 0 or (llm_provider and type(llm_provider).__name__ == "TemplateProvider"):
            try:
                from agos.evolution.providers.router import build_evolution_provider
                new_provider = await build_evolution_provider(_settings)
                if type(new_provider).__name__ != "TemplateProvider":
                    if type(llm_provider).__name__ == "TemplateProvider":
                        _logger.info("Evolution LLM upgraded: %s → %s",
                                     type(llm_provider).__name__, type(new_provider).__name__)
                    llm_provider = new_provider
            except Exception:
                pass

        # ── PRIORITY 0: Source patching (self-healing) — runs FIRST ──
        # Fix real infrastructure problems before wasting time on arxiv papers.
        # This is the most impactful evolution — fixing actual bugs.
        if source_patcher is not None:
            try:
                patch_results = await source_patcher.tick()
                for pr in patch_results:
                    if pr.get("action") == "applied":
                        _logger.info("SourcePatcher: fixed %s — %s", pr["file"], pr.get("rationale", "")[:60])
                    elif pr.get("action") == "verify":
                        _logger.info("SourcePatcher: verify %s — %s", pr["file"], "PASSED" if pr["verified"] else "FAILED")
                if patch_results:
                    await bus.emit("evolution.source_patcher_tick", {"cycle": cycle, "results": patch_results}, source="source_patcher")
            except Exception as e:
                _logger.debug("SourcePatcher error: %s", e)

        # ── PRIORITY 0.5: Demand-driven reasoning — solve real problems before arxiv ──
        if demand_collector and demand_collector.has_demands() and llm_provider:
            try:
                ds_result = await demand_solver.tick(
                    llm=llm_provider,
                    source_patcher=source_patcher,
                    tool_evolver=tool_evolver,
                )
                any_action = ds_result.get("solved", 0) + ds_result.get("principles", 0) + ds_result.get("patched", 0) + ds_result.get("tools_created", 0)
                if any_action > 0:
                    _logger.info("DemandSolver: solved=%d principles=%d patched=%d tools=%d told_user=%d",
                                 ds_result.get("solved", 0), ds_result.get("principles", 0),
                                 ds_result.get("patched", 0), ds_result.get("tools_created", 0),
                                 ds_result.get("told_user", 0))
                # Track LLM success — reset circuit breaker
                if any_action > 0:
                    _consecutive_llm_failures = 0
                elif ds_result.get("skipped", 0) > 0:
                    _consecutive_llm_failures += 1
            except Exception as e:
                _logger.warning("DemandSolver error: %s", e, exc_info=True)
                if "403" in str(e) or "429" in str(e) or "limit" in str(e).lower():
                    _consecutive_llm_failures += 3  # Fast-track circuit breaker for quota errors

        # ── Circuit breaker: if 5+ consecutive LLM failures, back off 10 min ──
        if _consecutive_llm_failures >= 5:
            _llm_backoff_until = _time.time() + 600  # 10 min
            _logger.warning("LLM circuit breaker TRIPPED (%d failures) — sleeping 10 min to save tokens",
                           _consecutive_llm_failures)
            _consecutive_llm_failures = 0
            await asyncio.sleep(600)
            continue

        # ── PRIORITY 0.7: Evolution Agent for impasses ──
        if (demand_collector and llm_provider
                and cycle % 3 == 0
                and type(llm_provider).__name__ != "TemplateProvider"):
            all_demands = demand_collector.top_demands(limit=10, include_all=True)
            impasse_demands = [d for d in all_demands
                               if (d.attempts >= 2 or d.priority >= 0.8)
                               and d.status not in ("resolved", "escalated")]
            _logger.info("Evolution Agent check: cycle=%d all_demands=%d impasse=%d llm=%s",
                         cycle, len(all_demands), len(impasse_demands),
                         type(llm_provider).__name__)
            if impasse_demands:
                try:
                    from agos.evolution.evolution_agent import EvolutionAgent
                    from agos.environment import EnvironmentProbe

                    demands_text = "\n".join(
                        f"[{d.kind}] {d.description[:100]} (attempts={d.attempts})"
                        for d in impasse_demands[:5]
                    )
                    evo_agent = EvolutionAgent(
                        event_bus=bus, audit=audit, llm=llm_provider,
                        tool_registry=tool_registry, source_patcher=source_patcher,
                        demand_collector=demand_collector, evo_memory=evo_memory,
                        tool_evolver=tool_evolver,
                    )
                    evo_result = await evo_agent.run({
                        "summary": f"{len(impasse_demands)} impasse demands",
                        "demand_count": len(impasse_demands),
                        "demands_text": demands_text,
                        "environment": EnvironmentProbe.summary()[:500],
                    })
                    _logger.info(
                        "Evolution Agent: %d tools, %d skills, %d rules, %d patches in %d turns",
                        len(evo_result.tools_created), len(evo_result.skills_created),
                        len(evo_result.rules_added), len(evo_result.patches_applied),
                        evo_result.turns_used,
                    )
                except Exception as e:
                    _logger.warning("Evolution Agent error: %s", e)

        # Persist evolution memory (insights, principles, reflections) every cycle
        if evo_memory is not None and evolution_state is not None:
            evolution_state.save_evolution_memory(evo_memory)
        if evolution_state is not None:
            evolution_state.increment_cycle()
            evolution_state.save(loom)

        # ── Tool evolution: demand-driven + discovery-based ──
        # PRIORITY 1: Generate tools directly from demand signals (no arxiv needed)
        if demand_collector.has_demands():
            try:
                for sig in demand_collector.top_demands(limit=3):
                    if sig.kind == "missing_tool":
                        # Extract tool name from context or description
                        tool_name = sig.context.get("tool", "")
                        if not tool_name:
                            # Try to extract from description
                            desc = sig.description.lower()
                            for kw in ["docker", "browser", "database", "email", "pdf",
                                       "image", "scrape", "translate", "deploy", "monitor"]:
                                if kw in desc:
                                    tool_name = kw
                                    break
                        # Validate: tool name must be a clean identifier
                        if tool_name and (len(tool_name) > 30
                                          or not tool_name.replace("_", "").replace("-", "").isalnum()):
                            _logger.debug("Rejected invalid tool name: %s", tool_name[:30])
                            tool_name = ""
                        if tool_name:
                            tool_evolver.request_tool(
                                name=tool_name,
                                description=sig.description,
                            )
                            _logger.info("Demand → tool request: %s", tool_name)
                            await bus.emit("evolution.demand_tool_request", {
                                "tool": tool_name,
                                "demand": sig.description[:100],
                            }, source="EvolutionEngine")
            except Exception as e:
                _logger.debug("Demand-to-tool error: %s", e)

        # PRIORITY 2: Tool evolution — ONLY when there are real demands (demand-gated)
        # Don't burn tokens on speculative evolution. No demands = sleep.
        run_tool_evo = demand_collector.has_demands()
        if run_tool_evo:
            try:
                tool_report = await tool_evolver.evolve_cycle(llm_provider=llm_provider)
                if tool_report["tools_deployed"] > 0:
                    _logger.info(
                        "Tool evolution: deployed %d new tools",
                        tool_report["tools_deployed"],
                    )
                    # Clear ALL related demands for deployed/activated tools
                    for tool_name in tool_report.get("deployed_names", []):
                        demand_collector.clear_resolved(f"tool_fail:{tool_name}")
                        demand_collector.clear_resolved(f"capability_gap:{tool_name}")
                        demand_collector.clear_resolved(f"missing_tool:{tool_name}")
                        demand_collector.clear_resolved("os_error:")
            except Exception as e:
                _logger.debug("Tool evolution error: %s", e)

        # SelfImprovementLoop + MetaEvolver removed: 32 cycles overnight, zero output.
        # Evolution is demand-driven: DemandSolver + ToolEvolver + SourcePatcher do real work.

        # ── Update artifact scores (federated scoring engine) ──
        if evolution_state is not None and demand_collector is not None:
            try:
                from agos.evolution.scoring import LocalScorer, update_archive_scores
                # Load or create scorer
                _scores_data = evolution_state.load_json("artifact_scores")
                _local_scorer = LocalScorer.from_dict(_scores_data) if _scores_data else LocalScorer()
                # Update scores based on demand resolution
                _local_scorer.update(demand_collector)
                # Push scores into DesignArchive for ALMA selection
                if design_archive is not None:
                    update_archive_scores(design_archive, _local_scorer)
                # Persist scores (used by sync + curator)
                evolution_state.save_json("artifact_scores", _local_scorer.to_dict())
            except Exception as e:
                _logger.debug("Scoring update error: %s", e)

        # ── Auto-share evolved artifacts with fleet ──
        pass  # auto-share removed — users share via git PRs

        # Persist demand signals across restarts
        if evolution_state is not None:
            evolution_state.save_json("demand_signals", demand_collector.to_dict())

        # Token-optimized sleep — like Unix interrupt-driven scheduling, not spin-polling.
        # The cheapest LLM call is the one you don't make.
        actionable = [d for d in demand_collector.top_demands(limit=5)
                      if d.status in ("active",) and d.should_attempt] if demand_collector else []
        if actionable:
            sleep_time = 60       # 1 min: fresh demands worth trying
        elif demand_collector and demand_collector.has_demands():
            sleep_time = 300      # 5 min: demands exist but all backing off or escalated
        else:
            sleep_time = 600      # 10 min: nothing to do, deep sleep
        await asyncio.sleep(sleep_time)
