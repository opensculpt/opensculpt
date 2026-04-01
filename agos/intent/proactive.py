"""Proactive Intelligence — the OS suggests before you ask.

Pattern detectors monitor the knowledge system and produce Suggestions:
- "You edited auth.py 3 times today, want me to run tests?"
- "5 recent failures in the build, want me to investigate?"
- "shell_exec used 8 times today, want me to create a shortcut?"
- "No activity in 2 days, want a status check?"

Detectors are pluggable. The ProactiveEngine runs them all and manages
the suggestion lifecycle (create, dismiss, act on).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.knowledge.base import Thread, ThreadQuery
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus


class Suggestion(BaseModel):
    """A proactive suggestion from the OS."""

    id: str = Field(default_factory=new_id)
    detector_name: str
    description: str
    confidence: float = 0.7
    suggested_action: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    dismissed: bool = False


class BaseDetector(ABC):
    """Detects a pattern in the knowledge system and produces suggestions."""

    name: str = ""

    @abstractmethod
    async def detect(
        self, loom: TheLoom, event_bus: EventBus | None = None
    ) -> list[Suggestion]:
        """Analyze the knowledge system and return suggestions."""
        ...


class RepetitiveEditDetector(BaseDetector):
    """Detects repeated edits to the same file — suggests tests or review."""

    name = "repetitive_edit"

    def __init__(self, threshold: int = 3) -> None:
        self._threshold = threshold

    async def detect(
        self, loom: TheLoom, event_bus: EventBus | None = None
    ) -> list[Suggestion]:
        suggestions = []

        # Look for file entities in the graph with many connections
        try:
            entities = await loom.graph.entities()
        except Exception:
            return suggestions

        file_entities = [e for e in entities if e.startswith("file:")]

        for entity in file_entities:
            conns = await loom.graph.connections(entity, direction="incoming")
            edit_conns = [c for c in conns if c.relation in ("edited", "modified", "wrote")]

            if len(edit_conns) >= self._threshold:
                filename = entity.replace("file:", "")
                suggestions.append(Suggestion(
                    detector_name=self.name,
                    description=f"'{filename}' has been edited {len(edit_conns)} times recently",
                    confidence=min(0.5 + len(edit_conns) * 0.1, 0.95),
                    suggested_action=f"run tests for {filename}",
                    context={
                        "file": filename,
                        "edit_count": len(edit_conns),
                    },
                ))

        return suggestions


class FailurePatternDetector(BaseDetector):
    """Detects repeated failures — suggests investigation."""

    name = "failure_pattern"

    def __init__(self, threshold: int = 3) -> None:
        self._threshold = threshold

    async def detect(
        self, loom: TheLoom, event_bus: EventBus | None = None
    ) -> list[Suggestion]:
        suggestions = []

        # Query episodic weave for recent error events
        q = ThreadQuery(
            kind="error",
            limit=50,
        )
        try:
            errors = await loom.episodic.query(q)
        except Exception:
            return suggestions

        if len(errors) < self._threshold:
            # Also check for events with "error" or "failure" in content
            q2 = ThreadQuery(text="error failure failed", limit=50)
            try:
                more = await loom.episodic.query(q2)
                errors.extend(more)
            except Exception:
                pass

        if len(errors) >= self._threshold:
            # Group by common words to find patterns
            recent = errors[:10]
            summary = "; ".join(e.content[:60] for e in recent[:3])
            suggestions.append(Suggestion(
                detector_name=self.name,
                description=f"{len(errors)} recent failures detected",
                confidence=min(0.5 + len(errors) * 0.05, 0.9),
                suggested_action="investigate recurring failures",
                context={
                    "error_count": len(errors),
                    "sample": summary,
                },
            ))

        return suggestions


class FrequentToolDetector(BaseDetector):
    """Detects frequently used tools — suggests automation."""

    name = "frequent_tool"

    def __init__(self, threshold: int = 5) -> None:
        self._threshold = threshold

    async def detect(
        self, loom: TheLoom, event_bus: EventBus | None = None
    ) -> list[Suggestion]:
        suggestions = []

        try:
            entities = await loom.graph.entities()
        except Exception:
            return suggestions

        tool_entities = [e for e in entities if e.startswith("tool:")]

        for entity in tool_entities:
            conns = await loom.graph.connections(entity, direction="incoming")
            use_conns = [c for c in conns if c.relation in ("used_tool", "executed")]

            if len(use_conns) >= self._threshold:
                tool_name = entity.replace("tool:", "")
                suggestions.append(Suggestion(
                    detector_name=self.name,
                    description=f"'{tool_name}' has been used {len(use_conns)} times",
                    confidence=0.6,
                    suggested_action=f"create a trigger or shortcut for {tool_name}",
                    context={
                        "tool": tool_name,
                        "use_count": len(use_conns),
                    },
                ))

        return suggestions


class IdleProjectDetector(BaseDetector):
    """Detects inactivity — suggests check-in."""

    name = "idle_project"

    def __init__(self, idle_hours: int = 48) -> None:
        self._idle_hours = idle_hours

    async def detect(
        self, loom: TheLoom, event_bus: EventBus | None = None
    ) -> list[Suggestion]:
        suggestions = []

        # Check timeline for recent activity
        try:
            events = await loom.timeline(limit=1)
        except Exception:
            return suggestions

        if not events:
            suggestions.append(Suggestion(
                detector_name=self.name,
                description="No activity recorded yet",
                confidence=0.5,
                suggested_action="start working on something",
                context={"idle_hours": 0},
            ))
            return suggestions

        last_event = events[0]
        idle_duration = datetime.now() - last_event.created_at
        idle_hours = idle_duration.total_seconds() / 3600

        if idle_hours >= self._idle_hours:
            suggestions.append(Suggestion(
                detector_name=self.name,
                description=f"No activity for {idle_hours:.0f} hours",
                confidence=min(0.4 + idle_hours / 200, 0.85),
                suggested_action="check in on project status",
                context={
                    "idle_hours": round(idle_hours, 1),
                    "last_event": last_event.content[:80],
                },
            ))

        return suggestions


class ProactiveEngine:
    """Runs pattern detectors and manages suggestions."""

    def __init__(
        self,
        loom: TheLoom | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._loom = loom
        self._event_bus = event_bus
        self._detectors: list[BaseDetector] = []
        self._suggestions: list[Suggestion] = []

    def register_detector(self, detector: BaseDetector) -> None:
        """Register a pattern detector."""
        self._detectors.append(detector)

    async def scan(self) -> list[Suggestion]:
        """Run all detectors and collect new suggestions."""
        if not self._loom:
            return []

        new_suggestions: list[Suggestion] = []

        for detector in self._detectors:
            try:
                found = await detector.detect(self._loom, self._event_bus)
                new_suggestions.extend(found)
            except Exception:
                pass

        # Store new suggestions
        self._suggestions.extend(new_suggestions)

        # Emit events for each suggestion
        if self._event_bus:
            for s in new_suggestions:
                await self._event_bus.emit(
                    "proactive.suggestion",
                    {
                        "suggestion_id": s.id,
                        "detector": s.detector_name,
                        "description": s.description,
                        "action": s.suggested_action,
                        "confidence": s.confidence,
                    },
                    source="proactive_engine",
                )

        # Store in knowledge system
        if self._loom:
            for s in new_suggestions:
                thread = Thread(
                    content=f"Suggestion: {s.description}\nAction: {s.suggested_action}",
                    kind="suggestion",
                    tags=["proactive", s.detector_name],
                    metadata={
                        "suggestion_id": s.id,
                        "detector": s.detector_name,
                        "confidence": s.confidence,
                        "context": s.context,
                    },
                    source="proactive_engine",
                    confidence=s.confidence,
                )
                await self._loom.semantic.store(thread)

        return new_suggestions

    async def get_suggestions(
        self, limit: int = 10, include_dismissed: bool = False
    ) -> list[Suggestion]:
        """Get suggestions, newest first."""
        results = self._suggestions
        if not include_dismissed:
            results = [s for s in results if not s.dismissed]
        results = sorted(results, key=lambda s: s.created_at, reverse=True)
        return results[:limit]

    async def dismiss(self, suggestion_id: str) -> bool:
        """Dismiss a suggestion."""
        for s in self._suggestions:
            if s.id == suggestion_id:
                s.dismissed = True
                return True
        return False

    async def act_on(
        self, suggestion_id: str, runtime: Any = None
    ) -> str | None:
        """Act on a suggestion by spawning an agent.

        Returns the agent output or None if suggestion not found.
        """
        suggestion = None
        for s in self._suggestions:
            if s.id == suggestion_id:
                suggestion = s
                break

        if not suggestion or not runtime:
            return None

        from agos.intent.personas import ORCHESTRATOR

        agent = await runtime.spawn(
            ORCHESTRATOR,
            user_message=suggestion.suggested_action,
        )
        result = await agent.wait()

        suggestion.dismissed = True
        return result
