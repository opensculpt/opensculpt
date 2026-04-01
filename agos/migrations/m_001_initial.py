"""Migration 001: Establish baseline schema.

This is a no-op — the initial schema is created by TheLoom.initialize().
Recording version 1 means all future migrations start from here.
"""

from __future__ import annotations

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """No-op: the initial schema is created by TheLoom.initialize()."""
    pass
