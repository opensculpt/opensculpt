"""A2A Client — discovers and delegates tasks to remote A2A agents.

Follows the same httpx-based async pattern as agos/mcp/client.py.

Usage:
    client = A2AClient()
    card = await client.discover("https://remote-agent.example.com")
    task = await client.send_message(
        "https://remote-agent.example.com",
        A2AMessage(parts=[A2APart(text="analyze this data")])
    )
    result = await client.get_task(
        "https://remote-agent.example.com", task["taskId"]
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import orjson

from agos.a2a.models import (
    A2AMessage,
    AgentCard,
    JsonRpcRequest,
)

_logger = logging.getLogger(__name__)


class A2AClient:
    """HTTP client for interacting with remote A2A agents."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def discover(self, base_url: str) -> AgentCard:
        """Fetch an agent's card from /.well-known/agent-card.json."""
        url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return AgentCard(**data)

    async def send_message(
        self, base_url: str, message: A2AMessage,
    ) -> dict[str, Any]:
        """Send a message to a remote agent, creating a task."""
        rpc = JsonRpcRequest(
            method="message/send",
            id=1,
            params={"message": message.model_dump(by_alias=True)},
        )
        return await self._rpc_call(base_url, rpc)

    async def get_task(self, base_url: str, task_id: str) -> dict[str, Any]:
        """Poll the status of a remote task."""
        rpc = JsonRpcRequest(
            method="tasks/get",
            id=1,
            params={"taskId": task_id},
        )
        return await self._rpc_call(base_url, rpc)

    async def cancel_task(self, base_url: str, task_id: str) -> dict[str, Any]:
        """Cancel a remote task."""
        rpc = JsonRpcRequest(
            method="tasks/cancel",
            id=1,
            params={"taskId": task_id},
        )
        return await self._rpc_call(base_url, rpc)

    async def _rpc_call(
        self, base_url: str, request: JsonRpcRequest,
    ) -> dict[str, Any]:
        """Execute a JSON-RPC call against a remote A2A endpoint."""
        url = f"{base_url.rstrip('/')}/a2a"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=request.model_dump())
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(
                    f"A2A RPC error: {data['error'].get('message', data['error'])}"
                )
            return data.get("result", {})


class A2ADirectory:
    """Local registry of known remote A2A agents.

    Persists to disk so discovered agents survive restarts.
    Provides skill-based matching for intent routing.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._agents: dict[str, AgentCard] = {}  # url → card
        self._state_path = state_path
        self._client = A2AClient()
        if state_path and state_path.exists():
            self._load()

    async def register(self, base_url: str) -> AgentCard:
        """Discover a remote agent and add it to the directory."""
        card = await self._client.discover(base_url)
        card.url = base_url.rstrip("/")
        self._agents[card.url] = card
        self._save()
        _logger.info("Registered A2A agent: %s at %s", card.name, card.url)
        return card

    def unregister(self, base_url: str) -> None:
        """Remove an agent from the directory."""
        url = base_url.rstrip("/")
        self._agents.pop(url, None)
        self._save()

    def find_by_skill(self, keywords: list[str]) -> list[tuple[AgentCard, AgentCard.__class__]]:
        """Find agents whose skills match the given keywords.

        Returns list of (card, matching_skill) tuples, sorted by
        number of tag matches (best match first).
        """
        results: list[tuple[AgentCard, Any, int]] = []

        kw_lower = [k.lower() for k in keywords]

        for card in self._agents.values():
            for skill in card.skills:
                # Count matches across tags, name, description, examples
                score = 0
                searchable = (
                    [t.lower() for t in skill.tags]
                    + [skill.name.lower(), skill.description.lower()]
                    + [e.lower() for e in skill.examples]
                )
                for kw in kw_lower:
                    if any(kw in s for s in searchable):
                        score += 1

                if score > 0:
                    results.append((card, skill, score))

        results.sort(key=lambda x: x[2], reverse=True)
        return [(card, skill) for card, skill, _ in results]

    def list_agents(self) -> list[dict]:
        """List all known remote agents."""
        return [
            {
                "name": card.name,
                "url": card.url,
                "description": card.description,
                "skills": [s.name for s in card.skills],
                "version": card.version,
            }
            for card in self._agents.values()
        ]

    def _save(self) -> None:
        if not self._state_path:
            return
        data = {
            url: card.model_dump(by_alias=True)
            for url, card in self._agents.items()
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_bytes(orjson.dumps(data))

    def _load(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            raw = orjson.loads(self._state_path.read_bytes())
            for url, card_data in raw.items():
                self._agents[url] = AgentCard(**card_data)
            _logger.info("Loaded %d A2A agents from directory", len(self._agents))
        except Exception as e:
            _logger.warning("Failed to load A2A directory: %s", e)
