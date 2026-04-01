"""Tests for the trigger base classes."""

import asyncio

import pytest

from agos.triggers.base import BaseTrigger, TriggerConfig


class DummyTrigger(BaseTrigger):
    """Minimal trigger for testing the base class."""

    def __init__(self, config: TriggerConfig, fire_count: int = 0):
        super().__init__(config)
        self._target_fires = fire_count
        self._loop_count = 0

    async def _watch_loop(self) -> None:
        while self._running and self._loop_count < self._target_fires:
            self._loop_count += 1
            await self._fire({"count": self._loop_count})
            await asyncio.sleep(0.01)
        self._running = False


def test_trigger_config_defaults():
    config = TriggerConfig(kind="test")
    assert config.kind == "test"
    assert config.id  # auto-generated
    assert config.active is True
    assert config.description == ""
    assert config.intent == ""
    assert config.params == {}


def test_trigger_config_custom():
    config = TriggerConfig(
        kind="file_watch",
        description="Watch src/",
        intent="review changes",
        params={"path": "./src"},
    )
    assert config.kind == "file_watch"
    assert config.intent == "review changes"
    assert config.params["path"] == "./src"


@pytest.mark.asyncio
async def test_trigger_start_stop():
    config = TriggerConfig(kind="test")
    trigger = DummyTrigger(config, fire_count=100)

    await trigger.start()
    assert trigger.is_running

    await asyncio.sleep(0.05)
    await trigger.stop()
    assert not trigger.is_running


@pytest.mark.asyncio
async def test_trigger_fires_callback():
    config = TriggerConfig(kind="test")
    trigger = DummyTrigger(config, fire_count=3)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)
    await trigger.start()

    # Wait for all fires to complete
    await asyncio.sleep(0.2)
    await trigger.stop()

    assert len(events) == 3
    assert events[0] == {"count": 1}
    assert events[2] == {"count": 3}


@pytest.mark.asyncio
async def test_trigger_no_callback():
    """Fire without a callback set — should not crash."""
    config = TriggerConfig(kind="test")
    trigger = DummyTrigger(config, fire_count=1)

    await trigger.start()
    await asyncio.sleep(0.1)
    await trigger.stop()
    # No error = success
