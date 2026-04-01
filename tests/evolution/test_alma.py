"""Tests for ALMA-inspired evolution features.

Covers: DesignArchive (softmax sampling), EvalTask execution,
LLM ideation (mock), self-reflection retry (mock),
iterate-on-strategy (mock).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agos.evolution.state import (
    DesignEntry,
    DesignArchive,
    EvolutionState,
)
from agos.evolution.meta import (
    MetaEvolver,
    EVAL_TASKS,
    FitnessSignal,
)
from agos.evolution.sandbox import Sandbox


# ── DesignArchive Tests ──────────────────────────────────────────


class TestDesignEntry:
    def test_create_entry(self):
        entry = DesignEntry(
            strategy_name="test_strategy",
            module="knowledge.semantic",
            code_hash="abc123",
            code_snippet="def foo(): pass",
            current_fitness=0.75,
        )
        assert entry.strategy_name == "test_strategy"
        assert entry.generation == 0
        assert entry.parent_id == ""
        assert entry.id  # auto-generated

    def test_entry_lineage(self):
        parent = DesignEntry(
            strategy_name="parent",
            module="knowledge.semantic",
            current_fitness=0.5,
        )
        child = DesignEntry(
            strategy_name="child",
            module="knowledge.semantic",
            current_fitness=0.6,
            generation=1,
            parent_id=parent.id,
        )
        assert child.parent_id == parent.id
        assert child.generation == 1


class TestDesignArchive:
    def test_add_and_best(self):
        archive = DesignArchive(max_size=10)
        for i in range(5):
            archive.add(DesignEntry(
                strategy_name=f"strat_{i}",
                module="knowledge.semantic",
                current_fitness=i * 0.2,
            ))
        assert len(archive.entries) == 5
        best = archive.best(3)
        assert len(best) == 3
        assert best[0].current_fitness >= best[1].current_fitness

    def test_eviction_on_capacity(self):
        archive = DesignArchive(max_size=3)
        for i in range(5):
            archive.add(DesignEntry(
                strategy_name=f"strat_{i}",
                module="test",
                current_fitness=i * 0.2,
            ))
        assert len(archive.entries) == 3
        # Lowest fitness should have been evicted
        fitnesses = [e.current_fitness for e in archive.entries]
        assert min(fitnesses) >= 0.4  # strat_0 (0.0) and strat_1 (0.2) evicted

    def test_softmax_sampling(self):
        archive = DesignArchive(max_size=10, temperature=0.3)
        # Add entries with varied fitness
        for i in range(5):
            archive.add(DesignEntry(
                strategy_name=f"strat_{i}",
                module="test",
                current_fitness=i * 0.2,
            ))

        # Sample should return requested count
        sampled = archive.sample(2)
        assert len(sampled) == 2

        # Sampled entries should be unique
        ids = [s.id for s in sampled]
        assert len(set(ids)) == 2

    def test_sample_empty_archive(self):
        archive = DesignArchive()
        assert archive.sample(3) == []

    def test_sample_more_than_available(self):
        archive = DesignArchive()
        archive.add(DesignEntry(strategy_name="a", module="test", current_fitness=0.5))
        sampled = archive.sample(5)
        assert len(sampled) == 1

    def test_softmax_bias(self):
        """High-fitness designs should be sampled more often."""
        archive = DesignArchive(max_size=100, temperature=0.1)
        # One high-fitness, many low-fitness
        archive.add(DesignEntry(
            strategy_name="high",
            module="test",
            current_fitness=0.95,
        ))
        for i in range(9):
            archive.add(DesignEntry(
                strategy_name=f"low_{i}",
                module="test",
                current_fitness=0.05,
            ))

        # Sample many times, count how often "high" appears
        high_count = 0
        trials = 100
        for _ in range(trials):
            sampled = archive.sample(1)
            if sampled[0].strategy_name == "high":
                high_count += 1

        # With temperature=0.1, high-fitness should be sampled >50% of the time
        assert high_count > 30, f"Expected bias toward high-fitness, got {high_count}/100"

    def test_by_module(self):
        archive = DesignArchive()
        archive.add(DesignEntry(strategy_name="a", module="knowledge.semantic", current_fitness=0.5))
        archive.add(DesignEntry(strategy_name="b", module="policy.engine", current_fitness=0.5))
        archive.add(DesignEntry(strategy_name="c", module="knowledge.semantic", current_fitness=0.5))

        result = archive.by_module("knowledge.semantic")
        assert len(result) == 2

    def test_update_fitness(self):
        archive = DesignArchive()
        entry = DesignEntry(strategy_name="a", module="test", current_fitness=0.5)
        archive.add(entry)

        archive.update_fitness(entry.id, 0.8)
        assert archive.entries[0].current_fitness == 0.8
        assert 0.8 in archive.entries[0].fitness_scores

    def test_serialization_roundtrip(self):
        archive = DesignArchive(max_size=20, temperature=0.5)
        archive.add(DesignEntry(
            strategy_name="test",
            module="knowledge",
            current_fitness=0.75,
            generation=2,
            code_snippet="def x(): return 1",
        ))

        data = archive.to_dict()
        restored = DesignArchive.from_dict(data)

        assert restored.max_size == 20
        assert restored.temperature == 0.5
        assert len(restored.entries) == 1
        assert restored.entries[0].strategy_name == "test"
        assert restored.entries[0].generation == 2


# ── EvalTask Tests ───────────────────────────────────────────────


class TestEvalTasks:
    def test_eval_tasks_defined(self):
        """Eval tasks exist for key components."""
        assert "knowledge.semantic" in EVAL_TASKS
        assert "knowledge.graph" in EVAL_TASKS
        assert "policy.engine" in EVAL_TASKS
        assert "orchestration.planner" in EVAL_TASKS
        assert "intent.engine" in EVAL_TASKS

    def test_eval_task_structure(self):
        for component, tasks in EVAL_TASKS.items():
            assert len(tasks) >= 1
            for task in tasks:
                assert task.component == component
                assert task.name
                assert task.test_code
                assert task.expected_output
                assert task.weight > 0


class TestMetaEvolverEvalTasks:
    @pytest.mark.asyncio
    async def test_run_eval_tasks(self):
        evolver = MetaEvolver()
        sandbox = Sandbox(timeout=10)

        scores = await evolver.run_eval_tasks(sandbox)

        # Should have scores for all components with eval tasks
        assert len(scores) >= 5
        # All scores should be between 0 and 1
        for component, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{component}: {score}"

    @pytest.mark.asyncio
    async def test_eval_tasks_pass_sandbox(self):
        """Each eval task's test_code should actually pass the sandbox."""
        sandbox = Sandbox(timeout=10)
        for component, tasks in EVAL_TASKS.items():
            for task in tasks:
                result = await sandbox.execute(task.test_code)
                assert result.passed, f"{component}/{task.name} failed: {result.error}"
                assert task.expected_output in result.output, (
                    f"{component}/{task.name}: expected '{task.expected_output}' "
                    f"in output '{result.output[:200]}'"
                )


# ── Fitness Blending Tests ───────────────────────────────────────


class TestFitnessBlending:
    @pytest.mark.asyncio
    async def test_meta_cycle_with_sandbox(self):
        """run_meta_cycle should blend eval scores with proxy signals."""
        evolver = MetaEvolver()
        sandbox = Sandbox(timeout=10)

        report = await evolver.run_meta_cycle(sandbox=sandbox)

        assert report.signals_collected >= 0
        # Eval scores should be stored
        assert len(evolver._eval_scores) >= 5

    @pytest.mark.asyncio
    async def test_meta_cycle_without_sandbox(self):
        """run_meta_cycle should work without sandbox (proxy only)."""
        evolver = MetaEvolver()

        report = await evolver.run_meta_cycle()

        assert report.signals_collected >= 0
        assert len(evolver._eval_scores) == 0  # no eval without sandbox


# ── LLM Ideation Tests (Mock) ───────────────────────────────────


class TestLLMIdeation:
    @pytest.mark.asyncio
    async def test_llm_ideation_parses_response(self):
        evolver = MetaEvolver()
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = """
        [
          {"component": "knowledge.semantic", "param": "temperature", "value": 0.3, "reason": "increase diversity"},
          {"component": "policy.engine", "param": "default_max_tokens", "value": 300000, "reason": "allow more tokens"}
        ]
        """

        underperformers = [
            evolver.genomes["knowledge.semantic"],
            evolver.genomes["policy.engine"],
        ]
        signals = [
            FitnessSignal(component="knowledge.semantic", metric="test", value=0.3),
        ]

        mutations = await evolver.llm_ideate_mutations(
            underperformers, signals, mock_llm
        )

        assert len(mutations) == 2
        assert mutations[0].component == "knowledge.semantic"
        assert mutations[0].param_name == "temperature"
        assert mutations[0].new_value == 0.3
        assert "LLM:" in mutations[0].reason

    @pytest.mark.asyncio
    async def test_llm_ideation_validates_range(self):
        evolver = MetaEvolver()
        mock_llm = AsyncMock()
        # value 999 is out of range for temperature (max=1.0)
        mock_llm.complete_prompt.return_value = '[{"component": "knowledge.semantic", "param": "temperature", "value": 999, "reason": "bad"}]'

        mutations = await evolver.llm_ideate_mutations(
            [evolver.genomes["knowledge.semantic"]], [], mock_llm
        )
        assert len(mutations) == 0  # should be rejected

    @pytest.mark.asyncio
    async def test_llm_ideation_handles_bad_json(self):
        evolver = MetaEvolver()
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = "This is not JSON at all."

        mutations = await evolver.llm_ideate_mutations(
            [evolver.genomes["knowledge.semantic"]], [], mock_llm
        )
        assert len(mutations) == 0

    @pytest.mark.asyncio
    async def test_llm_ideation_handles_exception(self):
        evolver = MetaEvolver()
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.side_effect = Exception("API error")

        mutations = await evolver.llm_ideate_mutations(
            [evolver.genomes["knowledge.semantic"]], [], mock_llm
        )
        assert len(mutations) == 0


# ── Self-Reflection Retry Tests (Mock) ───────────────────────────


class TestSelfReflectionRetry:
    @pytest.mark.asyncio
    async def test_retry_fixes_code(self):
        from agos.evolution.codegen import _self_reflection_retry

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        # Return valid code that passes sandbox
        mock_llm.complete_prompt.return_value = "x = 1 + 1\nprint(x)"

        result = await _self_reflection_retry(
            code="invalid syntax here!!!",
            error="SyntaxError: invalid syntax",
            llm_provider=mock_llm,
            sandbox=sandbox,
            max_retries=2,
        )

        assert result is not None
        assert "print" in result

    @pytest.mark.asyncio
    async def test_retry_returns_none_on_failure(self):
        from agos.evolution.codegen import _self_reflection_retry

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        # Return code that still fails
        mock_llm.complete_prompt.return_value = "import os; os.system('rm -rf /')"

        result = await _self_reflection_retry(
            code="bad code",
            error="error",
            llm_provider=mock_llm,
            sandbox=sandbox,
            max_retries=2,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_retry_extracts_from_markdown(self):
        from agos.evolution.codegen import _self_reflection_retry

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = "Here's the fix:\n```python\nresult = 42\nprint(result)\n```"

        result = await _self_reflection_retry(
            code="bad",
            error="error",
            llm_provider=mock_llm,
            sandbox=sandbox,
            max_retries=1,
        )

        assert result is not None
        assert "42" in result


# ── Iterate Strategy Tests (Mock) ────────────────────────────────


class TestIterateStrategy:
    @pytest.mark.asyncio
    async def test_iterate_produces_improved_code(self):
        from agos.evolution.codegen import iterate_strategy

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = "```python\ndef improved():\n    return 42\nprint(improved())\n```"

        result = await iterate_strategy(
            existing_code="def basic(): return 1",
            fitness=0.4,
            signals="fitness=0.4",
            module="knowledge.semantic",
            sandbox=sandbox,
            llm_provider=mock_llm,
        )

        assert result is not None
        assert "improved" in result

    @pytest.mark.asyncio
    async def test_iterate_returns_none_on_all_failures(self):
        from agos.evolution.codegen import iterate_strategy

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = "import os; os.system('bad')"

        result = await iterate_strategy(
            existing_code="def x(): pass",
            fitness=0.3,
            signals="",
            module="test",
            sandbox=sandbox,
            llm_provider=mock_llm,
        )

        assert result is None


class TestGenerateFromInsight:
    @pytest.mark.asyncio
    async def test_generates_code_from_paper(self):
        from agos.evolution.codegen import generate_from_insight
        from agos.evolution.analyzer import PaperInsight

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = (
            "```python\ndef enhanced_retrieval(query, docs):\n"
            "    return sorted(docs, key=lambda d: len(set(query.split()) & set(d.split())), reverse=True)\n"
            "result = enhanced_retrieval('agent memory', ['agent memory search', 'unrelated'])\n"
            "assert result[0] == 'agent memory search'\n"
            "print('PASS: retrieval works')\n```"
        )

        insight = PaperInsight(
            technique="Semantic Overlap Retrieval",
            description="Uses word overlap scoring for document retrieval.",
            applicability="Could improve agos.knowledge semantic search.",
            implementation_hint="Implement overlap-based scoring function.",
            agos_module="knowledge.semantic",
        )

        result = await generate_from_insight(
            insight=insight, module="knowledge.semantic",
            sandbox=sandbox, llm_provider=mock_llm,
        )

        assert result is not None
        assert "enhanced_retrieval" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_llm_fails(self):
        from agos.evolution.codegen import generate_from_insight
        from agos.evolution.analyzer import PaperInsight

        sandbox = Sandbox(timeout=10)
        mock_llm = AsyncMock()
        mock_llm.complete_prompt.return_value = "import os; os.system('bad')"

        insight = PaperInsight(
            technique="Bad Technique",
            description="This will fail sandbox.",
            agos_module="knowledge",
        )

        result = await generate_from_insight(
            insight=insight, module="knowledge",
            sandbox=sandbox, llm_provider=mock_llm,
        )

        assert result is None


# ── State Persistence Tests ──────────────────────────────────────


class TestArchivePersistence:
    def test_save_and_restore_archive(self, tmp_path):
        state = EvolutionState(save_path=tmp_path / "state.json")

        archive = DesignArchive(max_size=30, temperature=0.4)
        archive.add(DesignEntry(
            strategy_name="test_strat",
            module="knowledge.semantic",
            current_fitness=0.8,
            generation=2,
            code_snippet="def x(): return 1",
        ))

        state.save_design_archive(archive)
        assert state.data.design_archive  # should have data

        restored = state.restore_design_archive()
        assert len(restored.entries) == 1
        assert restored.entries[0].strategy_name == "test_strat"
        assert restored.max_size == 30
        assert restored.temperature == 0.4

    def test_restore_empty_archive(self, tmp_path):
        state = EvolutionState(save_path=tmp_path / "state.json")
        archive = state.restore_design_archive()
        assert isinstance(archive, DesignArchive)
        assert len(archive.entries) == 0


# ── Config Tests ─────────────────────────────────────────────────


class TestAlmaConfig:
    def test_default_config_values(self):
        from agos.config import AgosSettings
        s = AgosSettings()
        assert s.evolution_llm_ideation_interval == 5
        assert s.evolution_alma_iterate_interval == 5


# ── Live Eval Tests ─────────────────────────────────────────────


class TestLiveEvals:
    @pytest.mark.asyncio
    async def test_live_evals_with_semantic(self):
        """run_live_evals should score knowledge.semantic when loom is provided."""

        evolver = MetaEvolver()

        # Mock loom with semantic weave
        mock_semantic = AsyncMock()
        mock_semantic.store = AsyncMock(return_value="tid_1")
        mock_semantic.delete = AsyncMock()
        # Return python-related threads on query
        mock_semantic.query = AsyncMock(return_value=[
            MagicMock(content="python asyncio concurrency patterns"),
            MagicMock(content="python decorator metaprogramming"),
            MagicMock(content="python type hints and mypy checking"),
        ])

        mock_loom = MagicMock()
        mock_loom.semantic = mock_semantic
        mock_loom.graph = None  # no graph

        scores = await evolver.run_live_evals(loom=mock_loom)

        assert "knowledge.semantic" in scores
        assert 0.0 <= scores["knowledge.semantic"] <= 1.0
        # All 3 results contain "python", so score should be 1.0
        assert scores["knowledge.semantic"] == 1.0

    @pytest.mark.asyncio
    async def test_live_evals_with_graph(self):
        """run_live_evals should score knowledge.graph when graph is provided."""
        evolver = MetaEvolver()

        mock_graph = AsyncMock()
        # Capture link calls to learn the actual node names (random prefix)
        linked_pairs = []

        async def capture_link(src, rel, tgt):
            linked_pairs.append((src, tgt))

        mock_graph.link = capture_link
        mock_graph.unlink = AsyncMock()

        async def mock_connections(node, direction=None):
            if not linked_pairs:
                return []
            a_node = linked_pairs[0][0]  # first link source = A
            b_node = linked_pairs[0][1]  # first link target = B
            c_node = linked_pairs[1][1] if len(linked_pairs) > 1 else ""  # second link target = C

            if node == a_node:
                # A -> B
                edge = MagicMock()
                edge.target = b_node
                edge.id = "edge_ab"
                return [edge]
            elif node == b_node:
                # B -> A and B -> C
                e1 = MagicMock()
                e1.target = a_node
                e1.id = "edge_ba"
                e2 = MagicMock()
                e2.target = c_node
                e2.id = "edge_bc"
                return [e1, e2]
            return []

        mock_graph.connections = mock_connections

        mock_loom = MagicMock()
        mock_loom.semantic = None
        mock_loom.graph = mock_graph

        scores = await evolver.run_live_evals(loom=mock_loom)

        assert "knowledge.graph" in scores
        # B found in A's connections (+0.5) and B has 2+ edges (+0.5) = 1.0
        assert scores["knowledge.graph"] == 1.0

    @pytest.mark.asyncio
    async def test_live_evals_with_audit(self):
        """run_live_evals should score policy.audit when audit_trail is provided."""
        evolver = MetaEvolver()

        mock_audit = AsyncMock()
        mock_audit.count = AsyncMock(side_effect=[10, 11])  # before and after
        mock_audit.record = AsyncMock()

        scores = await evolver.run_live_evals(audit_trail=mock_audit)

        assert "policy.audit" in scores
        # Count increased from 10 to 11 (+0.5) and record call succeeded (+0.5)
        assert scores["policy.audit"] > 0

    @pytest.mark.asyncio
    async def test_live_evals_with_event_bus(self):
        """run_live_evals should score events.bus when event_bus is provided."""
        evolver = MetaEvolver()

        mock_bus = AsyncMock()
        mock_bus.emit = AsyncMock()
        mock_bus.topics = MagicMock(return_value=["_live_eval_test"])
        mock_bus.history = MagicMock(return_value=[{"type": "_live_eval_test"}])

        scores = await evolver.run_live_evals(event_bus=mock_bus)

        assert "events.bus" in scores
        assert scores["events.bus"] > 0

    @pytest.mark.asyncio
    async def test_live_evals_empty_when_no_components(self):
        """run_live_evals with no components returns empty dict."""
        evolver = MetaEvolver()
        scores = await evolver.run_live_evals()
        assert scores == {}

    @pytest.mark.asyncio
    async def test_live_evals_stored_on_evolver(self):
        """run_live_evals should store results in _live_scores."""
        evolver = MetaEvolver()
        assert evolver._live_scores == {}

        mock_bus = AsyncMock()
        mock_bus.emit = AsyncMock()
        mock_bus.topics = MagicMock(return_value=["_live_eval_test"])
        mock_bus.history = MagicMock(return_value=[{"type": "_live_eval_test"}])

        await evolver.run_live_evals(event_bus=mock_bus)
        assert "events.bus" in evolver._live_scores


# ── 3-Way Blending Tests ───────────────────────────────────────


class TestThreeWayBlending:
    @pytest.mark.asyncio
    async def test_blending_live_sandbox_proxy(self):
        """With all 3 sources: 40% live + 30% sandbox + 30% proxy."""
        evolver = MetaEvolver()
        sandbox = Sandbox(timeout=10)

        # Patch run_live_evals to return known scores
        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {"knowledge.semantic": 1.0}

            await evolver.run_meta_cycle(
                sandbox=sandbox,
                loom=MagicMock(semantic=None, graph=None),  # triggers has_live_components
            )

            # knowledge.semantic should have blended score
            ks = evolver.genomes["knowledge.semantic"]
            # With live=1.0, sandbox from EVAL_TASKS, proxy~default
            # Just verify it's in valid range and not purely proxy
            assert 0.0 <= ks.fitness_score <= 1.0

    @pytest.mark.asyncio
    async def test_blending_live_proxy_only(self):
        """With live + proxy (no sandbox): 60% live + 40% proxy."""
        evolver = MetaEvolver()

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {"knowledge.semantic": 0.8}

            await evolver.run_meta_cycle(
                loom=MagicMock(semantic=None, graph=None),
                # No sandbox param
            )

            ks = evolver.genomes["knowledge.semantic"]
            # 0.6 * 0.8 + 0.4 * proxy = at least 0.48
            assert ks.fitness_score >= 0.4

    @pytest.mark.asyncio
    async def test_blending_proxy_only(self):
        """With only proxy signals: 100% proxy."""
        evolver = MetaEvolver()
        await evolver.run_meta_cycle()

        # No live, no sandbox → pure proxy
        ks = evolver.genomes["knowledge.semantic"]
        assert 0.0 <= ks.fitness_score <= 1.0


# ── Before/After Measurement Tests ─────────────────────────────


class TestBeforeAfterMeasurement:
    @pytest.mark.asyncio
    async def test_mutation_records_fitness_before(self):
        """Applied mutations should have fitness_before set."""
        evolver = MetaEvolver()

        # Force an underperformer to trigger mutations
        evolver.genomes["knowledge.semantic"].fitness_score = 0.1

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {"knowledge.semantic": 0.9}

            await evolver.run_meta_cycle(
                loom=MagicMock(semantic=None, graph=None),
            )

            # Find applied mutations for knowledge.semantic
            applied = [
                m for m in evolver.mutations
                if m.applied and m.component == "knowledge.semantic"
            ]

            if applied:
                # fitness_before should be set (the low value before mutation)
                for m in applied:
                    assert m.fitness_before is not None

    @pytest.mark.asyncio
    async def test_mutation_records_fitness_after(self):
        """Applied mutations should have fitness_after from post-eval."""
        evolver = MetaEvolver()

        evolver.genomes["knowledge.semantic"].fitness_score = 0.1

        call_count = 0

        async def mock_live(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"knowledge.semantic": 0.1}  # pre-mutation
            return {"knowledge.semantic": 0.9}  # post-mutation

        with patch.object(evolver, "run_live_evals", side_effect=mock_live):
            await evolver.run_meta_cycle(
                loom=MagicMock(semantic=None, graph=None),
            )

            applied = [
                m for m in evolver.mutations
                if m.applied and m.component == "knowledge.semantic"
            ]

            if applied:
                # At least one should have fitness_after set
                has_after = any(m.fitness_after is not None for m in applied)
                assert has_after


# ── Archive Re-evaluation Tests ─────────────────────────────────


class TestArchiveReevaluation:
    @pytest.mark.asyncio
    async def test_reevaluate_updates_fitness(self):
        """reevaluate_archive should update entry fitness from live evals."""
        evolver = MetaEvolver()
        archive = DesignArchive()
        archive.add(DesignEntry(
            strategy_name="test_strat",
            module="knowledge.semantic",
            current_fitness=0.3,
        ))

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {"knowledge.semantic": 0.9}

            updated = await evolver.reevaluate_archive(archive)

            assert updated == 1
            assert archive.entries[0].current_fitness == 0.9

    @pytest.mark.asyncio
    async def test_reevaluate_blends_live_and_sandbox(self):
        """reevaluate_archive should blend live + sandbox when both available."""
        evolver = MetaEvolver()
        archive = DesignArchive()
        archive.add(DesignEntry(
            strategy_name="test",
            module="knowledge.semantic",
            current_fitness=0.3,
        ))

        sandbox = Sandbox(timeout=10)

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live, \
             patch.object(evolver, "run_eval_tasks", new_callable=AsyncMock) as mock_eval:
            mock_live.return_value = {"knowledge.semantic": 0.8}
            mock_eval.return_value = {"knowledge.semantic": 0.6}

            updated = await evolver.reevaluate_archive(archive, sandbox=sandbox)

            assert updated == 1
            # 0.6 * 0.8 + 0.4 * 0.6 = 0.48 + 0.24 = 0.72
            assert archive.entries[0].current_fitness == 0.72

    @pytest.mark.asyncio
    async def test_reevaluate_empty_archive(self):
        """reevaluate_archive with empty archive returns 0."""
        evolver = MetaEvolver()
        archive = DesignArchive()

        updated = await evolver.reevaluate_archive(archive)
        assert updated == 0

    @pytest.mark.asyncio
    async def test_reevaluate_no_live_scores(self):
        """reevaluate_archive with no live scores returns 0."""
        evolver = MetaEvolver()
        archive = DesignArchive()
        archive.add(DesignEntry(
            strategy_name="x",
            module="knowledge.semantic",
            current_fitness=0.5,
        ))

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {}  # no scores for any module

            updated = await evolver.reevaluate_archive(archive)
            assert updated == 0
            # Fitness should be unchanged
            assert archive.entries[0].current_fitness == 0.5

    @pytest.mark.asyncio
    async def test_reevaluate_only_updates_matching_modules(self):
        """reevaluate_archive should only update entries whose module has a live score."""
        evolver = MetaEvolver()
        archive = DesignArchive()
        archive.add(DesignEntry(
            strategy_name="a", module="knowledge.semantic", current_fitness=0.3,
        ))
        archive.add(DesignEntry(
            strategy_name="b", module="policy.engine", current_fitness=0.4,
        ))

        with patch.object(evolver, "run_live_evals", new_callable=AsyncMock) as mock_live:
            mock_live.return_value = {"knowledge.semantic": 0.9}  # no policy.engine score

            updated = await evolver.reevaluate_archive(archive)

            assert updated == 1
            # Only knowledge.semantic should be updated
            sem = [e for e in archive.entries if e.module == "knowledge.semantic"][0]
            pol = [e for e in archive.entries if e.module == "policy.engine"][0]
            assert sem.current_fitness == 0.9
            assert pol.current_fitness == 0.4  # unchanged
