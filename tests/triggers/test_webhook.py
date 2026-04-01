"""Tests for the webhook trigger."""

import asyncio

import pytest

from agos.triggers.base import TriggerConfig
from agos.triggers.webhook import WebhookTrigger


@pytest.mark.asyncio
async def test_webhook_receive_and_fire():
    config = TriggerConfig(
        kind="webhook",
        params={"path": "/hooks/github"},
    )
    trigger = WebhookTrigger(config)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)
    await trigger.start()

    # Simulate an incoming webhook
    await trigger.receive({"action": "push", "repo": "agos"})

    await asyncio.sleep(0.2)
    await trigger.stop()

    assert len(events) == 1
    assert events[0]["trigger_kind"] == "webhook"
    assert events[0]["path"] == "/hooks/github"
    assert events[0]["payload"]["action"] == "push"


@pytest.mark.asyncio
async def test_webhook_path_property():
    config = TriggerConfig(
        kind="webhook",
        params={"path": "/hooks/stripe"},
    )
    trigger = WebhookTrigger(config)
    assert trigger.path == "/hooks/stripe"


@pytest.mark.asyncio
async def test_webhook_default_path():
    config = TriggerConfig(
        kind="webhook",
        params={},
    )
    trigger = WebhookTrigger(config)
    assert trigger.path == f"/hooks/{config.id}"


@pytest.mark.asyncio
async def test_webhook_multiple_payloads():
    config = TriggerConfig(
        kind="webhook",
        params={"path": "/hooks/test"},
    )
    trigger = WebhookTrigger(config)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)

    await trigger.start()

    await trigger.receive({"event": "first"})
    await trigger.receive({"event": "second"})
    await trigger.receive({"event": "third"})

    await asyncio.sleep(0.5)
    await trigger.stop()

    assert len(events) == 3
    assert events[0]["payload"]["event"] == "first"
    assert events[2]["payload"]["event"] == "third"
