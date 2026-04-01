"""Tests for the file watcher trigger."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from agos.triggers.base import TriggerConfig
from agos.triggers.file_watch import FileWatchTrigger


@pytest.fixture
def watch_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.mark.asyncio
async def test_snapshot_empty_dir(watch_dir):
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir), "patterns": ["*"]},
    )
    trigger = FileWatchTrigger(config)
    snapshot = trigger._snapshot()
    assert snapshot == {}


@pytest.mark.asyncio
async def test_snapshot_with_files(watch_dir):
    (watch_dir / "a.py").write_text("hello")
    (watch_dir / "b.txt").write_text("world")

    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir), "patterns": ["*"]},
    )
    trigger = FileWatchTrigger(config)
    snapshot = trigger._snapshot()
    assert len(snapshot) == 2


@pytest.mark.asyncio
async def test_snapshot_with_pattern_filter(watch_dir):
    (watch_dir / "a.py").write_text("hello")
    (watch_dir / "b.txt").write_text("world")

    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir), "patterns": ["*.py"]},
    )
    trigger = FileWatchTrigger(config)
    snapshot = trigger._snapshot()
    assert len(snapshot) == 1
    assert any("a.py" in k for k in snapshot)


@pytest.mark.asyncio
async def test_diff_detects_added(watch_dir):
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir)},
    )
    trigger = FileWatchTrigger(config)

    old = {}
    new = {"file1.py": 1234.0}
    changes = trigger._diff(old, new)
    assert changes["added"] == ["file1.py"]
    assert changes["removed"] == []
    assert changes["modified"] == []


@pytest.mark.asyncio
async def test_diff_detects_removed(watch_dir):
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir)},
    )
    trigger = FileWatchTrigger(config)

    old = {"file1.py": 1234.0}
    new = {}
    changes = trigger._diff(old, new)
    assert changes["removed"] == ["file1.py"]
    assert changes["added"] == []


@pytest.mark.asyncio
async def test_diff_detects_modified(watch_dir):
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir)},
    )
    trigger = FileWatchTrigger(config)

    old = {"file1.py": 1234.0}
    new = {"file1.py": 5678.0}
    changes = trigger._diff(old, new)
    assert changes["modified"] == ["file1.py"]


@pytest.mark.asyncio
async def test_diff_no_changes(watch_dir):
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir)},
    )
    trigger = FileWatchTrigger(config)

    old = {"file1.py": 1234.0}
    new = {"file1.py": 1234.0}
    changes = trigger._diff(old, new)
    assert changes == {}


def test_summarize():
    changes = {"added": ["a.py"], "modified": ["b.py", "c.py"], "removed": []}
    summary = FileWatchTrigger._summarize(changes)
    assert "1 added" in summary
    assert "2 modified" in summary


@pytest.mark.asyncio
async def test_watch_fires_on_file_change(watch_dir):
    """Integration: actually watch a directory and detect a new file."""
    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(watch_dir), "patterns": ["*"], "interval": 0.2},
    )
    trigger = FileWatchTrigger(config)

    events = []

    async def on_event(data):
        events.append(data)

    trigger.on_fire(on_event)
    await trigger.start()

    # Wait for initial snapshot
    await asyncio.sleep(0.3)

    # Create a file — should trigger
    (watch_dir / "new_file.py").write_text("new content")

    # Wait for detection
    await asyncio.sleep(0.5)
    await trigger.stop()

    assert len(events) >= 1
    assert events[0]["trigger_kind"] == "file_watch"
    assert "added" in events[0]["changes"]


@pytest.mark.asyncio
async def test_watch_single_file(watch_dir):
    """Watch a specific file rather than a directory."""
    target = watch_dir / "watched.py"
    target.write_text("original")

    config = TriggerConfig(
        kind="file_watch",
        params={"path": str(target), "interval": 0.2},
    )
    trigger = FileWatchTrigger(config)
    snapshot = trigger._snapshot()
    assert len(snapshot) == 1
    assert str(target) in snapshot
