"""Migration runner — applies pending migrations on startup.

Migrations are Python modules in the `agos/migrations/` directory,
named `m_NNN_description.py` where NNN is a zero-padded version number.
Each must define an `async def upgrade(db: aiosqlite.Connection)` function.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import aiosqlite


MIGRATIONS_DIR = Path(__file__).parent
MIGRATION_PREFIX = "m_"


async def get_schema_version(db_path: str) -> int:
    """Get the current schema version from the database."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.commit()

        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return row[0] if row[0] is not None else 0


async def apply_migrations(db_path: str) -> list[int]:
    """Apply all pending migrations. Returns list of applied version numbers."""
    current = await get_schema_version(db_path)
    applied: list[int] = []

    # Discover migration modules
    migration_files = sorted(MIGRATIONS_DIR.glob(f"{MIGRATION_PREFIX}*.py"))

    for mf in migration_files:
        # Extract version number: m_001_description.py -> 1
        parts = mf.stem.split("_")
        if len(parts) < 2:
            continue
        try:
            version = int(parts[1])
        except ValueError:
            continue

        if version <= current:
            continue

        # Import and run migration
        module = importlib.import_module(f"agos.migrations.{mf.stem}")
        async with aiosqlite.connect(db_path) as db:
            await module.upgrade(db)
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (version,),
            )
            await db.commit()

        applied.append(version)

    return applied
