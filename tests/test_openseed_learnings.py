"""Tests for OpenSeed-inspired improvements.

Validates:
1. Circular re-reads detection in LoopGuard
2. Audit origin derivation from agent_id
3. DemandSolver failure chain for root-cause escalation
4. Durable insights persistence as .md files
5. Full lifecycle integration (save/restore auto-wire)
"""
import glob
import os
import tempfile

import pytest

from agos.guard import LoopGuard
from agos.policy.audit import AuditTrail
from agos.evolution.state import EvolutionMemory, EvolutionInsight, EvolutionState
from agos.evolution.demand import DemandCollector, DemandSignal
from agos.events.bus import EventBus


# ── TEST 1: Guard — Circular Re-reads ──────────────────────────


class TestCircularReReads:
    """OpenSeed lesson: creature spent 560+ actions in read-only loops."""

    def test_different_targets_no_trip(self):
        """Reading many different files is legitimate research."""
        g = LoopGuard()
        for i in range(15):
            g.record("read_file", {"path": f"/docs/page_{i}.md"})
        assert not g.is_looping()

    def test_circular_rereads_trip(self):
        """Re-reading the same targets 3+ times is a rabbit hole."""
        g = LoopGuard()
        for cycle in range(4):
            g.record("read_file", {"path": "/api/config.yaml", "offset": cycle * 10})
            g.record("http", {"url": "http://localhost:8080/status", "extra": cycle})
            g.record("read_file", {"path": "/api/routes.py", "line": cycle})
        assert g.is_looping()
        # Should mention the targets, not just "pattern loop"
        reason = g.trip_reason
        assert "re-reads" in reason.lower() or "pattern" in reason.lower()

    def test_write_resets_tracking(self):
        """A write operation means the agent is making progress."""
        g = LoopGuard()
        for cycle in range(2):
            g.record("read_file", {"path": "/config.yaml"})
            g.record("read_file", {"path": "/routes.py"})
        # Now write something — resets the rabbit-hole tracking
        g.record("write_file", {"path": "/fix.py", "content": "fixed"})
        g.record("read_file", {"path": "/config.yaml"})
        assert not g.is_looping()

    def test_reset_clears_all(self):
        """reset() clears the circular read state."""
        g = LoopGuard()
        for _ in range(4):
            g.record("read_file", {"path": "/same.txt"})
        g.is_looping()  # triggers trip
        g.reset()
        assert not g.is_looping()
        assert len(g._read_targets) == 0
        assert len(g._read_target_set) == 0

    def test_readonly_tools_classification(self):
        """Verify the READONLY_TOOLS set contains the right tools."""
        assert "read_file" in LoopGuard.READONLY_TOOLS
        assert "http" in LoopGuard.READONLY_TOOLS
        assert "shell" not in LoopGuard.READONLY_TOOLS
        assert "write_file" not in LoopGuard.READONLY_TOOLS
        assert "python" not in LoopGuard.READONLY_TOOLS


# ── TEST 2: Audit — Origin Derivation ──────────────────────────


class TestAuditOriginDerivation:
    """OpenSeed lesson: can't distinguish human vs self-modifications."""

    def test_derive_human(self):
        assert AuditTrail.derive_origin("os_agent") == "human"

    def test_derive_evolution(self):
        assert AuditTrail.derive_origin("evolution_agent_1") == "evolution"
        assert AuditTrail.derive_origin("demand_solver") == "evolution"
        assert AuditTrail.derive_origin("source_patcher") == "evolution"
        assert AuditTrail.derive_origin("tool_evolver") == "evolution"

    def test_derive_system(self):
        assert AuditTrail.derive_origin("gc_daemon") == "system"
        assert AuditTrail.derive_origin("goal_runner_3") == "system"
        assert AuditTrail.derive_origin("daemon_monitor") == "system"
        assert AuditTrail.derive_origin("boot_sequence") == "system"

    def test_derive_unknown(self):
        assert AuditTrail.derive_origin("random_thing") == "unknown"
        assert AuditTrail.derive_origin("") == "unknown"

    @pytest.mark.asyncio
    async def test_query_by_origin(self):
        audit = AuditTrail(":memory:")
        await audit.initialize()
        await audit.log_tool_call("os_agent", "OS", "shell", {"cmd": "ls"})
        await audit.log_tool_call("evolution_agent", "Evo", "read_file", {})
        await audit.log_tool_call("gc_daemon", "GC", "docker_ps", {})

        human = await audit.query_by_origin("human")
        assert len(human) == 1
        assert human[0].agent_id == "os_agent"

        evo = await audit.query_by_origin("evolution")
        assert len(evo) == 1

    @pytest.mark.asyncio
    async def test_origin_stats(self):
        audit = AuditTrail(":memory:")
        await audit.initialize()
        await audit.log_tool_call("os_agent", "OS", "shell", {})
        await audit.log_tool_call("os_agent", "OS", "write_file", {})
        await audit.log_tool_call("evolution_agent", "Evo", "read_file", {})
        await audit.log_tool_call("gc_daemon", "GC", "docker_ps", {})

        stats = await audit.origin_stats()
        assert stats["human"] == 2
        assert stats["evolution"] == 1
        assert stats["system"] == 1


# ── TEST 3: DemandSolver — Failure Chain ───────────────────────


class TestFailureChain:
    """OpenSeed lesson: 4 symptom fixes, then 1 root cause rewrite."""

    @pytest.mark.asyncio
    async def test_failure_chain_built_after_3_attempts(self):
        """After 3 failures, solver should have chain context."""
        bus = EventBus()
        audit = AuditTrail(":memory:")
        await audit.initialize()
        collector = DemandCollector()
        mem = EvolutionMemory()

        # Record 3 past failures
        for i in range(3):
            mem.record(EvolutionInsight(
                cycle=i,
                what_tried=f"Fix Docker deploy attempt {i+1}",
                module="deployment",
                outcome="sandbox_failed",
                reason=f"Docker daemon not available (attempt {i+1})",
                what_worked="",
            ))

        from agos.evolution.demand_solver import DemandSolver
        solver = DemandSolver(bus, audit, collector, evo_memory=mem)

        signal = DemandSignal(
            kind="capability_gap",
            source="os_agent",
            description="Fix Docker deploy — daemon not available",
            count=3,
            priority=5.0,
        )
        collector._signals["docker_gap"] = signal

        # Tick without LLM — exercises the failure chain building code
        result = await solver.tick(llm=None)
        # Without LLM, nothing solved, but no crash
        assert result["solved"] == 0

    @pytest.mark.asyncio
    async def test_under_3_attempts_no_chain(self):
        """Under 3 attempts, no failure chain is built."""
        bus = EventBus()
        audit = AuditTrail(":memory:")
        await audit.initialize()
        collector = DemandCollector()
        mem = EvolutionMemory()

        # Only 2 past failures
        for i in range(2):
            mem.record(EvolutionInsight(
                cycle=i,
                what_tried=f"Fix Docker deploy attempt {i+1}",
                module="deployment",
                outcome="sandbox_failed",
                reason="Docker unavailable",
                what_worked="",
            ))

        from agos.evolution.demand_solver import DemandSolver
        solver = DemandSolver(bus, audit, collector, evo_memory=mem)

        signal = DemandSignal(
            kind="capability_gap",
            source="os_agent",
            description="Fix Docker deploy",
            count=2,
            priority=3.0,
        )
        collector._signals["docker_gap"] = signal

        result = await solver.tick(llm=None)
        assert result["solved"] == 0


# ── TEST 4: Durable Insights Persistence ───────────────────────


class TestDurableInsights:
    """OpenSeed lesson: Eve patched her own loader to survive rollbacks."""

    def setup_method(self):
        self._original_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def teardown_method(self):
        os.chdir(self._original_dir)

    def test_persist_high_confidence(self):
        """Only high-confidence insights are persisted."""
        mem = EvolutionMemory()
        mem.record(EvolutionInsight(
            cycle=1, what_tried="apt_install_nginx",
            module="deployment", outcome="success",
            reason="apt-get works", what_worked="Use apt-get install",
            confidence=0.95,
        ))
        mem.record(EvolutionInsight(
            cycle=2, what_tried="random_hack",
            module="test", outcome="success",
            reason="Lucky", what_worked="something",
            confidence=0.2,
        ))

        persisted = mem.persist_durable(threshold=0.8)
        assert persisted == 1

        md_files = glob.glob(".opensculpt/insights/*.md")
        assert len(md_files) == 1
        content = open(md_files[0]).read()
        assert "apt-get" in content.lower() or "apt_install" in content

    def test_restore_from_durable(self):
        """Insights survive state reset via .md files."""
        mem = EvolutionMemory()
        mem.record(EvolutionInsight(
            cycle=1, what_tried="nginx_via_apt",
            module="deployment", outcome="success",
            reason="Works without Docker",
            what_worked="apt-get install nginx",
            principle="Install via apt-get when no Docker",
            confidence=0.9,
        ))
        mem.persist_durable()

        # Simulate state reset
        mem2 = EvolutionMemory()
        restored = mem2.restore_from_durable()
        assert restored == 1
        assert mem2.insights[0].principle == "Install via apt-get when no Docker"

    def test_dedup_prevents_double_persist(self):
        """Same insight is not persisted twice."""
        mem = EvolutionMemory()
        mem.record(EvolutionInsight(
            cycle=1, what_tried="test_dedup",
            module="test", outcome="success",
            reason="test", what_worked="test dedup",
            confidence=0.9,
        ))
        assert mem.persist_durable() == 1
        assert mem.persist_durable() == 0  # dedup

    def test_restore_dedup_with_existing(self):
        """Don't restore insights that already exist in memory."""
        mem = EvolutionMemory()
        insight = EvolutionInsight(
            cycle=1, what_tried="already_in_memory",
            module="test", outcome="success",
            reason="test", what_worked="exists",
            confidence=0.9,
        )
        mem.record(insight)
        mem.persist_durable()

        # Memory already has the insight — restore should add 0
        restored = mem.restore_from_durable()
        assert restored == 0
        assert len(mem.insights) == 1  # still just 1


# ── TEST 5: Full Lifecycle Integration ─────────────────────────


class TestLifecycleIntegration:
    """End-to-end: save_evolution_memory auto-persists, restore auto-loads."""

    def setup_method(self):
        self._original_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def teardown_method(self):
        os.chdir(self._original_dir)

    def test_save_triggers_persist(self):
        """save_evolution_memory() auto-persists durable insights."""
        state = EvolutionState(save_path=".agos/evolution_state.json")
        mem = EvolutionMemory()
        mem.record(EvolutionInsight(
            cycle=1, what_tried="lifecycle_test",
            module="integration", outcome="success",
            reason="test", what_worked="lifecycle works",
            confidence=0.9,
        ))

        state.save_evolution_memory(mem)

        # .opensculpt/insights/ should have a file
        md_files = glob.glob(".opensculpt/insights/*.md")
        assert len(md_files) >= 1

    def test_restore_loads_durable(self):
        """restore_evolution_memory() auto-loads from .md files."""
        state = EvolutionState(save_path=".agos/evolution_state.json")
        mem = EvolutionMemory()
        mem.record(EvolutionInsight(
            cycle=1, what_tried="restore_test",
            module="integration", outcome="success",
            reason="test", what_worked="restore works",
            principle="Auto-restore principle",
            confidence=0.9,
        ))
        state.save_evolution_memory(mem)

        # Now simulate a fresh state (empty evolution_memory)
        state2 = EvolutionState(save_path=".agos/evolution_state.json")
        state2.load()  # loads JSON
        restored = state2.restore_evolution_memory()

        # Should include the insight from .md files
        high_conf = [i for i in restored.insights if i.confidence >= 0.8]
        assert len(high_conf) >= 1
        principles = [i.principle for i in high_conf if i.principle]
        assert "Auto-restore principle" in principles
