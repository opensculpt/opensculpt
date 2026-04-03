"""Constraint Store — what the OS learned about this environment.

Every time a task fails because of an environment-specific reason
(SSO config, proxy, file locations, naming conventions, user preferences),
the constraint gets stored here. Future agents read relevant constraints
before acting, so the same failure never happens twice.

Constraints are the OS's institutional memory — the unwritten rules
that only get discovered through failure.

Types:
  - organizational: SSO, deployment gates, naming, compliance
  - environmental: proxy, certs, tools, paths, OS quirks
  - personal: preferred tools, work hours, communication style
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


from agos.knowledge import db as _db

_logger = logging.getLogger(__name__)


@dataclass
class Constraint:
    """A single environment constraint the OS learned."""

    id: str = ""
    type: str = "environmental"       # organizational | environmental | personal
    scope: str = "machine"            # company | machine | user | service
    key: str = ""                     # "proxy_config", "sso_provider", "tax_doc_location"
    value: str = ""                   # The actual constraint value
    description: str = ""             # Human-readable explanation
    applies_to: list[str] = field(default_factory=list)  # ["http_requests", "pip_install"]
    learned_from: str = ""            # goal/demand that taught us
    confidence: float = 0.8
    times_confirmed: int = 1          # How many times this proved correct
    shareable: bool = True            # Can be anonymized and federated?
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            raw = f"{self.type}:{self.scope}:{self.key}:{self.value[:100]}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_prompt_line(self) -> str:
        """Format for injection into LLM context."""
        applies = ", ".join(self.applies_to) if self.applies_to else "general"
        return f"- [{self.type}/{self.scope}] {self.description or self.key}: {self.value} (applies to: {applies})"

    def anonymize(self) -> dict:
        """Strip sensitive values for federation. Keep the pattern."""
        return {
            "type": self.type,
            "scope": self.scope,
            "key": self.key,
            "description": self._strip_sensitive(self.description),
            "applies_to": self.applies_to,
            "confidence": self.confidence,
            "times_confirmed": self.times_confirmed,
            "tags": self.tags,
        }

    @staticmethod
    def _strip_sensitive(text: str) -> str:
        """Remove IPs, URLs, paths, credentials from text."""
        text = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?", "<IP>", text)
        text = re.sub(r"https?://[^\s]+", "<URL>", text)
        text = re.sub(r"(/[\w./-]+){3,}", "<PATH>", text)
        text = re.sub(r"[A-Za-z0-9+/]{20,}={0,2}", "<TOKEN>", text)
        return text


# ── Simple tokenizer for relevance matching ─────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


class ConstraintStore:
    """SQLite-backed constraint store with relevance search."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create table if needed."""
        async with _db.connect(self._db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS constraints (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    applies_to TEXT DEFAULT '[]',
                    learned_from TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.8,
                    times_confirmed INTEGER DEFAULT 1,
                    shareable INTEGER DEFAULT 1,
                    tags TEXT DEFAULT '[]',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_type ON constraints(type)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_scope ON constraints(scope)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_key ON constraints(key)"
            )
            await conn.commit()

    async def store(self, constraint: Constraint) -> str:
        """Store or update a constraint. Returns the ID."""
        async with _db.connect(self._db_path) as conn:
            # Check if exists by key+scope (upsert)
            row = await conn.execute(
                "SELECT id, times_confirmed, confidence FROM constraints WHERE key = ? AND scope = ? AND type = ?",
                (constraint.key, constraint.scope, constraint.type),
            )
            existing = await row.fetchone()

            if existing:
                # Update existing — bump confidence and confirmation count
                new_confirmed = existing[1] + 1
                new_confidence = min(1.0, existing[2] + 0.05)
                await conn.execute(
                    """UPDATE constraints
                       SET value = ?, description = ?, applies_to = ?,
                           confidence = ?, times_confirmed = ?,
                           tags = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        constraint.value,
                        constraint.description,
                        json.dumps(constraint.applies_to),
                        new_confidence,
                        new_confirmed,
                        json.dumps(constraint.tags),
                        datetime.now().isoformat(),
                        existing[0],
                    ),
                )
                await conn.commit()
                _logger.info("Constraint updated: %s (confirmed %dx)", constraint.key, new_confirmed)
                return existing[0]
            else:
                await conn.execute(
                    """INSERT INTO constraints
                       (id, type, scope, key, value, description, applies_to,
                        learned_from, confidence, times_confirmed, shareable,
                        tags, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        constraint.id,
                        constraint.type,
                        constraint.scope,
                        constraint.key,
                        constraint.value,
                        constraint.description,
                        json.dumps(constraint.applies_to),
                        constraint.learned_from,
                        constraint.confidence,
                        constraint.times_confirmed,
                        1 if constraint.shareable else 0,
                        json.dumps(constraint.tags),
                        constraint.created_at,
                        constraint.updated_at,
                    ),
                )
                await conn.commit()
                _logger.info("Constraint stored: %s = %s", constraint.key, constraint.value[:50])
                return constraint.id

    async def find_relevant(
        self, task_description: str, top_k: int = 10, max_tokens: int = 2000
    ) -> list[Constraint]:
        """Find constraints relevant to a task using TF-IDF cosine similarity.

        Returns at most top_k constraints, capped at ~max_tokens of prompt text.
        """
        query_tf = _tf(_tokenize(task_description))
        if not query_tf:
            return []

        constraints = await self._all()
        if not constraints:
            return []

        scored: list[tuple[float, Constraint]] = []
        for c in constraints:
            # Build document from all text fields
            doc_text = f"{c.key} {c.value} {c.description} {' '.join(c.applies_to)} {' '.join(c.tags)}"
            doc_tf = _tf(_tokenize(doc_text))
            score = _cosine(query_tf, doc_tf)
            # Boost by confidence and confirmation count
            score *= c.confidence * min(2.0, 1.0 + 0.1 * c.times_confirmed)
            if score > 0.01:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Cap by token budget
        results: list[Constraint] = []
        token_count = 0
        for _, c in scored[:top_k]:
            line = c.to_prompt_line()
            token_count += len(line.split()) * 1.3  # rough token estimate
            if token_count > max_tokens:
                break
            results.append(c)

        return results

    async def find_by_key(self, key: str) -> Constraint | None:
        """Find a specific constraint by key."""
        async with _db.connect(self._db_path) as conn:
            row = await conn.execute(
                "SELECT * FROM constraints WHERE key = ? ORDER BY confidence DESC LIMIT 1",
                (key,),
            )
            data = await row.fetchone()
            if data:
                return self._row_to_constraint(data, row.description)
        return None

    async def find_by_scope(self, scope: str) -> list[Constraint]:
        """Get all constraints for a scope (company, machine, user, service)."""
        async with _db.connect(self._db_path) as conn:
            rows = await conn.execute(
                "SELECT * FROM constraints WHERE scope = ? ORDER BY confidence DESC",
                (scope,),
            )
            return [self._row_to_constraint(r, rows.description) async for r in rows]

    async def confirm(self, constraint_id: str) -> None:
        """Confirm a constraint proved correct again."""
        async with _db.connect(self._db_path) as conn:
            await conn.execute(
                """UPDATE constraints
                   SET times_confirmed = times_confirmed + 1,
                       confidence = MIN(1.0, confidence + 0.05),
                       updated_at = ?
                   WHERE id = ?""",
                (datetime.now().isoformat(), constraint_id),
            )
            await conn.commit()

    async def export_anonymized(self, min_confidence: float = 0.5) -> list[dict]:
        """Export shareable constraints with sensitive values stripped."""
        constraints = await self._all()
        return [
            c.anonymize()
            for c in constraints
            if c.shareable and c.confidence >= min_confidence
        ]

    async def import_federated(self, patterns: list[dict], source: str = "") -> int:
        """Import anonymized constraint patterns from federation.

        These are patterns (not exact values), so they get stored with
        lower confidence and the user's environment fills in specifics.
        """
        imported = 0
        for p in patterns:
            existing = await self.find_by_key(p.get("key", ""))
            if existing:
                continue  # Don't overwrite local knowledge with federated

            c = Constraint(
                type=p.get("type", "environmental"),
                scope=p.get("scope", "machine"),
                key=p.get("key", ""),
                value=p.get("description", ""),  # Federated has description, not value
                description=f"[federated from {source}] {p.get('description', '')}",
                applies_to=p.get("applies_to", []),
                confidence=p.get("confidence", 0.5) * 0.7,  # Trust discount
                times_confirmed=0,
                shareable=False,  # Don't re-share federated constraints
                tags=p.get("tags", []) + ["federated"],
            )
            await self.store(c)
            imported += 1

        return imported

    async def count(self) -> int:
        async with _db.connect(self._db_path) as conn:
            row = await conn.execute("SELECT COUNT(*) FROM constraints")
            result = await row.fetchone()
            return result[0] if result else 0

    async def has_data(self) -> bool:
        return await self.count() > 0

    async def _all(self) -> list[Constraint]:
        async with _db.connect(self._db_path) as conn:
            rows = await conn.execute(
                "SELECT * FROM constraints ORDER BY confidence DESC"
            )
            return [self._row_to_constraint(r, rows.description) async for r in rows]

    @staticmethod
    def _row_to_constraint(row: tuple, description: Any) -> Constraint:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        return Constraint(
            id=d["id"],
            type=d["type"],
            scope=d["scope"],
            key=d["key"],
            value=d["value"],
            description=d.get("description", ""),
            applies_to=json.loads(d.get("applies_to", "[]")),
            learned_from=d.get("learned_from", ""),
            confidence=d.get("confidence", 0.8),
            times_confirmed=d.get("times_confirmed", 1),
            shareable=bool(d.get("shareable", 1)),
            tags=json.loads(d.get("tags", "[]")),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
