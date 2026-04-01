"""Semantic Weave — what the OS understands.

Not just storage — understanding. Facts, concepts, and relationships
indexed for meaning-based retrieval. When an agent asks "what do I
know about authentication?", this weave returns semantically relevant
knowledge, not keyword matches.

Uses a simple but effective approach: TF-IDF-like term vectors stored
in SQLite. No external vector DB needed. For v0.1, this provides
good-enough semantic search without heavy dependencies.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime

import aiosqlite

from agos.knowledge import db as _db
from agos.knowledge.base import BaseWeave, Thread, ThreadQuery


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer — lowercase, split on non-alphanumeric."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Term frequency — normalized count of each token."""
    counts = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {t: c / total for t, c in counts.items()}


class SemanticWeave(BaseWeave):
    """Meaning-based knowledge store. SQLite-backed.

    Stores threads with term-frequency vectors for lightweight
    semantic search. Searches by cosine similarity between the
    query's terms and stored threads' terms.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._temperature: float = 0.0  # 0 = deterministic top-k (default)
        self._track_access: bool = False  # when True, record access counts

    async def initialize(self) -> None:
        async with _db.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS semantic (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    content TEXT NOT NULL,
                    kind TEXT DEFAULT 'fact',
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    ttl_seconds INTEGER,
                    source TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0,
                    terms TEXT DEFAULT '{}'
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sem_agent "
                "ON semantic(agent_id)"
            )
            # Safe migration: add access tracking columns if missing
            try:
                await db.execute(
                    "ALTER TABLE semantic ADD COLUMN access_count INTEGER DEFAULT 0"
                )
            except Exception:
                pass  # column already exists
            try:
                await db.execute(
                    "ALTER TABLE semantic ADD COLUMN last_accessed TEXT"
                )
            except Exception:
                pass
            await db.commit()

    async def store(self, thread: Thread) -> str:
        tokens = _tokenize(thread.content)
        tf = _compute_tf(tokens)

        async with _db.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO semantic "
                "(id, agent_id, content, kind, tags, metadata, created_at, "
                "ttl_seconds, source, confidence, terms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    json.dumps(tf),
                ),
            )
            await db.commit()
        return thread.id

    async def query(self, q: ThreadQuery) -> list[Thread]:
        if not q.text:
            # Fall back to simple filtering if no semantic query
            return await self._query_filtered(q)

        query_tokens = _tokenize(q.text)
        query_tf = _compute_tf(query_tokens)

        # Load all candidate threads and score them
        conditions = []
        params: list = []
        if q.agent_id:
            conditions.append("agent_id = ?")
            params.append(q.agent_id)
        if q.kind:
            conditions.append("kind = ?")
            params.append(q.kind)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM semantic WHERE {where}"

        scored: list[tuple[float, Thread]] = []
        async with _db.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                async for row in cursor:
                    stored_tf = json.loads(row["terms"])
                    score = self._cosine_similarity(query_tf, stored_tf)
                    if score > 0.01:  # minimum relevance threshold
                        thread = Thread(
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
                        )
                        scored.append((score, thread))

        # Sort by relevance
        scored.sort(key=lambda x: x[0], reverse=True)

        # If temperature > 0, use softmax sampling for diverse retrieval
        if self._temperature > 0 and len(scored) > q.limit:
            selected = self._softmax_sample(scored, q.limit, self._temperature)
        else:
            selected = [t for _, t in scored[: q.limit]]

        # Track access if enabled
        if self._track_access and selected:
            await self._record_access_batch([t.id for t in selected])

        return selected

    async def _query_filtered(self, q: ThreadQuery) -> list[Thread]:
        conditions = []
        params: list = []
        if q.agent_id:
            conditions.append("agent_id = ?")
            params.append(q.agent_id)
        if q.kind:
            conditions.append("kind = ?")
            params.append(q.kind)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM semantic WHERE {where} ORDER BY created_at DESC LIMIT ?"
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

    @staticmethod
    def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
        """Cosine similarity between two term-frequency vectors."""
        common = set(a.keys()) & set(b.keys())
        if not common:
            return 0.0
        dot = sum(a[k] * b[k] for k in common)
        mag_a = math.sqrt(sum(v * v for v in a.values()))
        mag_b = math.sqrt(sum(v * v for v in b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    # ── Softmax + Access Tracking ────────────────────────────────

    def set_temperature(self, temperature: float) -> None:
        """Set softmax temperature for probabilistic retrieval.

        0.0 = deterministic top-k (default).
        Higher values = more diversity in results.
        """
        self._temperature = max(0.0, temperature)

    def enable_access_tracking(self, enabled: bool = True) -> None:
        """Enable/disable recording access counts on query results."""
        self._track_access = enabled

    @staticmethod
    def _softmax_sample(
        scored: list[tuple[float, Thread]],
        k: int,
        temperature: float,
    ) -> list[Thread]:
        """Sample k items from scored list using softmax probabilities."""
        import random

        if not scored or k <= 0:
            return []

        scores = [s for s, _ in scored]
        max_score = max(scores)
        # Subtract max for numerical stability
        exp_scores = [math.exp((s - max_score) / temperature) for s in scores]
        total = sum(exp_scores)
        if total == 0:
            return [t for _, t in scored[:k]]
        probs = [e / total for e in exp_scores]

        # Weighted sampling without replacement
        indices = list(range(len(scored)))
        selected_indices = []
        remaining_probs = list(probs)
        remaining_indices = list(indices)

        for _ in range(min(k, len(scored))):
            total_p = sum(remaining_probs)
            if total_p == 0:
                break
            normalized = [p / total_p for p in remaining_probs]
            chosen_idx = random.choices(range(len(remaining_indices)), weights=normalized, k=1)[0]
            selected_indices.append(remaining_indices[chosen_idx])
            remaining_indices.pop(chosen_idx)
            remaining_probs.pop(chosen_idx)

        return [scored[i][1] for i in selected_indices]

    async def _record_access_batch(self, thread_ids: list[str]) -> None:
        """Increment access count and update last_accessed for threads."""
        now = datetime.now().isoformat()
        async with _db.connect(self._db_path) as db:
            for tid in thread_ids:
                await db.execute(
                    "UPDATE semantic SET access_count = access_count + 1, "
                    "last_accessed = ? WHERE id = ?",
                    (now, tid),
                )
            await db.commit()

    async def record_access(self, thread_id: str) -> None:
        """Record a single access to a thread."""
        await self._record_access_batch([thread_id])

    async def decay_confidence(
        self, days_inactive: int = 30, decay_factor: float = 0.95
    ) -> int:
        """Decay confidence for threads that haven't been accessed recently.

        Threads not accessed in `days_inactive` days get their confidence
        multiplied by `decay_factor`. Returns count of decayed threads.
        """
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days_inactive)).isoformat()
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE semantic SET confidence = confidence * ? "
                "WHERE (last_accessed IS NOT NULL AND last_accessed < ?) "
                "OR (last_accessed IS NULL AND created_at < ?)",
                (decay_factor, cutoff, cutoff),
            )
            await db.commit()
            return cursor.rowcount

    async def delete(self, thread_id: str) -> bool:
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM semantic WHERE id = ?", (thread_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def prune(self) -> int:
        now = datetime.now().isoformat()
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM semantic WHERE ttl_seconds IS NOT NULL "
                "AND datetime(created_at, '+' || ttl_seconds || ' seconds') < ?",
                (now,),
            )
            await db.commit()
            return cursor.rowcount
