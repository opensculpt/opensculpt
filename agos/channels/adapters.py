"""Channel adapters — 40 notification/communication channels.

Each channel sends messages to an external service via its API.
All channels are lightweight: just an async send() method + config validation.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agos.channels.base import ChannelRegistry

import httpx

from agos.channels.base import BaseChannel, ChannelMessage, ChannelResult

_logger = logging.getLogger(__name__)
_TIMEOUT = 15


# ── Webhook Channels ─────────────────────────────────────────


class WebhookChannel(BaseChannel):
    name = "webhook"
    description = "Generic HTTP webhook (POST JSON)"
    icon = "🔗"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "url", "label": "Webhook URL", "type": "url", "required": True},
            {"key": "headers", "label": "Headers (JSON)", "type": "text"},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "")
        if not url:
            return ChannelResult(channel=self.name, success=False, detail="No URL")
        headers = cfg.get("headers", {})
        payload = {"text": msg.text, "title": msg.title, "level": msg.level, **msg.data}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json=payload, headers=headers)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class DiscordChannel(BaseChannel):
    name = "discord"
    description = "Discord webhook"
    icon = "💬"

    def config_schema(self) -> list[dict]:
        return [{"key": "webhook_url", "label": "Webhook URL", "type": "url", "required": True}]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        content = f"**{msg.title}**\n{msg.text}" if msg.title else msg.text
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"content": content[:2000]})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class SlackChannel(BaseChannel):
    name = "slack"
    description = "Slack webhook or API"
    icon = "📱"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "webhook_url", "label": "Webhook URL", "type": "url"},
            {"key": "token", "label": "Bot Token", "type": "password"},
            {"key": "channel", "label": "Channel", "type": "text", "default": "#general"},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        token = cfg.get("token", "")
        channel = cfg.get("channel", "#general")
        text = f"*{msg.title}*\n{msg.text}" if msg.title else msg.text

        if token:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": channel, "text": text},
                )
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(url, json={"text": text})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class TelegramChannel(BaseChannel):
    name = "telegram"
    description = "Telegram Bot API"
    icon = "✈️"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "bot_token", "label": "Bot Token", "type": "password", "required": True},
            {"key": "chat_id", "label": "Chat ID", "type": "text", "required": True},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        token = cfg.get("bot_token", "")
        chat_id = cfg.get("chat_id", "")
        text = f"<b>{msg.title}</b>\n{msg.text}" if msg.title else msg.text
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class EmailChannel(BaseChannel):
    name = "email"
    description = "Email via SMTP relay API (Mailgun/SendGrid/Resend)"
    icon = "📧"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "provider", "label": "Provider", "type": "text", "default": "resend"},
            {"key": "api_key", "label": "API Key", "type": "password", "required": True},
            {"key": "to", "label": "Recipient Email", "type": "text", "required": True},
            {"key": "from", "label": "From Address", "type": "text", "default": "agos@localhost"},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        provider = cfg.get("provider", "resend")
        api_key = cfg.get("api_key", "")
        to = cfg.get("to", "")
        from_addr = cfg.get("from", "agos@localhost")

        if provider == "resend":
            url = "https://api.resend.com/emails"
            payload = {"from": from_addr, "to": [to], "subject": msg.title or "AGOS", "text": msg.text}
            headers = {"Authorization": f"Bearer {api_key}"}
        elif provider == "sendgrid":
            url = "https://api.sendgrid.com/v3/mail/send"
            payload = {
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": from_addr},
                "subject": msg.title or "AGOS",
                "content": [{"type": "text/plain", "value": msg.text}],
            }
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            return ChannelResult(channel=self.name, success=False, detail=f"Unknown provider: {provider}")

        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json=payload, headers=headers)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class MattermostChannel(BaseChannel):
    name = "mattermost"
    description = "Mattermost webhook"
    icon = "💭"

    def config_schema(self) -> list[dict]:
        return [{"key": "webhook_url", "label": "Webhook URL", "type": "url", "required": True}]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        text = f"**{msg.title}**\n{msg.text}" if msg.title else msg.text
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"text": text})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class TeamsChannel(BaseChannel):
    name = "teams"
    description = "Microsoft Teams webhook"
    icon = "🏢"

    def config_schema(self) -> list[dict]:
        return [{"key": "webhook_url", "label": "Webhook URL", "type": "url", "required": True}]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [
                        {"type": "TextBlock", "text": msg.title or "AGOS", "weight": "Bolder", "size": "Medium"},
                        {"type": "TextBlock", "text": msg.text, "wrap": True},
                    ],
                },
            }],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class PagerDutyChannel(BaseChannel):
    name = "pagerduty"
    description = "PagerDuty Events API v2"
    icon = "🚨"

    def config_schema(self) -> list[dict]:
        return [{"key": "routing_key", "label": "Routing Key", "type": "password", "required": True}]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        routing_key = cfg.get("routing_key", "")
        severity = {"critical": "critical", "error": "error", "warning": "warning"}.get(msg.level, "info")
        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": msg.text[:1024],
                "severity": severity,
                "source": "agos",
            },
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post("https://events.pagerduty.com/v2/enqueue", json=payload)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class OpsgenieChannel(BaseChannel):
    name = "opsgenie"
    description = "Opsgenie Alert API"
    icon = "🔔"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        api_key = cfg.get("api_key", "")
        priority = {"critical": "P1", "error": "P2", "warning": "P3"}.get(msg.level, "P4")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                "https://api.opsgenie.com/v2/alerts",
                headers={"Authorization": f"GenieKey {api_key}"},
                json={"message": msg.title or msg.text[:130], "description": msg.text, "priority": priority},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class TwilioSMSChannel(BaseChannel):
    name = "twilio_sms"
    description = "SMS via Twilio"
    icon = "📲"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        sid = cfg.get("account_sid", "")
        token = cfg.get("auth_token", "")
        from_num = cfg.get("from", "")
        to_num = cfg.get("to", "")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, auth=(sid, token), data={"From": from_num, "To": to_num, "Body": msg.text[:1600]})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class TwilioWhatsAppChannel(BaseChannel):
    name = "twilio_whatsapp"
    description = "WhatsApp via Twilio"
    icon = "💚"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        sid = cfg.get("account_sid", "")
        token = cfg.get("auth_token", "")
        from_num = cfg.get("from", "whatsapp:+14155238886")
        to_num = cfg.get("to", "")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, auth=(sid, token), data={
                "From": from_num, "To": f"whatsapp:{to_num}", "Body": msg.text[:1600],
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class PushoverChannel(BaseChannel):
    name = "pushover"
    description = "Pushover push notifications"
    icon = "📢"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "app_token", "label": "App Token", "type": "password", "required": True},
            {"key": "user_key", "label": "User Key", "type": "password", "required": True},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post("https://api.pushover.net/1/messages.json", data={
                "token": cfg.get("app_token", ""),
                "user": cfg.get("user_key", ""),
                "title": msg.title or "AGOS",
                "message": msg.text,
                "priority": {"critical": 2, "error": 1, "warning": 0}.get(msg.level, -1),
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class NtfyChannel(BaseChannel):
    name = "ntfy"
    description = "ntfy.sh push notifications"
    icon = "🔔"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "topic", "label": "Topic", "type": "text", "required": True},
            {"key": "server", "label": "Server URL", "type": "text", "default": "https://ntfy.sh"},
        ]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        base = cfg.get("server", "https://ntfy.sh")
        topic = cfg.get("topic", "agos")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{base}/{topic}", content=msg.text, headers={
                "Title": msg.title or "AGOS",
                "Priority": {"critical": "5", "error": "4", "warning": "3"}.get(msg.level, "2"),
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class GotifyChannel(BaseChannel):
    name = "gotify"
    description = "Gotify push notifications"
    icon = "📬"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:8080")
        token = cfg.get("app_token", "")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/message", params={"token": token}, json={
                "title": msg.title or "AGOS", "message": msg.text,
                "priority": {"critical": 10, "error": 7, "warning": 4}.get(msg.level, 1),
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class MatrixChannel(BaseChannel):
    name = "matrix"
    description = "Matrix/Element room messages"
    icon = "🔷"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        server = cfg.get("homeserver", "https://matrix.org")
        token = cfg.get("access_token", "")
        room = cfg.get("room_id", "")
        import time
        txn = str(int(time.time() * 1000))
        url = f"{server}/_matrix/client/r0/rooms/{room}/send/m.room.message/{txn}"
        body = f"**{msg.title}**\n{msg.text}" if msg.title else msg.text
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.put(url, headers={"Authorization": f"Bearer {token}"}, json={
                "msgtype": "m.text", "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": f"<b>{msg.title}</b><br>{msg.text}" if msg.title else msg.text,
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class RocketChatChannel(BaseChannel):
    name = "rocketchat"
    description = "Rocket.Chat webhook"
    icon = "🚀"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        text = f"**{msg.title}**\n{msg.text}" if msg.title else msg.text
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"text": text})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class ZulipChannel(BaseChannel):
    name = "zulip"
    description = "Zulip stream messages"
    icon = "💧"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        server = cfg.get("server", "")
        email = cfg.get("bot_email", "")
        api_key = cfg.get("api_key", "")
        stream = cfg.get("stream", "agos")
        topic = cfg.get("topic", msg.title or "notification")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{server}/api/v1/messages", auth=(email, api_key), data={
                "type": "stream", "to": stream, "topic": topic, "content": msg.text,
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class GoogleChatChannel(BaseChannel):
    name = "google_chat"
    description = "Google Chat webhook"
    icon = "🟢"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        text = f"*{msg.title}*\n{msg.text}" if msg.title else msg.text
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"text": text})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class LinearChannel(BaseChannel):
    name = "linear"
    description = "Linear issue creation"
    icon = "📐"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        api_key = cfg.get("api_key", "")
        team_id = cfg.get("team_id", "")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post("https://api.linear.app/graphql",
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json={"query": "mutation{issueCreate(input:{teamId:\"%s\",title:\"%s\",description:\"%s\"}){success}}" % (
                    team_id, (msg.title or "AGOS Alert").replace('"', '\\"'), msg.text[:10000].replace('"', '\\"'),
                )},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class JiraChannel(BaseChannel):
    name = "jira"
    description = "Jira issue creation"
    icon = "🔵"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "")  # https://xxx.atlassian.net
        email = cfg.get("email", "")
        token = cfg.get("api_token", "")
        project = cfg.get("project_key", "AGOS")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/rest/api/3/issue", auth=(email, token), json={
                "fields": {
                    "project": {"key": project},
                    "summary": msg.title or msg.text[:200],
                    "description": {"type": "doc", "version": 1, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": msg.text}]},
                    ]},
                    "issuetype": {"name": "Task"},
                },
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class GitHubIssueChannel(BaseChannel):
    name = "github_issue"
    description = "Create GitHub issue"
    icon = "🐙"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        token = cfg.get("token", "")
        repo = cfg.get("repo", "")  # owner/repo
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"https://api.github.com/repos/{repo}/issues",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"title": msg.title or "AGOS Alert", "body": msg.text},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class GitLabIssueChannel(BaseChannel):
    name = "gitlab_issue"
    description = "Create GitLab issue"
    icon = "🦊"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "https://gitlab.com")
        token = cfg.get("token", "")
        project_id = cfg.get("project_id", "")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/api/v4/projects/{project_id}/issues",
                headers={"PRIVATE-TOKEN": token},
                json={"title": msg.title or "AGOS Alert", "description": msg.text},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class DatadogChannel(BaseChannel):
    name = "datadog"
    description = "Datadog Events API"
    icon = "🐕"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        api_key = cfg.get("api_key", "")
        site = cfg.get("site", "datadoghq.com")
        alert_type = {"critical": "error", "error": "error", "warning": "warning"}.get(msg.level, "info")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"https://api.{site}/api/v1/events",
                headers={"DD-API-KEY": api_key},
                json={"title": msg.title or "AGOS", "text": msg.text, "alert_type": alert_type, "source_type_name": "agos"},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class GrafanaChannel(BaseChannel):
    name = "grafana"
    description = "Grafana annotations"
    icon = "📊"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:3000")
        token = cfg.get("api_key", "")
        import time
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/api/annotations",
                headers={"Authorization": f"Bearer {token}"},
                json={"text": f"{msg.title}: {msg.text}" if msg.title else msg.text, "time": int(time.time() * 1000)},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class PrometheusChannel(BaseChannel):
    name = "prometheus_alertmanager"
    description = "Prometheus Alertmanager"
    icon = "🔥"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:9093")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/api/v1/alerts", json=[{
                "labels": {"alertname": msg.title or "agos_alert", "severity": msg.level, "source": "agos"},
                "annotations": {"summary": msg.text},
            }])
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class InfluxDBChannel(BaseChannel):
    name = "influxdb"
    description = "InfluxDB line protocol write"
    icon = "📈"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:8086")
        token = cfg.get("token", "")
        org = cfg.get("org", "agos")
        bucket = cfg.get("bucket", "events")
        import time
        line = f'agos_event,level={msg.level} text="{msg.text[:500]}" {int(time.time() * 1e9)}'
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/api/v2/write", params={"org": org, "bucket": bucket},
                headers={"Authorization": f"Token {token}", "Content-Type": "text/plain"}, content=line)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class SentryChannel(BaseChannel):
    name = "sentry"
    description = "Sentry error tracking"
    icon = "🐛"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        dsn = cfg.get("dsn", "")
        if not dsn:
            return ChannelResult(channel=self.name, success=False, detail="No DSN")
        parsed = urllib.parse.urlparse(dsn)
        project_id = parsed.path.strip("/")
        key = parsed.username or ""
        host = parsed.hostname or ""
        store_url = f"https://{host}/api/{project_id}/store/"
        import time
        import uuid
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(store_url,
                headers={"X-Sentry-Auth": f"Sentry sentry_version=7,sentry_key={key}"},
                json={"event_id": uuid.uuid4().hex, "message": msg.text, "level": msg.level,
                       "timestamp": time.time(), "platform": "python", "logger": "agos"})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class ElasticsearchChannel(BaseChannel):
    name = "elasticsearch"
    description = "Elasticsearch document index"
    icon = "🔍"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:9200")
        index = cfg.get("index", "agos-events")
        auth = (cfg.get("username", ""), cfg.get("password", "")) if cfg.get("username") else None
        from datetime import datetime, timezone
        doc = {"title": msg.title, "text": msg.text, "level": msg.level, "@timestamp": datetime.now(timezone.utc).isoformat(), **msg.data}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/{index}/_doc", json=doc, auth=auth)
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class LokiChannel(BaseChannel):
    name = "loki"
    description = "Grafana Loki log push"
    icon = "📋"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:3100")
        import time
        ts = str(int(time.time() * 1e9))
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/loki/api/v1/push", json={
                "streams": [{"stream": {"source": "agos", "level": msg.level},
                             "values": [[ts, msg.text]]}],
            })
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class SNSChannel(BaseChannel):
    name = "aws_sns"
    description = "AWS SNS publish"
    icon = "☁️"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        # Uses boto3 if available, falls back to HTTP
        try:
            import boto3
            client = boto3.client("sns",
                region_name=cfg.get("region", "us-east-1"),
                aws_access_key_id=cfg.get("access_key", ""),
                aws_secret_access_key=cfg.get("secret_key", ""))
            client.publish(TopicArn=cfg.get("topic_arn", ""), Message=msg.text, Subject=msg.title or "AGOS")
            return ChannelResult(channel=self.name, success=True, detail="Published")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="boto3 not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class SQSChannel(BaseChannel):
    name = "aws_sqs"
    description = "AWS SQS queue message"
    icon = "📤"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        try:
            import boto3
            client = boto3.client("sqs",
                region_name=cfg.get("region", "us-east-1"),
                aws_access_key_id=cfg.get("access_key", ""),
                aws_secret_access_key=cfg.get("secret_key", ""))
            client.send_message(QueueUrl=cfg.get("queue_url", ""), MessageBody=json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}))
            return ChannelResult(channel=self.name, success=True, detail="Sent")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="boto3 not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class GCPPubSubChannel(BaseChannel):
    name = "gcp_pubsub"
    description = "Google Cloud Pub/Sub"
    icon = "🌐"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        # OAuth2 token based
        project = cfg.get("project_id", "")
        topic = cfg.get("topic", "agos-events")
        token = cfg.get("access_token", "")
        import base64
        data = base64.b64encode(json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}).encode()).decode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"https://pubsub.googleapis.com/v1/projects/{project}/topics/{topic}:publish",
                headers={"Authorization": f"Bearer {token}"},
                json={"messages": [{"data": data}]},
            )
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class AzureServiceBusChannel(BaseChannel):
    name = "azure_servicebus"
    description = "Azure Service Bus queue"
    icon = "🔷"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        ns = cfg.get("namespace", "")
        queue = cfg.get("queue", "agos")
        sas_token = cfg.get("sas_token", "")
        url = f"https://{ns}.servicebus.windows.net/{queue}/messages"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, headers={"Authorization": sas_token, "Content-Type": "application/json"},
                content=json.dumps({"title": msg.title, "text": msg.text}))
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class RedisChannel(BaseChannel):
    name = "redis_pubsub"
    description = "Redis Pub/Sub publish"
    icon = "🔴"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        try:
            import redis
            r = redis.Redis(host=cfg.get("host", "localhost"), port=cfg.get("port", 6379), password=cfg.get("password", ""))
            channel = cfg.get("channel", "agos:events")
            r.publish(channel, json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}))
            return ChannelResult(channel=self.name, success=True, detail="Published")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="redis not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class KafkaChannel(BaseChannel):
    name = "kafka"
    description = "Apache Kafka producer"
    icon = "🦅"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        try:
            from kafka import KafkaProducer
            producer = KafkaProducer(bootstrap_servers=cfg.get("brokers", "localhost:9092"))
            topic = cfg.get("topic", "agos-events")
            producer.send(topic, json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}).encode())
            producer.flush()
            return ChannelResult(channel=self.name, success=True, detail="Produced")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="kafka-python not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class RabbitMQChannel(BaseChannel):
    name = "rabbitmq"
    description = "RabbitMQ AMQP publish"
    icon = "🐰"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        try:
            import pika
            conn = pika.BlockingConnection(pika.URLParameters(cfg.get("url", "amqp://guest:guest@localhost/")))
            ch = conn.channel()
            exchange = cfg.get("exchange", "agos")
            routing_key = cfg.get("routing_key", "events")
            ch.basic_publish(exchange=exchange, routing_key=routing_key,
                body=json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}))
            conn.close()
            return ChannelResult(channel=self.name, success=True, detail="Published")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="pika not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class MQTTChannel(BaseChannel):
    name = "mqtt"
    description = "MQTT broker publish"
    icon = "📡"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        try:
            import paho.mqtt.publish as publish
            publish.single(
                cfg.get("topic", "agos/events"),
                json.dumps({"title": msg.title, "text": msg.text, "level": msg.level}),
                hostname=cfg.get("host", "localhost"),
                port=cfg.get("port", 1883),
            )
            return ChannelResult(channel=self.name, success=True, detail="Published")
        except ImportError:
            return ChannelResult(channel=self.name, success=False, detail="paho-mqtt not installed")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class FileLogChannel(BaseChannel):
    name = "file_log"
    description = "Append to local log file"
    icon = "📝"

    def config_schema(self) -> list[dict]:
        return [{"key": "path", "label": "Log File Path", "type": "text", "default": "agos_notifications.log"}]

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        from datetime import datetime, timezone
        path = cfg.get("path", "agos_notifications.log")
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] [{msg.level.upper()}] {msg.title}: {msg.text}\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            return ChannelResult(channel=self.name, success=True, detail=f"Wrote to {path}")
        except Exception as e:
            return ChannelResult(channel=self.name, success=False, detail=str(e))


class ConsoleChannel(BaseChannel):
    name = "console"
    description = "Print to stdout/stderr"
    icon = "🖥️"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        prefix = {"critical": "CRIT", "error": "ERR", "warning": "WARN"}.get(msg.level, "INFO")
        print(f"[{prefix}] {msg.title}: {msg.text}" if msg.title else f"[{prefix}] {msg.text}")
        return ChannelResult(channel=self.name, success=True, detail="Printed")


class HomeAssistantChannel(BaseChannel):
    name = "home_assistant"
    description = "Home Assistant notification"
    icon = "🏠"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("url", "http://localhost:8123")
        token = cfg.get("token", "")
        service = cfg.get("service", "notify.notify")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{url}/api/services/{service.replace('.', '/')}",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": msg.text, "title": msg.title or "AGOS"})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class IFTTTChannel(BaseChannel):
    name = "ifttt"
    description = "IFTTT webhook trigger"
    icon = "⚡"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        key = cfg.get("key", "")
        event = cfg.get("event", "agos_alert")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"https://maker.ifttt.com/trigger/{event}/with/key/{key}",
                json={"value1": msg.title, "value2": msg.text, "value3": msg.level})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class N8NChannel(BaseChannel):
    name = "n8n"
    description = "n8n webhook trigger"
    icon = "🔄"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"title": msg.title, "text": msg.text, "level": msg.level, **msg.data})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


class ZapierChannel(BaseChannel):
    name = "zapier"
    description = "Zapier webhook trigger"
    icon = "⚡"

    async def send(self, msg: ChannelMessage, cfg: dict[str, Any]) -> ChannelResult:
        url = cfg.get("webhook_url", "")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json={"title": msg.title, "text": msg.text, "level": msg.level})
        return ChannelResult(channel=self.name, success=r.is_success, detail=f"HTTP {r.status_code}")


# ── Registry helper ──────────────────────────────────────────

ALL_CHANNELS: list[type[BaseChannel]] = [
    WebhookChannel, DiscordChannel, SlackChannel, TelegramChannel, EmailChannel,
    MattermostChannel, TeamsChannel, PagerDutyChannel, OpsgenieChannel,
    TwilioSMSChannel, TwilioWhatsAppChannel, PushoverChannel, NtfyChannel,
    GotifyChannel, MatrixChannel, RocketChatChannel, ZulipChannel,
    GoogleChatChannel, LinearChannel, JiraChannel, GitHubIssueChannel,
    GitLabIssueChannel, DatadogChannel, GrafanaChannel, PrometheusChannel,
    InfluxDBChannel, SentryChannel, ElasticsearchChannel, LokiChannel,
    SNSChannel, SQSChannel, GCPPubSubChannel, AzureServiceBusChannel,
    RedisChannel, KafkaChannel, RabbitMQChannel, MQTTChannel,
    FileLogChannel, ConsoleChannel, HomeAssistantChannel, IFTTTChannel,
    N8NChannel, ZapierChannel,
]


def register_all_channels(registry: "ChannelRegistry") -> None:
    """Register all 42 built-in channel adapters."""
    for cls in ALL_CHANNELS:
        registry.register(cls())
