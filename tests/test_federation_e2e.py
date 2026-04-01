"""End-to-end tests for federated meta-evolution feature.

Tests the REAL code path: scoring → sync → curator → seed → contribute.
No mocks except for demand collector (which needs live EventBus to populate).
"""

import json
import shutil
import time
from pathlib import Path

import pytest

# ── Scoring Engine Tests ─────────────────────────────────────────


class TestArtifactScore:
    def test_composite_weights(self):
        from agos.evolution.scoring import ArtifactScore
        s = ArtifactScore(efficacy=1.0, adoption=1.0, stability=1.0)
        assert s.composite == pytest.approx(1.0)

    def test_composite_zero(self):
        from agos.evolution.scoring import ArtifactScore
        s = ArtifactScore(efficacy=0.0, adoption=0.0, stability=0.0)
        assert s.composite == 0.0

    def test_composite_weights_correct(self):
        from agos.evolution.scoring import ArtifactScore
        # Only efficacy = 1.0 → composite = 0.5
        s = ArtifactScore(efficacy=1.0, adoption=0.0, stability=0.0)
        assert s.composite == pytest.approx(0.5)
        # Only adoption = 1.0 → composite = 0.3
        s = ArtifactScore(efficacy=0.0, adoption=1.0, stability=0.0)
        assert s.composite == pytest.approx(0.3)
        # Only stability = 1.0 → composite = 0.2
        s = ArtifactScore(efficacy=0.0, adoption=0.0, stability=1.0)
        assert s.composite == pytest.approx(0.2)

    def test_roundtrip(self):
        from agos.evolution.scoring import ArtifactScore
        s = ArtifactScore(
            artifact_id="art1", efficacy=0.8, adoption=0.5,
            stability=0.3, demand_key="dk1", deployed_at=1000.0, regressed=True,
        )
        d = s.to_dict()
        s2 = ArtifactScore.from_dict(d)
        assert s2.artifact_id == "art1"
        assert s2.efficacy == pytest.approx(0.8)
        assert s2.adoption == pytest.approx(0.5)
        assert s2.stability == pytest.approx(0.3)
        assert s2.demand_key == "dk1"
        assert s2.deployed_at == 1000.0
        assert s2.regressed is True
        assert s2.composite == pytest.approx(s.composite, abs=0.01)


class TestLocalScorer:
    def _make_demand(self, key, status, last_attempt=0):
        class D:
            pass
        d = D()
        d.key = key
        d.status = status
        d.last_attempt_at = last_attempt
        return d

    def _make_collector(self, demands):
        class C:
            def __init__(self, ds):
                self._ds = ds
            def top_demands(self, limit=100, include_all=True):
                return self._ds
        return C(demands)

    def test_register_and_update_resolved(self):
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("t1", "missing_tool:docker", deployed_at=time.time() - 86400)
        collector = self._make_collector([
            self._make_demand("missing_tool:docker", "resolved"),
        ])
        scorer.update(collector)
        assert scorer.scores["t1"].efficacy == 1.0
        assert scorer.scores["t1"].stability > 0.0  # 1 day deployed

    def test_update_active_demand(self):
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("t1", "error:curl")
        collector = self._make_collector([
            self._make_demand("error:curl", "active"),
        ])
        scorer.update(collector)
        assert scorer.scores["t1"].efficacy == 0.1

    def test_update_escalated_after_deploy(self):
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("t1", "error:x", deployed_at=100.0)
        collector = self._make_collector([
            self._make_demand("error:x", "escalated", last_attempt=200.0),
        ])
        scorer.update(collector)
        assert scorer.scores["t1"].efficacy == 0.0
        assert scorer.scores["t1"].regressed is True
        assert scorer.scores["t1"].stability == 0.0

    def test_demand_cleared_entirely(self):
        """If demand was cleared from collector, that's strong resolution signal."""
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("t1", "missing_tool:x")
        # Empty collector — demand is gone
        collector = self._make_collector([])
        scorer.update(collector)
        assert scorer.scores["t1"].efficacy == 1.0

    def test_get_scores(self):
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("a", "d1")
        scorer.register_artifact("b", "d2")
        scores = scorer.get_scores()
        assert "a" in scores
        assert "b" in scores
        assert isinstance(scores["a"], float)

    def test_serialization_roundtrip(self):
        from agos.evolution.scoring import LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("t1", "dk1", deployed_at=1000.0)
        scorer.register_artifact("t2", "dk2", deployed_at=2000.0)
        d = scorer.to_dict()
        scorer2 = LocalScorer.from_dict(d)
        assert len(scorer2.scores) == 2
        assert scorer2.scores["t1"].demand_key == "dk1"
        assert scorer2.scores["t2"].deployed_at == 2000.0


class TestFleetScorer:
    def test_score_with_adoption(self):
        from agos.evolution.scoring import FleetScorer, ArtifactScore
        fleet = FleetScorer()
        local = ArtifactScore(artifact_id="t1", efficacy=0.8, stability=0.5)
        peers = [
            {"tools_deployed": ["t1"], "artifact_scores": {"t1": {"efficacy": 1.0}}},
            {"tools_deployed": ["t1"], "artifact_scores": {}},
            {"tools_deployed": [], "artifact_scores": {}},
        ]
        result = fleet.score_artifact("t1", local, peers)
        assert result.adoption == pytest.approx(2 / 3, abs=0.01)
        # Efficacy should be average of local (0.8) + peer (1.0)
        assert result.efficacy == pytest.approx(0.9)
        assert result.composite > local.composite  # fleet should boost score

    def test_score_no_peers(self):
        from agos.evolution.scoring import FleetScorer, ArtifactScore
        fleet = FleetScorer()
        local = ArtifactScore(artifact_id="t1", efficacy=0.6, stability=0.3)
        result = fleet.score_artifact("t1", local, [])
        assert result.adoption == 0.0
        assert result.efficacy == pytest.approx(0.6)

    def test_score_all(self):
        from agos.evolution.scoring import FleetScorer, LocalScorer
        scorer = LocalScorer()
        scorer.register_artifact("a", "d1")
        scorer.register_artifact("b", "d2")
        scorer.scores["a"].efficacy = 0.9
        scorer.scores["b"].efficacy = 0.3
        fleet = FleetScorer()
        peers = [{"tools_deployed": ["a"], "artifact_scores": {}}]
        results = fleet.score_all(scorer, peers)
        assert "a" in results
        assert "b" in results
        assert results["a"].adoption > results["b"].adoption


class TestUpdateArchiveScores:
    def test_updates_fitness(self):
        from agos.evolution.scoring import LocalScorer, update_archive_scores
        from agos.evolution.state import DesignArchive, DesignEntry
        archive = DesignArchive()
        e1 = DesignEntry(strategy_name="s1", module="m1")
        e2 = DesignEntry(strategy_name="s2", module="m2")
        archive.add(e1)
        archive.add(e2)

        scorer = LocalScorer()
        scorer.register_artifact(e1.id, "d1")
        scorer.scores[e1.id].efficacy = 0.9
        scorer.scores[e1.id].stability = 0.5

        updated = update_archive_scores(archive, scorer)
        assert updated == 1
        assert e1.current_fitness > 0


# ── SyncManifest Tests ───────────────────────────────────────────


class TestSyncManifestEfficacy:
    def test_has_efficacy_fields(self):
        from agos.evolution.sync import SyncManifest
        m = SyncManifest(instance_id="node1")
        d = m.to_dict()
        assert "resolved_demands" in d
        assert "tools_deployed" in d
        assert "artifact_scores" in d

    def test_roundtrip_with_efficacy(self):
        from agos.evolution.sync import SyncManifest
        m = SyncManifest(instance_id="node1", cycles_completed=50)
        m.resolved_demands = ["d1", "d2", "d3"]
        m.tools_deployed = ["tool_a.py", "tool_b.py"]
        m.artifact_scores = {
            "art1": {"efficacy": 0.8, "adoption": 0.5, "composite": 0.65},
            "art2": {"efficacy": 0.3, "composite": 0.2},
        }
        d = m.to_dict()
        m2 = SyncManifest.from_dict(d)
        assert m2.resolved_demands == ["d1", "d2", "d3"]
        assert m2.tools_deployed == ["tool_a.py", "tool_b.py"]
        assert m2.artifact_scores["art1"]["efficacy"] == 0.8
        assert len(m2.artifact_scores) == 2

    def test_empty_efficacy_roundtrip(self):
        from agos.evolution.sync import SyncManifest
        m = SyncManifest(instance_id="x")
        d = m.to_dict()
        m2 = SyncManifest.from_dict(d)
        assert m2.resolved_demands == []
        assert m2.tools_deployed == []
        assert m2.artifact_scores == {}


# ── DesignEntry Federation Fields ────────────────────────────────


class TestDesignEntryFederation:
    def test_new_fields_exist(self):
        from agos.evolution.state import DesignEntry
        e = DesignEntry(strategy_name="s", module="m")
        assert e.demand_key == ""
        assert e.artifact_score == 0.0
        assert e.adopted_by == []

    def test_new_fields_serialize(self):
        from agos.evolution.state import DesignEntry
        e = DesignEntry(
            strategy_name="s", module="m",
            demand_key="missing_tool:docker",
            artifact_score=0.75,
            adopted_by=["node1", "node2"],
        )
        d = e.model_dump()
        assert d["demand_key"] == "missing_tool:docker"
        assert d["artifact_score"] == 0.75
        assert d["adopted_by"] == ["node1", "node2"]

    def test_new_fields_deserialize(self):
        from agos.evolution.state import DesignEntry
        data = {
            "strategy_name": "s", "module": "m",
            "demand_key": "dk", "artifact_score": 0.5,
            "adopted_by": ["n1"],
        }
        e = DesignEntry(**data)
        assert e.demand_key == "dk"
        assert e.artifact_score == 0.5


# ── Curator Tests (against REAL fleet data) ──────────────────────


class TestFleetReport:
    def test_generates_report_from_real_fleet(self):
        fleet_dir = Path(".opensculpt-fleet")
        if not fleet_dir.exists():
            pytest.skip("No fleet data available")
        from agos.evolution.curator import generate_fleet_report
        report = generate_fleet_report(fleet_dir)
        assert "# OpenSculpt Fleet Report" in report
        assert "Per-Node Summary" in report
        assert "TOTAL" in report
        # Should have real node names
        assert "sales" in report or "devops" in report

    def test_report_with_empty_dir(self, tmp_path):
        from agos.evolution.curator import generate_fleet_report
        report = generate_fleet_report(tmp_path)
        assert "No nodes found" in report

    def test_report_with_nonexistent_dir(self):
        from agos.evolution.curator import generate_fleet_report
        report = generate_fleet_report(Path("/nonexistent/fleet"))
        assert "not found" in report


class TestNodeReport:
    def test_reads_real_node(self):
        node_dir = Path(".opensculpt-fleet/sales")
        if not node_dir.exists():
            pytest.skip("No fleet data")
        from agos.evolution.curator import NodeReport
        n = NodeReport(node_dir)
        assert n.name == "sales"
        assert n.cycles >= 0
        assert n.total_demands >= 0

    def test_handles_empty_node(self, tmp_path):
        from agos.evolution.curator import NodeReport
        node = tmp_path / "empty_node"
        node.mkdir()
        n = NodeReport(node)
        assert n.name == "empty_node"
        assert n.cycles == 0
        assert n.total_demands == 0


class TestCreateRelease:
    def test_creates_release_from_real_fleet(self):
        fleet_dir = Path(".opensculpt-fleet")
        if not fleet_dir.exists():
            pytest.skip("No fleet data")
        from agos.evolution.curator import create_release
        output = Path(".opensculpt/releases")
        # Count existing versions
        existing = list(output.glob("v*")) if output.exists() else []
        release_dir = create_release(fleet_dir, output_dir=output, min_score=0.0)
        assert release_dir.exists()
        assert (release_dir / "manifest.json").exists()
        assert (release_dir / "CHANGELOG.md").exists()
        # Manifest is valid JSON
        manifest = json.loads((release_dir / "manifest.json").read_text())
        assert "version" in manifest
        assert "tools_included" in manifest
        assert manifest["nodes_aggregated"] > 0

    def test_creates_release_empty_fleet(self, tmp_path):
        from agos.evolution.curator import create_release
        fleet = tmp_path / "fleet"
        fleet.mkdir()
        (fleet / "node1").mkdir()
        output = tmp_path / "releases"
        release_dir = create_release(fleet, output_dir=output)
        assert (release_dir / "manifest.json").exists()


class TestApplyRelease:
    def test_applies_tools_and_skills(self, tmp_path):
        from agos.evolution.curator import apply_release
        # Create a fake release
        release = tmp_path / "v1"
        release.mkdir()
        (release / "tools").mkdir()
        (release / "tools" / "my_tool.py").write_text("print('hello')")
        (release / "skills").mkdir()
        (release / "skills" / "my_skill.md").write_text("# Skill")
        (release / "constraints.md").write_text("# Constraints\n- rule one\n- rule two\n")
        (release / "resolutions.md").write_text("# Res\n## Fix crash\nDo X\n")
        (release / "manifest.json").write_text("{}")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        result = apply_release(release, workspace)
        assert result["tools"] == 1
        assert result["skills"] == 1
        assert result["constraints"] == 2
        assert result["resolutions"] == 1

    def test_no_duplicates_on_reapply(self, tmp_path):
        from agos.evolution.curator import apply_release
        release = tmp_path / "v1"
        release.mkdir()
        (release / "tools").mkdir()
        (release / "tools" / "t.py").write_text("x=1")
        (release / "manifest.json").write_text("{}")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        r1 = apply_release(release, workspace)
        r2 = apply_release(release, workspace)
        assert r1["tools"] == 1
        assert r2["tools"] == 0  # already exists, skip


class TestExportContribution:
    def test_exports_from_real_workspace(self):
        ws = Path(".opensculpt")
        if not ws.exists():
            pytest.skip("No workspace")
        from agos.evolution.curator import export_contribution
        contrib = export_contribution(ws)
        assert contrib.exists()
        # Should have at least demand_summary
        assert (contrib / "demand_summary.json").exists()
        summary = json.loads((contrib / "demand_summary.json").read_text())
        assert isinstance(summary, list)

    def test_anonymizes_content(self):
        from agos.evolution.curator import _anonymize_md
        text = "API key: sk-ant-api03-abc123def456 at 192.168.1.100 path /home/user/secret"
        result = _anonymize_md(text)
        assert "sk-ant" not in result
        assert "192.168.1.100" not in result
        assert "[REDACTED_KEY]" in result
        assert "[REDACTED_IP]" in result

    def test_anonymizes_github_tokens(self):
        from agos.evolution.curator import _anonymize_md
        text = "token: ghp_AAAAAAAAAAAABBBBBBBBBBBBCCCCCCCCCCCC1234"
        result = _anonymize_md(text)
        assert "ghp_" not in result
        assert "[REDACTED_TOKEN]" in result


# ── Config Tests ─────────────────────────────────────────────────


class TestConfig:
    def test_new_federation_settings(self):
        from agos.config import AgosSettings
        s = AgosSettings()
        assert hasattr(s, "seed_url")
        assert hasattr(s, "registry_url")
        assert hasattr(s, "fleet_dir")
        assert s.fleet_dir == ".opensculpt-fleet"


# ── Dashboard API Tests ──────────────────────────────────────────


class TestDashboardEndpoints:
    def test_scores_endpoint_exists(self):
        from agos.dashboard.app import dashboard_app
        routes = [r.path for r in dashboard_app.routes if hasattr(r, "path")]
        assert "/api/federation/scores" in routes

    def test_fleet_report_endpoint_exists(self):
        from agos.dashboard.app import dashboard_app
        routes = [r.path for r in dashboard_app.routes if hasattr(r, "path")]
        assert "/api/federation/fleet-report" in routes

    def test_sync_manifest_endpoint_exists(self):
        from agos.dashboard.app import dashboard_app
        routes = [r.path for r in dashboard_app.routes if hasattr(r, "path")]
        assert "/api/sync/manifest" in routes


# ── CLI Registration Tests ───────────────────────────────────────


class TestCLICommands:
    def test_seed_registered(self):
        from agos.cli.main import _SUBCOMMANDS
        assert "seed" in _SUBCOMMANDS

    def test_contribute_registered(self):
        from agos.cli.main import _SUBCOMMANDS
        assert "contribute" in _SUBCOMMANDS

    def test_curator_registered(self):
        from agos.cli.main import _SUBCOMMANDS
        assert "curator" in _SUBCOMMANDS


# ── Integration: Full Pipeline ───────────────────────────────────


class TestFullPipeline:
    """End-to-end: score → sync manifest → curator report → release → seed."""

    def test_score_to_release_to_seed(self, tmp_path):
        from agos.evolution.scoring import LocalScorer
        from agos.evolution.sync import SyncManifest
        from agos.evolution.curator import create_release, apply_release

        # Step 1: Score artifacts locally
        scorer = LocalScorer()
        scorer.register_artifact("tool_a", "missing:x", deployed_at=time.time() - 172800)
        scorer.scores["tool_a"].efficacy = 0.9
        scorer.scores["tool_a"].stability = 0.5
        assert scorer.get_scores()["tool_a"] > 0

        # Step 2: Build sync manifest with efficacy
        m = SyncManifest(instance_id="test-node", cycles_completed=100)
        m.resolved_demands = ["missing:x"]
        m.tools_deployed = ["tool_a"]
        m.artifact_scores = scorer.to_dict()
        d = m.to_dict()
        assert len(d["resolved_demands"]) == 1
        assert "tool_a" in d["artifact_scores"]

        # Step 3: Create a fake fleet node with real tool file
        fleet = tmp_path / "fleet"
        node = fleet / "test-node"
        node.mkdir(parents=True)
        (node / "evolution_state.json").write_text(json.dumps({
            "cycles_completed": 100, "strategies_applied": [],
        }))
        (node / "demand_signals.json").write_text(json.dumps({
            "signals": [{"key": "missing:x", "status": "resolved", "kind": "missing_tool"}],
        }))
        (node / "artifact_scores.json").write_text(json.dumps(
            scorer.to_dict()
        ))
        evolved = node / "evolved"
        evolved.mkdir()
        (evolved / "tool_a.py").write_text("async def handle(args):\n    return {'ok': True}\n")
        skills = node / "skills"
        skills.mkdir()
        (skills / "test_skill.md").write_text("# Test Skill\nHow to use tool_a\n")

        # Step 4: Curator creates release
        output = tmp_path / "releases"
        release_dir = create_release(fleet, output_dir=output, min_score=0.0)
        manifest = json.loads((release_dir / "manifest.json").read_text())
        assert manifest["tools_included"][0]["name"] == "tool_a"
        assert (release_dir / "tools" / "tool_a.py").exists()
        assert (release_dir / "skills" / "test_skill.md").exists()

        # Step 5: Seed into fresh workspace
        fresh_ws = tmp_path / "fresh"
        fresh_ws.mkdir()
        result = apply_release(release_dir, fresh_ws)
        assert result["tools"] == 1
        assert result["skills"] == 1
        # The tool file actually exists in the target
        target_tool = fresh_ws / "evolved" / "tool_a.py"
        assert target_tool.exists()
        assert "handle" in target_tool.read_text()
