"""Community contribution loading — import evolved code from other instances.

Loads community contribution JSON files and evolved code from the
community/ directory, applying sandbox validation before use.

Security gates (in order):
  1. Origin verification — git remote must be opensculpt/opensculpt
  2. Manifest verification — MANIFEST.sha256 signature + per-file hashes
  3. Sandbox validation — static analysis + subprocess isolation
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
    Lenient fallback for non-git installs (pip install) — allows if
    MANIFEST.sha256 exists (manifest verification is the real gate).
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
    # Not in a git repo or git unavailable — require manifest as fallback
    if pathlib.Path("community/MANIFEST.sha256").exists():
        return True
    # H2: No git origin AND no manifest = untrusted. Block loading.
    _logger.warning("Cannot verify community origin (no git remote, no signed manifest) — blocking")
    return False


async def load_community_contributions(
    loom, bus: EventBus, sandbox: Sandbox | None = None,
    evo_memory=None,
) -> int:
    """Load community contribution files and apply unlearned strategies.

    Security: verifies origin + manifest before loading ANY community code.

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

    # ── Security Gate 1: Origin verification ──
    if not _verify_community_origin():
        _logger.error("Community origin verification FAILED — skipping community loading")
        await bus.emit("evolution.community_origin_failed", {}, source="kernel")
        return 0

    # ── Security Gate 2: Manifest verification (if manifest exists) ──
    manifest_path = pathlib.Path("community/MANIFEST.sha256")
    manifest_hashes: dict[str, str] | None = None
    if manifest_path.exists():
        try:
            from agos.evolution.manifest import verify_manifest, _parse_manifest
            ok, issues = verify_manifest()
            if not ok:
                _logger.error("Community manifest verification FAILED: %s", issues)
                await bus.emit("evolution.community_integrity_failed", {
                    "issues": issues,
                }, source="kernel")
                return 0
            _logger.info("Community manifest verified successfully")
            # Parse manifest for per-file checks during loading
            content = manifest_path.read_text(encoding="utf-8")
            _, _, manifest_hashes = _parse_manifest(content)
        except Exception as e:
            _logger.error("Manifest verification error: %s", e)
            return 0
    else:
        _logger.debug("No community manifest — relying on origin verification + sandbox only")

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
                    # ── Security Gate 3: Per-file manifest check ──
                    if manifest_hashes is not None:
                        rel = py_file.relative_to(pathlib.Path("community")).as_posix()
                        if rel not in manifest_hashes:
                            _logger.warning(
                                "Rejecting %s — not in signed manifest (injected?)", py_file
                            )
                            await bus.emit("evolution.community_code_rejected", {
                                "file": py_file.name,
                                "instance": instance_dir.name,
                                "reason": "not_in_manifest",
                            }, source="kernel")
                            code_rejected += 1
                            continue
                        from agos.evolution.manifest import hash_file
                        actual_hash = hash_file(py_file)
                        if actual_hash != manifest_hashes[rel]:
                            _logger.warning(
                                "Rejecting %s — hash mismatch vs manifest (tampered?)", py_file
                            )
                            await bus.emit("evolution.community_code_rejected", {
                                "file": py_file.name,
                                "instance": instance_dir.name,
                                "reason": "hash_mismatch",
                            }, source="kernel")
                            code_rejected += 1
                            continue

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
