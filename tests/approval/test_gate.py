"""Tests for approval gate — human-in-the-loop tool call approval."""

from __future__ import annotations

import asyncio


from agos.approval.gate import (
    ApprovalMode,
    ApprovalGate,
    ApprovalRequest,
    DANGEROUS_TOOLS,
)
from agos.events.bus import EventBus


# ── ApprovalMode tests ─────────────────────────────────────────


def test_approval_modes():
    assert ApprovalMode.AUTO.value == "auto"
    assert ApprovalMode.CONFIRM_DANGEROUS.value == "confirm-dangerous"
    assert ApprovalMode.CONFIRM_ALL.value == "confirm-all"


def test_approval_request_model():
    req = ApprovalRequest(tool_name="shell", arguments={"command": "ls"})
    assert req.tool_name == "shell"
    assert req.id  # auto-generated
    assert req.timestamp  # auto-generated


# ── AUTO mode ──────────────────────────────────────────────────


async def test_auto_mode_always_approves():
    gate = ApprovalGate(mode=ApprovalMode.AUTO)
    assert await gate.check("shell", {"command": "rm -rf /"}) is True
    assert await gate.check("read_file", {"path": "/etc/passwd"}) is True
    assert await gate.check("http", {"url": "http://evil.com"}) is True


async def test_auto_mode_no_pending():
    gate = ApprovalGate(mode=ApprovalMode.AUTO)
    await gate.check("shell", {"command": "ls"})
    assert gate.pending_requests() == []


# ── CONFIRM_DANGEROUS mode ────────────────────────────────────


async def test_confirm_dangerous_approves_safe_tools():
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_DANGEROUS)
    # Tools NOT in DANGEROUS_TOOLS should auto-approve
    assert await gate.check("read_file", {"path": "/tmp"}) is True
    assert await gate.check("spawn_agent", {"name": "test"}) is True
    assert await gate.check("check_agent", {"name": "test"}) is True
    assert await gate.check("list_agents", {}) is True


async def test_confirm_dangerous_blocks_shell():
    """Shell tool should require approval in confirm-dangerous mode."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_DANGEROUS, timeout_seconds=1)

    # This will timeout after 1 second → returns False
    result = await gate.check("shell", {"command": "ls"})
    assert result is False


async def test_confirm_dangerous_blocks_all_dangerous():
    """All tools in DANGEROUS_TOOLS should require approval."""
    for tool in ["shell", "write_file", "http", "python"]:
        assert tool in DANGEROUS_TOOLS


# ── CONFIRM_ALL mode ──────────────────────────────────────────


async def test_confirm_all_blocks_everything():
    """Even safe tools need approval in confirm-all mode."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, timeout_seconds=1)
    result = await gate.check("read_file", {"path": "/tmp"})
    assert result is False


# ── Approval response flow ────────────────────────────────────


async def test_approval_response_unblocks():
    """respond(True) should unblock a pending check()."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, timeout_seconds=5)

    async def _approve_after_delay():
        await asyncio.sleep(0.1)
        pending = gate.pending_requests()
        assert len(pending) == 1
        await gate.respond(pending[0]["id"], approved=True)

    task = asyncio.create_task(_approve_after_delay())
    result = await gate.check("echo", {"message": "test"})
    await task

    assert result is True


async def test_rejection_response_returns_false():
    """respond(False) should make check() return False."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, timeout_seconds=5)

    async def _reject_after_delay():
        await asyncio.sleep(0.1)
        pending = gate.pending_requests()
        assert len(pending) == 1
        await gate.respond(pending[0]["id"], approved=False, reason="too dangerous")

    task = asyncio.create_task(_reject_after_delay())
    result = await gate.check("shell", {"command": "rm -rf /"})
    await task

    assert result is False


async def test_timeout_returns_false():
    """No response within timeout should return False."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, timeout_seconds=0)
    # timeout=0 means immediate timeout
    result = await gate.check("echo", {"message": "test"})
    assert result is False


async def test_pending_requests_listed():
    """pending_requests() should show waiting items."""
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, timeout_seconds=10)

    # Start a check in the background (will block waiting for approval)
    task = asyncio.create_task(gate.check("shell", {"command": "ls"}))
    await asyncio.sleep(0.05)

    pending = gate.pending_requests()
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "shell"

    # Resolve to clean up
    await gate.respond(pending[0]["id"], approved=True)
    await task


async def test_respond_to_unknown_returns_false():
    """Responding to a non-existent request should return False."""
    gate = ApprovalGate(mode=ApprovalMode.AUTO)
    result = await gate.respond("nonexistent-id", approved=True)
    assert result is False


async def test_event_emitted_on_request():
    """EventBus should receive approval.requested event."""
    bus = EventBus()
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_ALL, event_bus=bus, timeout_seconds=1)

    events = []

    async def _collect(e):
        events.append(e)

    bus.subscribe("approval.*", _collect)

    # Will timeout, but event should still be emitted
    await gate.check("shell", {"command": "ls"})

    assert len(events) >= 1
    assert events[0].topic == "approval.requested"
    assert events[0].data["tool_name"] == "shell"


async def test_set_mode():
    """set_mode should change the approval mode."""
    gate = ApprovalGate(mode=ApprovalMode.AUTO)
    assert gate.mode == ApprovalMode.AUTO

    gate.set_mode(ApprovalMode.CONFIRM_ALL)
    assert gate.mode == ApprovalMode.CONFIRM_ALL

    gate.set_mode(ApprovalMode.CONFIRM_DANGEROUS)
    assert gate.mode == ApprovalMode.CONFIRM_DANGEROUS
