"""Tests for the schedule trigger."""

import asyncio

import pytest

from agos.triggers.base import TriggerConfig
from agos.triggers.schedule import ScheduleTrigger


@pytest.mark.asyncio
async def test_schedule_fires_on_interval():
    config = TriggerConfig(
        kind="schedule",
        params={"interval_seconds": 0.1},
    )
    trigger = ScheduleTrigger(config)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)
    await trigger.start()

    # Wait enough for ~3 fires
    await asyncio.sleep(0.35)
    await trigger.stop()

    assert len(events) >= 2
    assert events[0]["trigger_kind"] == "schedule"
    assert events[0]["fire_count"] == 1


@pytest.mark.asyncio
async def test_schedule_max_fires():
    config = TriggerConfig(
        kind="schedule",
        params={"interval_seconds": 0.05, "max_fires": 3},
    )
    trigger = ScheduleTrigger(config)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)
    await trigger.start()

    # Wait long enough for it to finish
    await asyncio.sleep(0.5)
    await trigger.stop()

    assert len(events) == 3
    assert not trigger.is_running


@pytest.mark.asyncio
async def test_schedule_fire_count_property():
    config = TriggerConfig(
        kind="schedule",
        params={"interval_seconds": 0.05, "max_fires": 2},
    )
    trigger = ScheduleTrigger(config)
    async def noop(d):
        pass

    trigger.on_fire(noop)

    await trigger.start()
    await asyncio.sleep(0.3)
    await trigger.stop()

    assert trigger.fire_count == 2


@pytest.mark.asyncio
async def test_schedule_stop_before_fire():
    config = TriggerConfig(
        kind="schedule",
        params={"interval_seconds": 10},  # long interval
    )
    trigger = ScheduleTrigger(config)

    events = []
    async def on_event(d):
        events.append(d)

    trigger.on_fire(on_event)

    await trigger.start()
    assert trigger.is_running

    await asyncio.sleep(0.05)
    await trigger.stop()

    assert len(events) == 0  # never fired
    assert not trigger.is_running
