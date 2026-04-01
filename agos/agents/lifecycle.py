"""Agent lifecycle wrapper — spawn, run work, emit completion events."""
from __future__ import annotations

from agos.types import new_id
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail


# ── Helper: register agent + emit lifecycle ──────────────────────

async def agent_run(name: str, role: str, bus: EventBus, audit: AuditTrail, work_fn):
    """Run a real agent task: emit spawn, do work, emit complete."""
    aid = new_id()
    await bus.emit("agent.spawned", {"id": aid, "agent": name, "role": role}, source="kernel")
    await audit.log_state_change(aid, name, "created", "running")

    findings = []
    try:
        findings = await work_fn(aid, name, bus, audit)
    except Exception as e:
        await bus.emit("agent.error", {"agent": name, "error": str(e)[:200]}, source="kernel")
        await audit.log_state_change(aid, name, "running", "error")
        return

    await bus.emit("agent.completed", {
        "agent": name, "findings": len(findings),
        "summary": "; ".join(f[:60] for f in findings[:3])
    }, source="kernel")
    await audit.log_state_change(aid, name, "running", "completed")

