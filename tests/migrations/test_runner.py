"""Tests for the database migration runner."""

import pytest
import tempfile
import os

import aiosqlite

from agos.migrations.runner import get_schema_version, apply_migrations


# ── get_schema_version tests ────────────────────────────────────

@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_get_schema_version_empty_db(db_path):
    """Fresh database starts at version 0."""
    version = await get_schema_version(db_path)
    assert version == 0


@pytest.mark.asyncio
async def test_get_schema_version_creates_table(db_path):
    """schema_version table is created automatically."""
    await get_schema_version(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        row = await cursor.fetchone()
        assert row is not None


@pytest.mark.asyncio
async def test_get_schema_version_after_insert(db_path):
    """Returns the max version after manual insert."""
    await get_schema_version(db_path)

    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO schema_version (version) VALUES (5)")
        await db.commit()

    version = await get_schema_version(db_path)
    assert version == 5


# ── apply_migrations tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_migrations_baseline(db_path):
    """Applying migrations on a fresh DB runs the baseline m_001."""
    applied = await apply_migrations(db_path)
    assert 1 in applied

    # Running again applies nothing
    applied2 = await apply_migrations(db_path)
    assert applied2 == []


@pytest.mark.asyncio
async def test_apply_migrations_idempotent(db_path):
    """Running apply_migrations twice is safe."""
    await apply_migrations(db_path)
    await apply_migrations(db_path)

    version = await get_schema_version(db_path)
    assert version >= 1


@pytest.mark.asyncio
async def test_apply_migrations_records_version(db_path):
    """After migration, schema_version table has the correct version."""
    await apply_migrations(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT version FROM schema_version ORDER BY version")
        rows = await cursor.fetchall()
        versions = [r[0] for r in rows]
        assert 1 in versions


@pytest.mark.asyncio
async def test_apply_migrations_skips_already_applied(db_path):
    """Migrations already applied (version <= current) are skipped."""
    # Pre-set version to 999 so nothing runs
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.execute("INSERT INTO schema_version (version) VALUES (999)")
        await db.commit()

    applied = await apply_migrations(db_path)
    assert applied == []
