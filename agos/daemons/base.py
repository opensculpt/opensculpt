"""Daemon base class — autonomous capability package."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id

_logger = logging.getLogger(__name__)


class DaemonStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class DaemonResult(BaseModel):
    """Output from a daemon execution."""

    daemon_name: str
    success: bool
    summary: str = ""
    data: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat())


class Daemon(ABC):
    """Base class for all Daemons.

    A Daemon is an autonomous background workflow. Subclasses implement:
    - setup(): one-time initialization
    - tick(): called on each cycle (periodic daemons) or once (one-shot daemons)
    - teardown(): cleanup on stop
    """

    name: str = "unnamed"
    description: str = "No description"
    icon: str = "⚙"
    one_shot: bool = False  # True = run once, False = run periodically
    default_interval: int = 60  # seconds between ticks (for periodic daemons)

    def __init__(self) -> None:
        self.id = new_id()
        self.status = DaemonStatus.IDLE
        self.config: dict[str, Any] = {}
        self.results: list[DaemonResult] = []
        self._event_bus: Any = None
        self._audit: Any = None
        self._task: asyncio.Task | None = None
        self._started_at: float | None = None
        self._ticks: int = 0
        self._errors: int = 0

    def bind(self, event_bus: Any, audit: Any = None) -> None:
        """Bind infrastructure components."""
        self._event_bus = event_bus
        self._audit = audit

    async def emit(self, event: str, data: dict) -> None:
        """Emit an event through the bus."""
        if self._event_bus:
            await self._event_bus.emit(
                f"daemon.{self.name}.{event}", data, source=f"daemon:{self.name}"
            )
            # Also emit unprefixed so global subscribers (DemandCollector) hear it
            await self._event_bus.emit(event, data, source=f"daemon:{self.name}")

    def configure(self, config: dict) -> None:
        """Update hand configuration."""
        self.config.update(config)

    async def start(self, config: dict | None = None) -> None:
        """Start the hand."""
        if config:
            self.configure(config)
        self.status = DaemonStatus.RUNNING
        self._started_at = time.monotonic()
        self._ticks = 0
        self._errors = 0
        await self.emit("started", {"config": self.config})
        _logger.info("Daemon '%s' started with config: %s", self.name, self.config)

        try:
            await self.setup()
        except Exception as e:
            self.status = DaemonStatus.ERROR
            _logger.error("Daemon '%s' setup failed: %s", self.name, e)
            await self.emit("error", {"phase": "setup", "error": str(e)})
            return

        if self.one_shot:
            await self._run_once()
        else:
            await self._run_loop()

    async def _run_once(self) -> None:
        """Run tick once and complete."""
        try:
            await self.tick()
            self._ticks += 1
            self.status = DaemonStatus.COMPLETED
            await self.emit("completed", {"ticks": self._ticks})
        except Exception as e:
            self._errors += 1
            self.status = DaemonStatus.ERROR
            await self.emit("error", {"phase": "tick", "error": str(e)})
        finally:
            await self.teardown()

    async def _run_loop(self) -> None:
        """Run tick periodically until stopped."""
        interval = self.config.get("interval", self.default_interval)
        while self.status == DaemonStatus.RUNNING:
            try:
                await self.tick()
                self._ticks += 1
            except Exception as e:
                self._errors += 1
                _logger.warning("Daemon '%s' tick error: %s", self.name, e)
                await self.emit("error", {"phase": "tick", "error": str(e)})
                if self._errors > 10:
                    self.status = DaemonStatus.ERROR
                    break
            await asyncio.sleep(interval)
        await self.teardown()

    async def stop(self) -> None:
        """Stop the hand."""
        if self.status == DaemonStatus.RUNNING:
            self.status = DaemonStatus.IDLE
            await self.emit("stopped", {"ticks": self._ticks})
            _logger.info("Daemon '%s' stopped after %d ticks", self.name, self._ticks)

    def add_result(self, result: DaemonResult) -> None:
        """Store a result (keeps last 20)."""
        self.results.append(result)
        if len(self.results) > 20:
            self.results = self.results[-20:]

    def to_dict(self) -> dict:
        """Serialize hand state for API."""
        uptime = 0.0
        if self._started_at and self.status == DaemonStatus.RUNNING:
            uptime = time.monotonic() - self._started_at
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "status": self.status.value,
            "one_shot": self.one_shot,
            "config": self.config,
            "ticks": self._ticks,
            "errors": self._errors,
            "uptime_s": round(uptime, 1),
            "results_count": len(self.results),
            "last_result": self.results[-1].model_dump() if self.results else None,
        }

    # ── Subclass interface ──

    async def setup(self) -> None:
        """One-time initialization. Override if needed."""

    @abstractmethod
    async def tick(self) -> None:
        """Main work. Called once (one-shot) or periodically."""
        ...

    async def teardown(self) -> None:
        """Cleanup. Override if needed."""
