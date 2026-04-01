"""Tests for the proactive intelligence engine."""

import pytest
import tempfile
import os
from datetime import datetime, timedelta

from agos.intent.proactive import (
    Suggestion,
    RepetitiveEditDetector,
    FailurePatternDetector,
    FrequentToolDetector,
    IdleProjectDetector,
    ProactiveEngine,
)
from agos.knowledge.base import Thread, ThreadQuery
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
async def loom(db_path):
    lm = TheLoom(db_path)
    await lm.initialize()
    return lm


# ── Suggestion model tests ───────────────────────────────────────

def test_suggestion_model():
    s = Suggestion(
        detector_name="test",
        description="something detected",
        confidence=0.8,
        suggested_action="do this",
        context={"key": "value"},
    )
    assert s.id
    assert s.detector_name == "test"
    assert s.dismissed is False
    assert isinstance(s.created_at, datetime)


def test_suggestion_defaults():
    s = Suggestion(detector_name="d", description="desc")
    assert s.confidence == 0.7
    assert s.suggested_action == ""
    assert s.context == {}
    assert not s.dismissed


# ── RepetitiveEditDetector tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_repetitive_edit_no_files(loom):
    detector = RepetitiveEditDetector(threshold=3)
    suggestions = await detector.detect(loom)
    assert suggestions == []


@pytest.mark.asyncio
async def test_repetitive_edit_below_threshold(loom):
    # Add 2 edits (below threshold of 3)
    await loom.graph.link("agent:coder", "edited", "file:auth.py")
    await loom.graph.link("agent:coder", "edited", "file:auth.py")

    detector = RepetitiveEditDetector(threshold=3)
    suggestions = await detector.detect(loom)
    assert suggestions == []


@pytest.mark.asyncio
async def test_repetitive_edit_above_threshold(loom):
    for i in range(4):
        await loom.graph.link(f"agent:coder_{i}", "edited", "file:auth.py")

    detector = RepetitiveEditDetector(threshold=3)
    suggestions = await detector.detect(loom)
    assert len(suggestions) == 1
    assert "auth.py" in suggestions[0].description
    assert "run tests" in suggestions[0].suggested_action


# ── FailurePatternDetector tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_failure_pattern_no_errors(loom):
    detector = FailurePatternDetector(threshold=3)
    suggestions = await detector.detect(loom)
    assert suggestions == []


@pytest.mark.asyncio
async def test_failure_pattern_above_threshold(loom):
    for i in range(5):
        await loom.episodic.store(Thread(
            content=f"Build error {i}: module not found",
            kind="error",
        ))

    detector = FailurePatternDetector(threshold=3)
    suggestions = await detector.detect(loom)
    assert len(suggestions) == 1
    assert "5" in suggestions[0].description
    assert "investigate" in suggestions[0].suggested_action.lower()


# ── FrequentToolDetector tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_frequent_tool_no_tools(loom):
    detector = FrequentToolDetector(threshold=5)
    suggestions = await detector.detect(loom)
    assert suggestions == []


@pytest.mark.asyncio
async def test_frequent_tool_above_threshold(loom):
    for i in range(6):
        await loom.graph.link(f"agent:worker_{i}", "used_tool", "tool:shell_exec")

    detector = FrequentToolDetector(threshold=5)
    suggestions = await detector.detect(loom)
    assert len(suggestions) == 1
    assert "shell_exec" in suggestions[0].description
    assert "shortcut" in suggestions[0].suggested_action


# ── IdleProjectDetector tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_idle_no_events(loom):
    detector = IdleProjectDetector(idle_hours=48)
    suggestions = await detector.detect(loom)
    assert len(suggestions) == 1
    assert "no activity" in suggestions[0].description.lower()


@pytest.mark.asyncio
async def test_idle_recent_activity(loom):
    # Add a very recent event
    await loom.episodic.store(Thread(
        content="Just did something",
        kind="event",
    ))

    detector = IdleProjectDetector(idle_hours=48)
    suggestions = await detector.detect(loom)
    assert suggestions == []  # not idle


@pytest.mark.asyncio
async def test_idle_old_activity(loom):
    import aiosqlite

    # Store an event and manually backdate it
    thread = Thread(content="Old work", kind="event")
    await loom.episodic.store(thread)

    old_date = (datetime.now() - timedelta(hours=72)).isoformat()
    async with aiosqlite.connect(loom.episodic._db_path) as db:
        await db.execute(
            "UPDATE episodic SET created_at = ? WHERE id = ?",
            (old_date, thread.id),
        )
        await db.commit()

    detector = IdleProjectDetector(idle_hours=48)
    suggestions = await detector.detect(loom)
    assert len(suggestions) == 1
    assert "72" in suggestions[0].description or "hours" in suggestions[0].description


# ── ProactiveEngine tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_scan_no_loom():
    engine = ProactiveEngine()
    results = await engine.scan()
    assert results == []


@pytest.mark.asyncio
async def test_engine_scan_no_detectors(loom):
    engine = ProactiveEngine(loom=loom)
    results = await engine.scan()
    assert results == []


@pytest.mark.asyncio
async def test_engine_scan_with_detector(loom):
    # Create data that triggers idle detector
    engine = ProactiveEngine(loom=loom)
    engine.register_detector(IdleProjectDetector(idle_hours=0))

    results = await engine.scan()
    # Should find at least 1 suggestion (no events = idle)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_engine_get_suggestions(loom):
    engine = ProactiveEngine(loom=loom)
    engine.register_detector(IdleProjectDetector(idle_hours=0))

    await engine.scan()
    suggestions = await engine.get_suggestions()
    assert len(suggestions) >= 1


@pytest.mark.asyncio
async def test_engine_dismiss(loom):
    engine = ProactiveEngine(loom=loom)
    engine.register_detector(IdleProjectDetector(idle_hours=0))

    results = await engine.scan()
    assert len(results) >= 1

    sid = results[0].id
    ok = await engine.dismiss(sid)
    assert ok

    # Dismissed should be excluded by default
    active = await engine.get_suggestions()
    assert all(s.id != sid for s in active)

    # But visible with include_dismissed
    all_s = await engine.get_suggestions(include_dismissed=True)
    assert any(s.id == sid for s in all_s)


@pytest.mark.asyncio
async def test_engine_dismiss_not_found():
    engine = ProactiveEngine()
    assert not await engine.dismiss("nonexistent")


@pytest.mark.asyncio
async def test_engine_emits_events(loom):
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("proactive.*", handler)

    engine = ProactiveEngine(loom=loom, event_bus=bus)
    engine.register_detector(IdleProjectDetector(idle_hours=0))

    await engine.scan()
    assert len(received) >= 1
    assert received[0].topic == "proactive.suggestion"


@pytest.mark.asyncio
async def test_engine_stores_in_loom(loom):
    engine = ProactiveEngine(loom=loom)
    engine.register_detector(IdleProjectDetector(idle_hours=0))

    await engine.scan()

    # Should be stored in semantic weave
    results = await loom.semantic.query(ThreadQuery(kind="suggestion", limit=5))
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_engine_act_on_no_runtime(loom):
    engine = ProactiveEngine(loom=loom)
    engine._suggestions.append(Suggestion(
        detector_name="test", description="test", suggested_action="test",
    ))
    result = await engine.act_on(engine._suggestions[0].id, runtime=None)
    assert result is None
