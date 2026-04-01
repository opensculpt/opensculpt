"""Trigger Manager — orchestrates all triggers and connects them to agents.

When a trigger fires, the manager:
1. Takes the trigger's intent (what to tell the agent)
2. Adds context from the event (what happened)
3. Feeds it to the Intent Engine
4. The OS handles the rest
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from agos.triggers.base import BaseTrigger, TriggerConfig
from agos.triggers.file_watch import FileWatchTrigger
from agos.triggers.schedule import ScheduleTrigger
from agos.triggers.webhook import WebhookTrigger

console = Console()

# Map kind string to trigger class
TRIGGER_TYPES: dict[str, type[BaseTrigger]] = {
    "file_watch": FileWatchTrigger,
    "schedule": ScheduleTrigger,
    "webhook": WebhookTrigger,
}


class TriggerManager:
    """Manages all active triggers and routes their events to agents."""

    def __init__(self) -> None:
        self._triggers: dict[str, BaseTrigger] = {}
        self._on_fire_callback: Any = None

    def set_handler(self, callback: Any) -> None:
        """Set the callback that handles trigger events.

        Typically this routes to the Intent Engine:
            async def handler(intent: str): ...
        """
        self._on_fire_callback = callback

    async def register(self, config: TriggerConfig) -> BaseTrigger:
        """Create and start a trigger from config."""
        trigger_cls = TRIGGER_TYPES.get(config.kind)
        if trigger_cls is None:
            raise ValueError(f"Unknown trigger kind: {config.kind}")

        trigger = trigger_cls(config)

        async def on_fire(event_data: dict[str, Any]) -> None:
            # Build the message for the agent
            intent = config.intent
            if not intent:
                intent = f"A {config.kind} trigger fired: {config.description}"

            # Add event context
            summary = event_data.get("summary", "")
            if summary:
                intent = f"{intent}\n\nEvent details: {summary}"

            # Add raw details for file changes
            changes = event_data.get("changes", {})
            if changes:
                files = []
                for f in changes.get("modified", [])[:5]:
                    files.append(f"  modified: {f}")
                for f in changes.get("added", [])[:5]:
                    files.append(f"  added: {f}")
                for f in changes.get("removed", [])[:5]:
                    files.append(f"  removed: {f}")
                if files:
                    intent += "\n\nChanged files:\n" + "\n".join(files)

            console.print(f"[dim]trigger fired: {config.kind} — {config.description}[/dim]")

            if self._on_fire_callback:
                await self._on_fire_callback(intent)

        trigger.on_fire(on_fire)
        await trigger.start()
        self._triggers[config.id] = trigger
        return trigger

    async def unregister(self, trigger_id: str) -> bool:
        """Stop and remove a trigger."""
        trigger = self._triggers.pop(trigger_id, None)
        if trigger is None:
            return False
        await trigger.stop()
        return True

    async def stop_all(self) -> None:
        """Stop all triggers."""
        for trigger in self._triggers.values():
            await trigger.stop()
        self._triggers.clear()

    def list_triggers(self) -> list[dict]:
        """List all active triggers."""
        return [
            {
                "id": tid,
                "kind": t.config.kind,
                "description": t.config.description,
                "intent": t.config.intent,
                "active": t.is_running,
            }
            for tid, t in self._triggers.items()
        ]
