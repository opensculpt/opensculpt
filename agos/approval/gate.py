"""Approval Gate — human-in-the-loop for dangerous tool calls.

Sits between the OS agent's tool execution and actual tool invocation.
When active, dangerous tool calls pause and wait for human approval
via the dashboard before proceeding.

Modes:
  - AUTO: execute immediately (current behavior, default)
  - CONFIRM_DANGEROUS: shell/write/http/python need approval
  - CONFIRM_ALL: every tool call needs approval

Usage:
    gate = ApprovalGate(mode=ApprovalMode.CONFIRM_DANGEROUS, event_bus=bus)
    # In the agent's tool loop:
    if await gate.check("shell", {"command": "rm -rf /"}):
        result = await tools.execute("shell", {"command": "rm -rf /"})
    else:
        # Human rejected it
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id

_logger = logging.getLogger(__name__)


class ApprovalMode(str, Enum):
    AUTO = "auto"
    CONFIRM_DANGEROUS = "confirm-dangerous"
    CONFIRM_ALL = "confirm-all"


DANGEROUS_TOOLS = frozenset({
    "shell", "write_file", "http", "python",
    "shell_exec", "file_write", "python_exec", "http_request",
})


class ApprovalRequest(BaseModel):
    """A pending approval request shown in the dashboard."""

    id: str = Field(default_factory=new_id)
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ApprovalGate:
    """Controls whether tool calls proceed or wait for human approval.

    The gate uses asyncio.Event for async wait/notify between the agent
    (which calls check()) and the dashboard (which calls respond()).
    """

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.AUTO,
        event_bus: Any | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self._mode = mode
        self._bus = event_bus
        self._timeout = timeout_seconds
        self._pending: dict[str, dict] = {}

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    def set_mode(self, mode: ApprovalMode) -> None:
        self._mode = mode
        _logger.info("Approval mode changed to: %s", mode.value)

    async def check(self, tool_name: str, arguments: dict) -> bool:
        """Check if a tool call needs approval. Blocks until approved/rejected.

        Returns True if approved (or auto-approved), False if rejected or timed out.
        """
        if self._mode == ApprovalMode.AUTO:
            return True

        if (
            self._mode == ApprovalMode.CONFIRM_DANGEROUS
            and tool_name not in DANGEROUS_TOOLS
        ):
            return True

        return await self._request_approval(tool_name, arguments)

    async def _request_approval(self, tool_name: str, arguments: dict) -> bool:
        """Create an approval request and wait for a response."""
        request = ApprovalRequest(
            tool_name=tool_name,
            arguments=_truncate_args(arguments),
        )

        event = asyncio.Event()
        self._pending[request.id] = {
            "request": request,
            "event": event,
            "approved": None,
        }

        _logger.info("Approval requested for tool '%s' (id=%s)", tool_name, request.id)

        # Notify dashboard via event bus
        if self._bus:
            await self._bus.emit("approval.requested", {
                "id": request.id,
                "tool_name": tool_name,
                "arguments": _truncate_args(arguments),
            }, source="approval_gate")

        # Wait for human response
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            _logger.warning("Approval timed out for tool '%s' (id=%s)", tool_name, request.id)
            self._pending.pop(request.id, None)
            return False

        entry = self._pending.pop(request.id, None)
        if entry is None:
            return False

        approved = entry.get("approved", False)
        _logger.info(
            "Approval %s for tool '%s' (id=%s)",
            "granted" if approved else "denied", tool_name, request.id,
        )
        return approved

    async def respond(self, request_id: str, approved: bool, reason: str = "") -> bool:
        """Respond to a pending approval request (called by dashboard).

        Returns True if the request was found and responded to.
        """
        entry = self._pending.get(request_id)
        if entry is None:
            return False

        entry["approved"] = approved
        entry["event"].set()

        if self._bus:
            await self._bus.emit("approval.responded", {
                "id": request_id,
                "approved": approved,
                "reason": reason,
            }, source="approval_gate")

        return True

    def pending_requests(self) -> list[dict]:
        """List all pending approval requests."""
        return [
            {
                "id": v["request"].id,
                "tool_name": v["request"].tool_name,
                "arguments": v["request"].arguments,
                "timestamp": v["request"].timestamp,
            }
            for v in self._pending.values()
        ]


def _truncate_args(args: dict, max_len: int = 200) -> dict:
    """Truncate argument values for display."""
    return {
        k: (str(v)[:max_len] + "..." if len(str(v)) > max_len else str(v))
        for k, v in args.items()
    }
