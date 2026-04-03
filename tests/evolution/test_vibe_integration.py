"""Tests for vibe coding tool integration with evolution engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from agos.evolution.demand import DemandSignal, DemandCollector
from agos.evolution.demand_solver import DemandSolver
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail


# ── Fixtures ──

@pytest.fixture
def bus():
    b = EventBus()
    b.emit = AsyncMock()
    return b

@pytest.fixture
def audit():
    a = MagicMock(spec=AuditTrail)
    a.log = AsyncMock()
    return a

@pytest.fixture
def collector():
    dc = DemandCollector()
    dc._add_signal("err:1", "error", "shell", "Docker not available", 0.8)
    return dc

@pytest.fixture
def solver(bus, audit, collector):
    return DemandSolver(event_bus=bus, audit=audit, demand_collector=collector)


# ── P0: _try_vibe_tool doesn't crash ──

class TestTryVibeToolNoCrash:
    """The _try_vibe_tool method must exist and not raise AttributeError."""

    @pytest.mark.asyncio
    async def test_try_vibe_tool_exists(self, solver):
        """Method exists on DemandSolver — no AttributeError."""
        assert hasattr(solver, '_try_vibe_tool')
        assert callable(solver._try_vibe_tool)

    @pytest.mark.asyncio
    async def test_try_vibe_tool_returns_told_user(self, solver):
        """Returns 'told_user', doesn't crash."""
        demand = DemandSignal(
            kind="error", source="shell",
            description="Docker not available", priority=0.8,
        )
        result = await solver._try_vibe_tool(demand, "env summary")
        assert result == "told_user"

    @pytest.mark.asyncio
    async def test_try_vibe_tool_emits_event(self, solver, bus):
        """Must emit evolution.user_action_needed event."""
        demand = DemandSignal(
            kind="error", source="shell",
            description="Docker not available", priority=0.8,
        )
        await solver._try_vibe_tool(demand, "env summary")
        bus.emit.assert_called_once()
        call_args = bus.emit.call_args
        assert call_args[0][0] == "evolution.user_action_needed"
        event_data = call_args[0][1]
        assert "Docker not available" in event_data["message"]
        assert "demand" in event_data

    @pytest.mark.asyncio
    async def test_try_vibe_tool_does_not_invoke_subprocess(self, solver):
        """Must NOT shell out to any tool — just prepare context."""
        demand = DemandSignal(
            kind="error", source="shell",
            description="Docker not available", priority=0.8,
        )
        with patch('subprocess.run') as mock_run, \
             patch('asyncio.create_subprocess_exec') as mock_exec:
            await solver._try_vibe_tool(demand, "env summary")
            mock_run.assert_not_called()
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_vibe_tool_includes_tool_info_when_configured(self, solver, bus):
        """If user has a preferred tool, event should include tool name + instructions."""
        demand = DemandSignal(
            kind="error", source="shell",
            description="Docker not available", priority=0.8,
        )
        with patch('agos.setup_store.get_preferred_vibe_tool', return_value="claude_code"), \
             patch('agos.vibe_tools.get_tool_by_name') as mock_get:
            mock_tool = MagicMock()
            mock_tool.label = "Claude Code"
            mock_tool.how_to_use = "Open Claude Code in this repo."
            mock_get.return_value = mock_tool

            await solver._try_vibe_tool(demand, "env summary")

            event_data = bus.emit.call_args[0][1]
            assert event_data["tool"] == "Claude Code"
            assert "Claude Code" in event_data["how_to_use"]

    @pytest.mark.asyncio
    async def test_try_vibe_tool_works_without_config(self, solver, bus):
        """If no vibe tool configured, still works — just uses generic instructions."""
        demand = DemandSignal(
            kind="error", source="shell",
            description="Docker not available", priority=0.8,
        )
        with patch('agos.setup_store.get_preferred_vibe_tool', return_value=None):
            await solver._try_vibe_tool(demand, "env summary")

            event_data = bus.emit.call_args[0][1]
            assert event_data["tool"] == ""
            assert "sculpt demands" in event_data["how_to_use"]


# ── P0: tell_user action full path ──

class TestTellUserAction:
    """When LLM picks tell_user, the full code path must work."""

    @pytest.mark.asyncio
    async def test_full_tick_with_tell_user_no_crash(self, solver, bus, collector):
        """Full tick() that results in tell_user should not AttributeError."""
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=MagicMock(
            content='{"action":"tell_user","message":"Need Docker installed"}'
        ))
        try:
            _result = await solver.tick(llm=mock_llm)
        except AttributeError as e:
            if '_try_vibe_tool' in str(e):
                pytest.fail(f"_try_vibe_tool not implemented: {e}")
            raise


# ── vibe_bridge.py should not exist ──

class TestVibeBridgeDeleted:
    """vibe_bridge.py was a wrong approach and should be deleted."""

    def test_vibe_bridge_not_importable(self):
        """vibe_bridge should not be importable — it's deleted."""
        try:
            from agos.evolution import vibe_bridge  # noqa: F401
            pytest.fail("vibe_bridge.py should be deleted — wrong design")
        except ImportError:
            pass  # Expected

    def test_no_subprocess_claude_invocation(self):
        """No code should shell out to 'claude -p' for evolution."""
        solver_path = Path(__file__).parent.parent.parent / "agos" / "evolution" / "demand_solver.py"
        if solver_path.exists():
            code = solver_path.read_text(encoding="utf-8")
            assert 'claude -p' not in code


# ── Vibe tool detection still works ──

class TestVibeToolDetection:
    """Detection module must work independently of the bridge."""

    def test_detect_returns_list(self):
        from agos.vibe_tools import detect_vibe_tools
        tools = detect_vibe_tools(use_cache=False)
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_get_installed_filters_low_confidence(self):
        from agos.vibe_tools import get_installed_tools
        installed = get_installed_tools(min_confidence="medium")
        for t in installed:
            assert t.confidence in ("high", "medium")

    def test_summary_no_crash(self):
        from agos.vibe_tools import summary
        s = summary()
        assert isinstance(s, str)


# ── DEMANDS.md generation ──

class TestDemandsGeneration:
    """DEMANDS.md must be generated and readable."""

    def test_write_demands_md(self, tmp_path):
        with patch('agos.evolution.nudge.settings') as mock_settings:
            mock_settings.workspace_dir = tmp_path
            import json
            signals = {"signals": [
                {"kind": "error", "source": "shell", "description": "Docker failed",
                 "priority": 0.8, "status": "active", "attempts": 2, "context": {}},
            ]}
            (tmp_path / "demand_signals.json").write_text(json.dumps(signals))

            from agos.evolution.nudge import write_demands_md
            path = write_demands_md()
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "Docker failed" in content
            assert "How to Fix" in content

    def test_demands_md_empty_when_no_signals(self, tmp_path):
        with patch('agos.evolution.nudge.settings') as mock_settings:
            mock_settings.workspace_dir = tmp_path
            from agos.evolution.nudge import write_demands_md
            path = write_demands_md()
            content = path.read_text(encoding="utf-8")
            assert "No demands" in content or "healthy" in content.lower()


# ── Setup config persistence ──

class TestVibeToolConfig:
    """Vibe tool config in setup.json must persist correctly."""

    def test_roundtrip(self, tmp_path):
        from agos.setup_store import (
            set_vibe_tool_config, get_vibe_tools_config,
            set_preferred_vibe_tool, get_preferred_vibe_tool,
        )
        set_vibe_tool_config(tmp_path, "claude_code", {"enabled": True, "label": "Claude Code"})
        set_preferred_vibe_tool(tmp_path, "claude_code")

        cfg = get_vibe_tools_config(tmp_path)
        assert "claude_code" in cfg
        assert cfg["claude_code"]["enabled"] is True

        pref = get_preferred_vibe_tool(tmp_path)
        assert pref == "claude_code"

    def test_missing_config_returns_empty(self, tmp_path):
        from agos.setup_store import get_vibe_tools_config, get_preferred_vibe_tool
        assert get_vibe_tools_config(tmp_path) == {}
        assert get_preferred_vibe_tool(tmp_path) is None


# ── Nudge line ──

class TestNudgeLine:
    """CLI should show demand count after commands."""

    def test_nudge_line_with_demands(self):
        with patch('agos.evolution.nudge.get_demand_count', return_value=(3, 1)):
            from agos.evolution.nudge import nudge_line
            line = nudge_line()
            assert "demand" in line.lower()
            assert "sculpt demands" in line

    def test_nudge_line_empty_when_no_demands(self):
        with patch('agos.evolution.nudge.get_demand_count', return_value=(0, 0)):
            from agos.evolution.nudge import nudge_line
            line = nudge_line()
            assert line == ""
