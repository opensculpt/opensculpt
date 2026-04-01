"""File watcher trigger — reacts to filesystem changes.

"Watch my src/ folder and review any changes I make."
This makes that possible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agos.triggers.base import BaseTrigger, TriggerConfig


class FileWatchTrigger(BaseTrigger):
    """Watches a directory for file changes and fires on modifications.

    Uses polling (cross-platform, no extra deps). Checks every N seconds
    for files that changed since last check.

    Config params:
        path: str — directory or file to watch
        patterns: list[str] — glob patterns to match (e.g., ["*.py", "*.js"])
        interval: int — seconds between checks (default 2)
    """

    def __init__(self, config: TriggerConfig):
        super().__init__(config)
        self._path = Path(config.params.get("path", "."))
        self._patterns = config.params.get("patterns", ["*"])
        self._interval = config.params.get("interval", 2)
        self._last_snapshot: dict[str, float] = {}

    async def _watch_loop(self) -> None:
        # Take initial snapshot
        self._last_snapshot = self._snapshot()

        while self._running:
            await asyncio.sleep(self._interval)
            current = self._snapshot()
            changes = self._diff(self._last_snapshot, current)

            if changes:
                await self._fire({
                    "trigger_kind": "file_watch",
                    "path": str(self._path),
                    "changes": changes,
                    "summary": self._summarize(changes),
                })

            self._last_snapshot = current

    def _snapshot(self) -> dict[str, float]:
        """Get modification times for all matching files."""
        result: dict[str, float] = {}
        path = self._path

        if path.is_file():
            try:
                result[str(path)] = path.stat().st_mtime
            except OSError:
                pass
            return result

        if not path.is_dir():
            return result

        for pattern in self._patterns:
            for f in path.rglob(pattern):
                if f.is_file():
                    try:
                        result[str(f)] = f.stat().st_mtime
                    except OSError:
                        pass
        return result

    def _diff(self, old: dict[str, float], new: dict[str, float]) -> dict:
        """Compare snapshots and return changes."""
        added = [f for f in new if f not in old]
        removed = [f for f in old if f not in new]
        modified = [
            f for f in new
            if f in old and new[f] != old[f]
        ]

        if not added and not removed and not modified:
            return {}

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    @staticmethod
    def _summarize(changes: dict) -> str:
        parts = []
        if changes.get("added"):
            parts.append(f"{len(changes['added'])} added")
        if changes.get("modified"):
            parts.append(f"{len(changes['modified'])} modified")
        if changes.get("removed"):
            parts.append(f"{len(changes['removed'])} removed")
        return ", ".join(parts)
