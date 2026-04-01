"""MemoryNote — Zettelkasten-style linked knowledge (A-MEM inspired).

Traditional memory stores facts as isolated rows. MemoryNotes are
interconnected: each note has keywords, links to related notes, and
a context description. When you add a new note, it finds and links
to related existing notes — and can update their context too.

Inspired by: A-MEM (Agentic Memory for LLM Agents, 2025)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.knowledge.semantic import SemanticWeave
from agos.knowledge.graph import KnowledgeGraph
from agos.knowledge.base import Thread, ThreadQuery


class MemoryNote(BaseModel):
    """A single Zettelkasten-style note in the memory network."""

    id: str = Field(default_factory=new_id)
    content: str
    context: str = ""  # why this matters / when it's relevant
    keywords: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)  # IDs of related notes
    source: str = ""
    importance: float = 0.5  # 0.0 to 1.0 — evolved over time
    access_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class NoteStore:
    """Manages MemoryNotes with automatic linking and evolution.

    When you add a note, the store:
    1. Extracts keywords from the content
    2. Searches for related existing notes (semantic similarity)
    3. Creates bidirectional links
    4. Updates the knowledge graph
    5. Optionally evolves related notes' context

    This creates an ever-growing knowledge network that gets
    richer with every interaction.
    """

    def __init__(self, semantic: SemanticWeave, graph: KnowledgeGraph):
        self._semantic = semantic
        self._graph = graph
        self._notes: dict[str, MemoryNote] = {}

    async def add(
        self,
        content: str,
        context: str = "",
        keywords: list[str] | None = None,
        source: str = "",
        importance: float = 0.5,
    ) -> MemoryNote:
        """Add a new note and automatically link it to related notes."""
        # Auto-extract keywords if not provided
        if not keywords:
            keywords = self._extract_keywords(content)

        note = MemoryNote(
            content=content,
            context=context,
            keywords=keywords,
            source=source,
            importance=importance,
        )

        # Find related notes via semantic search
        related = await self._find_related(content, limit=5)

        # Create bidirectional links
        for related_note in related:
            note.links.append(related_note.id)
            if note.id not in related_note.links:
                related_note.links.append(note.id)
                related_note.updated_at = datetime.now()

        # Store the note
        self._notes[note.id] = note

        # Also store in semantic weave for persistence + search
        thread = Thread(
            id=note.id,
            content=content,
            kind="note",
            tags=keywords,
            metadata={
                "context": context,
                "links": note.links,
                "importance": importance,
                "source": source,
            },
            source=source,
            confidence=importance,
        )
        await self._semantic.store(thread)

        # Update knowledge graph
        for linked_id in note.links:
            await self._graph.link(
                source=f"note:{note.id}",
                relation="related_to",
                target=f"note:{linked_id}",
            )

        # Link keywords as entities
        for kw in keywords:
            await self._graph.link(
                source=f"note:{note.id}",
                relation="about",
                target=f"topic:{kw}",
            )

        return note

    async def get(self, note_id: str) -> MemoryNote | None:
        """Retrieve a note and bump its access count."""
        note = self._notes.get(note_id)
        if note:
            note.access_count += 1
        return note

    async def search(self, query: str, limit: int = 10) -> list[MemoryNote]:
        """Search notes by semantic similarity."""
        results = await self._semantic.query(
            ThreadQuery(text=query, kind="note", limit=limit)
        )
        notes = []
        for thread in results:
            note = self._notes.get(thread.id)
            if note:
                note.access_count += 1
                notes.append(note)
        return notes

    async def get_linked(self, note_id: str) -> list[MemoryNote]:
        """Get all notes linked to a given note."""
        note = self._notes.get(note_id)
        if not note:
            return []
        return [
            self._notes[lid]
            for lid in note.links
            if lid in self._notes
        ]

    async def evolve(self, note_id: str, new_context: str) -> MemoryNote | None:
        """Update a note's context — memory evolution.

        When new information arrives that changes how we should
        interpret an existing memory, call this to evolve it.
        """
        note = self._notes.get(note_id)
        if not note:
            return None
        note.context = new_context
        note.updated_at = datetime.now()
        return note

    async def boost(self, note_id: str, amount: float = 0.1) -> None:
        """Increase a note's importance (reinforcement)."""
        note = self._notes.get(note_id)
        if note:
            note.importance = min(1.0, note.importance + amount)

    async def decay(self, factor: float = 0.95) -> int:
        """Decay importance of all notes (forgetting curve).

        Notes that are frequently accessed resist decay.
        Returns count of notes that decayed below threshold.
        """
        pruned = 0
        to_remove = []
        for note in self._notes.values():
            # Recently accessed notes resist decay
            if note.access_count > 0:
                note.access_count = max(0, note.access_count - 1)
                continue
            note.importance *= factor
            if note.importance < 0.01:
                to_remove.append(note.id)
                pruned += 1

        for nid in to_remove:
            del self._notes[nid]

        return pruned

    async def by_topic(self, topic: str) -> list[MemoryNote]:
        """Find all notes about a specific topic via the knowledge graph."""
        conns = await self._graph.connections(f"topic:{topic}")
        notes = []
        for conn in conns:
            # conn.target or conn.source contains note:XXX
            nid = conn.source.replace("note:", "") if conn.source.startswith("note:") else conn.target.replace("note:", "")
            note = self._notes.get(nid)
            if note:
                notes.append(note)
        return notes

    def stats(self) -> dict[str, Any]:
        """Get memory network statistics."""
        notes = list(self._notes.values())
        if not notes:
            return {"total": 0, "avg_links": 0, "avg_importance": 0}
        total_links = sum(len(n.links) for n in notes)
        avg_importance = sum(n.importance for n in notes) / len(notes)
        return {
            "total": len(notes),
            "avg_links": total_links / len(notes),
            "avg_importance": round(avg_importance, 3),
            "most_connected": max(notes, key=lambda n: len(n.links)).id if notes else None,
            "most_important": max(notes, key=lambda n: n.importance).id if notes else None,
        }

    async def _find_related(self, content: str, limit: int = 5) -> list[MemoryNote]:
        """Find existing notes related to the given content."""
        if not self._notes:
            return []
        results = await self._semantic.query(
            ThreadQuery(text=content, kind="note", limit=limit)
        )
        return [
            self._notes[r.id]
            for r in results
            if r.id in self._notes
        ]

    @staticmethod
    def _extract_keywords(content: str, max_keywords: int = 5) -> list[str]:
        """Simple keyword extraction — split, filter stopwords, take top by length."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "out", "off", "over",
            "under", "again", "further", "then", "once", "here", "there", "when",
            "where", "why", "how", "all", "both", "each", "few", "more", "most",
            "other", "some", "such", "no", "nor", "not", "only", "own", "same",
            "so", "than", "too", "very", "just", "because", "but", "and", "or",
            "if", "while", "about", "up", "down", "it", "its", "this", "that",
            "these", "those", "i", "you", "he", "she", "we", "they", "what",
        }
        words = content.lower().split()
        # Filter: no stopwords, no short words, alphanumeric only
        candidates = [
            w for w in words
            if w not in stopwords and len(w) > 2 and w.isalpha()
        ]
        # Deduplicate preserving order
        seen = set()
        unique = []
        for w in candidates:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return unique[:max_keywords]
