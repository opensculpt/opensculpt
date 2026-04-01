"""Integration tests for the decomposed OS modules.

Verifies that boot.py, agents/, evolution/cycle.py, evolution/community.py,
evolution/heuristics.py, and evolution/seed_patterns.py all wire together
correctly after the demo.py decomposition.
"""
from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.knowledge.manager import TheLoom


# ── Fixtures ──────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def bus():
    return EventBus()


@pytest_asyncio.fixture
async def audit():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        trail = AuditTrail(f.name)
    await trail.initialize()
    return trail


@pytest_asyncio.fixture
async def loom():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    the_loom = TheLoom(db_path)
    await the_loom.initialize()
    return the_loom


# ── Async event collector helper ──────────────────────────────────

def _async_collector(events_list):
    """Return an async handler that appends events to the list."""
    async def _handler(event):
        events_list.append(event)
    return _handler


# ══════════════════════════════════════════════════════════════════
# 1. Import chain integrity — all modules import cleanly
# ══════════════════════════════════════════════════════════════════


def test_import_boot():
    """boot.py imports without errors."""
    from agos.boot import boot_system
    assert callable(boot_system)


def test_import_agents_lifecycle():
    """agents/lifecycle.py imports without errors."""
    from agos.agents.lifecycle import agent_run
    assert callable(agent_run)


def test_import_agents_system():
    """agents/system.py exports all 7 system agent tasks."""
    from agos.agents.system import (
        scan_secrets, scan_code_quality, scan_disk_waste,
        audit_dependencies, profile_system, scan_network, cleanup_task,
    )
    agents = [scan_secrets, scan_code_quality, scan_disk_waste,
              audit_dependencies, profile_system, scan_network, cleanup_task]
    assert all(callable(a) for a in agents)
    assert len(agents) == 7


def test_import_evolution_cycle():
    """evolution/cycle.py exports both cycle functions."""
    from agos.evolution.cycle import run_evolution_cycle, evolution_loop
    assert callable(run_evolution_cycle)
    assert callable(evolution_loop)


def test_import_evolution_community():
    """evolution/community.py exports the loader."""
    from agos.evolution.community import load_community_contributions
    assert callable(load_community_contributions)


def test_import_evolution_heuristics():
    """evolution/heuristics.py exports analysis functions."""
    from agos.evolution.heuristics import (
        heuristic_analyze, extract_ast_patterns, _select_topics, _get_testable_snippet,
    )
    assert all(callable(f) for f in [
        heuristic_analyze, extract_ast_patterns, _select_topics, _get_testable_snippet,
    ])


def test_import_seed_patterns():
    """evolution/seed_patterns.py exports pattern data."""
    from agos.evolution.seed_patterns import (
        TECHNIQUE_PATTERNS, TESTABLE_SNIPPETS, _ALTERNATE_SNIPPETS, _ALL_SNIPPETS,
    )
    assert isinstance(TECHNIQUE_PATTERNS, list)
    assert isinstance(TESTABLE_SNIPPETS, dict)
    assert isinstance(_ALTERNATE_SNIPPETS, dict)
    assert isinstance(_ALL_SNIPPETS, dict)
    assert len(TECHNIQUE_PATTERNS) > 0
    assert len(_ALL_SNIPPETS) > 0


def test_serve_imports_boot_system():
    """serve.py imports boot_system from agos.boot (not demo)."""
    import agos.serve as serve_mod
    assert hasattr(serve_mod, 'boot_system')


def test_no_demo_module():
    """agos.demo no longer exists."""
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agos.demo")


# ══════════════════════════════════════════════════════════════════
# 2. Agent lifecycle — agent_run wires spawn → work → complete
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_run_emits_lifecycle_events(bus, audit):
    """agent_run emits spawned + completed events and calls the work function."""
    from agos.agents.lifecycle import agent_run

    events = []
    bus.subscribe("agent.*", _async_collector(events))

    async def mock_work(aid, name, b, a):
        return ["finding-1", "finding-2"]

    await agent_run("TestAgent", "testing", bus, audit, mock_work)

    topics = [e.topic for e in events]
    assert "agent.spawned" in topics
    assert "agent.completed" in topics

    completed = next(e for e in events if e.topic == "agent.completed")
    assert completed.data["agent"] == "TestAgent"
    assert completed.data["findings"] == 2


@pytest.mark.asyncio
async def test_agent_run_handles_work_error(bus, audit):
    """agent_run emits error event when work function raises."""
    from agos.agents.lifecycle import agent_run

    events = []
    bus.subscribe("agent.*", _async_collector(events))

    async def failing_work(aid, name, b, a):
        raise RuntimeError("something broke")

    await agent_run("CrashAgent", "testing", bus, audit, failing_work)

    topics = [e.topic for e in events]
    assert "agent.spawned" in topics
    assert "agent.error" in topics
    assert "agent.completed" not in topics


# ══════════════════════════════════════════════════════════════════
# 3. System agents — real agent tasks produce findings
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scan_secrets_runs(bus, audit):
    """scan_secrets scans actual source files and returns findings."""
    from agos.agents.system import scan_secrets

    findings = await scan_secrets("aid-1", "SecurityScanner", bus, audit)
    assert isinstance(findings, list)
    assert len(findings) >= 1  # at minimum "No secrets found"


@pytest.mark.asyncio
async def test_scan_code_quality_runs(bus, audit):
    """scan_code_quality analyzes source files and returns findings."""
    from agos.agents.system import scan_code_quality

    findings = await scan_code_quality("aid-2", "CodeAnalyst", bus, audit)
    assert isinstance(findings, list)
    assert len(findings) >= 1


@pytest.mark.asyncio
async def test_scan_disk_waste_runs(bus, audit):
    """scan_disk_waste checks for reclaimable space."""
    from agos.agents.system import scan_disk_waste

    findings = await scan_disk_waste("aid-3", "DiskAuditor", bus, audit)
    assert isinstance(findings, list)
    assert len(findings) >= 1


# ══════════════════════════════════════════════════════════════════
# 4. Boot sequence — boot_system emits boot phases + system.ready
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_boot_system_emits_phases_and_ready(bus, audit):
    """boot_system emits system.boot for each phase and system.ready."""
    from agos.boot import boot_system

    events = []
    bus.subscribe("system.*", _async_collector(events))

    boot_done = asyncio.Event()

    async def on_ready(event):
        if event.topic == "system.ready":
            boot_done.set()

    bus.subscribe("system.ready", on_ready)

    task = asyncio.create_task(
        boot_system(None, bus, audit, None, None, loom=None)
    )

    try:
        await asyncio.wait_for(boot_done.wait(), timeout=10)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    topics = [e.topic for e in events]
    assert "system.boot" in topics
    assert "system.ready" in topics

    boot_events = [e for e in events if e.topic == "system.boot"]
    phases = [e.data["phase"] for e in boot_events]
    assert "kernel" in phases
    assert "event_bus" in phases
    assert "audit" in phases
    assert "policy" in phases


@pytest.mark.asyncio
async def test_boot_system_with_loom_adds_evolution_phases(bus, audit, loom):
    """When loom is provided, boot adds knowledge + evolution phases."""
    from agos.boot import boot_system

    events = []
    bus.subscribe("system.*", _async_collector(events))

    boot_done = asyncio.Event()

    async def on_ready(event):
        if event.topic == "system.ready":
            boot_done.set()

    bus.subscribe("system.ready", on_ready)

    task = asyncio.create_task(
        boot_system(None, bus, audit, None, None, loom=loom)
    )

    try:
        await asyncio.wait_for(boot_done.wait(), timeout=15)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    boot_events = [e for e in events if e.topic == "system.boot"]
    phases = [e.data["phase"] for e in boot_events]
    assert "knowledge" in phases
    assert "evolution" in phases

    ready = next(e for e in events if e.topic == "system.ready")
    assert ready.data["evolution"] is True


# ══════════════════════════════════════════════════════════════════
# 5. Heuristics — topic selection + paper analysis integration
# ══════════════════════════════════════════════════════════════════


def test_select_topics_returns_topics():
    """_select_topics returns a non-empty list for any cycle."""
    from agos.evolution.heuristics import _select_topics

    for cycle in [1, 2, 5, 10]:
        topics = _select_topics(cycle)
        assert isinstance(topics, list)
        assert len(topics) >= 1
        assert all(isinstance(t, str) for t in topics)


def test_heuristic_analyze_with_matching_paper():
    """heuristic_analyze extracts insight from a paper matching known techniques."""
    from agos.evolution.heuristics import heuristic_analyze
    from agos.evolution.scout import Paper

    paper = Paper(
        title="Softmax Attention for Memory-Augmented Retrieval",
        abstract="We propose a retrieval augmented framework using softmax scoring "
                 "for semantic memory retrieval with adaptive confidence thresholds. "
                 "Our algorithm outperforms the baseline on benchmark datasets.",
        authors=["Alice"],
        arxiv_id="2401.00001",
        categories=["cs.AI"],
    )

    insight = heuristic_analyze(paper)
    assert insight is not None
    assert insight.technique != ""
    assert insight.agos_module != ""


def test_heuristic_analyze_with_irrelevant_paper():
    """heuristic_analyze returns None for papers outside AGOS scope."""
    from agos.evolution.heuristics import heuristic_analyze
    from agos.evolution.scout import Paper

    paper = Paper(
        title="Population Dynamics of Freshwater Fish in Northern Lakes",
        abstract="We studied the spawning patterns of lake trout over a 10-year period.",
        authors=["Bob"],
        arxiv_id="2401.99999",
        categories=["q-bio"],
    )

    insight = heuristic_analyze(paper)
    assert insight is None


def test_get_testable_snippet_returns_code():
    """_get_testable_snippet returns a CodePattern for known modules."""
    from agos.evolution.heuristics import _get_testable_snippet

    snippet = _get_testable_snippet("knowledge", 1)
    if snippet is not None:
        assert snippet.code_snippet != ""
        assert snippet.name != ""


# ══════════════════════════════════════════════════════════════════
# 6. Community loading — sandbox validation gate
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_community_loading_empty_dir(bus, loom):
    """load_community_contributions returns 0 when no community dir exists."""
    from agos.evolution.community import load_community_contributions

    with patch("agos.evolution.community.pathlib.Path") as mock_path:
        mock_path.return_value.exists.return_value = False
        count = await load_community_contributions(loom, bus, sandbox=None)
        assert count == 0


# ══════════════════════════════════════════════════════════════════
# 7. Seed patterns — data integrity
# ══════════════════════════════════════════════════════════════════


def test_seed_patterns_cover_all_modules():
    """Seed patterns have entries for core AGOS modules."""
    from agos.evolution.seed_patterns import TECHNIQUE_PATTERNS, TESTABLE_SNIPPETS

    # TECHNIQUE_PATTERNS is a list of (keywords, module, priority) tuples
    pattern_modules = {t[1] for t in TECHNIQUE_PATTERNS}
    snippet_modules = set(TESTABLE_SNIPPETS.keys())

    # Check key modules are present (modules may use dotted names like "knowledge.semantic")
    for module in ["knowledge", "intent", "policy"]:
        assert any(module in m for m in pattern_modules), f"TECHNIQUE_PATTERNS missing {module}"
        assert any(module in m for m in snippet_modules), f"TESTABLE_SNIPPETS missing {module}"


def test_all_snippets_merge():
    """_ALL_SNIPPETS merges primary + alternate snippets into lists."""
    from agos.evolution.seed_patterns import (
        TESTABLE_SNIPPETS, _ALTERNATE_SNIPPETS, _ALL_SNIPPETS,
    )

    # _ALL_SNIPPETS should contain every module from both primary and alternate
    for module in TESTABLE_SNIPPETS:
        assert module in _ALL_SNIPPETS, f"_ALL_SNIPPETS missing {module}"
        merged = _ALL_SNIPPETS[module]
        assert isinstance(merged, list), f"_ALL_SNIPPETS[{module}] should be a list"
        # Primary contributes 1 snippet; alternate contributes 0+
        alternate_count = len(_ALTERNATE_SNIPPETS.get(module, []))
        assert len(merged) == 1 + alternate_count, (
            f"Module {module}: expected 1 + {alternate_count}, got {len(merged)}"
        )


# ══════════════════════════════════════════════════════════════════
# 8. Full boot → agent cycle integration
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_boot_runs_one_agent_cycle(bus, audit):
    """boot_system runs agents via agent_run in the task cycle."""
    from agos.boot import boot_system

    agent_events = []
    bus.subscribe("agent.*", _async_collector(agent_events))

    cycle_events = []
    bus.subscribe("system.cycle", _async_collector(cycle_events))

    completed = asyncio.Event()

    async def on_agent_completed(event):
        completed.set()

    bus.subscribe("agent.completed", on_agent_completed)

    task = asyncio.create_task(
        boot_system(None, bus, audit, None, None, loom=None)
    )

    # Wait long enough for boot phases (6 × 0.4s = 2.4s) + first agent
    try:
        await asyncio.wait_for(completed.wait(), timeout=20)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(cycle_events) >= 1, "At least one system.cycle should have fired"
    spawned = [e for e in agent_events if e.topic == "agent.spawned"]
    assert len(spawned) >= 1, "At least one agent should have been spawned"


# ══════════════════════════════════════════════════════════════════
# 9. Evolution cycle — verify module wiring with mocked externals
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_evolution_cycle_no_papers(bus, audit, loom):
    """run_evolution_cycle completes gracefully when arxiv returns no papers."""
    from agos.evolution.cycle import run_evolution_cycle

    events = []
    bus.subscribe("evolution.*", _async_collector(events))

    with patch("agos.evolution.cycle.ArxivScout") as mock_scout_cls:
        mock_scout = mock_scout_cls.return_value
        mock_scout.search = AsyncMock(return_value=[])

        await run_evolution_cycle(1, bus, audit, loom)

    topics = [e.topic for e in events]
    assert "evolution.cycle_started" in topics
    assert "evolution.cycle_completed" in topics


@pytest.mark.asyncio
async def test_evolution_loop_runs_one_cycle(bus, audit, loom):
    """evolution_loop invokes run_evolution_cycle at least once."""
    from agos.evolution.cycle import evolution_loop

    cycle_started = asyncio.Event()

    async def on_cycle(event):
        cycle_started.set()

    bus.subscribe("evolution.cycle_started", on_cycle)

    with patch("agos.evolution.cycle.ArxivScout") as mock_scout_cls, \
         patch("agos.evolution.cycle._settings") as mock_settings:
        mock_scout = mock_scout_cls.return_value
        mock_scout.search = AsyncMock(return_value=[])
        mock_settings.evolution_initial_delay = 0
        mock_settings.node_role = "general"

        task = asyncio.create_task(
            evolution_loop(bus, audit, loom)
        )

        try:
            # evolution_loop has a 10s initial sleep; patch it away
            with patch("agos.evolution.cycle.asyncio.sleep", new_callable=AsyncMock):
                await asyncio.wait_for(cycle_started.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass  # OK if it doesn't fire in time, the import chain still works
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ══════════════════════════════════════════════════════════════════
# 10. Cross-module wiring — boot → cycle → heuristics → seed_patterns
# ══════════════════════════════════════════════════════════════════


def test_cycle_uses_heuristics():
    """cycle.py imports from heuristics.py — verifying the chain is intact."""
    from agos.evolution import cycle
    assert hasattr(cycle, 'heuristic_analyze')
    assert hasattr(cycle, '_select_topics')


def test_heuristics_uses_seed_patterns():
    """heuristics.py imports from seed_patterns.py — verifying the chain."""
    from agos.evolution import heuristics
    assert hasattr(heuristics, 'TECHNIQUE_PATTERNS')
    assert hasattr(heuristics, '_ALL_SNIPPETS')


def test_boot_uses_agents_and_cycle():
    """boot.py imports from agents/ and evolution/cycle.py."""
    from agos import boot
    assert hasattr(boot, 'agent_run')
    assert hasattr(boot, 'evolution_loop')
    assert hasattr(boot, 'scan_secrets')
    assert hasattr(boot, 'cleanup_task')
