"""Trigger base — how the OS perceives the world.

Triggers are event sources that activate agents. They bridge the
gap between "I have to ask" and "the OS just knows."
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

from agos.types import new_id

TriggerCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TriggerConfig(BaseModel):
    """Configuration for a trigger instance."""

    id: str = Field(default_factory=new_id)
    kind: str  # "file_watch", "schedule", "webhook"
    description: str = ""
    intent: str = ""  # what to tell the agent when triggered
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    active: bool = True


class BaseTrigger(ABC):
    """Abstract trigger that watches for events and fires callbacks."""

    def __init__(self, config: TriggerConfig):
        self.config = config
        self._callback: TriggerCallback | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    def on_fire(self, callback: TriggerCallback) -> None:
        """Set the callback to invoke when this trigger fires."""
        self._callback = callback

    async def start(self) -> None:
        """Start watching for events."""
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _fire(self, event_data: dict[str, Any]) -> None:
        """Fire the trigger with event data."""
        if self._callback:
            await self._callback(event_data)

    @abstractmethod
    async def _watch_loop(self) -> None:
        """The main watch loop — subclasses implement this."""
        ...

    @property
    def is_running(self) -> bool:
        return self._running
