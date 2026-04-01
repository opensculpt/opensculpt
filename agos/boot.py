"""OS boot sequence — agents + evolution in parallel.

Boots the kernel, restores state, launches system agents and evolution loop.
"""
from __future__ import annotations

import asyncio
import logging

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry
from agos.evolution.state import EvolutionState
from agos.evolution.meta import MetaEvolver
from agos.evolution.codegen import load_evolved_strategies
from agos.config import settings as _settings
from agos.evolution.community import load_community_contributions
from agos.evolution.cycle import evolution_loop
from agos.agents.lifecycle import agent_run
from agos.agents.system import (
    scan_secrets, scan_code_quality, scan_disk_waste,
    audit_dependencies, profile_system, scan_network, cleanup_task,
)
from agos.agents.security import scan_vulnerabilities

_logger = logging.getLogger(__name__)


async def boot_system(runtime, bus: EventBus, audit: AuditTrail,
                      policy_engine, tracer, loom=None,
                      evolution_state: EvolutionState | None = None,
                      meta_evolver: MetaEvolver | None = None,
                      demand_collector=None) -> None:
    """Main loop: boot, then agents + evolution running simultaneously."""
    boot_phases = [
        ("kernel", "Agent runtime initialized"),
        ("event_bus", "Pub/sub event bus online"),
        ("audit", "Immutable audit trail active"),
        ("policy", "Policy engine loaded"),
        ("triggers", "Schedulers ready"),
        ("network", "Network stack initialized"),
    ]
    if loom:
        boot_phases.append(("knowledge", "TheLoom knowledge substrate online"))
        boot_phases.append(("evolution", "Self-evolution engine armed"))

    for phase, detail in boot_phases:
        await bus.emit("system.boot", {"phase": phase, "detail": detail}, source="kernel")
        await audit.record(AuditEntry(
            agent_name="kernel", action="boot", detail=f"[{phase}] {detail}", success=True,
        ))
        await asyncio.sleep(0.4)

    # ── Restore persisted evolution state ──
    if evolution_state is not None and loom is not None:
        if evolution_state.load():
            changes = evolution_state.restore_parameters(loom)
            for change in changes:
                await bus.emit("evolution.state_restored", {"change": change}, source="kernel")
            await bus.emit("evolution.state_loaded", {
                "cycles": evolution_state.data.cycles_completed,
                "strategies": len(evolution_state.data.strategies_applied),
                "patterns": len(evolution_state.data.discovered_patterns),
                "restored_params": len(changes),
            }, source="kernel")
            await audit.record(AuditEntry(
                agent_name="kernel", action="state_restore",
                detail=f"Restored {evolution_state.data.cycles_completed} cycles, {len(changes)} params",
                success=True,
            ))
        else:
            await bus.emit("evolution.state_fresh", {"detail": "No prior state found"}, source="kernel")

    # ── Restore meta-evolution state ──
    if meta_evolver is not None and evolution_state is not None:
        restored = evolution_state.restore_meta_state(meta_evolver)
        if restored > 0:
            await bus.emit("meta.state_restored", {
                "genomes_restored": restored,
                "total_genomes": len(meta_evolver.all_genomes()),
            }, source="kernel")
            # Re-apply persisted parameter mutations to live objects
            for genome in meta_evolver.all_genomes():
                for param in genome.params:
                    if param.current is not None and param.current != param.default:
                        from agos.evolution.meta import Mutation
                        m = Mutation(
                            component=genome.component,
                            param_name=param.name,
                            old_value=param.default,
                            new_value=param.current,
                            reason="Restored from persisted state",
                        )
                        await meta_evolver._apply_mutation(
                            m, loom=loom, policy_engine=policy_engine,
                            event_bus=bus, tracer=tracer,
                        )
            await bus.emit("meta.params_reapplied", {
                "genomes_with_mutations": restored,
            }, source="kernel")

    # ── Load evolved code from previous runs ──
    evolved_strategies = load_evolved_strategies()
    if evolved_strategies:
        await bus.emit("evolution.code_loaded", {
            "count": len(evolved_strategies),
            "files": [path.split("/")[-1] for path, _ in evolved_strategies],
        }, source="kernel")
        await audit.record(AuditEntry(
            agent_name="kernel", action="evolved_code_loaded",
            detail=f"Loaded {len(evolved_strategies)} evolved strategy modules",
            success=True,
        ))

    # ── Load community contributions (with sandbox validation) ──
    # Restore evolution memory so community merge can populate it
    _boot_evo_memory = None
    if evolution_state is not None:
        _boot_evo_memory = evolution_state.restore_evolution_memory()
    if loom is not None:
        from agos.evolution.sandbox import Sandbox as _BootSandbox
        n = await load_community_contributions(
            loom, bus, sandbox=_BootSandbox(timeout=10),
            evo_memory=_boot_evo_memory,
        )
        # Persist merged memory back
        if _boot_evo_memory is not None and evolution_state is not None:
            evolution_state.save_evolution_memory(_boot_evo_memory)
        if n > 0:
            await bus.emit("evolution.community_loaded", {"strategies": n}, source="kernel")

    # ── Initialize ALMA design archive ──
    design_archive = None
    if evolution_state is not None:
        design_archive = evolution_state.restore_design_archive()
        if design_archive.entries:
            await bus.emit("evolution.archive_restored", {
                "designs": len(design_archive.entries),
                "best_fitness": max(e.current_fitness for e in design_archive.entries),
            }, source="kernel")

    # ── Initialize LLM provider (auto-detects from setup.json, then local LLMs) ──
    # The router reads setup.json directly — no need to mangle anthropic_api_key here.
    # Only set anthropic_api_key if setup.json explicitly has an "anthropic" provider.
    if not _settings.anthropic_api_key:
        try:
            from agos.setup_store import load_setup
            import os as _os
            for ws in [str(_settings.workspace_dir), _os.path.join(_os.getcwd(), ".opensculpt")]:
                if _os.path.isdir(ws):
                    data = load_setup(ws)
                    providers = data.get("providers", {})
                    # Only set anthropic_api_key if the provider is actually Anthropic
                    anthropic_cfg = providers.get("anthropic", {})
                    if anthropic_cfg.get("enabled") and anthropic_cfg.get("api_key"):
                        _settings.anthropic_api_key = anthropic_cfg["api_key"]
                        _logger.info("Loaded Anthropic API key from setup store")
                        break
        except Exception:
            pass
    try:
        from agos.evolution.providers.router import build_evolution_provider
        llm_provider = await build_evolution_provider(_settings)
        await bus.emit("evolution.llm_ready", {
            "provider": getattr(llm_provider, "name", "unknown"),
        }, source="kernel")
    except Exception as e:
        _logger.info("LLM provider init failed, evolution uses heuristics: %s", e)
        llm_provider = None

    await bus.emit("system.ready", {"version": "0.1.0", "evolution": loom is not None}, source="kernel")

    # Launch knowledge consolidation in background (compresses old memories)
    if loom:
        asyncio.create_task(_consolidation_loop(loom, bus, audit))

    # Launch evolution in background
    if loom:
        asyncio.create_task(evolution_loop(
            bus, audit, loom,
            evolution_state=evolution_state,
            meta_evolver=meta_evolver,
            policy_engine=policy_engine,
            tracer=tracer,
            runtime=runtime,
            design_archive=design_archive,
            llm_provider=llm_provider,
            demand_collector=demand_collector,
        ))

    # Agent task cycle
    cycle = 0
    while True:
        cycle += 1
        await bus.emit("system.cycle", {"cycle": cycle}, source="scheduler")

        tasks = [
            ("SecurityScanner", "secret detection", scan_secrets),
            ("VulnScanner", "vulnerability scanning", scan_vulnerabilities),
            ("SystemProfiler", "resource profiling", profile_system),
            ("CodeAnalyst", "quality analysis", scan_code_quality),
            ("DiskAuditor", "storage analysis", scan_disk_waste),
            ("NetworkSentinel", "connectivity check", scan_network),
            ("DepAuditor", "dependency audit", audit_dependencies),
        ]

        if cycle % 3 == 0:
            tasks.append(("CacheCleaner", "cache cleanup", cleanup_task))

        for agent_name, role, work_fn in tasks:
            await agent_run(agent_name, role, bus, audit, work_fn)
            await asyncio.sleep(2)

        await bus.emit("system.cycle_complete", {"cycle": cycle, "agents": len(tasks)}, source="scheduler")
        await asyncio.sleep(15)


async def _consolidation_loop(loom, bus: EventBus, audit: AuditTrail) -> None:
    """Background task: compress old memories into patterns (like sleep).

    Runs every hour. Finds old episodic events, clusters them into
    semantic summaries, extracts patterns, and prunes stale data.
    This is what makes the OS get smarter over time without drowning in noise.
    """
    from agos.knowledge.consolidator import Consolidator
    consolidator = Consolidator(loom.episodic, loom.semantic, loom.graph)
    await asyncio.sleep(1800)  # First run after 30 minutes
    while True:
        try:
            report = await consolidator.consolidate(older_than_hours=24, min_cluster_size=3)
            patterns = await consolidator.extract_patterns(limit=20)
            strengthened = await consolidator.strengthen_important(threshold=5)
            await bus.emit("knowledge.consolidated", {
                "summaries": report.summaries_created,
                "pruned": report.events_pruned,
                "patterns": len(patterns),
                "strengthened": strengthened,
            }, source="consolidator")
            # Also decay MemoryNotes (forgetting curve)
            if hasattr(loom, 'notes'):
                await loom.notes.decay(factor=0.95)
        except Exception as e:
            _logger.debug("Consolidation cycle failed: %s", e)
        await asyncio.sleep(3600)  # Run every hour
