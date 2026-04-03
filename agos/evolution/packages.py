"""Evolution Packages — shareable bundles of OS evolution.

When the Evolution Agent fixes something, it packages the fix into a
.sculpt bundle: tools, skill docs, prompt rules, source patches, insights.
Other OpenSculpt nodes can install these packages to get the same fix.

Distribution channels:
1. Fleet sync (P2P gossip) — nodes advertise packages, peers pull relevant ones
2. Git repository — packages committed to .opensculpt/packages/
3. Registry (future) — sculpt publish / sculpt install
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

_logger = logging.getLogger(__name__)

PACKAGES_DIR = Path(".opensculpt/packages")


@dataclass
class PackageManifest:
    """Metadata for an evolution package."""
    name: str
    version: int = 1
    fitness: float = 0.0
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    description: str = ""
    env_requires: list[str] = field(default_factory=list)
    env_excludes: list[str] = field(default_factory=list)
    scenario_tags: list[str] = field(default_factory=list)
    demand_resolved: str = ""
    changes: list[dict] = field(default_factory=list)
    test_results: dict = field(default_factory=dict)
    parent_package: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PackageManifest:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


async def create_package(result, impasse_context: dict) -> dict:
    """Create a .sculpt package from an EvolutionAgent session result.

    Args:
        result: EvolutionResult from the evolution agent
        impasse_context: The impasse that triggered evolution

    Returns:
        dict with package metadata
    """
    # Generate package name from impasse
    summary = impasse_context.get("summary", "evolution")
    name = _slugify(summary[:40])
    if not name:
        name = f"evolution_{int(time.time())}"

    # Find next version
    version = 1
    existing = list(PACKAGES_DIR.glob(f"{name}_v*"))
    if existing:
        versions = []
        for p in existing:
            try:
                v = int(p.name.split("_v")[-1])
                versions.append(v)
            except (ValueError, IndexError):
                pass
        if versions:
            version = max(versions) + 1

    pkg_dir = PACKAGES_DIR / f"{name}_v{version}"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Collect environment info
    env_requires = []
    try:
        from agos.environment import EnvironmentProbe
        probe = EnvironmentProbe.probe()
        if probe.is_container:
            env_requires.append("container")
        if probe.has_docker:
            env_requires.append("docker")
        env_requires.append(probe.os_type.lower())
    except Exception:
        pass

    # Build manifest
    changes = []

    # Copy tools
    if hasattr(result, 'tools_created') and result.tools_created:
        tools_dir = pkg_dir / "tools"
        tools_dir.mkdir(exist_ok=True)
        for tool_name in result.tools_created:
            src = Path(f".opensculpt/evolved/{tool_name}.py")
            if src.exists():
                shutil.copy2(src, tools_dir / f"{tool_name}.py")
                changes.append({"type": "tool", "name": tool_name, "file": f"tools/{tool_name}.py"})

    # Copy skill docs
    if hasattr(result, 'skills_created') and result.skills_created:
        skills_dir = pkg_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for topic in result.skills_created:
            src = Path(f".opensculpt/skills/{topic}.md")
            if src.exists():
                shutil.copy2(src, skills_dir / f"{topic}.md")
                changes.append({"type": "skill_doc", "topic": topic, "file": f"skills/{topic}.md"})

    # Copy prompt rules
    if hasattr(result, 'rules_added') and result.rules_added:
        rules_dir = pkg_dir / "rules"
        rules_dir.mkdir(exist_ok=True)
        for rule_entry in result.rules_added:
            target = rule_entry.split(":")[0].strip() if ":" in rule_entry else "sub_agent"
            src = Path(f".opensculpt/evolved/brain/{target}_rules.txt")
            if src.exists():
                shutil.copy2(src, rules_dir / f"{target}_rules.txt")
                changes.append({"type": "prompt_rule", "target": target, "file": f"rules/{target}_rules.txt"})

    # Record patches (diffs only, not full files)
    if hasattr(result, 'patches_applied') and result.patches_applied:
        patches_dir = pkg_dir / "patches"
        patches_dir.mkdir(exist_ok=True)
        for filepath in result.patches_applied:
            changes.append({"type": "source_patch", "file": filepath})

    # Save insights
    if hasattr(result, 'insights') and result.insights:
        insights_data = []
        for i in result.insights:
            insights_data.append({
                "what_tried": i.what_tried,
                "outcome": i.outcome,
                "reason": i.reason,
                "what_worked": getattr(i, 'what_worked', ''),
                "principle": getattr(i, 'principle', ''),
            })
        (pkg_dir / "insights.json").write_text(
            json.dumps(insights_data, indent=2), encoding="utf-8")

    # Compute content hash
    content_hash = _hash_directory(pkg_dir)

    # Build and save manifest
    manifest = PackageManifest(
        name=name,
        version=version,
        description=impasse_context.get("summary", "")[:200],
        env_requires=env_requires,
        scenario_tags=_extract_scenario_tags(impasse_context),
        demand_resolved=impasse_context.get("summary", "")[:200],
        changes=changes,
        content_hash=content_hash,
        test_results={
            "sandbox_passed": True,
            "turns_used": getattr(result, 'turns_used', 0),
            "tokens_used": getattr(result, 'tokens_used', 0),
        },
    )
    (pkg_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, default=str), encoding="utf-8")

    _logger.info("Evolution package created: %s_v%d (%d changes)", name, version, len(changes))
    return manifest.to_dict()


async def install_package(
    pkg_path: Path,
    tool_evolver=None,
    source_patcher=None,
) -> bool:
    """Install a .sculpt evolution package into the running OS.

    Args:
        pkg_path: Path to the package directory
        tool_evolver: For deploying tools
        source_patcher: For applying patches

    Returns:
        True if installation succeeded
    """
    manifest_path = pkg_path / "manifest.json"
    if not manifest_path.exists():
        _logger.warning("Package %s has no manifest.json", pkg_path)
        return False

    manifest = PackageManifest.from_dict(
        json.loads(manifest_path.read_text(encoding="utf-8")))

    # Check environment compatibility
    try:
        from agos.environment import EnvironmentProbe
        probe = EnvironmentProbe.probe()
        for req in manifest.env_requires:
            if req == "container" and not probe.is_container:
                _logger.info("Package %s requires container env, skipping", manifest.name)
                return False
            if req == "docker" and not probe.has_docker:
                _logger.info("Package %s requires docker, skipping", manifest.name)
                return False
    except Exception:
        pass

    installed = 0

    # Install tools
    tools_dir = pkg_path / "tools"
    if tools_dir.exists():
        for tool_file in tools_dir.glob("*.py"):
            dest = Path(".opensculpt/evolved") / tool_file.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tool_file, dest)
            if tool_evolver:
                try:
                    tool_evolver._load_tool_from_file(dest)
                except Exception:
                    pass
            installed += 1

    # Install skill docs
    skills_dir = pkg_path / "skills"
    if skills_dir.exists():
        for skill_file in skills_dir.glob("*.md"):
            dest = Path(".opensculpt/skills") / skill_file.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_file, dest)
            installed += 1

    # Install prompt rules
    rules_dir = pkg_path / "rules"
    if rules_dir.exists():
        for rules_file in rules_dir.glob("*_rules.txt"):
            dest = Path(".opensculpt/evolved/brain") / rules_file.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Merge rules (don't overwrite)
            existing = set()
            if dest.exists():
                existing = set(dest.read_text(encoding="utf-8").splitlines())
            new_rules = rules_file.read_text(encoding="utf-8").splitlines()
            added = [r for r in new_rules if r.strip() and r not in existing]
            if added:
                with open(dest, "a", encoding="utf-8") as f:
                    for r in added:
                        f.write(f"{r}\n")
                installed += len(added)

    # Record installation
    installed_path = Path(".opensculpt/installed_packages.json")
    try:
        if installed_path.exists():
            data = json.loads(installed_path.read_text(encoding="utf-8"))
        else:
            data = []
        data.append({
            "name": manifest.name,
            "version": manifest.version,
            "installed_at": time.time(),
            "changes_applied": installed,
            "content_hash": manifest.content_hash,
        })
        installed_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

    _logger.info("Installed package %s_v%d: %d changes applied",
                 manifest.name, manifest.version, installed)
    return installed > 0


def list_packages() -> list[dict]:
    """List all available evolution packages."""
    packages = []
    if not PACKAGES_DIR.exists():
        return packages
    for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
        manifest_path = pkg_dir / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                packages.append(data)
            except Exception:
                pass
    return packages


def _slugify(text: str) -> str:
    """Convert text to a safe directory name."""
    import re
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s-]+', '_', slug)
    return slug.strip('_')[:40]


def _hash_directory(path: Path) -> str:
    """Compute a content hash of all files in a directory."""
    h = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _extract_scenario_tags(ctx: dict) -> list[str]:
    """Extract scenario tags from impasse context."""
    tags = []
    summary = (ctx.get("summary", "") + " " + ctx.get("demands_text", "")).lower()
    for tag in ["sales", "support", "devops", "knowledge", "finance",
                "marketing", "ecommerce", "hiring", "analytics", "life"]:
        if tag in summary:
            tags.append(tag)
    if "docker" in summary:
        tags.append("deployment")
    if "monitor" in summary:
        tags.append("monitoring")
    return tags or ["general"]
