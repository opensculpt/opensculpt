"""Episodic Weave — what happened.

The timeline of agent actions, decisions, and observations.
Think of it as the OS's autobiography — every moment recorded,
searchable, and available to inform future decisions.
"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from agos.knowledge import db as _db
from agos.knowledge.base import BaseWeave, Thread, ThreadQuery


class EpisodicWeave(BaseWeave):
    """Time-ordered log of events. SQLite-backed.

    Every agent action, tool call, user interaction, and decision
    is recorded here. Agents can ask "what happened yesterday?"
    and get precise, time-bounded answers.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        async with _db.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS episodic (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    content TEXT NOT NULL,
                    kind TEXT DEFAULT 'event',
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    ttl_seconds INTEGER,
                    source TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_agent_time "
                "ON episodic(agent_id, created_at DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_kind "
                "ON episodic(kind)"
            )
            await db.commit()

    async def store(self, thread: Thread) -> str:
        async with _db.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO episodic "
                "(id, agent_id, content, kind, tags, metadata, created_at, ttl_seconds, source, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread.id,
                    thread.agent_id,
                    thread.content,
                    thread.kind,
                    json.dumps(thread.tags),
                    json.dumps(thread.metadata),
                    thread.created_at.isoformat(),
                    thread.ttl_seconds,
                    thread.source,
                    thread.confidence,
                ),
            )
            await db.commit()
        return thread.id

    async def query(self, q: ThreadQuery) -> list[Thread]:
        conditions = []
        params: list = []

        if q.agent_id:
            conditions.append("agent_id = ?")
            params.append(q.agent_id)
        if q.kind:
            conditions.append("kind = ?")
            params.append(q.kind)
        if q.since:
            conditions.append("created_at >= ?")
            params.append(q.since.isoformat())
        if q.until:
            conditions.append("created_at <= ?")
            params.append(q.until.isoformat())
        if q.min_confidence > 0:
            conditions.append("confidence >= ?")
            params.append(q.min_confidence)
        if q.text:
            conditions.append("content LIKE ?")
            params.append(f"%{q.text}%")
        if q.tags:
            for tag in q.tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM episodic WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(q.limit)

        threads = []
        async with _db.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                async for row in cursor:
                    threads.append(Thread(
                        id=row["id"],
                        agent_id=row["agent_id"],
                        content=row["content"],
                        kind=row["kind"],
                        tags=json.loads(row["tags"]),
                        metadata=json.loads(row["metadata"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        ttl_seconds=row["ttl_seconds"],
                        source=row["source"],
                        confidence=row["confidence"],
                    ))
        return threads

    async def delete(self, thread_id: str) -> bool:
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM episodic WHERE id = ?", (thread_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def prune(self) -> int:
        now = datetime.now().isoformat()
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM episodic WHERE ttl_seconds IS NOT NULL "
                "AND datetime(created_at, '+' || ttl_seconds || ' seconds') < ?",
                (now,),
            )
            await db.commit()
            return cursor.rowcount
