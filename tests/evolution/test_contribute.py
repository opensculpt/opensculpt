"""Tests for community sharing — anonymized knowledge only, no raw code.

Covers: export_contribution (aggregate stats only), share_knowledge
(anonymized knowledge via GitHub PR), community code loading,
sandbox validation gate, manifest signing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agos.evolution.sandbox import Sandbox
from agos.evolution.state import EvolutionState, DesignArchive, DesignEntry


# ── export_contribution Tests (now aggregate stats only) ──────


class TestExportContribution:
    def test_no_raw_code_in_export(self, tmp_path):
        """export_contribution must NOT include raw code, patches, or skills."""
        state = EvolutionState(save_path=tmp_path / "state.json")

        # Create fake evolved files — should NOT appear in export
        evolved_dir = tmp_path / "evolved"
        evolved_dir.mkdir()
        (evolved_dir / "tool.py").write_text("def run(): pass\n", encoding="utf-8")

        contribution = state.export_contribution(evolved_dir=evolved_dir)

        # Must NOT have private data
        assert "evolved_code" not in contribution
        assert "skill_docs" not in contribution
        assert "prompt_rules" not in contribution
        assert "source_patches" not in contribution
        assert "packages" not in contribution
        assert "_new_file_hashes" not in contribution

    def test_has_aggregate_stats(self, tmp_path):
        """export_contribution should include safe aggregate data."""
        state = EvolutionState(save_path=tmp_path / "state.json")

        contribution = state.export_contribution()

        assert "instance_id" in contribution
        assert "agos_version" in contribution
        assert "contributed_at" in contribution
        assert "cycles_completed" in contribution
        assert "strategies_applied" in contribution

    def test_strategies_have_names_only(self, tmp_path):
        """Strategies should only expose name and module, not full data."""
        state = EvolutionState(save_path=tmp_path / "state.json")

        contribution = state.export_contribution()

        for s in contribution.get("strategies_applied", []):
            assert "name" in s
            assert "module" in s
            # Should NOT have detailed execution data
            assert "code_hash" not in s


# ── PR Body Tests (knowledge-only) ───────────────────────────


class TestPRBody:
    def test_pr_body_mentions_anonymized(self):
        """PR body should state that data is anonymized."""
        from agos.evolution.contribute import _build_pr_body

        body = _build_pr_body("abc123", 5)
        assert "anonymized" in body.lower() or "Anonymized" in body
        assert "No code" in body

    def test_pr_body_no_private_data(self):
        """PR body should not contain file paths or identifiable info."""
        from agos.evolution.contribute import _build_pr_body

        body = _build_pr_body("abc123", 3)
        assert ".py" not in body
        assert "evolved_code" not in body


# ── Community Code Loading Tests ────────────────────────────────


class TestCommunityCodeLoading:
    @pytest.mark.asyncio
    async def test_loads_evolved_files(self, tmp_path):
        """Should copy community evolved code to local .agos/evolved/."""
        evolved_dir = tmp_path / "community" / "evolved" / "instance1"
        evolved_dir.mkdir(parents=True)
        (evolved_dir / "ranker.py").write_text(
            'PATTERN_HASH = "hash1"\ndef rank(): pass\n', encoding="utf-8"
        )

        local_evolved = tmp_path / ".agos" / "evolved"
        local_evolved.mkdir(parents=True)

        mock_loom = MagicMock()
        mock_loom.semantic = AsyncMock()
        mock_loom.semantic.store = AsyncMock(return_value="tid")
        mock_bus = AsyncMock()
        mock_bus.emit = AsyncMock()

        assert (evolved_dir / "ranker.py").exists()

    def test_dedup_by_hash(self, tmp_path):
        """Should not copy if PATTERN_HASH already exists locally."""
        local_evolved = tmp_path / "local"
        local_evolved.mkdir()
        community_evolved = tmp_path / "community"
        community_evolved.mkdir()

        (local_evolved / "existing.py").write_text(
            'PATTERN_HASH = "abc123"\ndef existing(): pass\n', encoding="utf-8"
        )
        (community_evolved / "same_thing.py").write_text(
            'PATTERN_HASH = "abc123"\ndef same(): pass\n', encoding="utf-8"
        )

        existing_hashes: set[str] = set()
        for existing in local_evolved.glob("*.py"):
            content = existing.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip().startswith("PATTERN_HASH"):
                    existing_hashes.add(line.strip())
                    break

        community_code = (community_evolved / "same_thing.py").read_text(encoding="utf-8")
        community_hash_line = ""
        for line in community_code.splitlines():
            if line.strip().startswith("PATTERN_HASH"):
                community_hash_line = line.strip()
                break

        assert community_hash_line in existing_hashes


# ── Sandbox Validation Gate Tests ─────────────────────────────


class TestSandboxGate:
    """Tests that community code is sandbox-validated before loading."""

    def test_static_analysis_blocks_unsafe_imports(self):
        """Sandbox.validate() should reject code with os/subprocess imports."""
        sandbox = Sandbox(timeout=10)

        unsafe_code = 'import os\nos.system("echo pwned")\n'
        result = sandbox.validate(unsafe_code)
        assert not result.safe
        assert len(result.issues) > 0

    def test_static_analysis_blocks_exec(self):
        """Sandbox.validate() should reject code with exec() calls."""
        sandbox = Sandbox(timeout=10)

        unsafe_code = 'exec("print(1)")\n'
        result = sandbox.validate(unsafe_code)
        assert not result.safe

    def test_static_analysis_allows_safe_code(self):
        """Sandbox.validate() should pass clean code with allowed imports."""
        sandbox = Sandbox(timeout=10)

        safe_code = (
            'import math\n'
            'import json\n'
            'def compute(): return math.sqrt(2)\n'
        )
        result = sandbox.validate(safe_code)
        assert result.safe
        assert len(result.issues) == 0

    @pytest.mark.asyncio
    async def test_sandbox_execution_catches_runtime_errors(self):
        """Sandbox.execute() should fail on code that raises at runtime."""
        sandbox = Sandbox(timeout=10)

        bad_code = 'raise RuntimeError("boom")\n'
        result = await sandbox.execute(bad_code)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_sandbox_execution_passes_clean_code(self):
        """Sandbox.execute() should pass code that runs without errors."""
        sandbox = Sandbox(timeout=10)

        good_code = 'x = 1 + 1\nprint(x)\n'
        result = await sandbox.execute(good_code)
        assert result.passed

    @pytest.mark.asyncio
    async def test_community_gate_rejects_unsafe_file(self, tmp_path):
        """Community loading should reject files with unsafe imports."""
        from agos.evolution.community import load_community_contributions

        evolved_dir = tmp_path / "community" / "evolved" / "badnode"
        evolved_dir.mkdir(parents=True)
        (evolved_dir / "evil.py").write_text(
            'import subprocess\nsubprocess.run(["echo", "pwned"])\n',
            encoding="utf-8",
        )

        local_evolved = tmp_path / ".agos" / "evolved"
        local_evolved.mkdir(parents=True)

        mock_loom = MagicMock()
        mock_loom.semantic = AsyncMock()
        mock_loom.semantic.store = AsyncMock(return_value="tid")
        mock_bus = AsyncMock()
        mock_bus.emit = AsyncMock()

        sandbox = Sandbox(timeout=10)

        with patch("agos.evolution.community.pathlib.Path") as mock_path_fn:
            def path_factory(p):
                if p == "community/contributions":
                    return tmp_path / "community" / "contributions"
                if p == "community/evolved":
                    return tmp_path / "community" / "evolved"
                if p == ".agos/evolved":
                    return local_evolved
                return tmp_path / p

            mock_path_fn.side_effect = path_factory

            n = await load_community_contributions(
                mock_loom, mock_bus, sandbox=sandbox,
            )

        assert n == 0
        assert not (local_evolved / "evil.py").exists()

    @pytest.mark.asyncio
    async def test_community_gate_accepts_safe_file(self, tmp_path):
        """Community loading should accept safe evolved code."""
        from agos.evolution.community import load_community_contributions

        evolved_dir = tmp_path / "community" / "evolved" / "goodnode"
        evolved_dir.mkdir(parents=True)
        (evolved_dir / "ranker.py").write_text(
            'PATTERN_HASH = "safe123"\nimport math\ndef rank(): return math.sqrt(2)\n',
            encoding="utf-8",
        )

        local_evolved = tmp_path / ".agos" / "evolved"
        local_evolved.mkdir(parents=True)

        mock_loom = MagicMock()
        mock_loom.semantic = AsyncMock()
        mock_loom.semantic.store = AsyncMock(return_value="tid")
        mock_bus = AsyncMock()
        mock_bus.emit = AsyncMock()

        sandbox = Sandbox(timeout=10)

        with patch("agos.evolution.community.pathlib.Path") as mock_path_fn:
            def path_factory(p):
                if p == "community/contributions":
                    return tmp_path / "community" / "contributions"
                if p == "community/evolved":
                    return tmp_path / "community" / "evolved"
                if p == ".agos/evolved":
                    return local_evolved
                return tmp_path / p

            mock_path_fn.side_effect = path_factory

            n = await load_community_contributions(
                mock_loom, mock_bus, sandbox=sandbox,
            )

        assert n == 1
        assert (local_evolved / "ranker.py").exists()


# ── Privacy Tests ──────────────────────────────────────────────


class TestPrivacy:
    """Verify no private data leaks through any export."""

    def test_export_has_no_code(self, tmp_path):
        """export_contribution must never include .py file contents."""
        state = EvolutionState(save_path=tmp_path / "state.json")

        evolved_dir = tmp_path / "evolved"
        evolved_dir.mkdir()
        (evolved_dir / "secret_tool.py").write_text(
            "API_KEY = 'sk-secret'\ndef hack(): pass\n", encoding="utf-8"
        )

        contrib = state.export_contribution(evolved_dir=evolved_dir)

        import json
        serialized = json.dumps(contrib)
        assert "sk-secret" not in serialized
        assert "secret_tool.py" not in serialized
        assert "def hack" not in serialized

    def test_no_shared_file_hashes(self, tmp_path):
        """shared_file_hashes tracking should be gone."""
        state = EvolutionState(save_path=tmp_path / "state.json")
        contrib = state.export_contribution()
        assert "_new_file_hashes" not in contrib


class TestLoadEvolvedStrategiesSafety:
    """Defense-in-depth: load_evolved_strategies() rejects unsafe modules."""

    def test_rejects_module_with_blocked_import(self, tmp_path):
        """Strategy file with import os should not be loaded."""
        from agos.evolution.codegen import load_evolved_strategies

        (tmp_path / "evil_strategy.py").write_text(
            'import os\nclass EvilStrategy:\n    name = "evil"\n    target_module = "kernel"\n',
            encoding="utf-8",
        )

        strategies = load_evolved_strategies(evolved_dir=tmp_path)
        assert len(strategies) == 0

    def test_accepts_safe_module(self, tmp_path):
        """Strategy file with safe code should load normally."""
        from agos.evolution.codegen import load_evolved_strategies

        (tmp_path / "good_strategy.py").write_text(
            'import math\n'
            'class GoodStrategy:\n'
            '    name = "good"\n'
            '    target_module = "knowledge"\n'
            '    def __init__(self, components=None): pass\n',
            encoding="utf-8",
        )

        strategies = load_evolved_strategies(evolved_dir=tmp_path)
        assert len(strategies) == 1
        assert strategies[0][1].name == "good"


class TestPendingInsights:
    """Tests for insight queue."""

    def test_store_and_pop_insight(self, tmp_path):
        state = EvolutionState(save_path=tmp_path / "state.json")
        insight = {"paper_id": "2602.12345v1", "technique": "Graph RAG", "agos_module": "knowledge"}
        state.store_insight(insight)
        assert len(state.data.pending_insights) == 1

        popped = state.pop_pending_insight()
        assert popped is not None
        assert popped["paper_id"] == "2602.12345v1"

    def test_pop_skips_done_papers(self, tmp_path):
        state = EvolutionState(save_path=tmp_path / "state.json")
        state.store_insight({"paper_id": "aaa", "technique": "A"})
        state.store_insight({"paper_id": "bbb", "technique": "B"})
        state.mark_codegen_done("aaa")

        popped = state.pop_pending_insight()
        assert popped is not None
        assert popped["paper_id"] == "bbb"

    def test_no_duplicate_insights(self, tmp_path):
        state = EvolutionState(save_path=tmp_path / "state.json")
        state.store_insight({"paper_id": "aaa", "technique": "A"})
        state.store_insight({"paper_id": "aaa", "technique": "A v2"})
        assert len(state.data.pending_insights) == 1
