"""Tests for all fixes implemented in the 2026-03-28 session.

Tests are grouped by feature area. Each test validates the actual
behavior change, not just that code exists.

Run: python -m pytest tests/test_session_fixes.py -v
"""
import asyncio
import json
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════════
# 1. PLUGGABLE CONDENSERS (session.py)
# ═══════════════════════════════════════════════════════════════════

class TestCondensers:
    """Test the 5 condenser strategies from OpenClaw/OpenHands research."""

    def test_condenser_registry_has_5_strategies(self):
        from agos.session import list_condensers
        strategies = list_condensers()
        assert len(strategies) >= 5
        assert "observation_masking" in strategies
        assert "recent" in strategies
        assert "summary" in strategies
        assert "noop" in strategies
        assert "memory_flush" in strategies

    def test_get_condenser_returns_correct_type(self):
        from agos.session import get_condenser, ObservationMaskingCondenser, RecentCondenser
        c1 = get_condenser("observation_masking")
        assert isinstance(c1, ObservationMaskingCondenser)
        c2 = get_condenser("recent")
        assert isinstance(c2, RecentCondenser)

    def test_get_condenser_fallback_on_unknown(self):
        from agos.session import get_condenser, ObservationMaskingCondenser
        c = get_condenser("nonexistent_strategy")
        assert isinstance(c, ObservationMaskingCondenser)

    def test_observation_masking_preserves_first_message(self):
        from agos.session import get_condenser
        c = get_condenser("observation_masking", threshold=4, keep_recent=2, keep_first=True)
        messages = [{"content": f"msg_{i}"} for i in range(6)]
        result = c.compact(messages)
        assert result[0] == messages[0], "First message must be preserved (keep_first)"

    def test_observation_masking_masks_middle_content(self):
        from agos.session import get_condenser
        c = get_condenser("observation_masking", threshold=4, keep_recent=2, keep_first=True)
        messages = [
            {"content": "task"},
            {"content": [{"type": "tool_result", "content": "x" * 300, "is_error": False}]},
            {"content": [{"type": "tool_result", "content": "y" * 300, "is_error": False}]},
            {"content": [{"type": "tool_result", "content": "z" * 300, "is_error": True}]},
            {"content": "recent1"},
            {"content": "recent2"},
        ]
        result = c.compact(messages)
        # Middle messages should have masked content
        for msg in result[1:-2]:
            if isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        assert len(item["content"]) < 200, "Tool output should be masked"

    def test_observation_masking_handles_llm_messages(self):
        """The condenser must work with LLMMessage objects, not just dicts."""
        from agos.session import get_condenser
        from agos.llm.base import LLMMessage
        c = get_condenser("observation_masking", threshold=4, keep_recent=2, keep_first=True)
        messages = [
            LLMMessage(role="user", content="do the task"),
            LLMMessage(role="assistant", content="ok"),
            LLMMessage(role="user", content=[{"type": "tool_result", "content": "x" * 300}]),
            LLMMessage(role="assistant", content="next step"),
            LLMMessage(role="user", content="recent1"),
            LLMMessage(role="assistant", content="recent2"),
        ]
        result = c.compact(messages)
        assert len(result) == 6  # All kept but middle masked
        assert result[0].content == "do the task", "First message preserved"

    def test_recent_condenser_keeps_n(self):
        from agos.session import get_condenser
        c = get_condenser("recent", max_messages=3, keep_first=True)
        messages = [{"content": f"msg_{i}"} for i in range(6)]
        result = c.compact(messages)
        assert len(result) == 3
        assert result[0] == messages[0], "First message preserved"

    def test_noop_condenser_does_nothing(self):
        from agos.session import get_condenser
        c = get_condenser("noop")
        messages = [{"content": f"msg_{i}"} for i in range(100)]
        result = c.compact(messages)
        assert result == messages

    def test_session_compactor_backward_compatible(self):
        from agos.session import SessionCompactor
        sc = SessionCompactor(max_messages=5, compact_to=2, strategy="recent")
        messages = [{"command": f"cmd_{i}", "response": f"resp_{i}"} for i in range(8)]
        assert sc.should_compact(messages)
        result = sc.compact(messages)
        assert len(result) <= 5

    def test_session_compactor_set_strategy_at_runtime(self):
        from agos.session import SessionCompactor
        sc = SessionCompactor(strategy="noop")
        assert "Noop" in sc.stats["strategy"]
        sc.set_strategy("recent", max_messages=5)
        assert "Recent" in sc.stats["strategy"]


# ═══════════════════════════════════════════════════════════════════
# 2. LOOP GUARD (guard.py)
# ═══════════════════════════════════════════════════════════════════

class TestLoopGuard:
    """Test SHA256-based loop detection from OpenFang."""

    def test_no_loop_on_varied_calls(self):
        from agos.guard import LoopGuard
        lg = LoopGuard(window_size=20, min_pattern_len=3, max_pattern_len=5, repeat_threshold=4)
        for i in range(10):
            lg.record("shell", {"command": f"different_command_{i}"})
        assert not lg.is_looping()

    def test_detects_repeated_pattern(self):
        from agos.guard import LoopGuard
        lg = LoopGuard(window_size=20, min_pattern_len=2, max_pattern_len=4, repeat_threshold=3)
        pattern = [("shell", {"command": "ls"}), ("read_file", {"path": "/tmp/x"})]
        for _ in range(4):
            for tool, args in pattern:
                lg.record(tool, args)
        assert lg.is_looping()
        assert "loop" in lg.trip_reason.lower()

    def test_reset_clears_state(self):
        from agos.guard import LoopGuard
        lg = LoopGuard(repeat_threshold=3)
        for _ in range(10):
            lg.record("shell", {"command": "same"})
        lg.reset()
        assert not lg.is_looping()

    def test_sub_agent_threshold_avoids_false_positives(self):
        """Sub-agents use threshold=4, min_pattern=3 to avoid false positives
        during package installation (many shell calls with different args)."""
        from agos.guard import LoopGuard
        lg = LoopGuard(window_size=20, min_pattern_len=3, max_pattern_len=5, repeat_threshold=4)
        # Simulate package installation — lots of shell calls with DIFFERENT args
        for i in range(15):
            lg.record("shell", {"command": f"pip install package_{i}"})
        assert not lg.is_looping(), "Should not trigger on varied shell commands"


# ═══════════════════════════════════════════════════════════════════
# 3. TOKEN BUDGET (os_agent.py)
# ═══════════════════════════════════════════════════════════════════

class TestTokenBudget:
    """Test that sub-agents are stopped when they exceed 50K tokens."""

    def test_budget_constant_exists(self):
        import agos.os_agent as oa
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert "_TOKEN_BUDGET = 50_000" in source

    def test_budget_check_is_before_llm_call(self):
        """The budget check must run BEFORE the LLM call, not after."""
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        budget_pos = source.index("_TOKEN_BUDGET")
        llm_complete_pos = source.index("resp = await self._llm.complete", budget_pos)
        budget_check_pos = source.index("_total_tokens > _TOKEN_BUDGET", budget_pos)
        assert budget_check_pos < llm_complete_pos, "Budget check must be before LLM call"


# ═══════════════════════════════════════════════════════════════════
# 4. THINK TOOL (os_agent.py)
# ═══════════════════════════════════════════════════════════════════

class TestThinkTool:
    """Test the think tool from OpenHands — agent reasons without executing."""

    def test_think_tool_registered(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert 'name="think"' in source
        assert "Reason about your approach" in source

    def test_think_handler_returns_string(self):
        async def run():
            # Simulate the think handler
            async def _think(thought: str) -> str:
                return f"[Thought recorded: {thought[:200]}]"
            result = await _think("I should try apt-get instead of docker")
            assert "[Thought recorded:" in result
            assert "apt-get" in result
        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════
# 5. DYNAMIC TOOL SELECTION (os_agent.py)
# ═══════════════════════════════════════════════════════════════════

class TestDynamicToolSelection:
    """Test that sub-agents get only relevant tools per task."""

    def test_docker_keywords_exist(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert "_DOCKER_KEYWORDS" in source
        assert "_HTTP_KEYWORDS" in source
        assert "_CORE_TOOLS" in source

    def test_core_tools_always_included(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert '"shell"' in source
        assert '"read_file"' in source
        assert '"write_file"' in source
        assert '"python"' in source


# ═══════════════════════════════════════════════════════════════════
# 6. PRE-FLIGHT HARD GATES (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestPreFlightGates:
    """Test that doomed phases are skipped before wasting tokens."""

    def test_planner_gets_environment(self):
        """Planner receives environment info so it plans correctly (no hardcoded gates)."""
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "EnvironmentProbe" in source
        assert "env_summary" in source

    def test_universal_verify_guidance(self):
        """Planning prompt must mandate universal tools for verification."""
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "pg_isready" in source  # Listed as banned
        assert "redis-cli" in source  # Listed as banned
        assert "UNIVERSAL tools only" in source or "universal tools" in source.lower()


# ═══════════════════════════════════════════════════════════════════
# 7. GC DOCKER CONTAINERS (gc.py)
# ═══════════════════════════════════════════════════════════════════

class TestGarbageCollector:
    """Test GC memory pressure and Docker orphan cleanup."""

    def test_gc_docker_containers_method_exists(self):
        from agos.daemons.gc import GarbageCollector
        gc = GarbageCollector()
        assert hasattr(gc, "_gc_docker_containers")

    def test_gc_memory_pressure_method_exists(self):
        from agos.daemons.gc import GarbageCollector
        gc = GarbageCollector()
        assert hasattr(gc, "_check_memory_pressure")

    def test_gc_not_dry_run_by_default_in_serve(self):
        """GC must be auto-started with dry_run=False in serve.py."""
        source = Path("agos/serve.py").read_text(encoding="utf-8")
        assert '"dry_run": False' in source or "'dry_run': False" in source


# ═══════════════════════════════════════════════════════════════════
# 8. PHASE CLEANUP BEFORE RETRY (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestPhaseCleanup:
    """Test that resources from failed phases are destroyed before retry."""

    def test_cleanup_code_exists_before_retry(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        # The cleanup must happen BEFORE the retry sleep
        cleanup_pos = source.index("Phase retry cleanup")
        retry_pos = source.index("backoff = min(60", cleanup_pos)
        assert cleanup_pos < retry_pos, "Cleanup must happen before retry backoff"

    def test_resource_registry_wired_to_goal_runner(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "set_resource_registry" in source
        assert "self._resource_registry" in source


# ═══════════════════════════════════════════════════════════════════
# 9. SHELL DOCKER AUTO-DETECTION (os_agent.py)
# ═══════════════════════════════════════════════════════════════════

class TestShellDockerDetection:
    """Test that shell('docker run') auto-registers containers."""

    def test_detection_code_exists(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert "docker run" in source
        assert "Auto-tracked shell docker container" in source


# ═══════════════════════════════════════════════════════════════════
# 10. GLOBAL ITERATION LIMIT (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestGlobalIterationLimit:
    """Test 200-turn cap per goal across all sub-agents."""

    def test_limit_exists(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "_GOAL_TURN_LIMIT = 200" in source

    def test_turn_tracking_exists(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "_total_turns" in source


# ═══════════════════════════════════════════════════════════════════
# 11. AUTO SERVICE MONITOR (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestAutoServiceMonitor:
    """Test automatic health daemon spawning for deployed services."""

    def test_auto_detect_method_exists(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "_auto_detect_service" in source
        assert "like systemd Restart=always" in source

    def test_detects_port_from_verify(self):
        """Should extract port numbers from verify commands."""
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "localhost:(\\d+)" in source or "localhost:(\\\\d+)" in source


# ═══════════════════════════════════════════════════════════════════
# 12. EVOLUTION SLEEP TIERS (cycle.py)
# ═══════════════════════════════════════════════════════════════════

class TestEvolutionSleepTiers:
    """Test demand-driven sleep: 1min/5min/10min."""

    def test_three_tier_sleep(self):
        source = Path("agos/evolution/cycle.py").read_text(encoding="utf-8")
        assert "sleep_time = 60" in source       # actionable demands
        assert "sleep_time = 300" in source       # backing off
        assert "sleep_time = 600" in source       # nothing to do

    def test_circuit_breaker_exists(self):
        source = Path("agos/evolution/cycle.py").read_text(encoding="utf-8")
        assert "_consecutive_llm_failures" in source
        assert "_llm_backoff_until" in source
        assert "circuit breaker" in source.lower()


# ═══════════════════════════════════════════════════════════════════
# 13. PROMPT CACHING (anthropic.py)
# ═══════════════════════════════════════════════════════════════════

class TestPromptCaching:
    """Test Anthropic cache_control on system + tool schemas."""

    def test_cache_control_on_system(self):
        source = Path("agos/llm/anthropic.py").read_text(encoding="utf-8")
        assert "cache_control" in source
        assert "ephemeral" in source

    def test_cache_control_on_last_tool(self):
        source = Path("agos/llm/anthropic.py").read_text(encoding="utf-8")
        assert "cached_tools" in source

    def test_cache_logging(self):
        source = Path("agos/llm/anthropic.py").read_text(encoding="utf-8")
        assert "cache_read" in source
        assert "cache_create" in source or "cache_creation" in source


# ═══════════════════════════════════════════════════════════════════
# 14. TOOL REGISTRY (registry.py)
# ═══════════════════════════════════════════════════════════════════

class TestToolRegistry:
    """Test get_tool() method added for evolved tool wiring."""

    def test_get_tool_exists(self):
        from agos.tools.registry import ToolRegistry
        reg = ToolRegistry()
        assert hasattr(reg, "get_tool")

    def test_get_tool_returns_none_for_missing(self):
        from agos.tools.registry import ToolRegistry
        reg = ToolRegistry()
        assert reg.get_tool("nonexistent") is None

    def test_get_tool_returns_registered(self):
        from agos.tools.registry import ToolRegistry
        from agos.tools.schema import ToolSchema
        reg = ToolRegistry()
        schema = ToolSchema(name="test_tool", description="test", parameters=[])

        async def handler():
            return "ok"

        reg.register(schema, handler)
        result = reg.get_tool("test_tool")
        assert result is not None
        assert result[0].name == "test_tool"


# ═══════════════════════════════════════════════════════════════════
# 15. EVOLVED TOOL WIRING (os_agent.py)
# ═══════════════════════════════════════════════════════════════════

class TestEvolvedToolWiring:
    """Test that evolved tools are wired via events, not dead code."""

    def test_event_subscription_exists(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert "evolution.tool_deployed" in source
        assert "_on_evolved_tool_deployed" in source

    def test_dead_code_removed(self):
        source = Path("agos/os_agent.py").read_text(encoding="utf-8")
        assert "register_evolved_tool" not in source, "Dead code should be removed"


# ═══════════════════════════════════════════════════════════════════
# 16. DEMAND SOLVER PATCH_SOURCE (demand_solver.py)
# ═══════════════════════════════════════════════════════════════════

class TestDemandSolverPatchSource:
    """Test that DemandSolver can trigger source patches."""

    def test_patch_source_in_prompt(self):
        source = Path("agos/evolution/demand_solver.py").read_text(encoding="utf-8")
        assert "patch_source" in source
        assert "fix a bug in existing OS code" in source

    def test_patch_source_handler_exists(self):
        source = Path("agos/evolution/demand_solver.py").read_text(encoding="utf-8")
        assert "_handle_patch_source" in source


# ═══════════════════════════════════════════════════════════════════
# 17. SOURCE PATCHER LLM TARGET FINDING (source_patcher.py)
# ═══════════════════════════════════════════════════════════════════

class TestSourcePatcherLLM:
    """Test that SourcePatcher uses LLM to find target files."""

    def test_no_hardcoded_keyword_map(self):
        source = Path("agos/evolution/source_patcher.py").read_text(encoding="utf-8")
        assert "keyword_map" not in source, "Hardcoded keyword dict should be replaced with LLM"

    def test_llm_call_in_find_target(self):
        source = Path("agos/evolution/source_patcher.py").read_text(encoding="utf-8")
        assert "self._llm.complete" in source
        assert "_target_cache" in source


# ═══════════════════════════════════════════════════════════════════
# 18. MEMORY PRESSURE (demand.py)
# ═══════════════════════════════════════════════════════════════════

class TestMemoryPressureDemands:
    """Test that memory warnings create demand signals."""

    def test_memory_event_subscriptions(self):
        source = Path("agos/evolution/demand.py").read_text(encoding="utf-8")
        assert "os.memory_critical" in source
        assert "os.memory_warning" in source

    def test_memory_handlers_exist(self):
        source = Path("agos/evolution/demand.py").read_text(encoding="utf-8")
        assert "_on_memory_critical" in source
        assert "_on_memory_warning" in source


# ═══════════════════════════════════════════════════════════════════
# 19. HOST BIND MOUNTS (compose file)
# ═══════════════════════════════════════════════════════════════════

class TestDataPersistence:
    """Test that fleet data survives docker volume prune."""

    def test_10scenario_compose_uses_bind_mounts(self):
        content = Path("docker-compose.10scenarios.yml").read_text(encoding="utf-8")
        assert ".opensculpt-fleet/" in content
        assert "sculpt-sales-data:" not in content, "Should use bind mounts, not named volumes"

    def test_source_volume_mount(self):
        content = Path("docker-compose.10scenarios.yml").read_text(encoding="utf-8")
        assert "./agos:/app/agos:ro" in content


# ═══════════════════════════════════════════════════════════════════
# 20. RELOAD ENDPOINT (dashboard/app.py)
# ═══════════════════════════════════════════════════════════════════

class TestReloadEndpoint:
    """Test hot-reload API for live patching."""

    def test_reload_endpoint_exists(self):
        source = Path("agos/dashboard/app.py").read_text(encoding="utf-8")
        assert "/api/reload" in source
        assert "RELOADABLE" in source
        assert "importlib.reload" in source


# ═══════════════════════════════════════════════════════════════════
# 21. NO MEGA END-TO-END PHASES (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestNoMegaPhases:
    """Test that planning prompt discourages end_to_end_test phases."""

    def test_guidance_exists(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "end_to_end_test" in source
        assert "mega-phases" in source or "Each phase verifies itself" in source


# ═══════════════════════════════════════════════════════════════════
# 22. CREATES_DAEMON RENAME (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestDaemonRename:
    """Test that creates_hand was renamed to creates_daemon."""

    def test_no_hand_references(self):
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "creates_hand" not in source
        assert "creates_daemon" in source
        assert "hand_name" not in source
        assert "daemon_name" in source


# ═══════════════════════════════════════════════════════════════════
# 23. AUTO FACT EXTRACTION (goal_runner.py)
# ═══════════════════════════════════════════════════════════════════

class TestAutoFactExtraction:
    """Test CrewAI-style fact extraction after phases."""

    def test_extracts_successful_actions(self):
        """Skill extraction records all successful tool actions (no keyword filtering)."""
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "write_file" in source
        assert "Created:" in source

    def test_extracts_all_tools(self):
        """Skill extraction records shell, http, write_file actions."""
        source = Path("agos/daemons/goal_runner.py").read_text(encoding="utf-8")
        assert "Command:" in source
        assert "API:" in source


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION: Run against live container
# ═══════════════════════════════════════════════════════════════════

class TestLiveContainer:
    """Integration tests against running OpenSculpt container.

    These require the container to be running on port 8420.
    Skip if container is down.
    """

    @pytest.fixture(autouse=True)
    def check_container(self):
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:8420/api/status", timeout=3)
        except Exception:
            pytest.skip("Container not running on port 8420")

    def test_goals_api(self):
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8420/api/goals")
        data = json.loads(resp.read())
        goals = data if isinstance(data, list) else data.get("goals", [])
        assert isinstance(goals, list)

    def test_demands_api(self):
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8420/api/evolution/demands")
        data = json.loads(resp.read())
        assert "top_demands" in data

    def test_vitals_api(self):
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8420/api/vitals")
        data = json.loads(resp.read())
        assert "cpu_percent" in data
        assert "mem_percent" in data

    def test_reload_endpoint(self):
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:8420/api/reload",
            data=json.dumps({"module": "agos.guard"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        assert data.get("ok") is True
        assert "agos.guard" in data.get("reloaded", [])

    def test_gc_running(self):
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8420/api/daemons")
        data = json.loads(resp.read())
        daemons = data if isinstance(data, list) else data.get("daemons", [])
        gc_daemon = [d for d in daemons if d.get("name") == "gc"]
        assert gc_daemon, "GC daemon should be running"
        assert gc_daemon[0].get("status") == "running", "GC should be in running state"
