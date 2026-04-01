"""Tests for demand-driven evolution signals."""
import time
import pytest
from agos.evolution.demand import DemandSignal, DemandCollector
from agos.events.bus import Event


def _make_event(topic: str, data: dict) -> Event:
    return Event(topic=topic, data=data, source="test")


class TestDemandSignal:
    def test_merge_increases_count_and_priority(self):
        s1 = DemandSignal(kind="error", source="shell", description="fail", priority=0.5)
        s2 = DemandSignal(kind="error", source="shell", description="fail", priority=0.5)
        s1.merge(s2)
        assert s1.count == 2
        assert s1.priority > 0.5

    def test_priority_capped_at_one(self):
        s = DemandSignal(kind="error", source="x", description="x", priority=0.95)
        for _ in range(20):
            s.merge(DemandSignal(kind="error", source="x", description="x", priority=0.5))
        assert s.priority <= 1.0


class TestDemandCollector:
    @pytest.fixture
    def dc(self):
        return DemandCollector()

    @pytest.mark.asyncio
    async def test_os_error_creates_signal(self, dc):
        event = _make_event("os.error", {"command": "find images", "error": "tool not found"})
        await dc._on_os_error(event)
        assert dc.has_demands()
        top = dc.top_demands(limit=1)
        assert len(top) == 1
        assert top[0].kind == "missing_tool"

    @pytest.mark.asyncio
    async def test_tool_failure_escalates(self, dc):
        for i in range(5):
            event = _make_event("os.tool_result", {"tool": "shell", "ok": False, "preview": "timeout"})
            await dc._on_tool_result(event)
        top = dc.top_demands(limit=1)
        assert top[0].source == "shell"
        assert top[0].count == 5
        assert top[0].priority > 0.5

    @pytest.mark.asyncio
    async def test_expensive_command_signal(self, dc):
        event = _make_event("os.complete", {"command": "analyze codebase", "tokens": 80000, "turns": 20, "steps": 5})
        await dc._on_command_complete(event)
        # Both expensive and hard-task signals
        assert dc.pending_count() == 2

    @pytest.mark.asyncio
    async def test_agent_crash_signal(self, dc):
        event = _make_event("agent.error", {"agent": "SecurityScanner", "error": "OOM killed"})
        await dc._on_agent_error(event)
        top = dc.top_demands(limit=1)
        assert top[0].kind == "agent_crash"
        assert "SecurityScanner" in top[0].description

    def test_demand_topics_from_missing_tool(self, dc):
        dc._add_signal("missing:image", "missing_tool", "image_analyze",
                        "Missing tool for image analysis", 0.8,
                        {"tool": "image_analyze"})
        topics = dc.demand_topics(limit=2)
        assert len(topics) >= 1
        assert any("image" in t.lower() or "vision" in t.lower() for t in topics)

    def test_demand_context_for_codegen(self, dc):
        dc._add_signal("err:1", "error", "shell", "Shell tool failed 5 times", 0.7)
        dc._add_signal("miss:1", "missing_tool", "pdf", "Missing PDF extraction", 0.9)
        ctx = dc.demand_context_for_codegen()
        assert "Real problems to solve" in ctx
        assert "missing_tool" in ctx

    def test_persistence_roundtrip(self, dc):
        dc._add_signal("err:1", "error", "shell", "Shell failed", 0.6, {"tool": "shell"})
        dc._add_signal("miss:1", "missing_tool", "pdf", "No PDF tool", 0.8)
        dc._tool_failures["shell"] = 3
        dc._command_errors["find stuff"] = 2

        data = dc.to_dict()
        dc2 = DemandCollector.from_dict(data)
        assert dc2.pending_count() == 2
        assert dc2._tool_failures["shell"] == 3
        assert dc2._command_errors["find stuff"] == 2

    def test_clear_resolved(self, dc):
        dc._add_signal("tool_fail:shell", "error", "shell", "Shell failed", 0.5)
        dc._add_signal("tool_fail:http", "error", "http", "HTTP failed", 0.5)
        dc._add_signal("miss:pdf", "missing_tool", "pdf", "No PDF", 0.8)
        removed = dc.clear_resolved("tool_fail:")
        assert removed == 2
        assert dc.pending_count() == 1

    def test_summary(self, dc):
        dc._add_signal("err:1", "error", "shell", "Shell failed", 0.5)
        dc._add_signal("miss:1", "missing_tool", "pdf", "No PDF", 0.8)
        s = dc.summary()
        assert s["total_signals"] == 2
        assert "error" in s["by_kind"]
        assert "missing_tool" in s["by_kind"]
        assert len(s["top_demands"]) == 2

    def test_signal_eviction(self):
        dc = DemandCollector(max_signals=3)
        dc._add_signal("a", "error", "a", "low", 0.1)
        dc._add_signal("b", "error", "b", "med", 0.5)
        dc._add_signal("c", "error", "c", "high", 0.9)
        dc._add_signal("d", "error", "d", "higher", 0.95)
        assert dc.pending_count() == 3
        # Low-priority "a" should have been evicted
        assert "a" not in dc._signals

    @pytest.mark.asyncio
    async def test_no_demands_returns_empty(self, dc):
        assert not dc.has_demands()
        assert dc.demand_topics() == []
        assert dc.demand_context_for_codegen() == ""
        assert dc.top_demands() == []
