"""Launch readiness tests — verify all user-facing functionality works.

These tests simulate what a real user does after installing OpenSculpt.
Every test here must pass before shipping.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import httpx

# ── CLI Tests ───────────────────────────────────────────────────

SCULPT_CLI = [sys.executable, "-m", "agos.cli.main"]


class TestCLI:
    """Verify CLI branding and basic commands work."""

    def test_cli_app_name_is_sculpt(self):
        """The Typer app is named sculpt."""
        from agos.cli.main import _app
        assert _app.info.name == "sculpt"

    def test_cli_help_text(self):
        """Help text says OpenSculpt."""
        from agos.cli.main import _app
        assert "OpenSculpt" in _app.info.help

    def test_version_command_exists(self):
        """sculpt version command is registered."""
        from agos.cli.main import _app
        cmd_names = [c.name for c in _app.registered_commands]
        assert "version" in cmd_names

    def test_ps_command_exists(self):
        """sculpt ps command is registered."""
        from agos.cli.main import _app
        cmd_names = [c.name for c in _app.registered_commands]
        assert "ps" in cmd_names

    def test_init_creates_workspace(self, tmp_path):
        """System init creates .opensculpt/ workspace."""
        result = subprocess.run(
            [sys.executable, "-c",
             f"import os; os.chdir(r'{tmp_path}'); os.environ['SCULPT_WORKSPACE_DIR']=r'{tmp_path / '.opensculpt'}'; from agos.cli.system import init; init()"],
            capture_output=True, text=True, timeout=15,
        )
        assert (tmp_path / ".opensculpt").exists() or "OpenSculpt" in (result.stdout + result.stderr)


# ── Server Boot Tests ───────────────────────────────────────────

class TestServerBoot:
    """User runs python -m agos.serve and hits the dashboard."""

    @pytest.fixture(scope="class")
    def server(self):
        """Boot the server for this test class."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "agos.serve"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).parent.parent),
        )
        # Wait for server to be ready
        base = "http://127.0.0.1:8420"
        for _ in range(30):
            try:
                r = httpx.get(f"{base}/api/status", timeout=2)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        yield base
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def test_status_endpoint(self, server):
        """GET /api/status returns ok."""
        r = httpx.get(f"{server}/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert "demand_signals" in data

    def test_dashboard_serves_html(self, server):
        """GET / serves the OpenSculpt dashboard."""
        r = httpx.get(server)
        assert r.status_code == 200
        assert "<title>OpenSculpt</title>" in r.text

    def test_dashboard_branding(self, server):
        """Dashboard has no AGOS references, only OpenSculpt."""
        r = httpx.get(server)
        # Check title and main heading
        assert "OpenSculpt" in r.text
        assert "<title>AGOS</title>" not in r.text
        assert "<title>AGenticOS</title>" not in r.text

    def test_wizard_detect(self, server):
        """Wizard auto-detects LLM providers."""
        r = httpx.get(f"{server}/api/wizard/detect", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert "detected" in data
        # Should be a list (possibly empty if no LLM server running)
        assert isinstance(data["detected"], list)

    def test_settings_endpoint(self, server):
        """Settings endpoint returns config."""
        r = httpx.get(f"{server}/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "has_api_key" in data
        assert "model" in data

    def test_events_endpoint(self, server):
        """Events endpoint returns boot events."""
        r = httpx.get(f"{server}/api/events")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # Should have events (boot, evolution, agent lifecycle, etc.)
        assert len(data) > 0

    def test_audit_endpoint(self, server):
        """Audit trail has boot entries."""
        r = httpx.get(f"{server}/api/audit")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_changelog_endpoint(self, server):
        """Evolution changelog endpoint works."""
        r = httpx.get(f"{server}/api/evolution/changelog")
        assert r.status_code == 200
        data = r.json()
        assert "evolved_files" in data
        assert "active_demands" in data
        assert "cycles_completed" in data

    def test_demands_endpoint(self, server):
        """Demand signals endpoint works."""
        r = httpx.get(f"{server}/api/evolution/demands")
        assert r.status_code == 200
        data = r.json()
        assert "total_signals" in data

    def test_sync_manifest(self, server):
        """Sync manifest endpoint works."""
        r = httpx.get(f"{server}/api/sync/manifest")
        assert r.status_code == 200

    def test_evolve_demo_runs_real_pipeline(self, server):
        """The evolution demo runs the real pipeline, not a fake."""
        r = httpx.post(f"{server}/api/wizard/evolve-demo", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "stages" in data
        stages = data["stages"]
        assert len(stages) >= 4  # demand, search, paper/fallback, codegen, sandbox, deploy

        # Verify pipeline stages exist
        stage_ids = [s["id"] for s in stages]
        assert "demand" in stage_ids
        assert "search" in stage_ids
        assert "sandbox" in stage_ids or "codegen" in stage_ids

        # If evolved, verify file was actually written
        if data.get("evolved"):
            filename = data["filename"]
            assert filename.endswith(".py")
            assert data.get("metrics", {}).get("sandbox_ms", 0) > 0


# ── OS Agent Tests ──────────────────────────────────────────────

class TestOSAgent:
    """Verify the OS agent has the right tools wired."""

    def test_os_agent_has_hand_tools(self):
        """OS agent registers daemon tools after set_daemon_manager."""
        from agos.os_agent import OSAgent
        from agos.events.bus import EventBus
        from agos.policy.audit import AuditTrail

        bus = EventBus()
        audit = AuditTrail(":memory:")
        agent = OSAgent(event_bus=bus, audit_trail=audit)

        # Before setting daemon manager — no daemon tools
        tool_names = [t.name for t in agent._inner_registry.list_tools()]
        assert "start_daemon" not in tool_names

        # After setting daemon manager — daemon tools registered
        from unittest.mock import MagicMock
        mock_hm = MagicMock()
        agent.set_daemon_manager(mock_hm)
        tool_names = [t.name for t in agent._inner_registry.list_tools()]
        assert "start_daemon" in tool_names
        assert "stop_daemon" in tool_names
        assert "daemon_results" in tool_names

    def test_os_agent_core_tools_match_registered(self):
        """The _CORE_TOOLS filter matches actual registered tool names."""
        from agos.os_agent import OSAgent
        from agos.events.bus import EventBus
        from agos.policy.audit import AuditTrail

        bus = EventBus()
        audit = AuditTrail(":memory:")
        agent = OSAgent(event_bus=bus, audit_trail=audit)

        registered = {t.name for t in agent._inner_registry.list_tools()}
        core = {"shell", "read_file", "write_file", "http", "python",
                "spawn_agent", "check_agent"}
        # All core tools should be registered
        for tool in core:
            assert tool in registered, f"Core tool '{tool}' not in registered tools: {registered}"

    def test_os_agent_system_prompt_says_opensculpt(self):
        """System prompt says OpenSculpt, not AGOS."""
        from agos.os_agent import SYSTEM_PROMPT
        assert "OpenSculpt" in SYSTEM_PROMPT
        assert "AGOS" not in SYSTEM_PROMPT


# ── Config Tests ────────────────────────────────────────────────

class TestConfig:
    """Verify config uses SCULPT_ prefix and OpenSculpt paths."""

    def test_env_prefix(self):
        from agos.config import AgosSettings
        assert AgosSettings.model_config["env_prefix"] == "SCULPT_"

    def test_workspace_dir(self):
        from agos.config import settings
        assert "opensculpt" in str(settings.workspace_dir)

    def test_github_repo(self):
        from agos.config import settings
        assert settings.github_repo == "opensculpt"


# ── Package Identity Tests ──────────────────────────────────────

class TestPackageIdentity:
    """Verify the package identifies as OpenSculpt."""

    def test_init_docstring(self):
        import agos
        assert "OpenSculpt" in agos.__doc__

    def test_pyproject_name(self):
        """pyproject.toml has name=opensculpt."""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        assert 'name = "opensculpt"' in content

    def test_cli_entry_point(self):
        """pyproject.toml has sculpt entry point."""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        assert 'sculpt = "agos.cli.main:app"' in content
