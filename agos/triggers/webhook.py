"""Webhook trigger — HTTP endpoint that fires agents.

External services can POST to a webhook URL to trigger agent actions.
GitHub webhooks, Stripe events, monitoring alerts — all become
agent activations.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agos.triggers.base import BaseTrigger, TriggerConfig


class WebhookTrigger(BaseTrigger):
    """Receives HTTP webhooks and fires agent actions.

    Config params:
        path: str — URL path for this webhook (e.g., "/hooks/github")
        secret: str — optional shared secret for validation

    Note: The actual HTTP server is managed by the TriggerManager.
    This trigger registers a handler for a specific path.
    """

    def __init__(self, config: TriggerConfig):
        super().__init__(config)
        self._path = config.params.get("path", f"/hooks/{config.id}")
        self._secret = config.params.get("secret", "")
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def path(self) -> str:
        return self._path

    async def receive(self, payload: dict[str, Any]) -> None:
        """Called by the HTTP server when a webhook is received."""
        await self._queue.put(payload)

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._fire({
                    "trigger_kind": "webhook",
                    "path": self._path,
                    "payload": payload,
                })
            except asyncio.TimeoutError:
                continue
