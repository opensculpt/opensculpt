"""Knowledge Graph — entity relationships in The Loom.

Not just "what happened" or "what we know" — but HOW things connect.
Users, projects, files, concepts, agents — all linked in a graph
that agents can traverse to understand context.

Example: "user:abhis" --[works_on]--> "project:agos" --[uses]--> "tool:claude"
         "file:agent.py" --[defines]--> "class:Agent" --[depends_on]--> "class:StateMachine"
"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from agos.knowledge import db as _db
from agos.types import new_id


class Edge:
    """A relationship between two entities."""

    def __init__(
        self,
        source: str,
        relation: str,
        target: str,
        weight: float = 1.0,
        metadata: dict | None = None,
        edge_id: str | None = None,
    ):
        self.id = edge_id or new_id()
        self.source = source
        self.relation = relation
        self.target = target
        self.weight = weight
        self.metadata = metadata or {}
        self.created_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
            "weight": self.weight,
            "metadata": self.metadata,
        }


class KnowledgeGraph:
    """Graph of entity relationships. SQLite-backed.

    Entities are strings (e.g., "user:abhis", "file:agent.py").
    Relations are labeled edges (e.g., "works_on", "depends_on").
    Agents can traverse the graph to understand context and connections.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        async with _db.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS kg_edges (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_kg_source ON kg_edges(source)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_kg_target ON kg_edges(target)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_kg_relation ON kg_edges(relation)"
            )
            await db.commit()

    async def link(self, source: str, relation: str, target: str,
                   weight: float = 1.0, metadata: dict | None = None) -> Edge:
        """Create a relationship between two entities."""
        edge = Edge(source, relation, target, weight, metadata)
        async with _db.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO kg_edges "
                "(id, source, relation, target, weight, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    edge.id,
                    edge.source,
                    edge.relation,
                    edge.target,
                    edge.weight,
                    json.dumps(edge.metadata),
                    edge.created_at.isoformat(),
                ),
            )
            await db.commit()
        return edge

    async def connections(self, entity: str, relation: str | None = None,
                          direction: str = "outgoing") -> list[Edge]:
        """Find all connections from/to an entity.

        direction: "outgoing" (entity → ?), "incoming" (? → entity), "both"
        """
        edges = []
        async with _db.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            if direction in ("outgoing", "both"):
                sql = "SELECT * FROM kg_edges WHERE source = ?"
                params: list = [entity]
                if relation:
                    sql += " AND relation = ?"
                    params.append(relation)
                async with db.execute(sql, params) as cursor:
                    async for row in cursor:
                        edges.append(self._row_to_edge(row))

            if direction in ("incoming", "both"):
                sql = "SELECT * FROM kg_edges WHERE target = ?"
                params = [entity]
                if relation:
                    sql += " AND relation = ?"
                    params.append(relation)
                async with db.execute(sql, params) as cursor:
                    async for row in cursor:
                        edges.append(self._row_to_edge(row))

        return edges

    async def neighbors(self, entity: str, depth: int = 1) -> set[str]:
        """Find all entities within N hops of an entity."""
        visited: set[str] = {entity}
        frontier: set[str] = {entity}

        for _ in range(depth):
            next_frontier: set[str] = set()
            for e in frontier:
                conns = await self.connections(e, direction="both")
                for edge in conns:
                    other = edge.target if edge.source == e else edge.source
                    if other not in visited:
                        visited.add(other)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        visited.discard(entity)
        return visited

    async def entities(self) -> set[str]:
        """List all unique entities in the graph."""
        result: set[str] = set()
        async with _db.connect(self._db_path) as db:
            async with db.execute("SELECT DISTINCT source FROM kg_edges") as cursor:
                async for row in cursor:
                    result.add(row[0])
            async with db.execute("SELECT DISTINCT target FROM kg_edges") as cursor:
                async for row in cursor:
                    result.add(row[0])
        return result

    async def unlink(self, edge_id: str) -> bool:
        """Remove an edge."""
        async with _db.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM kg_edges WHERE id = ?", (edge_id,))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_edge(row) -> Edge:
        return Edge(
            source=row["source"],
            relation=row["relation"],
            target=row["target"],
            weight=row["weight"],
            metadata=json.loads(row["metadata"]),
            edge_id=row["id"],
        )
