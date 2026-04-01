"""Tests for the ambient watcher system."""

import pytest
from datetime import datetime

from agos.ambient.watcher import (
    Observation,
    GitWatcher,
    FileActivityWatcher,
    DailyBriefingWatcher,
    AmbientManager,
)
from agos.triggers.manager import TriggerManager
from agos.events.bus import EventBus


# ── Observation model tests ──────────────────────────────────────

def test_observation_model():
    obs = Observation(
        watcher_name="test",
        kind="test_kind",
        summary="Something happened",
        detail="Details here",
        suggested_action="do something",
    )
    assert obs.id
    assert obs.watcher_name == "test"
    assert obs.kind == "test_kind"
    assert obs.confidence == 0.8
    assert obs.suggested_action == "do something"
    assert isinstance(obs.created_at, datetime)


def test_observation_defaults():
    obs = Observation(watcher_name="w", kind="k", summary="s")
    assert obs.detail == ""
    assert obs.suggested_action == ""
    assert obs.confidence == 0.8


# ── GitWatcher tests ─────────────────────────────────────────────

def test_git_watcher_init():
    gw = GitWatcher(repo_path="/tmp", check_interval=30)
    assert gw.name == "git_watcher"
    assert gw._repo_path == "/tmp"
    assert gw._check_interval == 30
    assert not gw.is_running


def test_git_watcher_trigger_config():
    gw = GitWatcher(check_interval=120)
    config = gw._make_trigger_config()
    assert config.kind == "schedule"
    assert config.params["interval_seconds"] == 120
    assert "120s" in config.description


@pytest.mark.asyncio
async def test_git_watcher_on_trigger_no_repo():
    """Should handle missing git repo gracefully."""
    gw = GitWatcher(repo_path="/nonexistent/path")
    # Should not raise
    await gw._on_trigger({"fire_count": 1})
    assert len(gw._observations) == 0


@pytest.mark.asyncio
async def test_git_watcher_first_check_no_observation():
    """First check just records the commit hash, no observation."""
    gw = GitWatcher()
    gw._last_commit = ""
    # Simulate a trigger — if git works, it just records the hash
    await gw._on_trigger({"fire_count": 1})
    # First check: no observation (just learning the current state)
    assert len(gw._observations) == 0


# ── FileActivityWatcher tests ────────────────────────────────────

def test_file_activity_init():
    fw = FileActivityWatcher(watch_path="/src", patterns=["*.py", "*.ts"])
    assert fw.name == "file_activity"
    assert fw._watch_path == "/src"
    assert fw._patterns == ["*.py", "*.ts"]


def test_file_activity_trigger_config():
    fw = FileActivityWatcher(watch_path="./myproject", check_interval=10)
    config = fw._make_trigger_config()
    assert config.kind == "file_watch"
    assert config.params["path"] == "./myproject"
    assert config.params["interval"] == 10


@pytest.mark.asyncio
async def test_file_activity_on_trigger_with_changes():
    fw = FileActivityWatcher()
    await fw._on_trigger({
        "changes": {
            "added": ["new_file.py"],
            "modified": ["src/main.py"],
            "removed": [],
        },
        "summary": "2 files changed",
    })
    assert len(fw._observations) == 1
    obs = fw._observations[0]
    assert obs.kind == "file_change"
    assert "1 added" in obs.summary
    assert "1 modified" in obs.summary


@pytest.mark.asyncio
async def test_file_activity_suggests_tests_for_test_files():
    fw = FileActivityWatcher()
    await fw._on_trigger({
        "changes": {
            "added": [],
            "modified": ["tests/test_auth.py"],
            "removed": [],
        },
    })
    assert len(fw._observations) == 1
    assert fw._observations[0].suggested_action == "run tests"


@pytest.mark.asyncio
async def test_file_activity_suggests_validate_for_config():
    fw = FileActivityWatcher()
    await fw._on_trigger({
        "changes": {
            "added": [],
            "modified": ["config.yaml"],
            "removed": [],
        },
    })
    assert len(fw._observations) == 1
    assert "validate" in fw._observations[0].suggested_action.lower()


@pytest.mark.asyncio
async def test_file_activity_no_obs_on_empty_changes():
    fw = FileActivityWatcher()
    await fw._on_trigger({"changes": {"added": [], "modified": [], "removed": []}})
    assert len(fw._observations) == 0


@pytest.mark.asyncio
async def test_file_activity_no_obs_on_no_data():
    fw = FileActivityWatcher()
    await fw._on_trigger({})
    assert len(fw._observations) == 0


# ── DailyBriefingWatcher tests ───────────────────────────────────

def test_daily_briefing_init():
    db = DailyBriefingWatcher(interval_hours=12)
    assert db.name == "daily_briefing"
    assert db._interval_hours == 12


def test_daily_briefing_trigger_config():
    db = DailyBriefingWatcher(interval_hours=6)
    config = db._make_trigger_config()
    assert config.kind == "schedule"
    assert config.params["interval_seconds"] == 6 * 3600


@pytest.mark.asyncio
async def test_daily_briefing_no_loom():
    db = DailyBriefingWatcher()
    await db._on_trigger({"fire_count": 1})
    assert len(db._observations) == 0


@pytest.mark.asyncio
async def test_daily_briefing_with_empty_timeline(tmp_path):
    """Generates a 'no activity' briefing when timeline is empty."""
    from agos.knowledge.manager import TheLoom
    loom = TheLoom(str(tmp_path / "test.db"))
    await loom.initialize()

    db = DailyBriefingWatcher()
    db._loom = loom
    await db._on_trigger({"fire_count": 1})

    assert len(db._observations) == 1
    assert "no recent activity" in db._observations[0].summary.lower()


@pytest.mark.asyncio
async def test_daily_briefing_with_events(tmp_path):
    """Generates a summary when there are events."""
    from agos.knowledge.manager import TheLoom
    from agos.knowledge.base import Thread

    loom = TheLoom(str(tmp_path / "test.db"))
    await loom.initialize()

    # Add some events
    for i in range(5):
        await loom.episodic.store(Thread(
            content=f"Event {i} happened",
            kind="event",
        ))

    db = DailyBriefingWatcher()
    db._loom = loom
    await db._on_trigger({"fire_count": 1})

    assert len(db._observations) == 1
    obs = db._observations[0]
    assert obs.kind == "briefing"
    assert "5 events" in obs.summary


# ── BaseAmbientWatcher observation tests ─────────────────────────

@pytest.mark.asyncio
async def test_observe_stores_in_loom(tmp_path):
    from agos.knowledge.manager import TheLoom

    loom = TheLoom(str(tmp_path / "test.db"))
    await loom.initialize()

    fw = FileActivityWatcher()
    fw._loom = loom

    obs = Observation(
        watcher_name="file_activity",
        kind="file_change",
        summary="3 files changed",
    )
    await fw._observe(obs)

    # Should be stored in semantic weave
    from agos.knowledge.base import ThreadQuery
    results = await loom.semantic.query(ThreadQuery(text="files changed", limit=5))
    assert len(results) >= 1
    assert "file_change" in results[0].tags


@pytest.mark.asyncio
async def test_observe_emits_event():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("ambient.*", handler)

    fw = FileActivityWatcher()
    fw._event_bus = bus

    await fw._observe(Observation(
        watcher_name="file_activity",
        kind="file_change",
        summary="something changed",
    ))

    assert len(received) == 1
    assert received[0].data["watcher"] == "file_activity"


def test_recent_observations():
    fw = FileActivityWatcher()
    for i in range(5):
        fw._observations.append(Observation(
            watcher_name="test", kind="test", summary=f"obs {i}",
        ))
    recent = fw.recent_observations(limit=3)
    assert len(recent) == 3
    assert "obs 4" in recent[0].summary  # most recent first


# ── AmbientManager tests ────────────────────────────────────────

def test_ambient_manager_register():
    mgr = AmbientManager()
    mgr.register(GitWatcher())
    mgr.register(FileActivityWatcher())
    assert len(mgr.list_watchers()) == 2


def test_ambient_manager_list_watchers():
    mgr = AmbientManager()
    mgr.register(GitWatcher())
    watchers = mgr.list_watchers()
    assert watchers[0]["name"] == "git_watcher"
    assert watchers[0]["running"] is False
    assert watchers[0]["observations"] == 0


@pytest.mark.asyncio
async def test_ambient_manager_start_stop_all():
    mgr = AmbientManager()
    mgr.register(GitWatcher(check_interval=3600))
    mgr.register(DailyBriefingWatcher(interval_hours=999))

    tm = TriggerManager()
    started = await mgr.start_all(tm)
    assert started == 2

    for w in mgr.list_watchers():
        assert w["running"] is True

    stopped = await mgr.stop_all()
    assert stopped == 2

    for w in mgr.list_watchers():
        assert w["running"] is False


@pytest.mark.asyncio
async def test_ambient_manager_start_stop_one():
    mgr = AmbientManager()
    mgr.register(GitWatcher(check_interval=3600))
    mgr.register(FileActivityWatcher(check_interval=3600))

    ok = await mgr.start_one("git_watcher")
    assert ok
    assert mgr._watchers["git_watcher"].is_running
    assert not mgr._watchers["file_activity"].is_running

    ok = await mgr.stop_one("git_watcher")
    assert ok
    assert not mgr._watchers["git_watcher"].is_running


@pytest.mark.asyncio
async def test_ambient_manager_start_one_not_found():
    mgr = AmbientManager()
    assert not await mgr.start_one("nonexistent")


@pytest.mark.asyncio
async def test_ambient_manager_observations():
    mgr = AmbientManager()
    gw = GitWatcher()
    fw = FileActivityWatcher()
    mgr.register(gw)
    mgr.register(fw)

    gw._observations.append(Observation(watcher_name="git", kind="commit", summary="git obs"))
    fw._observations.append(Observation(watcher_name="file", kind="change", summary="file obs"))

    obs = mgr.observations(limit=10)
    assert len(obs) == 2
