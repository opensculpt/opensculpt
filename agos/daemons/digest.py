"""DigestDaemon — periodic activity summaries.

Compiles what happened in AGOS into a human-readable digest:
events, evolution cycles, agent activity, errors.

Usage from dashboard:
    Start "digest" with config: {"interval": 3600}  # hourly digest
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)


class DigestDaemon(Daemon):
    name = "digest"
    description = "Periodic activity summaries — what happened in your OS"
    icon = "📊"
    one_shot = False
    default_interval = 3600  # hourly

    async def tick(self) -> None:
        """Compile activity digest from audit trail + events."""
        summary_parts = []
        data: dict = {}

        # Get system status
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://127.0.0.1:8420/api/status")
                if resp.status_code == 200:
                    status = resp.json()
                    data["status"] = status
                    summary_parts.append(
                        f"System: {status.get('agents_running', 0)} agents running, "
                        f"{status.get('evolution_cycles', 0)} evolution cycles, "
                        f"uptime {self._fmt_uptime(status.get('uptime_s', 0))}"
                    )
        except Exception:
            summary_parts.append("System: status unavailable")

        # Get evolution state
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://127.0.0.1:8420/api/evolution/state")
                if resp.status_code == 200:
                    evo = resp.json()
                    data["evolution"] = {
                        "cycles": evo.get("cycles_completed", 0),
                        "strategies": len(evo.get("strategies_applied", [])),
                    }
                    summary_parts.append(
                        f"Evolution: {evo.get('cycles_completed', 0)} cycles, "
                        f"{len(evo.get('strategies_applied', []))} strategies"
                    )
        except Exception:
            pass

        # Get audit trail stats
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://127.0.0.1:8420/api/audit")
                if resp.status_code == 200:
                    audit_data = resp.json()
                    entries = audit_data if isinstance(audit_data, list) else audit_data.get("entries", [])
                    if entries:
                        recent = entries[-20:]
                        actions = {}
                        for e in recent:
                            action = e.get("action", "unknown")
                            actions[action] = actions.get(action, 0) + 1

                        top_actions = sorted(actions.items(), key=lambda x: -x[1])[:5]
                        data["recent_actions"] = dict(top_actions)
                        summary_parts.append(
                            f"Activity: {', '.join(f'{a}({c})' for a, c in top_actions)}"
                        )
        except Exception:
            pass

        # Get vitals
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://127.0.0.1:8420/api/vitals")
                if resp.status_code == 200:
                    vitals = resp.json()
                    cpu = vitals.get("cpu_percent", 0)
                    mem = vitals.get("memory_percent", 0)
                    data["vitals"] = {"cpu": cpu, "memory": mem}
                    if cpu > 80 or mem > 80:
                        summary_parts.append(f"⚠️ High resource usage: CPU {cpu}%, Memory {mem}%")
        except Exception:
            pass

        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        digest = f"[{now}] " + " | ".join(summary_parts) if summary_parts else "No activity"

        await self.emit("digest_ready", {"digest": digest})

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=True,
            summary=digest,
            data=data,
        ))

        # Send webhook if configured
        webhook_url = self.config.get("webhook_url")
        if webhook_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(webhook_url, json={
                        "content": f"📊 **AGOS Digest**\n{digest}",
                        "text": f"AGOS Digest: {digest}",
                    })
            except Exception as e:
                _logger.warning("Digest webhook failed: %s", e)

    @staticmethod
    def _fmt_uptime(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
