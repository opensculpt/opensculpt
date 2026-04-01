"""Tests for the agent messaging channel."""

import asyncio

import pytest

from agos.coordination.channel import Channel, Message


@pytest.mark.asyncio
async def test_post_and_receive():
    ch = Channel(name="test")
    received = []

    async def handler(msg: Message):
        received.append(msg)

    ch.subscribe("agent-1", handler)
    ch.subscribe("agent-2", lambda m: asyncio.sleep(0))  # dummy

    await ch.post("agent-2", "hello from agent 2")

    assert len(received) == 1
    assert received[0].sender == "agent-2"
    assert received[0].content == "hello from agent 2"


@pytest.mark.asyncio
async def test_sender_does_not_receive_own_message():
    ch = Channel(name="test")
    received = []

    async def handler(msg: Message):
        received.append(msg)

    ch.subscribe("agent-1", handler)

    await ch.post("agent-1", "talking to myself")

    assert len(received) == 0  # sender should not get own message


@pytest.mark.asyncio
async def test_broadcast_goes_to_all():
    ch = Channel(name="test")
    received_1 = []
    received_2 = []

    async def handler_1(msg: Message):
        received_1.append(msg)

    async def handler_2(msg: Message):
        received_2.append(msg)

    ch.subscribe("agent-1", handler_1)
    ch.subscribe("agent-2", handler_2)

    await ch.broadcast("system announcement")

    assert len(received_1) == 1
    assert len(received_2) == 1
    assert received_1[0].sender == "system"
    assert received_1[0].content == "system announcement"


@pytest.mark.asyncio
async def test_history_preserved():
    ch = Channel(name="test")

    async def noop(msg):
        pass

    ch.subscribe("a1", noop)
    ch.subscribe("a2", noop)

    await ch.post("a1", "first")
    await ch.post("a2", "second")
    await ch.post("a1", "third")

    assert len(ch.history) == 3
    assert ch.history[0].content == "first"
    assert ch.history[2].content == "third"


@pytest.mark.asyncio
async def test_unsubscribe():
    ch = Channel(name="test")
    received = []

    async def handler(msg: Message):
        received.append(msg)

    ch.subscribe("agent-1", handler)
    ch.unsubscribe("agent-1")

    await ch.post("agent-2", "hello")

    assert len(received) == 0


@pytest.mark.asyncio
async def test_member_count_and_members():
    ch = Channel(name="test")

    async def noop(msg):
        pass

    ch.subscribe("a1", noop)
    ch.subscribe("a2", noop)
    ch.subscribe("a3", noop)

    assert ch.member_count == 3
    assert set(ch.members) == {"a1", "a2", "a3"}


@pytest.mark.asyncio
async def test_message_kinds():
    ch = Channel(name="test")
    received = []

    async def handler(msg: Message):
        received.append(msg)

    ch.subscribe("listener", handler)

    await ch.post("sender", "found the bug", kind="result")
    await ch.post("sender", "need help", kind="request", metadata={"urgent": True})

    assert received[0].kind == "result"
    assert received[1].kind == "request"
    assert received[1].metadata["urgent"] is True
