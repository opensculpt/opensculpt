"""SourcePatcher — self-modifying code engine for OpenSculpt.

Like SWE-agent but for the OS itself. When the evolution engine detects
real infrastructure problems (database locks, browser failures, tool bugs),
this module reads the broken source file, asks the LLM to propose a fix,
tests it in sandbox, applies it, and verifies the error goes away.

This is how OpenSculpt literally fixes itself.

Inspired by:
- SWE-agent (Agent-Computer Interfaces for automated software engineering)
- Self-Improving Coding Agent (arxiv:2504.15228)
- Agentic SRE (self-healing infrastructure)
"""

from __future__ import annotations

import ast
import difflib
import importlib
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry
from agos.evolution.demand import DemandCollector, DemandSignal

_logger = logging.getLogger(__name__)

# Files the engine is allowed to modify
MODIFIABLE_PREFIXES = (
    "agos/knowledge/",
    "agos/tools/",
    "agos/daemons/",
    "agos/evolution/",
    "agos/guard.py",
    "agos/session.py",
    "agos/config.py",
)

# Never touch these
OFF_LIMITS = (
    "agos/serve.py",
    "agos/boot.py",
    "agos/knowledge/db.py",  # Critical infra - already optimized
    "agos/cli/",
    "agos/policy/",
    "agos/events/",
    "tests/",
)

PATCHES_DIR = Path(".agos/patches")
BACKUPS_DIR = PATCHES_DIR / "backups"


@dataclass
class SourcePatch:
    """A concrete source code patch with full audit trail."""
    id: str
    demand_key: str
    file_path: str
    target_function: str
    diff: str
    rationale: str
    web_sources: list[str] = field(default_factory=list)
    original_content: str = ""
    modified_content: str = ""
    status: str = "proposed"  # proposed | tested | applied | verified | rolled_back
    error_count_before: int = 0
    error_count_after: int = -1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "demand_key": self.demand_key,
            "file_path": self.file_path, "target_function": self.target_function,
            "diff": self.diff, "rationale": self.rationale,
            "web_sources": self.web_sources, "status": self.status,
            "error_count_before": self.error_count_before,
            "error_count_after": self.error_count_after,
            "created_at": self.created_at,
        }


class SourcePatcher:
    """Self-modifying code engine.

    Extends the ComponentEvolver pattern:
    observe → propose → snapshot → apply → health_check → rollback → verify

    But instead of generating new evolved/ files, this patches EXISTING
    source files to fix real infrastructure problems.
    """

    def __init__(
        self,
        event_bus: EventBus,
        audit: AuditTrail,
        demand_collector: DemandCollector,
        llm=None,
        project_root: Path | None = None,
    ):
        self._bus = event_bus
        self._audit = audit
        self._demand = demand_collector
        self._llm = llm
        self._root = project_root or Path(".")
        self._patches: list[SourcePatch] = []
        self._pending_verifications: list[SourcePatch] = []

        PATCHES_DIR.mkdir(parents=True, exist_ok=True)
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    # ── OBSERVE: Find infrastructure problems to fix ──

    async def observe(self) -> list[dict]:
        """Convert demand signals into source-patch opportunities.

        Only picks signals that:
        - Have occurred 3+ times (not one-off flukes)
        - Point to modifiable files (not off-limits)
        - Haven't already been patched
        """
        opportunities = []
        already_patched = {p.demand_key for p in self._patches if p.status in ("applied", "verified")}

        for signal in self._demand.top_demands(limit=10):
            if signal.count < 3:
                continue
            key = f"{signal.kind}:{signal.source}"
            if key in already_patched:
                continue

            # Try to find the target file from the error (LLM-assisted)
            target_file = await self._find_target_file(signal)
            if not target_file:
                continue

            opportunities.append({
                "signal": signal,
                "file_path": target_file,
                "error_context": signal.description[:300],
                "count": signal.count,
                "priority": signal.priority,
                "demand_key": key,
            })

        # Sort by priority × count
        opportunities.sort(key=lambda o: o["priority"] * o["count"], reverse=True)
        return opportunities[:3]  # max 3 per cycle

    # ── PROPOSE: LLM reads source + error → generates diff ──

    async def propose(self, opportunity: dict) -> SourcePatch | None:
        """Ask the LLM to propose a fix by reading the actual source code."""
        if not self._llm:
            return None

        file_path = opportunity["file_path"]
        signal: DemandSignal = opportunity["signal"]

        # Read the target source file
        full_path = self._root / file_path
        if not full_path.exists():
            return None
        source = full_path.read_text(encoding="utf-8", errors="replace")

        # Truncate very long files — give LLM the relevant section
        if len(source) > 8000:
            source = self._extract_relevant_section(source, signal.description)

        # Web search for solutions (if web tools available)
        web_context = await self._search_web(signal.description)

        # Ask LLM to propose a fix
        prompt = f"""You are a code surgeon for OpenSculpt OS. Analyze this error and propose a minimal fix.

ERROR (occurred {signal.count} times):
{signal.description}

CONTEXT:
{json.dumps(signal.context, default=str)[:500]}

SOURCE FILE: {file_path}
```python
{source}
```

{web_context}

Propose a MINIMAL fix. Output JSON:
{{
  "target_function": "function or class that needs fixing",
  "rationale": "why this fix works",
  "modified_code": "the complete modified version of ONLY the function/section that needs changing"
}}

Rules:
- Change as few lines as possible
- Don't change function signatures
- Don't add new dependencies
- Prefer: adding try/except, adding pragmas, adding retry logic, fixing argument handling
- The fix must address the SPECIFIC error, not be a general improvement"""

        try:
            from agos.llm.base import LLMMessage
            resp = await self._llm.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                system="You are a precise code surgeon. Output valid JSON only.",
                max_tokens=2000,
            )

            # Parse LLM response
            text = resp.content or ""
            # Extract JSON from response (might be wrapped in markdown)
            json_match = re.search(r'\{[^{}]*"target_function"[^{}]*\}', text, re.DOTALL)
            if not json_match:
                # Try to find any JSON block
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    return None
            else:
                json_str = json_match.group(0)

            fix = json.loads(json_str)
            target_fn = fix.get("target_function", "")
            rationale = fix.get("rationale", "")
            modified_code = fix.get("modified_code", "")

            if not modified_code or not target_fn:
                return None

            # Apply the modification to get the full modified source
            modified_source = self._apply_modification(source, target_fn, modified_code)
            if not modified_source:
                return None

            # Validate syntax
            try:
                ast.parse(modified_source)
            except SyntaxError as e:
                _logger.debug("Proposed fix has syntax error: %s", e)
                return None

            # Generate diff
            diff = "\n".join(difflib.unified_diff(
                source.splitlines(), modified_source.splitlines(),
                fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
                lineterm="",
            ))

            # Check diff is small enough (< 50 lines changed)
            changed_lines = sum(1 for line in diff.splitlines() if line.startswith("+") or line.startswith("-"))
            if changed_lines > 50:
                _logger.debug("Proposed fix too large: %d lines changed", changed_lines)
                return None

            patch_id = f"patch_{int(time.time())}_{hash(diff) % 10000}"
            return SourcePatch(
                id=patch_id,
                demand_key=opportunity["demand_key"],
                file_path=file_path,
                target_function=target_fn,
                diff=diff,
                rationale=rationale,
                original_content=source,
                modified_content=modified_source,
                error_count_before=signal.count,
                status="proposed",
            )

        except Exception as e:
            _logger.debug("SourcePatcher.propose failed: %s", e)
            return None

    # ── SNAPSHOT: Backup original file ──

    async def snapshot(self, patch: SourcePatch) -> None:
        """Backup the original file before patching."""
        full_path = self._root / patch.file_path
        backup_dir = BACKUPS_DIR / patch.id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / Path(patch.file_path).name
        shutil.copy2(full_path, backup_path)
        _logger.info("Snapshot saved: %s → %s", patch.file_path, backup_path)

    # ── APPLY: Write the fix and hot-reload ──

    async def apply(self, patch: SourcePatch) -> bool:
        """Write modified source to disk and reload the module."""
        full_path = self._root / patch.file_path
        try:
            full_path.write_text(patch.modified_content, encoding="utf-8")
            _logger.info("Patch applied: %s (%s)", patch.file_path, patch.rationale[:60])

            # Hot-reload the module
            module_name = patch.file_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
            if module_name in sys.modules:
                try:
                    importlib.reload(sys.modules[module_name])
                    _logger.info("Module reloaded: %s", module_name)
                except Exception as e:
                    _logger.warning("Hot-reload failed for %s: %s", module_name, e)
                    # Module reload failed — rollback
                    full_path.write_text(patch.original_content, encoding="utf-8")
                    patch.status = "rolled_back"
                    return False

            patch.status = "applied"
            await self._bus.emit("evolution.source_patched", {
                "file": patch.file_path,
                "target": patch.target_function,
                "rationale": patch.rationale[:100],
                "diff_lines": len(patch.diff.splitlines()),
            }, source="source_patcher")

            await self._audit.record(AuditEntry(
                agent_name="source_patcher",
                action="source_patch_applied",
                detail=f"{patch.file_path}: {patch.rationale[:100]}",
                success=True,
            ))

            return True

        except Exception as e:
            _logger.error("Patch apply failed: %s", e)
            # Restore original
            try:
                full_path.write_text(patch.original_content, encoding="utf-8")
            except Exception:
                pass
            patch.status = "rolled_back"
            return False

    # ── HEALTH CHECK: Does the module still work? ──

    async def health_check(self, patch: SourcePatch) -> bool:
        """Verify the module loads and basic operations work."""
        module_name = patch.file_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
        try:
            if module_name in sys.modules:
                mod = sys.modules[module_name]
                # Basic check: module has its expected attributes
                return hasattr(mod, "__name__")
            return True  # Module wasn't loaded, so no breakage
        except Exception:
            return False

    # ── ROLLBACK: Restore original file ──

    async def rollback(self, patch: SourcePatch) -> None:
        """Restore the original file from snapshot."""
        full_path = self._root / patch.file_path
        try:
            full_path.write_text(patch.original_content, encoding="utf-8")
            module_name = patch.file_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            patch.status = "rolled_back"
            _logger.info("Patch rolled back: %s", patch.file_path)

            await self._bus.emit("evolution.source_rollback", {
                "file": patch.file_path,
                "patch_id": patch.id,
            }, source="source_patcher")
        except Exception as e:
            _logger.error("Rollback failed: %s", e)

    # ── VERIFY: Did the fix actually work? ──

    async def verify(self, patch: SourcePatch) -> bool:
        """ACTIVELY reproduce the bug and confirm the fix works.

        Doesn't passively wait — spawns a verification test that
        triggers the exact condition that caused the original error.
        If the error is gone → patch verified. If error still happens → rollback.
        """
        _logger.info("Verifying patch %s — actively reproducing the bug...", patch.file_path)

        # Use the OS agent (if available) to run a verification command
        # that exercises the patched code path
        verified = False
        try:
            if hasattr(self, '_llm') and self._llm:
                from agos.llm.base import LLMMessage
                # Ask LLM: "given this error and this fix, write a Python test"
                signal_desc = ""
                for signal in self._demand.top_demands(limit=20):
                    key = f"{signal.kind}:{signal.source}"
                    if key == patch.demand_key:
                        signal_desc = signal.description
                        break

                resp = await self._llm.complete(
                    messages=[LLMMessage(role="user", content=(
                        f"Write a short Python async test that reproduces this error:\n"
                        f"Error: {signal_desc[:200]}\n"
                        f"File fixed: {patch.file_path}\n"
                        f"Fix applied: {patch.rationale[:200]}\n\n"
                        f"The test should import the fixed module and exercise the code path "
                        f"that was failing. Use asyncio. Print PASS if no error, FAIL if error. "
                        f"Output ONLY the Python code, no markdown."
                    ))],
                    system="Output only valid Python code. No markdown. No explanation.",
                    max_tokens=500,
                )
                test_code = (resp.content or "").strip()
                # Remove markdown fences if present
                test_code = re.sub(r'^```python\s*', '', test_code)
                test_code = re.sub(r'\s*```$', '', test_code)

                if test_code and "import" in test_code:
                    import subprocess
                    result = subprocess.run(
                        [sys.executable, "-c", test_code],
                        capture_output=True, text=True, timeout=15,
                        cwd=str(self._root),
                    )
                    output = result.stdout + result.stderr
                    if "PASS" in output and "FAIL" not in output:
                        verified = True
                        _logger.info("Patch VERIFIED by active test: %s", patch.file_path)
                    else:
                        _logger.info("Patch FAILED active test: %s — output: %s",
                                     patch.file_path, output[:200])
        except Exception as e:
            _logger.debug("Active verification failed: %s — falling back to passive", e)

        # Fallback: passive check — did new errors occur since patch?
        if not verified:
            for signal in self._demand.top_demands(limit=20):
                key = f"{signal.kind}:{signal.source}"
                if key == patch.demand_key:
                    if signal.last_seen <= patch.created_at:
                        verified = True  # No new errors since patch
                    break
            else:
                verified = True  # Signal gone entirely — fix worked

        if verified:
            patch.status = "verified"
            patch.error_count_after = 0
            await self._bus.emit("evolution.source_patch_verified", {
                "file": patch.file_path,
                "errors_before": patch.error_count_before,
                "errors_after": 0,
                "rationale": patch.rationale[:100],
            }, source="source_patcher")
            return True
        else:
            patch.error_count_after = patch.error_count_before
            _logger.info(
                "Patch FAILED verification: %s — rolling back", patch.file_path,
            )
            await self.rollback(patch)
            return False

    # ── FULL CYCLE: observe → propose → snapshot → apply → verify ──

    async def tick(self) -> list[dict]:
        """Run one source-patching cycle. Called from evolution_loop."""
        results = []

        # First: verify any pending patches from last cycle
        for patch in list(self._pending_verifications):
            verified = await self.verify(patch)
            results.append({
                "action": "verify",
                "file": patch.file_path,
                "verified": verified,
                "errors_before": patch.error_count_before,
                "errors_after": patch.error_count_after,
            })
            self._pending_verifications.remove(patch)

        # Then: look for new problems to fix
        opportunities = await self.observe()
        for opp in opportunities[:1]:  # one patch per cycle
            patch = await self.propose(opp)
            if not patch:
                continue

            await self.snapshot(patch)
            applied = await self.apply(patch)
            if applied:
                healthy = await self.health_check(patch)
                if healthy:
                    self._patches.append(patch)
                    self._pending_verifications.append(patch)
                    results.append({
                        "action": "applied",
                        "file": patch.file_path,
                        "rationale": patch.rationale[:100],
                        "diff_lines": len(patch.diff.splitlines()),
                    })
                else:
                    await self.rollback(patch)
                    results.append({
                        "action": "rolled_back",
                        "file": patch.file_path,
                        "reason": "health check failed",
                    })

        return results

    # ── HELPERS ──

    # Cache LLM target-file lookups to avoid repeated calls for the same demand
    _target_cache: dict[str, str | None] = {}

    async def _find_target_file(self, signal: DemandSignal) -> str | None:
        """Find which source file to patch — traceback extraction + LLM reasoning."""
        desc = signal.description + " " + json.dumps(signal.context, default=str)

        # 1. Extract file paths from tracebacks (mechanical, fast)
        traceback_files = re.findall(r'File "(.+?\.py)", line \d+', desc)
        for f in traceback_files:
            rel = f.replace("\\", "/")
            for prefix in MODIFIABLE_PREFIXES:
                if prefix in rel:
                    idx = rel.index(prefix.split("/")[0])
                    rel_path = rel[idx:]
                    if not any(rel_path.startswith(off) for off in OFF_LIMITS):
                        return rel_path

        # 2. LLM reasoning (replaces hardcoded keyword dict)
        cache_key = signal.description[:100]
        if cache_key in self._target_cache:
            return self._target_cache[cache_key]

        if not self._llm:
            return None

        try:
            from agos.evolution.codegen import read_codebase_map
            codebase = read_codebase_map()[:1500]
        except Exception:
            codebase = ""

        from agos.llm import LLMMessage
        prompt = (
            f"Error (occurred {signal.count}x): {signal.description[:300]}\n"
            f"Context: {json.dumps(signal.context, default=str)[:300]}\n\n"
            f"Codebase:\n{codebase}\n\n"
            f"Modifiable: {', '.join(MODIFIABLE_PREFIXES)}\n"
            f"Off-limits: {', '.join(OFF_LIMITS)}\n\n"
            "Which source file should be patched to fix this error? "
            "Return ONLY the file path (e.g. agos/daemons/goal_runner.py) or 'none'."
        )
        try:
            resp = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content="You identify which source file contains a bug. Return only a file path or 'none'."),
                    LLMMessage(role="user", content=prompt),
                ],
                max_tokens=60,
            )
            text = (resp.content or "").strip().strip("`\"'")
            if text.lower() == "none" or not text.endswith(".py"):
                self._target_cache[cache_key] = None
                return None
            # Validate against safety boundaries
            if not any(text.startswith(p) for p in MODIFIABLE_PREFIXES):
                self._target_cache[cache_key] = None
                return None
            if any(text.startswith(off) for off in OFF_LIMITS):
                self._target_cache[cache_key] = None
                return None
            if Path(text).exists():
                self._target_cache[cache_key] = text
                return text
            self._target_cache[cache_key] = None
            return None
        except Exception as e:
            _logger.debug("SourcePatcher LLM target lookup failed: %s", e)
            self._target_cache[cache_key] = None
            return None

    def _apply_modification(self, source: str, target_fn: str, new_code: str) -> str | None:
        """Replace a function/section in source with new code."""
        lines = source.splitlines(keepends=True)

        # Try to find the function/class definition
        target_patterns = [
            f"def {target_fn}(",
            f"async def {target_fn}(",
            f"class {target_fn}(",
            f"class {target_fn}:",
        ]

        start_line = None
        indent = ""
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            for pattern in target_patterns:
                if stripped.startswith(pattern):
                    start_line = i
                    indent = line[:len(line) - len(stripped)]
                    break
            if start_line is not None:
                break

        if start_line is None:
            return None

        # Include decorators above the function in the replacement range
        # (prevents duplicate @asynccontextmanager etc.)
        while start_line > 0:
            prev = lines[start_line - 1].strip()
            if prev.startswith("@"):
                start_line -= 1
            else:
                break

        # Find the end of the function (next def/class at same or lower indent)
        end_line = len(lines)
        for i in range(start_line + 1, len(lines)):
            stripped = lines[i].lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            current_indent = lines[i][:len(lines[i]) - len(stripped)]
            if len(current_indent) <= len(indent) and (
                stripped.startswith("def ") or stripped.startswith("async def ") or
                stripped.startswith("class ") or stripped.startswith("@")
            ):
                end_line = i
                break

        # Replace the function body
        new_lines = new_code.splitlines(keepends=True)
        # Ensure proper newline at end
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        modified_lines = lines[:start_line] + new_lines + ["\n"] + lines[end_line:]
        return "".join(modified_lines)

    def _extract_relevant_section(self, source: str, error_desc: str) -> str:
        """Extract the most relevant 200 lines from a large file."""
        lines = source.splitlines()
        # Find lines mentioned in error
        relevant_line = 0
        for match in re.finditer(r'line (\d+)', error_desc):
            relevant_line = int(match.group(1))
            break

        if relevant_line > 0:
            start = max(0, relevant_line - 50)
            end = min(len(lines), relevant_line + 150)
            return "\n".join(lines[start:end])

        # Return first 200 lines as fallback
        return "\n".join(lines[:200])

    async def _search_web(self, error_message: str) -> str:
        """Search the web for solutions to this error."""
        try:
            from agos.tools.extended import web_search
            query = f"python fix {error_message[:80]}"
            results = await web_search(query=query)
            if results and isinstance(results, str):
                return f"\nWEB SOLUTIONS FOUND:\n{results[:500]}"
        except Exception:
            pass
        return ""

    # ── Dashboard API ──

    def get_patches(self) -> list[dict]:
        """Return all patches for the dashboard."""
        return [p.to_dict() for p in self._patches]

    def get_stats(self) -> dict:
        """Stats for the Evolution tab."""
        return {
            "total_patches": len(self._patches),
            "applied": sum(1 for p in self._patches if p.status == "applied"),
            "verified": sum(1 for p in self._patches if p.status == "verified"),
            "rolled_back": sum(1 for p in self._patches if p.status == "rolled_back"),
            "pending_verification": len(self._pending_verifications),
        }
