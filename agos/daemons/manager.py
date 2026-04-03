"""DaemonManager — lifecycle management for autonomous Daemons."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agos.daemons.base import Daemon, DaemonStatus

if TYPE_CHECKING:
    from agos.daemons.gc import GarbageCollector
    from agos.daemons.goal_runner import GoalRunner
    from agos.hands.base import Hand

_logger = logging.getLogger(__name__)


class DaemonManager:
    """Discovers, starts, stops, and manages Daemons."""

    def __init__(self, event_bus: Any, audit: Any = None) -> None:
        self._bus = event_bus
        self._audit = audit
        self._daemons: dict[str, Hand] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._loom: Any = None
        self._llm: Any = None

    def set_loom(self, loom: Any) -> None:
        """Set TheLoom so DomainDaemons can read/write knowledge."""
        self._loom = loom

    def set_llm(self, llm: Any) -> None:
        """Set LLM provider so DomainDaemons can reason."""
        self._llm = llm

    def register(self, hand: Hand) -> None:
        """Register a hand (makes it available to start)."""
        hand.bind(self._bus, self._audit)
        self._daemons[hand.name] = hand
        _logger.info("Registered hand: %s — %s", hand.name, hand.description)

    async def start_daemon(self, name: str, config: dict | None = None) -> dict:
        """Start a hand by name. Returns status dict."""
        hand = self._daemons.get(name)
        if not hand:
            return {"success": False, "error": f"Unknown hand: {name}"}

        if hand.status == DaemonStatus.RUNNING:
            return {"success": False, "error": f"Daemon '{name}' already running"}

        # Reset state for re-start
        hand.status = DaemonStatus.IDLE
        hand._ticks = 0
        hand._errors = 0

        task = asyncio.create_task(hand.start(config or {}))
        self._tasks[name] = task

        # Don't await — it runs in background
        return {"success": True, "hand": name, "status": "starting"}

    async def stop_daemon(self, name: str) -> dict:
        """Stop a running hand."""
        hand = self._daemons.get(name)
        if not hand:
            return {"success": False, "error": f"Unknown hand: {name}"}

        await hand.stop()

        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        return {"success": True, "hand": name, "status": "stopped"}

    def list_daemons(self) -> list[dict]:
        """List all registered daemons with their status."""
        return [h.to_dict() for h in self._daemons.values()]

    def get_daemon(self, name: str) -> Daemon | None:
        """Get a hand by name."""
        return self._daemons.get(name)

    def get_results(self, name: str, limit: int = 10) -> list[dict]:
        """Get recent results from a hand."""
        hand = self._daemons.get(name)
        if not hand:
            return []
        return [r.model_dump() for r in hand.results[-limit:]]

    async def shutdown(self) -> None:
        """Stop all running daemons."""
        for name in list(self._tasks.keys()):
            await self.stop_daemon(name)

    def register_builtin_daemons(self) -> None:
        """Register all built-in daemons."""
        from agos.daemons.research import ResearchDaemon
        from agos.daemons.monitor import MonitorDaemon
        from agos.daemons.digest import DigestDaemon
        from agos.daemons.scheduler import SchedulerDaemon
        from agos.daemons.gc import GarbageCollector

        self.register(ResearchDaemon())
        self.register(MonitorDaemon())
        self.register(DigestDaemon())
        self.register(SchedulerDaemon())

        self._gc = GarbageCollector()
        self.register(self._gc)

        from agos.daemons.goal_runner import GoalRunner
        self._goal_runner = GoalRunner()
        self.register(self._goal_runner)

        # Auto-start GoalRunner if there are existing goals (survive restarts)
        import asyncio

        async def _auto_start_goal_runner():
            await asyncio.sleep(5)  # Wait for OS agent to wire up
            goals = self._goal_runner.get_goals()
            active = [g for g in goals if g.get("status") in ("active", "planning")]
            if active:
                await self.start_daemon("goal_runner")
                import logging
                logging.getLogger(__name__).info(
                    "Auto-started GoalRunner: %d active goals found on disk", len(active))

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_auto_start_goal_runner())
        except RuntimeError:
            # No event loop yet (sync CLI context) — goal runner starts when server boots
            pass

    async def create_domain_daemon(self, name: str, config: dict) -> Any:
        """Create and start a DomainDaemon dynamically.

        Called by GoalRunner after a goal phase that needs ongoing monitoring.
        The daemon gets LLM + TheLoom access to do domain-specific work.
        """
        from agos.daemons.domain import DomainDaemon

        daemon = DomainDaemon()
        daemon.name = name
        daemon.description = config.get("task", "Domain worker")[:80]
        daemon.default_interval = config.get("interval", 3600)
        if self._loom:
            daemon.set_loom(self._loom)
        if self._llm:
            daemon.set_llm(self._llm)
        self.register(daemon)
        result = await self.start_daemon(name, config)
        _logger.info("Created domain daemon '%s': %s", name, result)
        return daemon

    def get_goal_runner(self) -> "GoalRunner":
        """Get the goal runner hand for creating goals."""
        return getattr(self, "_goal_runner", None)

    def get_gc(self) -> "GarbageCollector":
        """Get the garbage collector daemon."""
        return getattr(self, "_gc", None)
