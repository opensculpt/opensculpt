"""Community contribution loading — import evolved code from other instances.

Loads community contribution JSON files and evolved code from the
community/ directory, applying sandbox validation before use.

Security gates (in order):
  1. Origin verification — git remote must be opensculpt/opensculpt
  2. Sandbox validation — static analysis + subprocess isolation
"""
from __future__ import annotations

import json as _json
import logging
import pathlib
import subprocess as _subprocess

from agos.events.bus import EventBus
from agos.evolution.sandbox import Sandbox
from agos.knowledge.base import Thread

_logger = logging.getLogger(__name__)


def _verify_community_origin() -> bool:
    """Check that community/ content comes from the official opensculpt repo.

    Verifies git remote origin contains opensculpt/opensculpt.
    Lenient for non-git installs (pip install) — git integrity via
    commit SHAs is sufficient, no custom signing needed.
    """
    try:
        result = _subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip().lower()
            if "opensculpt/opensculpt" in url:
                return True
            _logger.warning(
                "Community origin mismatch: expected opensculpt/opensculpt, got %s",
                result.stdout.strip(),
            )
            return False
    except Exception:
        pass
    # Not in a git repo or git unavailable — allow (pip installs, dev setups)
    _logger.debug("Cannot verify community origin (no git remote) — allowing")
    return True


async def load_community_contributions(
    loom, bus: EventBus, sandbox: Sandbox | None = None,
    evo_memory=None,
) -> int:
    """Load community contribution files and apply unlearned strategies.

    Security: verifies origin before loading, sandbox-validates all code.
    All users get community code equally (maintainer-curated).
    """
    from agos.config import settings as _settings
    from datetime import datetime, timedelta

    # ── Security Gate 1: Origin verification ──
    if not _verify_community_origin():
        _logger.error("Community origin verification FAILED — skipping community loading")
        await bus.emit("evolution.community_origin_failed", {}, source="kernel")
        return 0

    contrib_dir = pathlib.Path("community/contributions")
    evolved_dir = pathlib.Path("community/evolved")

    loaded = 0

    # ── Load strategy metadata from contribution JSONs ──
    if contrib_dir.exists():
        for f in sorted(contrib_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))

                for s in data.get("strategies_applied", []):
                    name = s.get("name", "")
                    module = s.get("module", "")
                    if name and module:
                        await loom.semantic.store(Thread(
                            content=f"Community strategy: {name} for {module}",
                            kind="community_strategy",
                            tags=["community", "evolution", module],
                            metadata={"source_instance": data.get("instance_id", ""), "strategy": name},
                            source=f"community:{f.stem}",
                        ))
                        loaded += 1

                # HyperAgents: merge cross-node evolution memory
                if evo_memory is not None and data.get("evolution_memory"):
                    merged = evo_memory.merge_remote(
                        data["evolution_memory"],
                        source_instance=data.get("instance_id", ""),
                    )
                    if merged > 0:
                        await bus.emit("evolution.community_memory_merged", {
                            "source_instance": data.get("instance_id", "")[:8],
                            "insights_merged": merged,
                        }, source="kernel")
            except Exception as e:
                _logger.warning("Failed to load community contribution %s: %s", f, e)

    # ── Load evolved code files from community/evolved/*/ ──
    code_loaded = 0
    code_rejected = 0
    if evolved_dir.exists():
        local_evolved = pathlib.Path(".agos/evolved")
        local_evolved.mkdir(parents=True, exist_ok=True)

        # Create sandbox for community code validation
        _sandbox = sandbox or Sandbox(timeout=10)

        # Collect existing code hashes to avoid duplicates
        existing_hashes: set[str] = set()
        for existing in local_evolved.glob("*.py"):
            try:
                content = existing.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.strip().startswith("PATTERN_HASH"):
                        existing_hashes.add(line.strip())
                        break
            except Exception:
                pass

        for instance_dir in sorted(evolved_dir.iterdir()):
            if not instance_dir.is_dir():
                continue
            for py_file in sorted(instance_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                try:
                    code = py_file.read_text(encoding="utf-8")

                    # Check dedup by PATTERN_HASH
                    code_hash_line = ""
                    for line in code.splitlines():
                        if line.strip().startswith("PATTERN_HASH"):
                            code_hash_line = line.strip()
                            break

                    if code_hash_line and code_hash_line in existing_hashes:
                        continue  # Already have this pattern

                    # ── Sandbox gate: validate before loading ──
                    # 1. Static analysis — blocks dangerous imports/calls
                    validation = _sandbox.validate(code)
                    if not validation.safe:
                        _logger.warning(
                            "Community code %s failed static analysis: %s",
                            py_file, validation.issues,
                        )
                        await bus.emit("evolution.community_code_rejected", {
                            "file": py_file.name,
                            "instance": instance_dir.name,
                            "reason": "static_analysis",
                            "issues": validation.issues,
                        }, source="kernel")
                        code_rejected += 1
                        continue

                    # 2. Subprocess execution — runs in isolated process
                    exec_result = await _sandbox.execute(code)
                    if not exec_result.passed:
                        _logger.warning(
                            "Community code %s failed sandbox execution: %s",
                            py_file, exec_result.error[:200],
                        )
                        await bus.emit("evolution.community_code_rejected", {
                            "file": py_file.name,
                            "instance": instance_dir.name,
                            "reason": "sandbox_execution",
                            "error": exec_result.error[:200],
                        }, source="kernel")
                        code_rejected += 1
                        continue

                    # Only copy after passing both validation gates
                    target = local_evolved / py_file.name
                    if not target.exists():
                        target.write_text(code, encoding="utf-8")
                        if code_hash_line:
                            existing_hashes.add(code_hash_line)
                        code_loaded += 1
                except Exception as e:
                    _logger.warning("Failed to load community evolved code %s: %s", py_file, e)

    if code_loaded > 0:
        await bus.emit("evolution.community_code_loaded", {
            "files": code_loaded,
            "rejected": code_rejected,
        }, source="kernel")

    return loaded + code_loaded
