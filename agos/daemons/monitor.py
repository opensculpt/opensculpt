"""MonitorDaemon — autonomous URL/service monitoring with alerts.

Watches URLs periodically and alerts when status changes, content changes,
or services go down. Useful for uptime monitoring, API health checks,
and change detection.

Usage from dashboard:
    Start "monitor" with config: {
        "targets": [
            {"url": "https://api.example.com/health", "name": "API"},
            {"url": "https://mysite.com", "name": "Website"}
        ],
        "interval": 60
    }
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)


class MonitorDaemon(Daemon):
    name = "monitor"
    description = "Watch URLs/services — alerts on downtime or changes"
    icon = "📡"
    one_shot = False
    default_interval = 60  # check every 60s

    def __init__(self) -> None:
        super().__init__()
        self._prev_states: dict[str, dict] = {}

    async def setup(self) -> None:
        targets = self.config.get("targets", [])
        if not targets:
            # Default: monitor AGOS itself
            self.config["targets"] = [
                {"url": "http://localhost:8420/api/status", "name": "AGOS Dashboard"},
            ]

    async def tick(self) -> None:
        targets = self.config.get("targets", [])
        results = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for target in targets:
                url = target.get("url", "")
                name = target.get("name", url[:40])
                if not url:
                    continue

                result = await self._check_target(client, url, name)
                results.append(result)

                # Check for state changes
                prev = self._prev_states.get(url, {})
                if prev:
                    if prev.get("status") == "up" and result["status"] == "down":
                        await self.emit("alert", {
                            "type": "down",
                            "target": name,
                            "url": url,
                            "error": result.get("error", ""),
                        })
                        await self._notify(f"🔴 {name} is DOWN: {result.get('error', '')}")
                    elif prev.get("status") == "down" and result["status"] == "up":
                        await self.emit("alert", {
                            "type": "recovered",
                            "target": name,
                            "url": url,
                            "response_ms": result["response_ms"],
                        })
                        await self._notify(f"🟢 {name} is back UP ({result['response_ms']}ms)")
                    elif prev.get("content_hash") and result.get("content_hash"):
                        if prev["content_hash"] != result["content_hash"]:
                            await self.emit("alert", {
                                "type": "content_changed",
                                "target": name,
                                "url": url,
                            })
                            await self._notify(f"🔄 {name} content changed")

                self._prev_states[url] = result

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=True,
            summary=f"Checked {len(results)} targets: "
                    + ", ".join(f"{r['name']}={'✓' if r['status'] == 'up' else '✗'}" for r in results),
            data={"targets": results},
        ))

    async def _check_target(self, client: httpx.AsyncClient, url: str, name: str) -> dict:
        """Check a single URL target."""
        import time
        start = time.monotonic()
        try:
            resp = await client.get(url)
            elapsed = round((time.monotonic() - start) * 1000)
            body = resp.text[:2000]
            content_hash = hashlib.md5(body.encode()).hexdigest()

            return {
                "name": name,
                "url": url,
                "status": "up",
                "http_status": resp.status_code,
                "response_ms": elapsed,
                "content_hash": content_hash,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            elapsed = round((time.monotonic() - start) * 1000)
            return {
                "name": name,
                "url": url,
                "status": "down",
                "error": str(e)[:200],
                "response_ms": elapsed,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def _notify(self, message: str) -> None:
        """Send notification through configured channel."""
        webhook_url = self.config.get("webhook_url")
        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    # Discord/Slack compatible webhook
                    await client.post(webhook_url, json={"content": message, "text": message})
            except Exception as e:
                _logger.warning("Webhook notification failed: %s", e)
