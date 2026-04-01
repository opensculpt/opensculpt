"""Channel base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ChannelMessage(BaseModel):
    """A message to send through a channel."""
    text: str
    title: str = ""
    level: str = "info"  # info, warning, error, critical
    data: dict[str, Any] = Field(default_factory=dict)


class ChannelResult(BaseModel):
    channel: str
    success: bool
    detail: str = ""


class BaseChannel(ABC):
    """Abstract base for notification/communication channels."""

    name: str = "base"
    description: str = ""
    icon: str = ""

    @abstractmethod
    async def send(self, message: ChannelMessage, config: dict[str, Any]) -> ChannelResult:
        """Send a message through this channel."""
        ...

    def config_schema(self) -> list[dict]:
        """Config fields this channel needs. Override in subclasses for UI forms."""
        return []

    def validate_config(self, config: dict[str, Any]) -> str | None:
        """Return error string if config is invalid, None if ok."""
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "icon": self.icon}


class ChannelRegistry:
    """Central registry for all notification channels."""

    def __init__(self) -> None:
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)

    def list_channels(self) -> list[dict]:
        return [ch.to_dict() for ch in self._channels.values()]

    @property
    def count(self) -> int:
        return len(self._channels)

    async def broadcast(self, message: ChannelMessage, configs: dict[str, dict]) -> list[ChannelResult]:
        """Send to multiple channels."""
        results = []
        for name, config in configs.items():
            ch = self._channels.get(name)
            if ch:
                try:
                    r = await ch.send(message, config)
                    results.append(r)
                except Exception as e:
                    results.append(ChannelResult(channel=name, success=False, detail=str(e)))
        return results
