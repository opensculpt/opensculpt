"""Ambient Watchers — always-on background intelligence.

The OS doesn't just respond to commands. It watches your world:
- Git repos for new commits
- Project files for changes
- Daily activity for briefings

Watchers use the existing trigger system under the hood. They register
triggers, handle the fire events, and produce Observations that get
stored in the knowledge system and emitted on the event bus.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.knowledge.base import Thread
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus
from agos.triggers.base import TriggerConfig
from agos.triggers.manager import TriggerManager


class Observation(BaseModel):
    """Something an ambient watcher noticed."""

    id: str = Field(default_factory=new_id)
    watcher_name: str
    kind: str  # "git_commit", "file_change", "briefing", etc.
    summary: str
    detail: str = ""
    confidence: float = 0.8
    suggested_action: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class BaseAmbientWatcher(ABC):
    """Base for ambient watchers. Each watcher registers a trigger
    and handles fire events autonomously."""

    name: str = ""

    def __init__(self) -> None:
        self._running = False
        self._trigger_manager: TriggerManager | None = None
        self._event_bus: EventBus | None = None
        self._loom: TheLoom | None = None
        self._trigger_id: str | None = None
        self._observations: list[Observation] = []

    async def start(
        self,
        trigger_manager: TriggerManager,
        event_bus: EventBus | None = None,
        loom: TheLoom | None = None,
    ) -> None:
        """Start this watcher by registering its trigger."""
        self._trigger_manager = trigger_manager
        self._event_bus = event_bus
        self._loom = loom

        config = self._make_trigger_config()
        self._trigger_id = config.id

        # Set handler before registering
        trigger_manager.set_handler(self._on_trigger_raw)
        await trigger_manager.register(config)
        self._running = True

    async def stop(self) -> None:
        """Stop this watcher and unregister its trigger."""
        if self._trigger_manager and self._trigger_id:
            await self._trigger_manager.unregister(self._trigger_id)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abstractmethod
    def _make_trigger_config(self) -> TriggerConfig:
        """Create the trigger config for this watcher."""
        ...

    @abstractmethod
    async def _on_trigger(self, event_data: dict[str, Any]) -> None:
        """Handle a trigger fire. Subclasses produce observations here."""
        ...

    async def _on_trigger_raw(self, event_data: dict[str, Any]) -> None:
        """Internal callback adapter."""
        await self._on_trigger(event_data)

    async def _observe(self, observation: Observation) -> None:
        """Record an observation in memory and emit it on the event bus."""
        self._observations.append(observation)

        # Store in knowledge system
        if self._loom:
            thread = Thread(
                content=f"{observation.summary}\n\n{observation.detail}",
                kind="observation",
                tags=["ambient", self.name, observation.kind],
                metadata={
                    "watcher": self.name,
                    "observation_kind": observation.kind,
                    "suggested_action": observation.suggested_action,
                },
                source=f"ambient:{self.name}",
                confidence=observation.confidence,
            )
            await self._loom.semantic.store(thread)

        # Emit event
        if self._event_bus:
            await self._event_bus.emit(
                "ambient.observation",
                {
                    "watcher": self.name,
                    "kind": observation.kind,
                    "summary": observation.summary,
                    "suggested_action": observation.suggested_action,
                },
                source=f"ambient:{self.name}",
            )

    def recent_observations(self, limit: int = 10) -> list[Observation]:
        """Get recent observations from this watcher."""
        return list(reversed(self._observations[-limit:]))


class GitWatcher(BaseAmbientWatcher):
    """Watches a git repo for new commits."""

    name = "git_watcher"

    def __init__(self, repo_path: str = ".", check_interval: int = 60) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._check_interval = check_interval
        self._last_commit: str = ""

    def _make_trigger_config(self) -> TriggerConfig:
        return TriggerConfig(
            kind="schedule",
            description=f"Git watcher: check every {self._check_interval}s",
            intent="check git for new commits",
            params={"interval_seconds": self._check_interval, "max_fires": 0},
        )

    async def _on_trigger(self, event_data: dict[str, Any]) -> None:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return

            lines = result.stdout.strip().split("\n")
            if not lines or not lines[0]:
                return

            current_head = lines[0].split()[0] if lines[0] else ""

            if self._last_commit and current_head != self._last_commit:
                # New commits detected
                new_count = 0
                for line in lines:
                    commit_hash = line.split()[0] if line else ""
                    if commit_hash == self._last_commit:
                        break
                    new_count += 1

                detail = "\n".join(lines[:new_count])
                await self._observe(Observation(
                    watcher_name=self.name,
                    kind="git_commit",
                    summary=f"{new_count} new commit(s) detected",
                    detail=detail,
                    suggested_action="review recent changes",
                ))

            self._last_commit = current_head

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass


class FileActivityWatcher(BaseAmbientWatcher):
    """Watches project directories for file changes."""

    name = "file_activity"

    def __init__(
        self,
        watch_path: str = ".",
        patterns: list[str] | None = None,
        check_interval: int = 5,
    ) -> None:
        super().__init__()
        self._watch_path = watch_path
        self._patterns = patterns or ["*.py"]
        self._check_interval = check_interval

    def _make_trigger_config(self) -> TriggerConfig:
        return TriggerConfig(
            kind="file_watch",
            description=f"File activity: watch {self._watch_path}",
            intent="check for file changes",
            params={
                "path": self._watch_path,
                "patterns": self._patterns,
                "interval": self._check_interval,
            },
        )

    async def _on_trigger(self, event_data: dict[str, Any]) -> None:
        summary = event_data.get("summary", "")
        changes = event_data.get("changes", {})

        if not summary and not changes:
            return

        # Categorize changes
        added = changes.get("added", [])
        modified = changes.get("modified", [])
        removed = changes.get("removed", [])

        if not added and not modified and not removed:
            return

        # Detect patterns
        suggested = ""
        test_files = [f for f in (added + modified) if "test" in f.lower()]
        config_files = [f for f in (added + modified)
                        if any(f.endswith(ext) for ext in (".toml", ".cfg", ".ini", ".yaml", ".yml", ".json"))]
        src_files = [f for f in (added + modified) if f.endswith(".py") and "test" not in f.lower()]

        if test_files:
            suggested = "run tests"
        elif config_files:
            suggested = "validate configuration"
        elif src_files:
            suggested = "run tests for changed modules"

        parts = []
        if added:
            parts.append(f"{len(added)} added")
        if modified:
            parts.append(f"{len(modified)} modified")
        if removed:
            parts.append(f"{len(removed)} removed")

        await self._observe(Observation(
            watcher_name=self.name,
            kind="file_change",
            summary=f"File changes detected: {', '.join(parts)}",
            detail=summary or str(changes),
            suggested_action=suggested,
        ))


class DailyBriefingWatcher(BaseAmbientWatcher):
    """Generates daily activity summaries from episodic memory."""

    name = "daily_briefing"

    def __init__(self, interval_hours: int = 24) -> None:
        super().__init__()
        self._interval_hours = interval_hours

    def _make_trigger_config(self) -> TriggerConfig:
        return TriggerConfig(
            kind="schedule",
            description=f"Daily briefing: every {self._interval_hours}h",
            intent="generate daily briefing",
            params={
                "interval_seconds": self._interval_hours * 3600,
                "max_fires": 0,
            },
        )

    async def _on_trigger(self, event_data: dict[str, Any]) -> None:
        if not self._loom:
            return

        # Query recent timeline events
        events = await self._loom.timeline(limit=50)

        if not events:
            await self._observe(Observation(
                watcher_name=self.name,
                kind="briefing",
                summary="Daily briefing: no recent activity recorded",
                suggested_action="",
            ))
            return

        # Categorize events
        kinds: dict[str, int] = {}
        agents: set[str] = set()
        for ev in events:
            kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
            if ev.agent_id:
                agents.add(ev.agent_id)

        summary_parts = []
        total = len(events)
        summary_parts.append(f"{total} events recorded")
        if agents:
            summary_parts.append(f"{len(agents)} agent(s) active")
        for kind, count in sorted(kinds.items(), key=lambda x: -x[1])[:5]:
            summary_parts.append(f"{count} {kind}")

        detail_lines = []
        for ev in events[:10]:
            detail_lines.append(
                f"[{ev.created_at.strftime('%H:%M')}] {ev.kind}: {ev.content[:80]}"
            )

        await self._observe(Observation(
            watcher_name=self.name,
            kind="briefing",
            summary=f"Daily briefing: {', '.join(summary_parts)}",
            detail="\n".join(detail_lines),
            suggested_action="review activity and plan next steps" if total > 5 else "",
            confidence=0.9,
        ))


class AmbientManager:
    """Manages all ambient watchers."""

    def __init__(self) -> None:
        self._watchers: dict[str, BaseAmbientWatcher] = {}
        self._trigger_manager: TriggerManager | None = None
        self._event_bus: EventBus | None = None
        self._loom: TheLoom | None = None

    def register(self, watcher: BaseAmbientWatcher) -> None:
        """Register a watcher (doesn't start it)."""
        self._watchers[watcher.name] = watcher

    async def start_all(
        self,
        trigger_manager: TriggerManager,
        event_bus: EventBus | None = None,
        loom: TheLoom | None = None,
    ) -> int:
        """Start all registered watchers. Returns count started."""
        self._trigger_manager = trigger_manager
        self._event_bus = event_bus
        self._loom = loom

        started = 0
        for watcher in self._watchers.values():
            if not watcher.is_running:
                # Each watcher gets its own trigger manager to avoid handler conflicts
                wm = TriggerManager()
                await watcher.start(wm, event_bus, loom)
                started += 1
        return started

    async def stop_all(self) -> int:
        """Stop all running watchers. Returns count stopped."""
        stopped = 0
        for watcher in self._watchers.values():
            if watcher.is_running:
                await watcher.stop()
                stopped += 1
        return stopped

    async def start_one(self, name: str) -> bool:
        """Start a specific watcher by name."""
        watcher = self._watchers.get(name)
        if not watcher or watcher.is_running:
            return False
        wm = TriggerManager()
        await watcher.start(
            wm,
            self._event_bus,
            self._loom,
        )
        return True

    async def stop_one(self, name: str) -> bool:
        """Stop a specific watcher by name."""
        watcher = self._watchers.get(name)
        if not watcher or not watcher.is_running:
            return False
        await watcher.stop()
        return True

    def list_watchers(self) -> list[dict[str, Any]]:
        """List all watchers with status."""
        return [
            {
                "name": w.name,
                "running": w.is_running,
                "observations": len(w._observations),
            }
            for w in self._watchers.values()
        ]

    def observations(self, limit: int = 20) -> list[Observation]:
        """Get recent observations from all watchers, newest first."""
        all_obs: list[Observation] = []
        for w in self._watchers.values():
            all_obs.extend(w._observations)
        all_obs.sort(key=lambda o: o.created_at, reverse=True)
        return all_obs[:limit]
