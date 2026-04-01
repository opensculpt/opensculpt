"""Tests for the event bus."""

import pytest

from agos.events.bus import EventBus, Event


@pytest.mark.asyncio
async def test_emit_and_subscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("agent.started", handler)
    await bus.emit("agent.started", {"agent_id": "a1"})

    assert len(received) == 1
    assert received[0].topic == "agent.started"
    assert received[0].data["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_wildcard_subscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("agent.*", handler)
    await bus.emit("agent.started", {"id": "a1"})
    await bus.emit("agent.completed", {"id": "a2"})
    await bus.emit("tool.called", {"name": "file_read"})  # should NOT match

    assert len(received) == 2


@pytest.mark.asyncio
async def test_star_matches_all():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("*", handler)
    await bus.emit("agent.started")
    await bus.emit("tool.called")
    await bus.emit("policy.violation")

    assert len(received) == 3


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("test", handler)
    await bus.emit("test")
    assert len(received) == 1

    bus.unsubscribe("test", handler)
    await bus.emit("test")
    assert len(received) == 1  # no new events


@pytest.mark.asyncio
async def test_history():
    bus = EventBus()
    await bus.emit("a.1", {"x": 1})
    await bus.emit("a.2", {"x": 2})
    await bus.emit("b.1", {"x": 3})

    all_events = bus.history()
    assert len(all_events) == 3

    a_events = bus.history(topic_filter="a.*")
    assert len(a_events) == 2


@pytest.mark.asyncio
async def test_history_limit():
    bus = EventBus(history_limit=5)
    for i in range(10):
        await bus.emit("test", {"i": i})

    assert len(bus.history()) == 5


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    r1, r2 = [], []

    async def h1(e: Event):
        r1.append(e)

    async def h2(e: Event):
        r2.append(e)

    bus.subscribe("event", h1)
    bus.subscribe("event", h2)
    await bus.emit("event")

    assert len(r1) == 1
    assert len(r2) == 1


@pytest.mark.asyncio
async def test_emit_returns_event():
    bus = EventBus()
    event = await bus.emit("test.topic", {"key": "value"}, source="system")

    assert event.topic == "test.topic"
    assert event.data["key"] == "value"
    assert event.source == "system"
    assert event.id


@pytest.mark.asyncio
async def test_subscriber_count():
    bus = EventBus()
    assert bus.subscriber_count == 0

    async def h(e):
        pass

    bus.subscribe("a", h)
    bus.subscribe("b", h)
    assert bus.subscriber_count == 2


@pytest.mark.asyncio
async def test_topics():
    bus = EventBus()
    await bus.emit("agent.started")
    await bus.emit("tool.called")
    await bus.emit("agent.started")

    topics = bus.topics()
    assert "agent.started" in topics
    assert "tool.called" in topics


@pytest.mark.asyncio
async def test_ws_connection_count():
    bus = EventBus()
    assert bus.ws_connection_count == 0

    async def fake_ws(e):
        pass

    bus.add_ws_connection(fake_ws)
    assert bus.ws_connection_count == 1

    bus.remove_ws_connection(fake_ws)
    assert bus.ws_connection_count == 0


@pytest.mark.asyncio
async def test_ws_receives_events():
    bus = EventBus()
    ws_received = []

    async def fake_ws(event: Event):
        ws_received.append(event)

    bus.add_ws_connection(fake_ws)
    await bus.emit("test.ws", {"hello": "world"})

    assert len(ws_received) == 1
    assert ws_received[0].topic == "test.ws"
