"""Tests for the ComponentEvolver observation mechanisms.

Verifies that each evolver correctly detects gaps from audit trail data,
network probes, and environment variables.
"""

import os
import tempfile
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio

from agos.policy.audit import AuditTrail, AuditEntry
from agos.events.bus import EventBus
from agos.evolution.component_evolver import (
    AgentEvolver,
    HandEvolver,
    ProviderEvolver,
    ToolImprover,
    BrainEvolver,
    SelfImprovementLoop,
)
from agos.evolution.tool_evolver import ToolEvolver, ToolNeed


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def audit(db_path):
    trail = AuditTrail(db_path)
    await trail.initialize()
    return trail


@pytest_asyncio.fixture
async def bus():
    return EventBus()


# ── AuditTrail.recent() ───────────────────────────────────


@pytest.mark.asyncio
async def test_audit_recent_returns_entries(audit):
    """recent() delegates to query() and returns latest entries."""
    for i in range(10):
        await audit.record(AuditEntry(
            agent_name="test", action="ping", detail=f"entry-{i}", success=True,
        ))
    results = await audit.recent(limit=5)
    assert len(results) == 5
    # Most recent first
    assert results[0].detail == "entry-9"


@pytest.mark.asyncio
async def test_audit_recent_empty(audit):
    """recent() on empty trail returns empty list."""
    results = await audit.recent(limit=10)
    assert results == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. AGENT EVOLVER — detects low success rates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_agent_evolver_detects_low_success_rate(audit, bus):
    """AgentEvolver flags agents with <70% success rate."""
    # 3 successes + 7 failures = 30% success rate
    for i in range(3):
        await audit.record(AuditEntry(
            agent_name="flaky_agent", action="task", detail="ok", success=True,
        ))
    for i in range(7):
        await audit.record(AuditEntry(
            agent_name="flaky_agent", action="task",
            detail="timeout exceeded", success=False,
        ))

    evolver = AgentEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp["name"] == "flaky_agent"
    assert opp["context"]["success_rate"] == pytest.approx(0.3)
    assert opp["priority"] == pytest.approx(0.7)
    assert "timeout exceeded" in opp["context"]["recent_errors"]


@pytest.mark.asyncio
async def test_agent_evolver_ignores_healthy_agents(audit, bus):
    """Agents above 70% success rate are not flagged."""
    for i in range(8):
        await audit.record(AuditEntry(
            agent_name="good_agent", action="task", detail="done", success=True,
        ))
    for i in range(2):
        await audit.record(AuditEntry(
            agent_name="good_agent", action="task", detail="minor error", success=False,
        ))

    evolver = AgentEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_agent_evolver_ignores_low_activity(audit, bus):
    """Agents with fewer than 5 total actions are skipped."""
    for i in range(4):
        await audit.record(AuditEntry(
            agent_name="new_agent", action="task", detail="fail", success=False,
        ))

    evolver = AgentEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_agent_evolver_proposes_prompt_patch(audit, bus):
    """Proposals include prompt patches derived from error patterns."""
    for i in range(3):
        await audit.record(AuditEntry(
            agent_name="buggy", action="x", detail="ok", success=True,
        ))
    for i in range(7):
        await audit.record(AuditEntry(
            agent_name="buggy", action="x",
            detail="file not found: /tmp/x.txt", success=False,
        ))

    evolver = AgentEvolver(bus, audit)
    opportunities = await evolver.observe()
    assert len(opportunities) == 1

    proposal = await evolver.propose(opportunities[0])
    assert proposal is not None
    assert "not found" in proposal.config["prompt_patch"].lower() or "verify" in proposal.config["prompt_patch"].lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. HAND EVOLVER — detects repeated commands
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_hand_evolver_detects_repeated_commands(audit, bus):
    """HandEvolver flags commands executed 5+ times."""
    for i in range(8):
        await audit.record(AuditEntry(
            agent_name="user", action="shell_command",
            detail=f"git status --short", success=True,
        ))

    evolver = HandEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp["context"]["command"] == "git"
    assert opp["context"]["executions"] == 8
    assert opp["priority"] == pytest.approx(min(0.9, 8 / 20))


@pytest.mark.asyncio
async def test_hand_evolver_ignores_infrequent_commands(audit, bus):
    """Commands run fewer than 5 times are not flagged."""
    for i in range(4):
        await audit.record(AuditEntry(
            agent_name="user", action="shell_command",
            detail="docker ps", success=True,
        ))

    evolver = HandEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_hand_evolver_only_reads_shell_actions(audit, bus):
    """Only shell_command, os_shell, tool_execution actions are counted."""
    for i in range(10):
        await audit.record(AuditEntry(
            agent_name="user", action="some_other_action",
            detail="git push", success=True,
        ))

    evolver = HandEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_hand_evolver_proposes_hand_code(audit, bus):
    """Proposals generate valid Python Hand class code."""
    for i in range(6):
        await audit.record(AuditEntry(
            agent_name="user", action="os_shell",
            detail="python manage.py check", success=True,
        ))

    evolver = HandEvolver(bus, audit)
    opportunities = await evolver.observe()
    assert len(opportunities) >= 1

    proposal = await evolver.propose(opportunities[0])
    assert proposal is not None
    assert proposal.code  # has generated code
    assert "class EvolvedHand" in proposal.code
    assert proposal.change_type == "create"

    # Code must parse as valid Python
    import ast
    ast.parse(proposal.code)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. PROVIDER EVOLVER — detects env-var API keys
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_provider_evolver_detects_env_var(audit, bus, monkeypatch):
    """ProviderEvolver finds API keys in environment variables."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_123")

    evolver = ProviderEvolver(bus, audit)
    # Mock HTTP probes so they don't block on real network
    evolver._PROBE_TARGETS = []
    opportunities = await evolver.observe()

    env_opps = [o for o in opportunities if o["context"].get("env_var") == "GROQ_API_KEY"]
    assert len(env_opps) == 1
    assert env_opps[0]["name"] == "groq_env"
    assert env_opps[0]["priority"] == 0.8


@pytest.mark.asyncio
async def test_provider_evolver_skips_already_discovered(audit, bus, monkeypatch):
    """Already-discovered providers are not flagged again."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    evolver = ProviderEvolver(bus, audit)
    evolver._PROBE_TARGETS = []
    evolver._discovered["openai_env"] = {"name": "openai_env"}  # pre-mark

    opportunities = await evolver.observe()

    env_opps = [o for o in opportunities if o["name"] == "openai_env"]
    assert len(env_opps) == 0


@pytest.mark.asyncio
async def test_provider_evolver_proposes_config(audit, bus, monkeypatch):
    """Proposals include base_url and env_var info."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")

    evolver = ProviderEvolver(bus, audit)
    evolver._PROBE_TARGETS = []
    opportunities = await evolver.observe()
    ds_opps = [o for o in opportunities if o["name"] == "deepseek_env"]
    assert len(ds_opps) == 1

    proposal = await evolver.propose(ds_opps[0])
    assert proposal is not None
    assert "deepseek" in proposal.config["base_url"]
    assert proposal.config["env_var"] == "DEEPSEEK_API_KEY"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TOOL IMPROVER — detects high failure rates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_tool_improver_detects_failing_tool(audit, bus):
    """ToolImprover flags tools with >20% failure rate."""
    for i in range(7):
        await audit.record(AuditEntry(
            agent_name="agent1", action="tool_execution",
            detail="file_read: success", success=True,
        ))
    for i in range(5):
        await audit.record(AuditEntry(
            agent_name="agent1", action="tool_execution",
            detail="file_read: Permission denied", success=False,
        ))

    evolver = ToolImprover(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) >= 1
    opp = [o for o in opportunities if o["name"] == "file_read"][0]
    assert opp["context"]["failure_rate"] == pytest.approx(5 / 12)
    assert any("Permission denied" in e for e in opp["context"]["errors"])


@pytest.mark.asyncio
async def test_tool_improver_ignores_healthy_tool(audit, bus):
    """Tools with <=20% failure rate are not flagged."""
    for i in range(9):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="http_call: ok", success=True,
        ))
    for i in range(1):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="http_call: timeout", success=False,
        ))

    evolver = ToolImprover(bus, audit)
    opportunities = await evolver.observe()

    http_opps = [o for o in opportunities if o["name"] == "http_call"]
    assert len(http_opps) == 0


@pytest.mark.asyncio
async def test_tool_improver_proposes_improvements(audit, bus):
    """Proposals identify the right improvement types from error patterns."""
    for i in range(3):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="file_write: ok", success=True,
        ))
    for i in range(4):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="file_write: Permission denied on /etc/secret", success=False,
        ))

    evolver = ToolImprover(bus, audit)
    opportunities = await evolver.observe()
    assert len(opportunities) >= 1

    proposal = await evolver.propose(opportunities[0])
    assert proposal is not None
    assert "add_permission_check" in proposal.config["improvements"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. BRAIN EVOLVER — detects OS agent failure patterns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_brain_evolver_detects_os_failures(audit, bus):
    """BrainEvolver flags when OS agent has >3 failures."""
    for i in range(5):
        await audit.record(AuditEntry(
            agent_name="OSAgent", action="command",
            detail=f"timeout on long task {i}", success=False,
        ))
    for i in range(2):
        await audit.record(AuditEntry(
            agent_name="OSAgent", action="command",
            detail="executed ls", success=True,
        ))

    evolver = BrainEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp["name"] == "brain_improvement"
    assert len(opp["context"]["failures"]) == 5
    assert opp["priority"] == pytest.approx(min(0.8, 5 / 20))


@pytest.mark.asyncio
async def test_brain_evolver_ignores_few_failures(audit, bus):
    """BrainEvolver does not flag when failures <= 3."""
    for i in range(3):
        await audit.record(AuditEntry(
            agent_name="OSAgent", action="command",
            detail="some error", success=False,
        ))

    evolver = BrainEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_brain_evolver_ignores_other_agents(audit, bus):
    """BrainEvolver only looks at OSAgent/os_agent entries."""
    for i in range(10):
        await audit.record(AuditEntry(
            agent_name="SecurityScanner", action="scan",
            detail="scan failed", success=False,
        ))

    evolver = BrainEvolver(bus, audit)
    opportunities = await evolver.observe()

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_brain_evolver_proposes_rules_from_errors(audit, bus):
    """BrainEvolver extracts rules from error keyword patterns."""
    errors = [
        "timeout on API call",
        "file not found: /data/config.yaml",
        "connection refused to port 5432",
        "timeout waiting for response",
    ]
    for err in errors:
        await audit.record(AuditEntry(
            agent_name="os_agent", action="cmd", detail=err, success=False,
        ))

    evolver = BrainEvolver(bus, audit)
    evolver._learned_rules = []  # Reset — don't load from disk
    opportunities = await evolver.observe()
    assert len(opportunities) == 1

    proposal = await evolver.propose(opportunities[0])
    assert proposal is not None
    rules = proposal.config["new_rules"]
    assert len(rules) >= 1
    # Should have timeout-related rule
    assert any("timeout" in r.lower() or "retry" in r.lower() for r in rules)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TOOL EVOLVER — detects tool failures & missing tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_tool_evolver_detects_high_failure_tool(audit, bus):
    """ToolEvolver.observe_usage() flags tools with >30% failure rate."""
    for i in range(6):
        await audit.record(AuditEntry(
            agent_name="agent", action="tool_execution",
            detail="web_scrape: fetched ok", success=True,
        ))
    for i in range(4):
        await audit.record(AuditEntry(
            agent_name="agent", action="tool_execution",
            detail="web_scrape: connection timeout", success=False,
        ))

    evolver = ToolEvolver(event_bus=bus, audit=audit)
    needs = await evolver.observe_usage()

    failure_needs = [n for n in needs if n.source == "failure"]
    assert len(failure_needs) == 1
    assert failure_needs[0].name == "web_scrape_improved"
    assert "failure rate" in failure_needs[0].description


@pytest.mark.asyncio
async def test_tool_evolver_detects_missing_tool(audit, bus):
    """ToolEvolver.observe_usage() flags tools that agents tried to use but don't exist."""
    await audit.record(AuditEntry(
        agent_name="coder", action="tool_not_found",
        detail="'code_review' not found", success=False,
    ))
    await audit.record(AuditEntry(
        agent_name="coder", action="tool_execution",
        detail="Tool 'code_review' not found", success=False,
    ))

    evolver = ToolEvolver(event_bus=bus, audit=audit)
    needs = await evolver.observe_usage()

    gap_needs = [n for n in needs if n.source == "usage_gap"]
    assert len(gap_needs) >= 1
    assert any(n.name == "code_review" for n in gap_needs)


@pytest.mark.asyncio
async def test_tool_evolver_ignores_low_failure_tool(audit, bus):
    """ToolEvolver.observe_usage() ignores tools below 30% failure rate."""
    for i in range(9):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="git_status: ok", success=True,
        ))
    for i in range(1):
        await audit.record(AuditEntry(
            agent_name="a", action="tool_execution",
            detail="git_status: error", success=False,
        ))

    evolver = ToolEvolver(event_bus=bus, audit=audit)
    needs = await evolver.observe_usage()

    failure_needs = [n for n in needs if n.source == "failure"]
    assert len(failure_needs) == 0


@pytest.mark.asyncio
async def test_tool_evolver_arxiv_keyword_matching(bus):
    """ToolEvolver.observe_arxiv_tools() matches papers to tool needs by keyword."""
    db_path = tempfile.mkstemp(suffix=".db")[1]
    audit = AuditTrail(db_path)
    await audit.initialize()

    evolver = ToolEvolver(event_bus=bus, audit=audit)
    papers = [
        {"title": "Efficient Retrieval-Augmented Generation for Code", "abstract": "We present a new retrieval system...", "id": "2401.00001"},
        {"title": "Web Browsing Agents", "abstract": "Autonomous web browsing for tasks", "id": "2401.00002"},
        {"title": "Quantum Computing Advances", "abstract": "Nothing related to tools here", "id": "2401.00003"},
    ]

    needs = await evolver.observe_arxiv_tools(papers)

    names = [n.name for n in needs]
    assert "rag_retrieve" in names
    assert "web_browse" in names
    # Quantum paper should not match any tool
    assert not any(n.context.get("paper_id") == "2401.00003" for n in needs)

    try:
        os.unlink(db_path)
    except OSError:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. SELF-IMPROVEMENT LOOP — orchestrator scheduling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_self_improvement_loop_staggered_schedule(audit, bus):
    """SelfImprovementLoop runs one evolver per cycle, rotating every 5."""
    loop = SelfImprovementLoop(event_bus=bus, audit=audit)
    # Disable HTTP probes on provider evolver
    loop.provider_evolver._PROBE_TARGETS = []

    # Cycle 1 → offset 1 → HandEvolver
    report = await loop.tick(1)
    assert report["cycle"] == 1

    # Cycle 5 → offset 0 → AgentEvolver
    report = await loop.tick(5)
    assert report["cycle"] == 5


@pytest.mark.asyncio
async def test_self_improvement_loop_status(audit, bus):
    """status() returns current state of all evolvers."""
    loop = SelfImprovementLoop(event_bus=bus, audit=audit)
    loop.provider_evolver._PROBE_TARGETS = []
    await loop.tick(1)  # run one cycle

    status = loop.status()
    assert "agent_patches" in status
    assert "discovered_providers" in status
    assert "learned_rules" in status
    assert "tool_stats" in status
    assert status["cycle"] == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. FULL LIFECYCLE — observe → propose → apply → check
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_agent_evolver_full_lifecycle(audit, bus):
    """Full cycle: observe → propose → snapshot → apply → health_check."""
    # Seed failures
    for i in range(3):
        await audit.record(AuditEntry(
            agent_name="slow_bot", action="task", detail="done", success=True,
        ))
    for i in range(7):
        await audit.record(AuditEntry(
            agent_name="slow_bot", action="task",
            detail="timeout waiting for API", success=False,
        ))

    evolver = AgentEvolver(bus, audit)
    results = await evolver.evolve_cycle()

    assert len(results) == 1
    assert results[0].success is True
    assert len(results[0].changes) >= 1

    # Verify prompt patches are stored
    patch = evolver.get_prompt_patches("slow_bot")
    assert "timeout" in patch.lower() or "slow_bot" in patch.lower()


@pytest.mark.asyncio
async def test_brain_evolver_full_lifecycle(audit, bus, tmp_path):
    """Full cycle: observe → propose → apply → health_check for BrainEvolver."""
    for i in range(5):
        await audit.record(AuditEntry(
            agent_name="OSAgent", action="cmd",
            detail="connection refused to database", success=False,
        ))

    evolver = BrainEvolver(bus, audit)
    # Use isolated evolved dir so prior test runs don't interfere
    evolver.evolved_dir = tmp_path / "evolved"
    evolver._learned_rules = []  # Reset — don't load from disk
    results = await evolver.evolve_cycle()

    assert len(results) == 1
    assert results[0].success is True

    rules = evolver.get_learned_rules()
    assert len(rules) >= 1
    assert any("connect" in r.lower() or "service" in r.lower() or "verify" in r.lower() for r in rules)
