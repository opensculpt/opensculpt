"""Community contribution loading — import evolved code from other instances.

Loads community contribution JSON files and evolved code from the
community/ directory, applying sandbox validation before use.
"""
from __future__ import annotations

import json as _json
import logging
import pathlib

from agos.events.bus import EventBus
from agos.evolution.sandbox import Sandbox
from agos.knowledge.base import Thread

_logger = logging.getLogger(__name__)


async def load_community_contributions(
    loom, bus: EventBus, sandbox: Sandbox | None = None,
    evo_memory=None,
) -> int:
    """Load community contribution files and apply unlearned strategies.

    Reciprocity model:
    - Contributors (GitHub token + auto-share on): load ALL community strategies
    - Non-contributors: load only contributions older than 7 days (weekly bundled)

    This incentivizes instances to share their learnings for real-time access.

    Also loads evolved code files from community/evolved/*/ into local
    .agos/evolved/ so they can be used without re-discovering the same papers.
    All community code is sandbox-validated before loading.
    """
    from agos.config import settings as _settings
    from datetime import datetime, timedelta

    contrib_dir = pathlib.Path("community/contributions")
    evolved_dir = pathlib.Path("community/evolved")

    is_contributor = bool(_settings.github_token and _settings.auto_share_every > 0)
    cutoff = datetime.utcnow() - timedelta(days=7)

    loaded = 0
    skipped = 0

    # ── Load strategy metadata from contribution JSONs ──
    if contrib_dir.exists():
        for f in sorted(contrib_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))

                # Reciprocity gate: non-contributors only get week-old contributions
                if not is_contributor:
                    contributed_at = data.get("contributed_at", "")
                    if contributed_at:
                        try:
                            ts = datetime.fromisoformat(contributed_at)
                            if ts > cutoff:
                                skipped += 1
                                continue
                        except ValueError:
                            pass

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

    if skipped > 0:
        await bus.emit("evolution.community_gated", {
            "skipped": skipped,
            "reason": "non-contributor: only weekly updates loaded",
        }, source="kernel")

    if code_loaded > 0:
        await bus.emit("evolution.community_code_loaded", {
            "files": code_loaded,
            "rejected": code_rejected,
        }, source="kernel")

    return loaded + code_loaded
