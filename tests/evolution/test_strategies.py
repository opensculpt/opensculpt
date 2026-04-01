"""Tests for the ALMA-inspired integration strategies."""

import pytest
import tempfile
import os

from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.manager import TheLoom, MemoryLayer
from agos.knowledge.consolidator import Consolidator
from agos.knowledge.base import Thread, ThreadQuery
from agos.evolution.integrator import EvolutionIntegrator, EvolutionProposal
from agos.evolution.analyzer import PaperInsight
from agos.evolution.strategies.memory_softmax import SoftmaxScoringStrategy
from agos.evolution.strategies.memory_layered import LayeredRetrievalStrategy
from agos.evolution.strategies.memory_semaphore import SemaphoreBatchStrategy
from agos.evolution.strategies.memory_confidence import AdaptiveConfidenceStrategy


# ── Helpers ──────────────────────────────────────────────────────

def _make_proposal(module="knowledge.semantic"):
    insight = PaperInsight(
        paper_id="alma-001",
        paper_title="ALMA Memory Paper",
        technique="Test Technique",
        description="A test",
        applicability="Very applicable",
        priority="high",
        agos_module=module,
        implementation_hint="Implement it",
    )
    return EvolutionProposal(insight=insight, status="accepted")


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
async def semantic(db_path):
    s = SemanticWeave(db_path)
    await s.initialize()
    return s


@pytest.fixture
async def loom(db_path):
    lm = TheLoom(db_path)
    await lm.initialize()
    return lm


@pytest.fixture
async def consolidator(db_path):
    lm = TheLoom(db_path)
    await lm.initialize()
    return Consolidator(lm.episodic, lm.semantic, lm.graph)


# ── Softmax Strategy Tests ───────────────────────────────────────

class TestSoftmaxStrategy:
    @pytest.mark.asyncio
    async def test_validate(self, semantic):
        s = SoftmaxScoringStrategy(semantic)
        valid, reason = s.validate(_make_proposal())
        assert valid

    @pytest.mark.asyncio
    async def test_snapshot(self, semantic):
        s = SoftmaxScoringStrategy(semantic)
        snap = await s.snapshot()
        assert snap["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_apply(self, semantic):
        s = SoftmaxScoringStrategy(semantic)
        changes = await s.apply(_make_proposal())
        assert len(changes) == 2
        assert semantic._temperature == 0.3

    @pytest.mark.asyncio
    async def test_rollback(self, semantic):
        s = SoftmaxScoringStrategy(semantic)
        snap = await s.snapshot()
        await s.apply(_make_proposal())
        assert semantic._temperature == 0.3
        await s.rollback(snap)
        assert semantic._temperature == 0.0

    @pytest.mark.asyncio
    async def test_health_check(self, semantic):
        s = SoftmaxScoringStrategy(semantic)
        assert await s.health_check()

    @pytest.mark.asyncio
    async def test_softmax_sampling(self, semantic):
        """With temperature > 0, query results should vary."""
        # Store some threads
        for i in range(10):
            await semantic.store(Thread(
                content=f"memory pattern number {i} about neural retrieval",
                kind="fact",
            ))

        # Deterministic mode
        q = ThreadQuery(text="memory neural retrieval", limit=5)
        r1 = await semantic.query(q)
        r2 = await semantic.query(q)
        ids_1 = [t.id for t in r1]
        ids_2 = [t.id for t in r2]
        assert ids_1 == ids_2  # deterministic

        # Softmax mode — results may differ
        semantic.set_temperature(1.0)
        results = []
        for _ in range(5):
            r = await semantic.query(q)
            results.append(tuple(t.id for t in r))
        # At least some variation is expected with high temperature
        # (not guaranteed on every run, but statistically very likely)
        # We just verify it doesn't crash and returns correct count
        assert all(len(r) == 5 for r in results)


# ── Layered Strategy Tests ───────────────────────────────────────

class TestLayeredStrategy:
    @pytest.mark.asyncio
    async def test_validate(self, loom):
        s = LayeredRetrievalStrategy(loom)
        valid, reason = s.validate(_make_proposal("knowledge.manager"))
        assert valid

    @pytest.mark.asyncio
    async def test_snapshot(self, loom):
        s = LayeredRetrievalStrategy(loom)
        snap = await s.snapshot()
        assert snap["use_layered_recall"] is False
        assert snap["layers"] == []

    @pytest.mark.asyncio
    async def test_apply(self, loom):
        s = LayeredRetrievalStrategy(loom)
        changes = await s.apply(_make_proposal("knowledge.manager"))
        assert loom._use_layered_recall is True
        assert len(loom._layers) == 2
        assert any("semantic" in c.lower() for c in changes)
        assert any("episodic" in c.lower() for c in changes)

    @pytest.mark.asyncio
    async def test_rollback(self, loom):
        s = LayeredRetrievalStrategy(loom)
        snap = await s.snapshot()
        await s.apply(_make_proposal("knowledge.manager"))
        assert loom._use_layered_recall is True
        await s.rollback(snap)
        assert loom._use_layered_recall is False

    @pytest.mark.asyncio
    async def test_health_check(self, loom):
        s = LayeredRetrievalStrategy(loom)
        assert await s.health_check()

    @pytest.mark.asyncio
    async def test_layered_recall(self, loom):
        """Layered recall checks layers in priority order."""
        # Store in semantic
        await loom.semantic.store(Thread(
            content="semantic knowledge about memory patterns",
            kind="fact",
        ))
        # Store in episodic
        await loom.episodic.store(Thread(
            content="episodic event about memory patterns",
            kind="event",
        ))

        # Default flat recall
        flat_results = await loom.recall("memory patterns", limit=5)
        assert len(flat_results) >= 1

        # Enable layered recall
        strategy = LayeredRetrievalStrategy(loom)
        await strategy.apply(_make_proposal("knowledge.manager"))

        layered_results = await loom.recall("memory patterns", limit=5)
        assert len(layered_results) >= 1

    @pytest.mark.asyncio
    async def test_memory_layer_model(self):
        layer = MemoryLayer(name="test", weave=None, priority=5, enabled=True)
        assert layer.name == "test"
        assert layer.priority == 5
        assert layer.enabled

    @pytest.mark.asyncio
    async def test_add_layer(self, loom):
        loom.add_layer("custom", loom.semantic, priority=20)
        assert len(loom._layers) == 1
        assert loom._layers[0].name == "custom"
        assert loom._layers[0].priority == 20

    @pytest.mark.asyncio
    async def test_layers_sorted_by_priority(self, loom):
        loom.add_layer("low", loom.semantic, priority=0)
        loom.add_layer("high", loom.episodic, priority=100)
        loom.add_layer("mid", loom.semantic, priority=50)
        assert loom._layers[0].name == "high"
        assert loom._layers[1].name == "mid"
        assert loom._layers[2].name == "low"


# ── Semaphore Strategy Tests ────────────────────────────────────

class TestSemaphoreStrategy:
    @pytest.mark.asyncio
    async def test_validate(self, consolidator):
        s = SemaphoreBatchStrategy(consolidator)
        valid, reason = s.validate(_make_proposal("knowledge.consolidator"))
        assert valid

    @pytest.mark.asyncio
    async def test_snapshot(self, consolidator):
        s = SemaphoreBatchStrategy(consolidator)
        snap = await s.snapshot()
        assert snap["max_concurrent_writes"] == 5

    @pytest.mark.asyncio
    async def test_apply(self, consolidator):
        s = SemaphoreBatchStrategy(consolidator)
        changes = await s.apply(_make_proposal("knowledge.consolidator"))
        assert len(changes) == 2
        assert consolidator._max_concurrent_writes == 5

    @pytest.mark.asyncio
    async def test_rollback(self, consolidator):
        consolidator._max_concurrent_writes = 3
        s = SemaphoreBatchStrategy(consolidator)
        snap = await s.snapshot()
        await s.apply(_make_proposal("knowledge.consolidator"))
        await s.rollback(snap)
        assert consolidator._max_concurrent_writes == 3

    @pytest.mark.asyncio
    async def test_health_check(self, consolidator):
        s = SemaphoreBatchStrategy(consolidator)
        assert await s.health_check()

    @pytest.mark.asyncio
    async def test_batch_delete(self, consolidator):
        """Test that batch delete works concurrently."""
        # Store some episodes
        ep = consolidator._episodic
        ids = []
        for i in range(5):
            tid = await ep.store(Thread(content=f"event {i}", kind="event"))
            ids.append(tid)

        deleted = await consolidator._batch_delete(ids, ep)
        assert deleted == 5

    @pytest.mark.asyncio
    async def test_batch_store(self, consolidator):
        """Test that batch store works concurrently."""
        sem = consolidator._semantic
        threads = [
            Thread(content=f"fact {i}", kind="fact") for i in range(5)
        ]
        stored = await consolidator._batch_store(threads, sem)
        assert stored == 5


# ── Adaptive Confidence Strategy Tests ───────────────────────────

class TestAdaptiveConfidenceStrategy:
    @pytest.mark.asyncio
    async def test_validate(self, semantic):
        s = AdaptiveConfidenceStrategy(semantic)
        valid, reason = s.validate(_make_proposal())
        assert valid

    @pytest.mark.asyncio
    async def test_snapshot(self, semantic):
        s = AdaptiveConfidenceStrategy(semantic)
        snap = await s.snapshot()
        assert snap["track_access"] is False

    @pytest.mark.asyncio
    async def test_apply(self, semantic):
        s = AdaptiveConfidenceStrategy(semantic)
        changes = await s.apply(_make_proposal())
        assert semantic._track_access is True
        assert len(changes) == 3

    @pytest.mark.asyncio
    async def test_rollback(self, semantic):
        s = AdaptiveConfidenceStrategy(semantic)
        snap = await s.snapshot()
        await s.apply(_make_proposal())
        assert semantic._track_access is True
        await s.rollback(snap)
        assert semantic._track_access is False

    @pytest.mark.asyncio
    async def test_health_check(self, semantic):
        s = AdaptiveConfidenceStrategy(semantic)
        assert await s.health_check()

    @pytest.mark.asyncio
    async def test_access_tracking(self, semantic):
        """With access tracking enabled, queried threads get access counts."""
        tid = await semantic.store(Thread(
            content="knowledge about neural memory patterns",
            kind="fact",
        ))

        semantic.enable_access_tracking(True)

        # Query to trigger access recording
        q = ThreadQuery(text="neural memory", limit=5)
        await semantic.query(q)

        # Check access was recorded
        import aiosqlite
        async with aiosqlite.connect(semantic._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT access_count, last_accessed FROM semantic WHERE id = ?",
                (tid,),
            )
            row = await cursor.fetchone()
            assert row["access_count"] == 1
            assert row["last_accessed"] is not None

    @pytest.mark.asyncio
    async def test_record_access(self, semantic):
        tid = await semantic.store(Thread(
            content="something important", kind="fact",
        ))
        await semantic.record_access(tid)
        await semantic.record_access(tid)

        import aiosqlite
        async with aiosqlite.connect(semantic._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT access_count FROM semantic WHERE id = ?", (tid,),
            )
            row = await cursor.fetchone()
            assert row["access_count"] == 2

    @pytest.mark.asyncio
    async def test_decay_confidence(self, semantic):
        """Confidence decays for inactive threads."""
        from datetime import datetime, timedelta

        # Store a thread with old timestamp
        old_thread = Thread(
            content="old forgotten knowledge",
            kind="fact",
            confidence=1.0,
        )
        await semantic.store(old_thread)

        # Manually set the created_at to be very old
        import aiosqlite
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        async with aiosqlite.connect(semantic._db_path) as db:
            await db.execute(
                "UPDATE semantic SET created_at = ? WHERE id = ?",
                (old_date, old_thread.id),
            )
            await db.commit()

        # Decay with 30-day threshold
        decayed = await semantic.decay_confidence(days_inactive=30, decay_factor=0.5)
        assert decayed >= 1

        # Verify confidence was reduced
        async with aiosqlite.connect(semantic._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT confidence FROM semantic WHERE id = ?",
                (old_thread.id,),
            )
            row = await cursor.fetchone()
            assert row["confidence"] == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_set_temperature(self, semantic):
        semantic.set_temperature(0.5)
        assert semantic._temperature == 0.5
        semantic.set_temperature(-1.0)
        assert semantic._temperature == 0.0  # clamped

    @pytest.mark.asyncio
    async def test_enable_access_tracking(self, semantic):
        assert semantic._track_access is False
        semantic.enable_access_tracking(True)
        assert semantic._track_access is True
        semantic.enable_access_tracking(False)
        assert semantic._track_access is False


# ── Full integration flow test ───────────────────────────────────

class TestFullIntegrationFlow:
    @pytest.mark.asyncio
    async def test_apply_softmax_via_integrator(self, semantic):
        """Full flow: register strategy → apply proposal → verify → rollback."""
        integrator = EvolutionIntegrator()
        strategy = SoftmaxScoringStrategy(semantic)
        integrator.register_strategy(strategy)

        proposal = _make_proposal()
        result = await integrator.apply(proposal)

        assert result.success
        assert semantic._temperature == 0.3
        assert proposal.status == "integrated"

        # Rollback
        rolled = await integrator.rollback(result.version_id)
        assert rolled
        assert semantic._temperature == 0.0

    @pytest.mark.asyncio
    async def test_apply_confidence_via_integrator(self, semantic):
        integrator = EvolutionIntegrator()
        strategy = AdaptiveConfidenceStrategy(semantic)
        integrator.register_strategy(strategy)

        proposal = _make_proposal()
        result = await integrator.apply(proposal)

        assert result.success
        assert semantic._track_access is True

    @pytest.mark.asyncio
    async def test_apply_layered_via_integrator(self, loom):
        integrator = EvolutionIntegrator()
        strategy = LayeredRetrievalStrategy(loom)
        integrator.register_strategy(strategy)

        proposal = _make_proposal("knowledge.manager")
        result = await integrator.apply(proposal)

        assert result.success
        assert loom._use_layered_recall is True
        assert len(loom._layers) == 2


# ── Pattern code extraction tests ─────────────────────────────


class TestExtractPatternCode:
    """Tests for _extract_pattern_code used by load_evolved_strategies."""

    def test_extracts_repr_string(self):
        from agos.evolution.codegen import _extract_pattern_code
        module = "PATTERN_CODE = 'def add(a, b):\\n    return a + b\\n'"
        result = _extract_pattern_code(module)
        assert result == "def add(a, b):\n    return a + b\n"

    def test_extracts_triple_quoted(self):
        from agos.evolution.codegen import _extract_pattern_code
        module = 'PATTERN_CODE = """def add(a, b):\n    return a + b\n"""'
        result = _extract_pattern_code(module)
        assert result == "def add(a, b):\n    return a + b\n"

    def test_returns_none_for_no_pattern(self):
        from agos.evolution.codegen import _extract_pattern_code
        result = _extract_pattern_code("x = 1\ny = 2\n")
        assert result is None

    def test_extracts_from_full_wrapper(self):
        """Simulates extracting pattern code from a real wrapper module."""
        from agos.evolution.codegen import _extract_pattern_code
        wrapper = (
            'from __future__ import annotations\n'
            'import logging\n'
            'PATTERN_CODE = ' + repr("def hello():\n    return 'world'\n") + '\n'
            'exec(compile(PATTERN_CODE, "<test>", "exec"))\n'
        )
        result = _extract_pattern_code(wrapper)
        assert result == "def hello():\n    return 'world'\n"
        # The pattern code itself should NOT contain exec/compile
        assert "exec" not in result
        assert "compile" not in result
