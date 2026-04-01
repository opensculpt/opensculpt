"""Channel — async message bus for agent-to-agent communication.

Agents in a team can post messages to a shared channel and subscribe
to messages from other agents. This enables real-time coordination
without polling or shared memory hacks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

from agos.types import AgentId, ChannelId, new_id


class Message(BaseModel):
    """A message sent through a channel."""

    id: str = Field(default_factory=new_id)
    channel_id: ChannelId = ""
    sender: AgentId
    content: str
    kind: str = "text"  # text, result, request, status
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


MessageHandler = Callable[[Message], Awaitable[None]]


class Channel:
    """Async message channel for agent communication.

    Agents subscribe to a channel and receive messages in real time.
    Messages are also stored in history for late joiners.

    Usage:
        ch = Channel("team-alpha")
        ch.subscribe("agent-1", handler)
        await ch.post("agent-2", "I found the bug in line 42")
    """

    def __init__(self, channel_id: ChannelId | None = None, name: str = ""):
        self.id: ChannelId = channel_id or new_id()
        self.name = name or self.id
        self._subscribers: dict[AgentId, MessageHandler] = {}
        self._history: list[Message] = []
        self._lock = asyncio.Lock()

    def subscribe(self, agent_id: AgentId, handler: MessageHandler) -> None:
        """Subscribe an agent to this channel."""
        self._subscribers[agent_id] = handler

    def unsubscribe(self, agent_id: AgentId) -> None:
        """Remove an agent from this channel."""
        self._subscribers.pop(agent_id, None)

    async def post(
        self,
        sender: AgentId,
        content: str,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Post a message to the channel. All subscribers except sender get it."""
        msg = Message(
            channel_id=self.id,
            sender=sender,
            content=content,
            kind=kind,
            metadata=metadata or {},
        )

        async with self._lock:
            self._history.append(msg)

        # Deliver to all subscribers except the sender
        tasks = []
        for agent_id, handler in self._subscribers.items():
            if agent_id != sender:
                tasks.append(handler(msg))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return msg

    async def broadcast(
        self,
        content: str,
        kind: str = "status",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Post a system-level message to all subscribers."""
        msg = Message(
            channel_id=self.id,
            sender="system",
            content=content,
            kind=kind,
            metadata=metadata or {},
        )

        async with self._lock:
            self._history.append(msg)

        tasks = [handler(msg) for handler in self._subscribers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return msg

    @property
    def history(self) -> list[Message]:
        """Get full message history (read-only copy)."""
        return list(self._history)

    @property
    def member_count(self) -> int:
        return len(self._subscribers)

    @property
    def members(self) -> list[AgentId]:
        return list(self._subscribers.keys())
