"""Tests for the audit trail."""

import tempfile

import pytest
import pytest_asyncio

from agos.policy.audit import AuditTrail, AuditEntry


@pytest_asyncio.fixture
async def audit():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    trail = AuditTrail(db_path)
    await trail.initialize()
    return trail


@pytest.mark.asyncio
async def test_record_and_query(audit):
    trail = audit
    entry = AuditEntry(
        agent_id="a1",
        agent_name="coder",
        action="tool_call",
        detail="Called file_read",
    )
    await trail.record(entry)

    results = await trail.query()
    assert len(results) == 1
    assert results[0].action == "tool_call"


@pytest.mark.asyncio
async def test_log_tool_call(audit):
    trail = audit
    entry = await trail.log_tool_call(
        agent_id="a1",
        agent_name="coder",
        tool_name="file_read",
        arguments={"path": "/tmp/test.py"},
        result="file contents here",
        success=True,
    )

    assert entry.action == "tool_call"
    assert entry.tool_name == "file_read"
    assert entry.success is True

    results = await trail.query(agent_id="a1")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_log_policy_violation(audit):
    trail = audit
    entry = await trail.log_policy_violation(
        agent_id="a2",
        agent_name="rogue",
        tool_name="shell_exec",
        violation="Agent 'rogue' not allowed to use shell_exec",
    )

    assert entry.success is False
    assert entry.policy_violation != ""

    violations = await trail.violations()
    assert len(violations) == 1
    assert violations[0].tool_name == "shell_exec"


@pytest.mark.asyncio
async def test_log_state_change(audit):
    trail = audit
    entry = await trail.log_state_change(
        agent_id="a3",
        agent_name="analyst",
        from_state="ready",
        to_state="running",
    )

    assert entry.action == "state_change"
    assert "ready -> running" in entry.detail


@pytest.mark.asyncio
async def test_query_by_action(audit):
    trail = audit
    await trail.log_tool_call("a1", "coder", "file_read", {})
    await trail.log_tool_call("a1", "coder", "shell_exec", {})
    await trail.log_state_change("a1", "coder", "ready", "running")

    tool_calls = await trail.query(action="tool_call")
    assert len(tool_calls) == 2

    state_changes = await trail.query(action="state_change")
    assert len(state_changes) == 1


@pytest.mark.asyncio
async def test_count(audit):
    trail = audit
    assert await trail.count() == 0

    await trail.log_tool_call("a1", "coder", "file_read", {})
    await trail.log_tool_call("a1", "coder", "file_write", {})

    assert await trail.count() == 2


@pytest.mark.asyncio
async def test_query_limit(audit):
    trail = audit
    for i in range(10):
        await trail.log_tool_call("a1", "coder", f"tool_{i}", {})

    results = await trail.query(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_repr(audit):
    trail = audit
    assert "AuditTrail" in repr(trail)


@pytest.mark.asyncio
async def test_multiple_agents(audit):
    trail = audit
    await trail.log_tool_call("a1", "coder", "file_read", {})
    await trail.log_tool_call("a2", "analyst", "web_search", {})
    await trail.log_tool_call("a1", "coder", "file_write", {})

    coder_entries = await trail.query(agent_id="a1")
    assert len(coder_entries) == 2

    analyst_entries = await trail.query(agent_id="a2")
    assert len(analyst_entries) == 1
