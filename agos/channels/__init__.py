"""Notification channels — 42 adapters for webhooks, chat, email, queues, and more."""

from agos.channels.base import BaseChannel, ChannelMessage, ChannelResult, ChannelRegistry
from agos.channels.adapters import ALL_CHANNELS, register_all_channels

__all__ = [
    "BaseChannel", "ChannelMessage", "ChannelResult", "ChannelRegistry",
    "ALL_CHANNELS", "register_all_channels",
]
