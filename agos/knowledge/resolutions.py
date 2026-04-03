"""Resolution Cache — how problems were solved.

When a demand is resolved (locally or via federation), the resolution
pattern gets cached here. Before attempting any fix, the OS checks
this cache first — a high-confidence match means instant resolution
instead of full investigation.

Resolutions store KNOWLEDGE (strategy, steps) not CODE. Each
instance's Claude Code writes environment-appropriate code based
on the resolution knowledge.

Federation shares anonymized resolutions across instances.
Outcome feedback adjusts confidence: patterns that work across
environments rise, fragile ones sink.
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
class Resolution:
    """A cached resolution pattern."""

    id: str = ""
    symptom: str = ""                           # "EspoCRM returning 502"
    symptom_fingerprint: str = ""               # Normalized: "service_http_502"
    root_cause: str = ""                        # "PHP-FPM pool exhausted"
    root_cause_category: str = ""               # "resource_exhaustion"
    fix_strategy: str = ""                      # HOW to fix (knowledge, not code)
    investigation_steps: list[str] = field(default_factory=list)
    environment_match: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.7
    success_rate: float = 1.0
    times_used: int = 1
    times_succeeded: int = 1
    source: str = "local"                       # "local" | "federated"
    source_instance: str = ""
    tags: list[str] = field(default_factory=list)
    domain: str = ""                            # "sales", "devops", "support", etc.
    created_at: str = ""
    last_used_at: str = ""

    def __post_init__(self):
        if not self.id:
            raw = f"{self.symptom_fingerprint}:{self.root_cause_category}:{self.fix_strategy[:100]}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.last_used_at:
            self.last_used_at = self.created_at
        if not self.symptom_fingerprint and self.symptom:
            self.symptom_fingerprint = _fingerprint(self.symptom)

    def to_prompt_line(self) -> str:
        """Format for injection into LLM context."""
        steps = " → ".join(self.investigation_steps[:3]) if self.investigation_steps else ""
        return (
            f"- [{self.root_cause_category}] {self.symptom}: "
            f"root cause = {self.root_cause}. "
            f"Fix: {self.fix_strategy}"
            f"{f' (steps: {steps})' if steps else ''}"
            f" [confidence={self.confidence:.0%}, used {self.times_used}x]"
        )

    def anonymize(self) -> dict:
        """Strip sensitive data for federation."""
        return {
            "symptom_fingerprint": self.symptom_fingerprint,
            "root_cause": self.root_cause,
            "root_cause_category": self.root_cause_category,
            "fix_strategy": self.fix_strategy,
            "investigation_steps": self.investigation_steps,
            "environment_match": {
                k: v for k, v in self.environment_match.items()
                if k in ("has_docker", "has_apt", "in_container", "os_family", "has_systemd")
            },
            "confidence": self.confidence,
            "success_rate": self.success_rate,
            "times_used": self.times_used,
            "times_succeeded": self.times_succeeded,
            "tags": self.tags,
            "domain": self.domain,
        }


def _fingerprint(symptom: str) -> str:
    """Normalize a symptom into a fingerprint for matching."""
    text = symptom.lower().strip()
    # Remove specific values, keep structure
    text = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?", "IP", text)
    text = re.sub(r"https?://[^\s]+", "URL", text)
    text = re.sub(r"port \d+", "port N", text)
    text = re.sub(r"\d{3,}", "N", text)
    # Tokenize and sort for order-independent matching
    tokens = sorted(set(re.findall(r"[a-z_]+", text)))
    return "_".join(tokens[:10])


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


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


class ResolutionCache:
    """SQLite-backed resolution pattern cache."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        async with _db.connect(self._db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS resolutions (
                    id TEXT PRIMARY KEY,
                    symptom TEXT NOT NULL,
                    symptom_fingerprint TEXT NOT NULL,
                    root_cause TEXT DEFAULT '',
                    root_cause_category TEXT DEFAULT '',
                    fix_strategy TEXT DEFAULT '',
                    investigation_steps TEXT DEFAULT '[]',
                    environment_match TEXT DEFAULT '{}',
                    confidence REAL DEFAULT 0.7,
                    success_rate REAL DEFAULT 1.0,
                    times_used INTEGER DEFAULT 1,
                    times_succeeded INTEGER DEFAULT 1,
                    source TEXT DEFAULT 'local',
                    source_instance TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    domain TEXT DEFAULT '',
                    created_at TEXT,
                    last_used_at TEXT
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_fp ON resolutions(symptom_fingerprint)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_cat ON resolutions(root_cause_category)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_domain ON resolutions(domain)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_conf ON resolutions(confidence DESC)"
            )
            await conn.commit()

    async def record(self, resolution: Resolution) -> str:
        """Record a resolution. Upserts by fingerprint + category."""
        async with _db.connect(self._db_path) as conn:
            row = await conn.execute(
                """SELECT id, times_used, times_succeeded, confidence
                   FROM resolutions
                   WHERE symptom_fingerprint = ? AND root_cause_category = ?""",
                (resolution.symptom_fingerprint, resolution.root_cause_category),
            )
            existing = await row.fetchone()

            if existing:
                new_used = existing[1] + 1
                new_succeeded = existing[2] + 1
                new_rate = new_succeeded / new_used
                new_confidence = min(1.0, existing[3] + 0.05)
                await conn.execute(
                    """UPDATE resolutions
                       SET times_used = ?, times_succeeded = ?,
                           success_rate = ?, confidence = ?,
                           last_used_at = ?, fix_strategy = ?
                       WHERE id = ?""",
                    (new_used, new_succeeded, new_rate, new_confidence,
                     datetime.now().isoformat(), resolution.fix_strategy, existing[0]),
                )
                await conn.commit()
                _logger.info("Resolution updated: %s (used %dx)", resolution.symptom_fingerprint, new_used)
                return existing[0]
            else:
                await conn.execute(
                    """INSERT INTO resolutions
                       (id, symptom, symptom_fingerprint, root_cause,
                        root_cause_category, fix_strategy, investigation_steps,
                        environment_match, confidence, success_rate,
                        times_used, times_succeeded, source, source_instance,
                        tags, domain, created_at, last_used_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        resolution.id, resolution.symptom,
                        resolution.symptom_fingerprint, resolution.root_cause,
                        resolution.root_cause_category, resolution.fix_strategy,
                        json.dumps(resolution.investigation_steps),
                        json.dumps(resolution.environment_match),
                        resolution.confidence, resolution.success_rate,
                        resolution.times_used, resolution.times_succeeded,
                        resolution.source, resolution.source_instance,
                        json.dumps(resolution.tags), resolution.domain,
                        resolution.created_at, resolution.last_used_at,
                    ),
                )
                await conn.commit()
                _logger.info("Resolution cached: %s → %s", resolution.symptom_fingerprint, resolution.fix_strategy[:60])
                return resolution.id

    async def find(
        self, symptom: str, env: dict | None = None,
        domain: str = "", top_k: int = 3,
    ) -> list[Resolution]:
        """Find resolutions matching a symptom.

        Uses fingerprint match first, then falls back to semantic similarity.
        Filters by environment compatibility if env is provided.
        """
        fingerprint = _fingerprint(symptom)
        results: list[Resolution] = []

        # Exact fingerprint match (fast path)
        async with _db.connect(self._db_path) as conn:
            rows = await conn.execute(
                """SELECT * FROM resolutions
                   WHERE symptom_fingerprint = ?
                   ORDER BY confidence DESC, times_used DESC
                   LIMIT ?""",
                (fingerprint, top_k),
            )
            async for row in rows:
                r = self._row_to_resolution(row, rows.description)
                if self._env_compatible(r, env):
                    results.append(r)

        if results:
            return results[:top_k]

        # Semantic fallback — search all resolutions by cosine similarity
        all_resolutions = await self._all()
        query_tf = _tf(_tokenize(symptom))

        scored: list[tuple[float, Resolution]] = []
        for r in all_resolutions:
            doc_text = f"{r.symptom} {r.root_cause} {r.fix_strategy} {' '.join(r.tags)}"
            doc_tf_val = _tf(_tokenize(doc_text))
            score = _cosine(query_tf, doc_tf_val)
            score *= r.confidence
            if domain and r.domain and r.domain != domain:
                score *= 0.5  # Penalize cross-domain
            if score > 0.05 and self._env_compatible(r, env):
                scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    async def update_outcome(self, resolution_id: str, succeeded: bool) -> None:
        """Report whether a resolution worked. Adjusts confidence and success_rate."""
        async with _db.connect(self._db_path) as conn:
            row = await conn.execute(
                "SELECT times_used, times_succeeded, confidence FROM resolutions WHERE id = ?",
                (resolution_id,),
            )
            data = await row.fetchone()
            if not data:
                return

            new_used = data[0] + 1
            new_succeeded = data[1] + (1 if succeeded else 0)
            new_rate = new_succeeded / new_used
            # Confidence adjusts based on outcome
            delta = 0.05 if succeeded else -0.1
            new_confidence = max(0.0, min(1.0, data[2] + delta))

            await conn.execute(
                """UPDATE resolutions
                   SET times_used = ?, times_succeeded = ?,
                       success_rate = ?, confidence = ?,
                       last_used_at = ?
                   WHERE id = ?""",
                (new_used, new_succeeded, new_rate, new_confidence,
                 datetime.now().isoformat(), resolution_id),
            )
            await conn.commit()

            if new_rate < 0.3 and new_used >= 5:
                _logger.warning(
                    "Resolution %s has low success rate (%.0f%% over %d uses) — consider removing",
                    resolution_id, new_rate * 100, new_used,
                )

    async def export(self, min_confidence: float = 0.5, domain: str = "") -> list[dict]:
        """Export anonymized resolutions for federation."""
        all_r = await self._all()
        return [
            r.anonymize()
            for r in all_r
            if r.confidence >= min_confidence
            and (not domain or r.domain == domain or not r.domain)
        ]

    async def import_federated(
        self, patterns: list[dict], source_instance: str = ""
    ) -> int:
        """Import federated resolution patterns. Trust discount applied."""
        imported = 0
        for p in patterns:
            fp = p.get("symptom_fingerprint", "")
            cat = p.get("root_cause_category", "")
            if not fp:
                continue

            # Check if we already have this
            async with _db.connect(self._db_path) as conn:
                row = await conn.execute(
                    "SELECT id FROM resolutions WHERE symptom_fingerprint = ? AND root_cause_category = ?",
                    (fp, cat),
                )
                if await row.fetchone():
                    continue  # Don't overwrite local with federated

            r = Resolution(
                symptom=fp,  # Federated doesn't have raw symptom
                symptom_fingerprint=fp,
                root_cause=p.get("root_cause", ""),
                root_cause_category=cat,
                fix_strategy=p.get("fix_strategy", ""),
                investigation_steps=p.get("investigation_steps", []),
                environment_match=p.get("environment_match", {}),
                confidence=p.get("confidence", 0.5) * 0.7,  # Trust discount
                success_rate=p.get("success_rate", 0.5),
                times_used=p.get("times_used", 1),
                times_succeeded=p.get("times_succeeded", 1),
                source="federated",
                source_instance=source_instance,
                tags=p.get("tags", []) + ["federated"],
                domain=p.get("domain", ""),
            )
            await self.record(r)
            imported += 1

        return imported

    async def prune(self, max_age_days: int = 90, min_confidence: float = 0.1) -> int:
        """Remove stale resolutions. Returns count pruned."""
        _cutoff = datetime.now().isoformat()  # Simplified — decay by confidence
        async with _db.connect(self._db_path) as conn:
            result = await conn.execute(
                "DELETE FROM resolutions WHERE confidence < ? AND source = 'federated'",
                (min_confidence,),
            )
            await conn.commit()
            return result.rowcount

    async def count(self) -> int:
        async with _db.connect(self._db_path) as conn:
            row = await conn.execute("SELECT COUNT(*) FROM resolutions")
            result = await row.fetchone()
            return result[0] if result else 0

    async def _all(self) -> list[Resolution]:
        async with _db.connect(self._db_path) as conn:
            rows = await conn.execute(
                "SELECT * FROM resolutions ORDER BY confidence DESC"
            )
            return [self._row_to_resolution(r, rows.description) async for r in rows]

    @staticmethod
    def _env_compatible(resolution: Resolution, env: dict | None) -> bool:
        """Check if a resolution's environment requirements match."""
        if not env or not resolution.environment_match:
            return True
        for key, required in resolution.environment_match.items():
            if key in env and env[key] != required:
                return False
        return True

    @staticmethod
    def _row_to_resolution(row: tuple, description: Any) -> Resolution:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        return Resolution(
            id=d["id"],
            symptom=d.get("symptom", ""),
            symptom_fingerprint=d.get("symptom_fingerprint", ""),
            root_cause=d.get("root_cause", ""),
            root_cause_category=d.get("root_cause_category", ""),
            fix_strategy=d.get("fix_strategy", ""),
            investigation_steps=json.loads(d.get("investigation_steps", "[]")),
            environment_match=json.loads(d.get("environment_match", "{}")),
            confidence=d.get("confidence", 0.7),
            success_rate=d.get("success_rate", 1.0),
            times_used=d.get("times_used", 1),
            times_succeeded=d.get("times_succeeded", 1),
            source=d.get("source", "local"),
            source_instance=d.get("source_instance", ""),
            tags=json.loads(d.get("tags", "[]")),
            domain=d.get("domain", ""),
            created_at=d.get("created_at", ""),
            last_used_at=d.get("last_used_at", ""),
        )
