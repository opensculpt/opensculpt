"""Release management — version bumping and changelog generation.

Supports semantic versioning: MAJOR.MINOR.PATCH
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class BumpType(str, Enum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


def get_current_version(pyproject_path: Path | None = None) -> str:
    """Read current version from pyproject.toml."""
    path = pyproject_path or Path("pyproject.toml")
    content = path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def bump_version(current: str, bump_type: BumpType) -> str:
    """Calculate the next version."""
    parts = current.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {current}")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if bump_type == BumpType.MAJOR:
        return f"{major + 1}.0.0"
    elif bump_type == BumpType.MINOR:
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def update_version_in_pyproject(
    new_version: str, pyproject_path: Path | None = None
) -> None:
    """Update version in pyproject.toml."""
    path = pyproject_path or Path("pyproject.toml")
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    path.write_text(content, encoding="utf-8")


def generate_changelog_entry(
    version: str,
    git_log: str = "",
    evolution_proposals: list[dict[str, Any]] | None = None,
) -> str:
    """Generate a changelog entry for a version."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## [{version}] -- {date}", ""]

    if git_log:
        lines.append("### Changes")
        for line in git_log.strip().split("\n"):
            if line.strip():
                lines.append(f"- {line.strip()}")
        lines.append("")

    if evolution_proposals:
        lines.append("### Evolution Proposals Applied")
        for p in evolution_proposals:
            desc = p.get("description", "")[:80]
            lines.append(f"- **{p['technique']}** ({p['module']}): {desc}")
        lines.append("")

    return "\n".join(lines)


def prepend_to_changelog(
    entry: str, changelog_path: Path | None = None
) -> None:
    """Prepend a new entry to the CHANGELOG.md file."""
    path = changelog_path or Path("CHANGELOG.md")

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        # Insert after the header line
        if existing.startswith("# Changelog"):
            header_end = existing.index("\n") + 1
            content = (
                existing[:header_end] + "\n" + entry + "\n" + existing[header_end:]
            )
        else:
            content = entry + "\n" + existing
    else:
        content = "# Changelog\n\n" + entry

    path.write_text(content, encoding="utf-8")
