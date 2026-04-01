"""Tests for the Trigger Manager."""

import asyncio

import pytest

from agos.triggers.base import TriggerConfig
from agos.triggers.manager import TriggerManager


@pytest.fixture
def manager():
    return TriggerManager()


@pytest.mark.asyncio
async def test_register_file_watch(manager):
    config = TriggerConfig(
        kind="file_watch",
        description="Watch tests",
        intent="review changes",
        params={"path": ".", "interval": 60},
    )
    trigger = await manager.register(config)
    assert trigger.is_running

    active = manager.list_triggers()
    assert len(active) == 1
    assert active[0]["kind"] == "file_watch"
    assert active[0]["active"] is True

    await manager.stop_all()


@pytest.mark.asyncio
async def test_register_schedule(manager):
    config = TriggerConfig(
        kind="schedule",
        description="Periodic check",
        intent="check health",
        params={"interval_seconds": 3600},
    )
    trigger = await manager.register(config)
    assert trigger.is_running

    await manager.stop_all()


@pytest.mark.asyncio
async def test_register_webhook(manager):
    config = TriggerConfig(
        kind="webhook",
        description="GitHub webhook",
        intent="review PR",
        params={"path": "/hooks/github"},
    )
    trigger = await manager.register(config)
    assert trigger.is_running

    await manager.stop_all()


@pytest.mark.asyncio
async def test_register_unknown_kind(manager):
    config = TriggerConfig(kind="nonexistent")
    with pytest.raises(ValueError, match="Unknown trigger kind"):
        await manager.register(config)


@pytest.mark.asyncio
async def test_unregister(manager):
    config = TriggerConfig(
        kind="schedule",
        params={"interval_seconds": 3600},
    )
    await manager.register(config)

    removed = await manager.unregister(config.id)
    assert removed

    active = manager.list_triggers()
    assert len(active) == 0


@pytest.mark.asyncio
async def test_unregister_nonexistent(manager):
    removed = await manager.unregister("nonexistent-id")
    assert not removed


@pytest.mark.asyncio
async def test_stop_all(manager):
    for i in range(3):
        config = TriggerConfig(
            kind="schedule",
            params={"interval_seconds": 3600},
        )
        await manager.register(config)

    assert len(manager.list_triggers()) == 3

    await manager.stop_all()
    assert len(manager.list_triggers()) == 0


@pytest.mark.asyncio
async def test_handler_called_on_fire(manager):
    """When a trigger fires, the manager's handler should be called."""
    handled_intents = []

    async def handler(intent: str):
        handled_intents.append(intent)

    manager.set_handler(handler)

    config = TriggerConfig(
        kind="schedule",
        description="Quick test",
        intent="do something",
        params={"interval_seconds": 0.1, "max_fires": 1},
    )
    await manager.register(config)

    await asyncio.sleep(0.3)
    await manager.stop_all()

    assert len(handled_intents) >= 1
    assert "do something" in handled_intents[0]


@pytest.mark.asyncio
async def test_list_triggers_details(manager):
    config = TriggerConfig(
        kind="file_watch",
        description="Watch src",
        intent="review code",
        params={"path": ".", "interval": 60},
    )
    await manager.register(config)

    triggers = manager.list_triggers()
    assert len(triggers) == 1

    t = triggers[0]
    assert t["id"] == config.id
    assert t["kind"] == "file_watch"
    assert t["description"] == "Watch src"
    assert t["intent"] == "review code"
    assert t["active"] is True

    await manager.stop_all()
