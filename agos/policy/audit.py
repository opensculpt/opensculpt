"""Audit Trail — immutable log of every agent action.

Every tool call, policy check, and agent lifecycle event gets
recorded here. The audit log is append-only and timestamped.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field

from agos.types import new_id


class AuditEntry(BaseModel):
    """A single audit log entry."""

    id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_id: str = ""
    agent_name: str = ""
    action: str = ""  # "tool_call", "policy_check", "state_change", etc.
    detail: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    success: bool = True
    policy_violation: str = ""


class AuditTrail:
    """Append-only audit log backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._entries: list[AuditEntry] = []
        self._lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create the audit table if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        try:
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA busy_timeout=30000")
            await self._db.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass  # WAL mode is a performance optimization, not critical
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                agent_id TEXT,
                agent_name TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                tool_name TEXT,
                arguments TEXT,
                result TEXT,
                success INTEGER DEFAULT 1,
                policy_violation TEXT
            )
        """)
        await self._db.commit()

    async def record(self, entry: AuditEntry) -> None:
        """Record an audit entry (immutable append)."""
        async with self._lock:
            self._entries.append(entry)
            if self._db:
                await self._db.execute(
                    """INSERT INTO audit_log
                       (id, timestamp, agent_id, agent_name, action,
                        detail, tool_name, arguments, result, success,
                        policy_violation)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.id,
                        entry.timestamp.isoformat(),
                        entry.agent_id,
                        entry.agent_name,
                        entry.action,
                        entry.detail,
                        entry.tool_name,
                        str(entry.arguments),
                        entry.result,
                        int(entry.success),
                        entry.policy_violation,
                    ),
                )
                await self._db.commit()

    async def log_tool_call(
        self,
        agent_id: str,
        agent_name: str,
        tool_name: str,
        arguments: dict,
        result: str = "",
        success: bool = True,
    ) -> AuditEntry:
        """Convenience: log a tool call."""
        entry = AuditEntry(
            agent_id=agent_id,
            agent_name=agent_name,
            action="tool_call",
            detail=f"Called {tool_name}",
            tool_name=tool_name,
            arguments=arguments,
            result=result[:500],
            success=success,
        )
        await self.record(entry)
        return entry

    async def log_policy_violation(
        self,
        agent_id: str,
        agent_name: str,
        tool_name: str,
        violation: str,
    ) -> AuditEntry:
        """Convenience: log a policy violation."""
        entry = AuditEntry(
            agent_id=agent_id,
            agent_name=agent_name,
            action="policy_violation",
            detail=f"Blocked: {tool_name}",
            tool_name=tool_name,
            success=False,
            policy_violation=violation,
        )
        await self.record(entry)
        return entry

    async def log_state_change(
        self,
        agent_id: str,
        agent_name: str,
        from_state: str,
        to_state: str,
    ) -> AuditEntry:
        """Convenience: log an agent state transition."""
        entry = AuditEntry(
            agent_id=agent_id,
            agent_name=agent_name,
            action="state_change",
            detail=f"{from_state} -> {to_state}",
        )
        await self.record(entry)
        return entry

    async def query(
        self,
        agent_id: str = "",
        action: str = "",
        limit: int = 50,
    ) -> list[AuditEntry]:
        """Query the audit log with filters."""
        # In-memory fast path
        results = self._entries

        if agent_id:
            results = [e for e in results if e.agent_id == agent_id]
        if action:
            results = [e for e in results if e.action == action]

        # Most recent first
        results = sorted(results, key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    async def recent(self, limit: int = 50) -> list[AuditEntry]:
        """Get the most recent N entries (no filters)."""
        return await self.query(limit=limit)

    async def count(self) -> int:
        """Total number of audit entries."""
        return len(self._entries)

    async def violations(self, limit: int = 50) -> list[AuditEntry]:
        """Get recent policy violations."""
        return await self.query(action="policy_violation", limit=limit)

    # ── Origin derivation (OpenSeed "surgery confound" lesson) ────
    # Instead of adding an origin column + threading it through every caller,
    # derive origin from agent_id at query time.  80% of the value, 20% of
    # the churn.  If an agent_id doesn't match any prefix, "unknown" is safe.

    _ORIGIN_MAP: dict[str, str] = {
        "os_agent": "human",       # user-initiated commands
        "evolution": "evolution",   # evolution_agent, evolution_cycle, etc.
        "demand_solver": "evolution",
        "source_patcher": "evolution",
        "tool_evolver": "evolution",
        "gc": "system",
        "goal_runner": "system",
        "daemon": "system",
        "boot": "system",
    }

    @classmethod
    def derive_origin(cls, agent_id: str) -> str:
        """Derive action origin from agent_id without schema changes.

        Returns 'human', 'evolution', 'system', or 'unknown'.
        """
        aid = (agent_id or "").lower()
        for prefix, origin in cls._ORIGIN_MAP.items():
            if prefix in aid:
                return origin
        return "unknown"

    async def query_by_origin(
        self, origin: str, limit: int = 50,
    ) -> list[AuditEntry]:
        """Query audit log filtered by derived origin."""
        results = [
            e for e in self._entries
            if self.derive_origin(e.agent_id) == origin
        ]
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    async def origin_stats(self) -> dict[str, int]:
        """Count audit entries by origin — useful for measuring
        how much is human-driven vs self-evolved vs system."""
        stats: dict[str, int] = {}
        for e in self._entries:
            o = self.derive_origin(e.agent_id)
            stats[o] = stats.get(o, 0) + 1
        return stats

    def __repr__(self) -> str:
        return f"AuditTrail(entries={len(self._entries)})"
