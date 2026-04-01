"""Schedule trigger — time-based activation.

"Every morning at 9am, summarize my git activity."
"Every 30 minutes, check if my server is healthy."
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from agos.triggers.base import BaseTrigger, TriggerConfig


class ScheduleTrigger(BaseTrigger):
    """Fires on a time schedule.

    Config params:
        interval_seconds: int — fire every N seconds
        max_fires: int — stop after N fires (0 = unlimited)
    """

    def __init__(self, config: TriggerConfig):
        super().__init__(config)
        self._interval = config.params.get("interval_seconds", 60)
        self._max_fires = config.params.get("max_fires", 0)
        self._fire_count = 0

    async def _watch_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)

            if not self._running:
                break

            self._fire_count += 1
            await self._fire({
                "trigger_kind": "schedule",
                "fire_count": self._fire_count,
                "interval_seconds": self._interval,
                "fired_at": datetime.now().isoformat(),
            })

            if self._max_fires > 0 and self._fire_count >= self._max_fires:
                self._running = False
                break

    @property
    def fire_count(self) -> int:
        return self._fire_count
