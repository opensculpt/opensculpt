"""Tests for the evolution daemon."""

import asyncio
import tempfile

import pytest
import pytest_asyncio

from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus
from agos.evolution.engine import EvolutionEngine, EvolutionReport
from agos.evolution.sandbox import Sandbox
from agos.evolution.daemon import EvolutionDaemon
from agos.evolution.pipeline import EvolutionPipelineConfig



class FakeScout:
    async def search_recent(self, days=7, max_results=20):
        return []


class FakeAnalyzer:
    async def analyze_batch(self, papers):
        return []


class FakeRepoScout:
    async def find_repo(self, *args, **kwargs):
        return None

    async def fetch_repo(self, *args, **kwargs):
        return None


class FakeCodeAnalyzer:
    async def analyze_repo(self, *args, **kwargs):
        return None


@pytest_asyncio.fixture
async def engine():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    loom = TheLoom(db_path)
    await loom.initialize()

    return EvolutionEngine(
        scout=FakeScout(),
        analyzer=FakeAnalyzer(),
        loom=loom,
        event_bus=EventBus(),
        repo_scout=FakeRepoScout(),
        code_analyzer=FakeCodeAnalyzer(),
        sandbox=Sandbox(),
    )


# ── EvolutionDaemon tests ──────────────────────────────────────

def test_daemon_init(engine):
    daemon = EvolutionDaemon(engine)
    assert not daemon.is_running
    assert daemon.history == []


@pytest.mark.asyncio
async def test_daemon_start_stop(engine):
    daemon = EvolutionDaemon(engine)
    await daemon.start()
    assert daemon.is_running

    await daemon.stop()
    assert not daemon.is_running


@pytest.mark.asyncio
async def test_daemon_start_idempotent(engine):
    daemon = EvolutionDaemon(engine)
    await daemon.start()
    await daemon.start()  # should not create second task
    assert daemon.is_running
    await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_run_once(engine):
    daemon = EvolutionDaemon(engine)
    report = await daemon.run_once()
    assert isinstance(report, EvolutionReport)
    assert len(daemon.history) == 1


@pytest.mark.asyncio
async def test_daemon_run_once_records_history(engine):
    daemon = EvolutionDaemon(engine)
    await daemon.run_once()
    await daemon.run_once()
    assert len(daemon.history) == 2


@pytest.mark.asyncio
async def test_daemon_emits_events(engine):
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("evolution.*", handler)

    daemon = EvolutionDaemon(engine, event_bus=bus)
    await daemon.start()
    # Give it a moment to emit the started event
    await asyncio.sleep(0.1)
    await daemon.stop()

    topics = [e.topic for e in received]
    assert "evolution.daemon_started" in topics
    assert "evolution.daemon_stopped" in topics


@pytest.mark.asyncio
async def test_daemon_config_defaults():
    config = EvolutionPipelineConfig()
    assert config.auto_merge_low_risk is False
    assert config.require_human_review is True
    assert config.evolution_interval_hours == 168


@pytest.mark.asyncio
async def test_daemon_no_event_bus(engine):
    """Daemon works without an event bus."""
    daemon = EvolutionDaemon(engine, event_bus=None)
    report = await daemon.run_once()
    assert isinstance(report, EvolutionReport)
