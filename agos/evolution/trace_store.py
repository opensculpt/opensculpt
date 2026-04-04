"""Execution trace storage — raw tool call logs for evolution diagnosis.

Meta-Harness (Stanford, arXiv:2603.28052) proved that giving an evolution
proposer access to full execution traces (not summaries) improves harness
optimization from 38.7% to 56.7%.  Summaries actively hurt.

This module stores append-only JSONL traces so the EvolutionAgent can
inspect WHY past attempts failed, not just THAT they failed.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

# Cap tool output at 2000 chars per trace entry.
# 10x the current 200-char audit truncation, but not unlimited.
_MAX_OUTPUT_CHARS = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceStore:
    """Append-only JSONL trace writer/reader.

    Files live under .opensculpt/traces/:
      - goal_{goal_id}.jsonl   — tool calls from goal phase execution
      - evo_cycle_{N}.jsonl    — evolution cycle actions
    """

    def __init__(self, traces_dir: Path | str | None = None) -> None:
        self._dir = Path(traces_dir) if traces_dir else Path(".opensculpt/traces")

    def write_goal_trace(
        self, goal_id: str, phase_name: str, steps: list[dict],
    ) -> None:
        """Write tool steps from a goal phase execution."""
        self._dir.mkdir(parents=True, exist_ok=True)
        # Avoid double-prefix (goal IDs often start with "goal_")
        fname = goal_id if goal_id.startswith("goal_") else f"goal_{goal_id}"
        path = self._dir / f"{fname}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                # Phase boundary marker
                f.write(json.dumps({
                    "ts": _now(), "kind": "phase_start",
                    "tool": "", "args": {},
                    "output": "", "ok": True,
                    "duration_ms": 0, "context": phase_name,
                }, default=str) + "\n")
                for step in steps:
                    entry = {
                        "ts": _now(),
                        "kind": "tool_call",
                        "tool": step.get("tool", ""),
                        "args": step.get("full_args", step.get("args", {})),
                        "output": str(
                            step.get("full_output", step.get("preview", ""))
                        )[:_MAX_OUTPUT_CHARS],
                        "ok": step.get("ok", False),
                        "duration_ms": step.get("ms", 0),
                        "context": phase_name,
                    }
                    f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            _logger.debug("Failed to write goal trace: %s", e)

    def write_evo_trace(self, cycle: int, entry: dict) -> None:
        """Write a trace entry from an evolution cycle action."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"evo_cycle_{cycle}.jsonl"
        entry.setdefault("ts", _now())
        entry.setdefault("kind", "action")
        entry.setdefault("source", f"demand_solver:cycle_{cycle}")
        # Cap output
        if "output" in entry:
            entry["output"] = str(entry["output"])[:_MAX_OUTPUT_CHARS]
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            _logger.debug("Failed to write evo trace: %s", e)

    def read_trace(self, trace_id: str, last_n: int = 30) -> list[dict]:
        """Read trace entries.  trace_id can be a goal_id or 'cycle_N'."""
        # Try direct match first
        for prefix in ("goal_", "evo_cycle_", "evo_"):
            path = self._dir / f"{prefix}{trace_id}.jsonl"
            if path.exists():
                return self._read_last_n(path, last_n)
        # Fuzzy match: search for files containing the ID
        if self._dir.exists():
            for f in sorted(self._dir.glob("*.jsonl")):
                if trace_id in f.stem:
                    return self._read_last_n(f, last_n)
        return []

    def list_traces(self, limit: int = 20) -> list[dict]:
        """List available trace files with metadata."""
        if not self._dir.exists():
            return []
        traces = []
        for f in sorted(self._dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True):
            stat = f.stat()
            # Count lines (entries) cheaply
            try:
                line_count = sum(1 for _ in open(f, encoding="utf-8", errors="ignore"))
            except Exception:
                line_count = 0
            traces.append({
                "id": f.stem,
                "file": f.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "entries": line_count,
                "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
            })
            if len(traces) >= limit:
                break
        return traces

    @staticmethod
    def _read_last_n(path: Path, n: int) -> list[dict]:
        """Read last N lines from a JSONL file."""
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        except Exception:
            return []
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return result
