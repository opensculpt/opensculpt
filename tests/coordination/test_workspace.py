"""Tests for the shared workspace."""

import pytest

from agos.coordination.workspace import Workspace


@pytest.mark.asyncio
async def test_put_and_get():
    ws = Workspace(name="test")

    await ws.put("findings", "The API rate limit is 100/s", author="researcher")
    art = await ws.get("findings")

    assert art is not None
    assert art.key == "findings"
    assert art.value == "The API rate limit is 100/s"
    assert art.author == "researcher"


@pytest.mark.asyncio
async def test_get_value():
    ws = Workspace()

    await ws.put("count", 42, author="analyst", kind="data")
    val = await ws.get_value("count")

    assert val == 42


@pytest.mark.asyncio
async def test_get_value_default():
    ws = Workspace()

    val = await ws.get_value("missing", default="N/A")
    assert val == "N/A"


@pytest.mark.asyncio
async def test_update_existing():
    ws = Workspace()

    await ws.put("draft", "version 1", author="coder")
    await ws.put("draft", "version 2", author="reviewer")

    art = await ws.get("draft")
    assert art.value == "version 2"
    assert art.author == "reviewer"

    # Should still be a single artifact
    keys = await ws.keys()
    assert keys == ["draft"]


@pytest.mark.asyncio
async def test_delete():
    ws = Workspace()

    await ws.put("temp", "data", author="a1")
    deleted = await ws.delete("temp")
    assert deleted

    art = await ws.get("temp")
    assert art is None


@pytest.mark.asyncio
async def test_delete_nonexistent():
    ws = Workspace()
    deleted = await ws.delete("nope")
    assert not deleted


@pytest.mark.asyncio
async def test_list_artifacts():
    ws = Workspace()

    await ws.put("a", "first", author="a1")
    await ws.put("b", "second", author="a2")
    await ws.put("c", "third", author="a3")

    arts = await ws.list_artifacts()
    assert len(arts) == 3


@pytest.mark.asyncio
async def test_keys():
    ws = Workspace()

    await ws.put("x", 1, author="a")
    await ws.put("y", 2, author="a")

    keys = await ws.keys()
    assert set(keys) == {"x", "y"}


@pytest.mark.asyncio
async def test_clear():
    ws = Workspace()

    await ws.put("a", 1, author="a")
    await ws.put("b", 2, author="a")
    await ws.clear()

    keys = await ws.keys()
    assert keys == []


@pytest.mark.asyncio
async def test_summary():
    ws = Workspace()

    assert ws.summary() == "Workspace is empty."

    await ws.put("findings", "The bug is in auth.py", author="reviewer")
    summary = ws.summary()

    assert "findings" in summary
    assert "reviewer" in summary
    assert "auth.py" in summary
