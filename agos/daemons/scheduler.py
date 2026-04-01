"""SchedulerDaemon — cron-like scheduled command execution.

Run shell commands on a schedule. Useful for periodic tasks like
git pulls, backups, log rotation, health checks.

Usage from dashboard:
    Start "scheduler" with config: {
        "tasks": [
            {"name": "git-pull", "command": "cd /app && git pull", "interval": 3600},
            {"name": "disk-check", "command": "df -h /", "interval": 1800}
        ]
    }
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)

# Commands that are never allowed (safety)
_BLOCKED = frozenset({
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "fork bomb",
    "chmod -R 777 /", "shutdown", "reboot", "halt", "init 0",
})


class SchedulerDaemon(Daemon):
    name = "scheduler"
    description = "Run commands on schedule — cron-like task automation"
    icon = "⏰"
    one_shot = False
    default_interval = 30  # check schedule every 30s

    def __init__(self) -> None:
        super().__init__()
        self._last_run: dict[str, float] = {}

    async def tick(self) -> None:
        tasks = self.config.get("tasks", [])
        if not tasks:
            return

        now = time.monotonic()
        ran = []

        for task in tasks:
            name = task.get("name", "unnamed")
            command = task.get("command", "").strip()
            interval = task.get("interval", 3600)

            if not command:
                continue

            # Safety check
            if any(blocked in command.lower() for blocked in _BLOCKED):
                await self.emit("blocked", {"task": name, "reason": "blocked command"})
                continue

            # Check if it's time to run
            last = self._last_run.get(name, 0)
            if now - last < interval:
                continue

            self._last_run[name] = now

            # Execute
            result = await self._run_command(name, command)
            ran.append(result)

            await self.emit("task_executed", {
                "task": name,
                "success": result["success"],
                "duration_ms": result["duration_ms"],
                "output_preview": result["output"][:200],
            })

        if ran:
            self.add_result(DaemonResult(
                daemon_name=self.name,
                success=all(r["success"] for r in ran),
                summary=f"Ran {len(ran)} tasks: " + ", ".join(
                    f"{r['name']}={'✓' if r['success'] else '✗'}" for r in ran
                ),
                data={"tasks_executed": ran},
            ))

    async def _run_command(self, name: str, command: str) -> dict:
        """Execute a shell command with timeout."""
        timeout = self.config.get("timeout", 30)
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
            elapsed = round((time.monotonic() - start) * 1000)

            output = stdout.decode(errors="replace")[:2000]
            if stderr:
                output += "\n--- stderr ---\n" + stderr.decode(errors="replace")[:1000]

            return {
                "name": name,
                "command": command,
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "output": output,
                "duration_ms": elapsed,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        except asyncio.TimeoutError:
            elapsed = round((time.monotonic() - start) * 1000)
            return {
                "name": name,
                "command": command,
                "success": False,
                "exit_code": -1,
                "output": f"Timeout after {timeout}s",
                "duration_ms": elapsed,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            elapsed = round((time.monotonic() - start) * 1000)
            return {
                "name": name,
                "command": command,
                "success": False,
                "exit_code": -1,
                "output": str(e),
                "duration_ms": elapsed,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
