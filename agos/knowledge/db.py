"""SQLite connection helper with WAL mode and busy timeout.

Every knowledge DB connection goes through this to prevent
"database is locked" errors from concurrent access by the
OS agent, sub-agents, evolution engine, and consolidator.

NOTE: This file is critical infrastructure. The SourcePatcher
should NOT modify it — add agos/knowledge/db.py to OFF_LIMITS.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import aiosqlite


@asynccontextmanager
async def connect(db_path: str):
    """Open a SQLite connection with WAL mode and busy timeout."""
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.commit()
    except Exception:
        pass
    try:
        yield db
    finally:
        await db.close()
