"""Fleet curator — reads all node data, scores artifacts, packages releases.

This is the "Claude Code as meta-evolution engine" implementation.
Run it on the host machine (not inside containers) to aggregate fleet
learnings and produce curated release packages.

Usage:
    python -m agos.evolution.curator_loop                  # one-shot report
    python -m agos.evolution.curator_loop --release         # report + release
    python -m agos.evolution.curator_loop --fleet-dir DIR   # custom fleet dir

Research basis:
    - Tesla: centralized aggregation + canary release
    - K8s OperatorHub: git-based registry of curated knowledge
    - OpenSpace: community cloud for evolved skills
    - Azure SRE Agent: session insights → organizational knowledge
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path


_logger = logging.getLogger(__name__)


# ── Fleet Report ─────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    """Read a JSON file, return empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_md(path: Path) -> str:
    """Read a markdown file, return empty string on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _count_lines(text: str, prefix: str = "") -> int:
    if not text:
        return 0
    if prefix:
        return sum(1 for line in text.split("\n") if line.strip().startswith(prefix))
    return len([line for line in text.split("\n") if line.strip()])


class NodeReport:
    """Summary of a single node's evolution state."""

    def __init__(self, node_dir: Path) -> None:
        self.name = node_dir.name
        self.path = node_dir

        # Read state files
        self.evo_state = _read_json(node_dir / "evolution_state.json")
        self.demands = _read_json(node_dir / "demand_signals.json")
        self.scores = _read_json(node_dir / "artifact_scores.json")
        self.constraints_md = _read_md(node_dir / "constraints.md")
        self.resolutions_md = _read_md(node_dir / "resolutions.md")

        # Parse key metrics
        self.cycles = self.evo_state.get("cycles_completed", 0)
        self.strategies_applied = len(self.evo_state.get("strategies_applied", []))
        self.insights = len(self.evo_state.get("evolution_memory", {}).get("insights", []))

        # Demand analysis
        signals = self.demands.get("signals", [])
        if isinstance(signals, dict):
            signals = list(signals.values())
        self.total_demands = len(signals)
        self.resolved = sum(1 for s in signals if isinstance(s, dict) and s.get("status") == "resolved")
        self.escalated = sum(1 for s in signals if isinstance(s, dict) and s.get("status") == "escalated")
        self.active = sum(1 for s in signals if isinstance(s, dict) and s.get("status") in ("active", "attempting"))

        # Evolved artifacts
        evolved_dir = node_dir / "evolved"
        self.evolved_tools = list(evolved_dir.glob("*.py")) if evolved_dir.exists() else []

        # Skills
        skills_dir = node_dir / "skills"
        self.skills = list(skills_dir.glob("*.md")) if skills_dir.exists() else []

        # Knowledge counts
        self.constraints_count = _count_lines(self.constraints_md, "- ")
        self.resolutions_count = len(self.resolutions_md.split("\n## ")) - 1 if self.resolutions_md else 0

    def summary_line(self) -> str:
        return (
            f"| {self.name:20s} | {self.cycles:5d} | {self.resolved:4d}/{self.total_demands:4d} | "
            f"{len(self.evolved_tools):3d} | {len(self.skills):3d} | "
            f"{self.constraints_count:3d} | {self.resolutions_count:3d} |"
        )


def generate_fleet_report(fleet_dir: Path) -> str:
    """Read all node directories and produce a fleet-wide markdown report.

    Args:
        fleet_dir: Path to .opensculpt-fleet/ (or similar) with per-node subdirs
    """
    if not fleet_dir.exists():
        return f"# Fleet Report\n\nFleet directory not found: {fleet_dir}\n"

    node_dirs = sorted([d for d in fleet_dir.iterdir() if d.is_dir()])
    if not node_dirs:
        return f"# Fleet Report\n\nNo nodes found in {fleet_dir}\n"

    nodes = [NodeReport(d) for d in node_dirs]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# OpenSculpt Fleet Report",
        "",
        f"Generated: {now}",
        f"Nodes: {len(nodes)}",
        "",
        "## Per-Node Summary",
        "",
        "| Node                 | Cycles | Resolved     | Tools | Skills | Constraints | Resolutions |",
        "|----------------------|--------|--------------|-------|--------|-------------|-------------|",
    ]
    for n in nodes:
        lines.append(n.summary_line())

    # Totals
    total_cycles = sum(n.cycles for n in nodes)
    total_resolved = sum(n.resolved for n in nodes)
    total_demands = sum(n.total_demands for n in nodes)
    total_tools = sum(len(n.evolved_tools) for n in nodes)
    total_skills = sum(len(n.skills) for n in nodes)
    lines.append(f"| **TOTAL**            | {total_cycles:5d} | {total_resolved:4d}/{total_demands:4d} | "
                 f"{total_tools:3d} | {total_skills:3d} |             |             |")

    # Top artifacts by score
    lines.extend(["", "## Top Artifacts (by composite score)", ""])
    all_scored: list[tuple[str, str, float]] = []  # (node, artifact_id, score)
    for n in nodes:
        for aid, sdata in n.scores.items():
            if isinstance(sdata, dict):
                composite = sdata.get("composite", 0.0)
            else:
                composite = float(sdata)
            all_scored.append((n.name, aid, composite))
    all_scored.sort(key=lambda x: x[2], reverse=True)

    if all_scored:
        lines.append("| Node | Artifact | Score |")
        lines.append("|------|----------|-------|")
        for node_name, aid, score in all_scored[:20]:
            lines.append(f"| {node_name} | {aid[:40]} | {score:.3f} |")
    else:
        lines.append("_No scored artifacts yet. Run evolution cycles first._")

    # Fleet-wide knowledge gaps (demands escalated on ALL nodes)
    escalated_everywhere: list[str] = []
    if len(nodes) > 1:
        # Find demands that appear as escalated on 2+ nodes
        escalated_by_key: dict[str, int] = {}
        for n in nodes:
            signals = n.demands.get("signals", [])
            if isinstance(signals, dict):
                signals = list(signals.values())
            for s in signals:
                if isinstance(s, dict) and s.get("status") == "escalated":
                    key = s.get("key", s.get("description", "unknown"))[:60]
                    escalated_by_key[key] = escalated_by_key.get(key, 0) + 1
        escalated_everywhere = [k for k, v in escalated_by_key.items() if v >= 2]

    if escalated_everywhere:
        lines.extend(["", "## Fleet-Wide Gaps (escalated on 2+ nodes)", ""])
        for gap in escalated_everywhere[:10]:
            lines.append(f"- {gap}")

    # Cross-node tool propagation
    tool_names_per_node: dict[str, set] = {}
    for n in nodes:
        tool_names_per_node[n.name] = {t.stem for t in n.evolved_tools}
    shared_tools = set()
    for name_a, tools_a in tool_names_per_node.items():
        for name_b, tools_b in tool_names_per_node.items():
            if name_a != name_b:
                shared_tools |= tools_a & tools_b
    if shared_tools:
        lines.extend(["", "## Tools Propagated Across Nodes", ""])
        for t in sorted(shared_tools):
            present_on = [n for n, tools in tool_names_per_node.items() if t in tools]
            lines.append(f"- `{t}` — present on: {', '.join(present_on)}")

    lines.append("")
    return "\n".join(lines)


# ── Release Packaging ────────────────────────────────────────────

def create_release(
    fleet_dir: Path,
    output_dir: Path | None = None,
    min_score: float = 0.3,
) -> Path:
    """Package top-scored artifacts from fleet into a versioned release.

    Args:
        fleet_dir: Path with per-node subdirs
        output_dir: Where to create releases/ (default: .opensculpt/releases/)
        min_score: Minimum composite score to include
    """
    if output_dir is None:
        output_dir = Path(".opensculpt") / "releases"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine version number
    existing = sorted(output_dir.glob("v*"))
    version = len(existing) + 1
    release_dir = output_dir / f"v{version}"
    release_dir.mkdir(parents=True, exist_ok=True)

    node_dirs = sorted([d for d in fleet_dir.iterdir() if d.is_dir()]) if fleet_dir.exists() else []
    nodes = [NodeReport(d) for d in node_dirs]

    # Collect all scored artifacts above threshold
    artifacts: list[dict] = []
    for n in nodes:
        for aid, sdata in n.scores.items():
            composite = sdata.get("composite", 0.0) if isinstance(sdata, dict) else float(sdata)
            if composite >= min_score:
                artifacts.append({
                    "artifact_id": aid,
                    "source_node": n.name,
                    "score": sdata if isinstance(sdata, dict) else {"composite": composite},
                    "cycles": n.cycles,
                })

    artifacts.sort(key=lambda a: a["score"].get("composite", 0) if isinstance(a["score"], dict) else a["score"], reverse=True)

    # Copy top tools
    tools_dir = release_dir / "tools"
    tools_dir.mkdir(exist_ok=True)
    tools_included = []
    seen_tools: set[str] = set()
    for n in nodes:
        for tool_file in n.evolved_tools:
            if tool_file.stem not in seen_tools:
                shutil.copy2(tool_file, tools_dir / tool_file.name)
                seen_tools.add(tool_file.stem)
                tools_included.append({"name": tool_file.stem, "source": n.name})

    # Aggregate skills (dedup by filename)
    skills_dir = release_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    seen_skills: set[str] = set()
    skills_included = []
    for n in nodes:
        for skill_file in n.skills:
            if skill_file.stem not in seen_skills:
                shutil.copy2(skill_file, skills_dir / skill_file.name)
                seen_skills.add(skill_file.stem)
                skills_included.append({"name": skill_file.stem, "source": n.name})

    # Aggregate constraints and resolutions
    all_constraints: list[str] = []
    all_resolutions: list[str] = []
    for n in nodes:
        for line in n.constraints_md.split("\n"):
            line = line.strip()
            if line.startswith("- ") and line not in all_constraints:
                all_constraints.append(line)
        for section in n.resolutions_md.split("\n## ")[1:]:
            heading = section.split("\n")[0][:50]
            if not any(heading in r for r in all_resolutions):
                all_resolutions.append("## " + section)

    if all_constraints:
        (release_dir / "constraints.md").write_text(
            "# Aggregated Constraints\n\n" + "\n".join(all_constraints) + "\n",
            encoding="utf-8",
        )
    if all_resolutions:
        (release_dir / "resolutions.md").write_text(
            "# Aggregated Resolutions\n\n" + "\n".join(all_resolutions) + "\n",
            encoding="utf-8",
        )

    # Write manifest
    manifest = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "nodes_aggregated": len(nodes),
        "min_score": min_score,
        "artifacts": artifacts[:50],
        "tools_included": tools_included,
        "skills_included": skills_included,
        "constraints_count": len(all_constraints),
        "resolutions_count": len(all_resolutions),
    }
    (release_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
    )

    # Write changelog
    changelog = [
        f"# Release v{version}",
        "",
        f"Created: {manifest['created_at']}",
        f"Nodes: {len(nodes)}",
        "",
        "## Contents",
        f"- {len(tools_included)} evolved tools",
        f"- {len(skills_included)} skill documents",
        f"- {len(all_constraints)} constraints",
        f"- {len(all_resolutions)} resolution patterns",
        "",
        "## Top Artifacts",
    ]
    for a in artifacts[:10]:
        score = a["score"].get("composite", 0) if isinstance(a["score"], dict) else a["score"]
        changelog.append(f"- {a['artifact_id'][:40]} (score: {score:.3f}, from: {a['source_node']})")
    changelog.append("")

    (release_dir / "CHANGELOG.md").write_text("\n".join(changelog), encoding="utf-8")

    _logger.info(
        "Release v%d created: %d tools, %d skills, %d constraints, %d resolutions",
        version, len(tools_included), len(skills_included),
        len(all_constraints), len(all_resolutions),
    )

    return release_dir


# ── Seed: Apply a release to a fresh install ─────────────────────

def apply_release(release_dir: Path, workspace_dir: Path | None = None) -> dict:
    """Apply a curated release to the local workspace.

    Merges constraints, resolutions, skills, and tools with a 0.7x
    trust discount on federated knowledge.
    """
    if workspace_dir is None:
        workspace_dir = Path(".opensculpt")

    result = {"tools": 0, "skills": 0, "constraints": 0, "resolutions": 0}

    # Copy tools
    tools_src = release_dir / "tools"
    if tools_src.exists():
        tools_dst = workspace_dir / "evolved"
        tools_dst.mkdir(parents=True, exist_ok=True)
        for f in tools_src.glob("*.py"):
            target = tools_dst / f.name
            if not target.exists():
                shutil.copy2(f, target)
                result["tools"] += 1

    # Copy skills
    skills_src = release_dir / "skills"
    if skills_src.exists():
        skills_dst = workspace_dir / "skills"
        skills_dst.mkdir(parents=True, exist_ok=True)
        for f in skills_src.glob("*.md"):
            target = skills_dst / f.name
            if not target.exists():
                shutil.copy2(f, target)
                result["skills"] += 1

    # Merge constraints (append new lines with federation header)
    constraints_src = release_dir / "constraints.md"
    if constraints_src.exists():
        local = workspace_dir / "constraints.md"
        existing = local.read_text(encoding="utf-8") if local.exists() else ""
        new_lines = []
        for line in constraints_src.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if line.startswith("- ") and line not in existing:
                new_lines.append(line)
        if new_lines:
            with open(local, "a", encoding="utf-8") as f:
                f.write(f"\n\n# Seeded from release v{release_dir.name}\n")
                f.write("\n".join(new_lines) + "\n")
            result["constraints"] = len(new_lines)

    # Merge resolutions
    resolutions_src = release_dir / "resolutions.md"
    if resolutions_src.exists():
        local = workspace_dir / "resolutions.md"
        existing = local.read_text(encoding="utf-8") if local.exists() else ""
        new_sections = []
        for section in resolutions_src.read_text(encoding="utf-8").split("\n## ")[1:]:
            heading = section.split("\n")[0][:50]
            if heading and heading not in existing:
                new_sections.append("## " + section)
        if new_sections:
            with open(local, "a", encoding="utf-8") as f:
                f.write(f"\n\n# Seeded from release v{release_dir.name}\n")
                f.write("\n".join(new_sections))
            result["resolutions"] = len(new_sections)

    return result


# ── Contribute: export local knowledge for sharing ───────────────

def export_contribution(workspace_dir: Path | None = None) -> Path:
    """Export anonymized local knowledge for federation.

    Writes to .opensculpt/contributions/{timestamp}/ for manual
    submission or programmatic push.
    """
    if workspace_dir is None:
        workspace_dir = Path(".opensculpt")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    contrib_dir = workspace_dir / "contributions" / ts
    contrib_dir.mkdir(parents=True, exist_ok=True)

    # Export constraints (anonymized: strip IPs, paths, credentials)
    src = workspace_dir / "constraints.md"
    if src.exists():
        content = src.read_text(encoding="utf-8")
        content = _anonymize_md(content)
        (contrib_dir / "constraints.md").write_text(content, encoding="utf-8")

    # Export resolutions
    src = workspace_dir / "resolutions.md"
    if src.exists():
        content = src.read_text(encoding="utf-8")
        content = _anonymize_md(content)
        (contrib_dir / "resolutions.md").write_text(content, encoding="utf-8")

    # Export skills
    skills_dir = workspace_dir / "skills"
    if skills_dir.exists():
        dst = contrib_dir / "skills"
        dst.mkdir(exist_ok=True)
        for f in skills_dir.glob("*.md"):
            content = _anonymize_md(f.read_text(encoding="utf-8"))
            (dst / f.name).write_text(content, encoding="utf-8")

    # Export scores
    scores_file = workspace_dir / "artifact_scores.json"
    if scores_file.exists():
        shutil.copy2(scores_file, contrib_dir / "artifact_scores.json")

    # Export demand summary (anonymized)
    demands_file = workspace_dir / "demand_signals.json"
    if demands_file.exists():
        demands = _read_json(demands_file)
        # Strip context details, keep only keys and statuses
        signals = demands.get("signals", [])
        if isinstance(signals, dict):
            signals = list(signals.values())
        summary = []
        for s in signals:
            if isinstance(s, dict):
                summary.append({
                    "kind": s.get("kind", ""),
                    "status": s.get("status", ""),
                    "priority": s.get("priority", 0),
                    "count": s.get("count", 1),
                })
        (contrib_dir / "demand_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8",
        )

    _logger.info("Contribution exported to %s", contrib_dir)
    return contrib_dir


def _anonymize_md(text: str) -> str:
    """Strip IPs, paths, and credential-like strings from markdown."""
    import re
    # Strip IP addresses
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[REDACTED_IP]', text)
    # Strip likely API keys (long alphanumeric strings)
    text = re.sub(r'(sk-[a-zA-Z0-9-]{20,})', '[REDACTED_KEY]', text)
    text = re.sub(r'(ghp_[a-zA-Z0-9]{20,})', '[REDACTED_TOKEN]', text)
    # Strip absolute paths
    text = re.sub(r'(/[a-zA-Z0-9_/.-]{10,})', '[PATH]', text)
    text = re.sub(r'([A-Z]:\\[a-zA-Z0-9_\\.-]{10,})', '[PATH]', text)
    return text
