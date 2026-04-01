"""Event Bus — pub/sub with wildcard matching.

All subsystems emit events. The bus routes them to subscribers.
Supports topic wildcards: "agent.*" matches "agent.started", "agent.completed".
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

from agos.types import new_id

EventHandler = Callable[["Event"], Awaitable[None]]


class Event(BaseModel):
    """A system event."""

    id: str = Field(default_factory=new_id)
    topic: str
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EventBus:
    """Async pub/sub event bus with wildcard topic matching.

    Subscribe to "agent.*" to receive all agent events.
    Subscribe to "*" to receive everything.
    """

    def __init__(self, history_limit: int = 500) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._lock = asyncio.Lock()
        self._ws_connections: list[Any] = []

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        """Subscribe to events matching a topic pattern."""
        self._subscribers[pattern].append(handler)

    def unsubscribe(self, pattern: str, handler: EventHandler) -> None:
        """Remove a subscription."""
        handlers = self._subscribers.get(pattern, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, topic: str, data: dict | None = None, source: str = "") -> Event:
        """Emit an event to all matching subscribers."""
        event = Event(topic=topic, data=data or {}, source=source)

        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit:]

        # Find matching handlers
        tasks = []
        for pattern, handlers in self._subscribers.items():
            if fnmatch.fnmatch(topic, pattern):
                for handler in handlers:
                    tasks.append(handler(event))

        # Broadcast to WebSocket connections
        for ws_send in self._ws_connections:
            tasks.append(ws_send(event))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return event

    def add_ws_connection(self, send_fn: EventHandler) -> None:
        """Register a WebSocket connection for live event streaming."""
        self._ws_connections.append(send_fn)

    def remove_ws_connection(self, send_fn: EventHandler) -> None:
        """Remove a WebSocket connection."""
        if send_fn in self._ws_connections:
            self._ws_connections.remove(send_fn)

    def history(self, topic_filter: str = "*", limit: int = 50) -> list[Event]:
        """Get recent events, optionally filtered by topic pattern."""
        if topic_filter == "*":
            events = self._history
        else:
            events = [
                e for e in self._history
                if fnmatch.fnmatch(e.topic, topic_filter)
            ]
        return list(reversed(events[-limit:]))

    @property
    def subscriber_count(self) -> int:
        return sum(len(h) for h in self._subscribers.values())

    @property
    def ws_connection_count(self) -> int:
        return len(self._ws_connections)

    def topics(self) -> list[str]:
        """Get all topics that have been emitted."""
        return list({e.topic for e in self._history})
