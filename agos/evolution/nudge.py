"""Evolution nudge system — tells users their OS needs help evolving.

Like `npm audit` or `brew upgrade` notices, but for an agentic OS.
After every CLI command, if demands exist, show a one-liner nudge.
Also generates DEMANDS.md for AI coding tools to read.

The loop:
  1. OS hits problems → demands accumulate
  2. CLI/Dashboard nudges: "N demands, paste this into your AI tool"
  3. User copies prompt → pastes into Claude Code / Cursor / Codex
  4. AI tool reads DEMANDS.md, fixes code
  5. User runs: sculpt verify
  6. OS confirms: "N demands resolved"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agos.config import settings

_logger = logging.getLogger(__name__)


def get_demand_count() -> tuple[int, int]:
    """Return (total_active, total_escalated) demand counts.

    Fast — reads JSON file, no LLM calls.
    """
    signals_path = Path(settings.workspace_dir) / "demand_signals.json"
    if not signals_path.exists():
        return 0, 0
    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
        signals = data.get("signals", [])
        if isinstance(signals, dict):
            signals = list(signals.values())
        active = sum(
            1 for s in signals
            if isinstance(s, dict) and s.get("status") in ("active", "attempting")
        )
        escalated = sum(
            1 for s in signals
            if isinstance(s, dict) and s.get("status") == "escalated"
        )
        return active, escalated
    except Exception:
        return 0, 0


def nudge_line() -> str:
    """One-line nudge for CLI footer. Empty string if no demands."""
    active, escalated = get_demand_count()
    total = active + escalated
    if total == 0:
        return ""
    if escalated > 0:
        return (
            f"\n  \033[33m⚡ {total} evolution demand{'s' if total != 1 else ''} "
            f"({escalated} need your help). "
            f"Run: sculpt demands --prompt\033[0m"
        )
    return (
        f"\n  \033[2m⚡ {total} evolution demand{'s' if total != 1 else ''} pending. "
        f"Run: sculpt demands --prompt\033[0m"
    )


def write_demands_md() -> Path:
    """Write .opensculpt/DEMANDS.md — readable by any AI coding tool.

    This is the universal interface between OpenSculpt and Claude Code,
    Cursor, Codex, Copilot, Aider, or any tool that reads markdown.
    """
    signals_path = Path(settings.workspace_dir) / "demand_signals.json"
    output_path = Path(settings.workspace_dir) / "DEMANDS.md"

    if not signals_path.exists():
        output_path.write_text(
            "# OpenSculpt Evolution Demands\n\nNo demands yet. The OS is healthy.\n",
            encoding="utf-8",
        )
        return output_path

    data = json.loads(signals_path.read_text(encoding="utf-8"))
    signals = data.get("signals", [])
    if isinstance(signals, dict):
        signals = list(signals.values())

    # Filter to actionable demands
    actionable = [
        s for s in signals
        if isinstance(s, dict) and s.get("status") in ("active", "attempting", "escalated")
    ]
    actionable.sort(key=lambda s: s.get("priority", 0), reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# OpenSculpt Evolution Demands",
        "",
        f"Auto-generated: {now}",
        f"Total: {len(actionable)} actionable demands",
        "",
        "These are real problems the OS encountered but couldn't solve on its own.",
        "Read the relevant source files, write a fix, and run `sculpt verify`.",
        "",
    ]

    if not actionable:
        lines.append("**No actionable demands.** The OS is healthy.")
    else:
        for i, s in enumerate(actionable[:15], 1):
            kind = s.get("kind", "unknown")
            desc = s.get("description", "no description")
            source = s.get("source", "unknown")
            priority = s.get("priority", 0)
            attempts = s.get("attempts", 0)
            status = s.get("status", "active")

            lines.append(f"## {i}. [{status.upper()}] {desc[:80]}")
            lines.append(f"- **Kind**: {kind}")
            lines.append(f"- **Source**: {source}")
            lines.append(f"- **Priority**: {priority:.2f} | **Attempts**: {attempts}")

            ctx = s.get("context", {})
            if isinstance(ctx, dict):
                if ctx.get("command"):
                    lines.append(f"- **Command**: `{ctx['command'][:120]}`")
                if ctx.get("error"):
                    lines.append(f"- **Error**: `{ctx['error'][:250]}`")
                if ctx.get("tool"):
                    lines.append(f"- **Tool**: {ctx['tool']}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## How to Fix",
        "",
        "1. Read `ARCHITECTURE.md` for system architecture and wiring rules",
        "2. Read the relevant source files in `agos/`",
        "3. Write the minimal fix",
        "4. Run: `python -m pytest tests/ --ignore=tests/test_frontend_playwright.py -q`",
        "5. Run: `sculpt verify` to confirm demands were resolved",
        "6. Run: `sculpt contribute` to share your fix with the fleet",
        "",
        "## Architecture Quick Reference",
        "",
        "- OS Agent (brain): `agos/os_agent.py`",
        "- Evolution: `agos/evolution/` (cycle.py, demand_solver.py, tool_evolver.py)",
        "- Tools: `agos/tools/`",
        "- Daemons: `agos/daemons/`",
        "- Knowledge: `agos/knowledge/`",
        "- Dashboard: `agos/dashboard/app.py`",
        "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    _logger.debug("Wrote DEMANDS.md with %d demands", len(actionable))
    return output_path


def write_tool_configs() -> list[str]:
    """Write config files for all major AI coding tools.

    These all point to .opensculpt/DEMANDS.md as the evolution context.
    Returns list of files created.
    """
    created = []
    project_root = Path(".")

    evolution_context = (
        "# OpenSculpt Evolution Context\n"
        "\n"
        "You are working on OpenSculpt, a self-evolving agentic OS.\n"
        "The OS generates demand signals when it encounters problems it can't solve.\n"
        "\n"
        "Before making changes, read:\n"
        "1. `.opensculpt/DEMANDS.md` — What the OS needs fixed (auto-updated)\n"
        "2. `ARCHITECTURE.md` — System architecture and wiring rules\n"
        "3. `.opensculpt/constraints.md` — Environment constraints learned\n"
        "4. `.opensculpt/resolutions.md` — Past resolution patterns\n"
        "\n"
        "After making changes:\n"
        "1. Run: `python -m pytest tests/ -q`\n"
        "2. Run: `sculpt verify` — checks if demands were resolved\n"
        "3. Run: `sculpt contribute` — shares your fix with the fleet\n"
    )

    # AGENTS.md — works for OpenAI Codex + GitHub Copilot
    agents_path = project_root / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(evolution_context, encoding="utf-8")
        created.append("AGENTS.md")

    # .cursor/rules/opensculpt.mdc
    cursor_dir = project_root / ".cursor" / "rules"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_file = cursor_dir / "opensculpt.mdc"
    if not cursor_file.exists():
        cursor_file.write_text(
            "---\ndescription: OpenSculpt evolution context\nalwaysApply: true\n---\n\n"
            + evolution_context,
            encoding="utf-8",
        )
        created.append(".cursor/rules/opensculpt.mdc")

    # .windsurf/rules/opensculpt.md
    windsurf_dir = project_root / ".windsurf" / "rules"
    windsurf_dir.mkdir(parents=True, exist_ok=True)
    windsurf_file = windsurf_dir / "opensculpt.md"
    if not windsurf_file.exists():
        windsurf_file.write_text(evolution_context, encoding="utf-8")
        created.append(".windsurf/rules/opensculpt.md")

    # .github/copilot-instructions.md
    github_dir = project_root / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)
    copilot_file = github_dir / "copilot-instructions.md"
    if not copilot_file.exists():
        copilot_file.write_text(evolution_context, encoding="utf-8")
        created.append(".github/copilot-instructions.md")

    return created
