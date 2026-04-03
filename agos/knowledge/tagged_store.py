"""Tagged knowledge store — scalable constraint & resolution management.

Instead of one flat .md file that breaks at 50+ users, knowledge is stored
in environment-tagged directories:

    .opensculpt/constraints/
    ├── _index.md          ← 1-line per constraint (LLM scans this first)
    ├── general.md         ← universal constraints
    ├── macos.md           ← macOS-specific
    ├── windows.md         ← Windows-specific
    ├── linux-debian.md    ← Debian/Ubuntu
    ├── docker.md          ← Docker environments
    ├── no-docker.md       ← No Docker
    ├── corporate-proxy.md ← Proxy/firewall
    ├── low-memory.md      ← <1GB RAM
    ├── arm64.md           ← ARM architectures
    └── container.md       ← Running inside container

    .opensculpt/resolutions/
    ├── _index.md          ← symptom fingerprint → file
    ├── deployment.md      ← service deployment failures
    ├── networking.md      ← connection/proxy/port
    ├── packages.md        ← pip/apt/brew install
    ├── docker.md          ← container-specific
    ├── auth.md            ← authentication/credentials
    └── general.md         ← uncategorized

The OS loads ONLY files matching the current environment.
A Raspberry Pi never reads macos.md. A Mac never reads arm64.md.

Scales to 10,000+ constraints because each node reads 3-5 files (~60-120 entries).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

# ── Environment tag generation ────────────────────────────────────

# Maps EnvironmentProbe fields → constraint tags
_OS_TAG_MAP = {
    "Darwin": "macos",
    "Windows": "windows",
    "Linux": "linux",
}

_LINUX_DISTRO_FILES = {
    "debian": "linux-debian",
    "ubuntu": "linux-debian",
    "alpine": "linux-alpine",
    "centos": "linux-rhel",
    "rhel": "linux-rhel",
    "fedora": "linux-rhel",
    "arch": "linux-arch",
}


def environment_tags(env=None) -> list[str]:
    """Generate constraint tags from the current environment.

    Returns tags like ["linux", "linux-debian", "docker", "low-memory", "general"].
    These determine which constraint/resolution files get loaded.
    """
    if env is None:
        try:
            from agos.environment import EnvironmentProbe
            env = EnvironmentProbe.probe()
        except Exception:
            return ["general"]

    tags = ["general"]

    # OS type
    os_tag = _OS_TAG_MAP.get(env.os_name, "linux")
    tags.append(os_tag)

    # Linux distro detection
    if env.os_name == "Linux":
        try:
            os_release = Path("/etc/os-release").read_text().lower()
            for distro_key, distro_tag in _LINUX_DISTRO_FILES.items():
                if distro_key in os_release:
                    tags.append(distro_tag)
                    break
        except Exception:
            pass

    # Docker / container
    if env.docker_available:
        tags.append("docker")
    else:
        tags.append("no-docker")

    if env.in_container:
        tags.append("container")

    # Architecture
    if env.os_arch in ("aarch64", "arm64", "armv7l"):
        tags.append("arm64")

    # Resource constraints
    if env.memory_total_mb and env.memory_total_mb < 1024:
        tags.append("low-memory")

    # Network/proxy (detected if HTTP_PROXY or HTTPS_PROXY set)
    import os
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy"):
        tags.append("corporate-proxy")

    return tags


# ── Fingerprinting (dedup) ────────────────────────────────────────

def fingerprint(text: str) -> str:
    """Normalize a constraint/resolution to a dedup key.

    "proxy at 10.0.0.1:8080 needs NTLM" → "needs_ntlm_proxy"
    "proxy at 192.168.1.1:3128 needs auth" → "auth_needs_proxy"

    Same fingerprint = same knowledge, skip duplicate.
    """
    # Strip IPs, ports, paths, numbers
    cleaned = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b', '', text)
    cleaned = re.sub(r':\d+', '', cleaned)
    cleaned = re.sub(r'[/\\][\w/.\\-]+', '', cleaned)
    cleaned = re.sub(r'\b\d+\b', '', cleaned)

    # Extract meaningful words (>3 chars)
    words = set(w.lower() for w in re.findall(r'[a-zA-Z]{4,}', cleaned))

    # Remove noise words
    noise = {"this", "that", "with", "from", "have", "been", "will",
             "should", "could", "would", "need", "when", "does", "make"}
    words -= noise

    return "_".join(sorted(words)[:6])


# ── Tag classification (which file should a constraint go in?) ────

# Keyword → tag mapping for auto-classification
_CONSTRAINT_TAG_KEYWORDS = {
    "macos": ["macos", "darwin", "brew", "homebrew", "airplay", "apple", "xcode"],
    "windows": ["windows", "winget", "chocolatey", "powershell", "wsl", "cmd.exe"],
    "linux-debian": ["apt-get", "apt", "dpkg", "ubuntu", "debian"],
    "linux-rhel": ["yum", "dnf", "rpm", "centos", "rhel", "fedora"],
    "linux-alpine": ["apk", "alpine", "musl"],
    "docker": ["docker", "dockerfile", "compose", "container image", "docker-compose"],
    "no-docker": ["no docker", "without docker", "docker not available", "docker unavailable"],
    "corporate-proxy": ["proxy", "firewall", "ntlm", "corporate", "vpn"],
    "arm64": ["arm64", "aarch64", "raspberry", "armv7"],
    "low-memory": ["memory", "oom", "out of memory", "swap", "low ram"],
    "container": ["container", "cgroup", "inside container", "dockerenv"],
}


def classify_tag(text: str, env_tags: list[str] | None = None) -> str:
    """Determine which tag file a constraint belongs in.

    Priority: keyword match > current environment > "general"
    """
    text_lower = text.lower()

    # Try keyword matching first
    for tag, keywords in _CONSTRAINT_TAG_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return tag

    # Fall back to the most specific current environment tag
    if env_tags:
        # Prefer specific tags over general
        specific = [t for t in env_tags if t not in ("general", "no-docker")]
        if specific:
            return specific[0]

    return "general"


# ── Tagged Constraint Store ───────────────────────────────────────

class TaggedConstraintStore:
    """Environment-tagged constraint storage.

    Constraints are stored in .opensculpt/constraints/{tag}.md files.
    Only files matching the current environment are loaded.
    """

    def __init__(self, base_dir: Path | str | None = None):
        if base_dir is None:
            from agos.config import settings
            base_dir = settings.workspace_dir
        self._base = Path(base_dir)
        self._constraints_dir = self._base / "constraints"
        self._constraints_dir.mkdir(parents=True, exist_ok=True)

        # Ensure index exists
        index = self._constraints_dir / "_index.md"
        if not index.exists():
            index.write_text("# Constraint Index\n\n", encoding="utf-8")

    def add(self, text: str, env_tags: list[str] | None = None, source: str = "") -> bool:
        """Add a constraint to the appropriate tagged file.

        Returns True if added, False if duplicate.
        """
        fp = fingerprint(text)
        tag = classify_tag(text, env_tags)
        tag_file = self._constraints_dir / f"{tag}.md"

        # Check dedup via index
        index_path = self._constraints_dir / "_index.md"
        index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        if fp and fp in index_text:
            return False  # Duplicate

        # Check dedup via exact match in tag file
        existing = tag_file.read_text(encoding="utf-8") if tag_file.exists() else ""
        if text.strip() in existing:
            return False

        # Write to tag file
        header = f"# {tag} constraints\n" if not tag_file.exists() else ""
        with open(tag_file, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write(f"\n- {text.strip()}")
            if source:
                f.write(f"  <!-- source: {source} -->")
            f.write("\n")

        # Update index
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(f"- [{tag}] {text.strip()[:80]}  <!-- fp:{fp} -->\n")

        _logger.info("Constraint added [%s]: %s", tag, text[:60])
        return True

    def load(self, env_tags: list[str] | None = None, max_chars: int = 6000) -> str:
        """Load constraints matching the current environment.

        Returns merged text from all matching tag files, within char budget.
        """
        if env_tags is None:
            env_tags = environment_tags()

        texts = []
        total = 0

        for tag in env_tags:
            tag_file = self._constraints_dir / f"{tag}.md"
            if tag_file.exists():
                content = tag_file.read_text(encoding="utf-8").strip()
                if content and len(content) > 10:
                    if total + len(content) > max_chars:
                        # Partial read — take what fits
                        remaining = max_chars - total
                        if remaining > 100:
                            texts.append(content[:remaining])
                        break
                    texts.append(content)
                    total += len(content)

        return "\n\n".join(texts) if texts else ""

    def load_index(self, max_chars: int = 4000) -> str:
        """Load just the index — one line per constraint for broad scanning."""
        index_path = self._constraints_dir / "_index.md"
        if index_path.exists():
            text = index_path.read_text(encoding="utf-8")
            return text[:max_chars]
        return ""

    def count(self) -> int:
        """Total constraints across all tag files."""
        total = 0
        for f in self._constraints_dir.glob("*.md"):
            if f.name == "_index.md":
                continue
            total += sum(1 for line in f.read_text(encoding="utf-8").split("\n")
                        if line.strip().startswith("- "))
        return total

    def migrate_flat_file(self, flat_path: Path | None = None) -> int:
        """Migrate an existing flat constraints.md into tagged files.

        Returns number of constraints migrated.
        """
        if flat_path is None:
            flat_path = self._base / "constraints.md"
        if not flat_path.exists():
            return 0

        text = flat_path.read_text(encoding="utf-8")
        migrated = 0
        env_tags = environment_tags()

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                constraint_text = line[2:].strip()
                if constraint_text and self.add(constraint_text, env_tags):
                    migrated += 1

        if migrated > 0:
            # Rename old file as backup
            backup = flat_path.with_suffix(".md.migrated")
            flat_path.rename(backup)
            _logger.info("Migrated %d constraints from flat file, backup at %s", migrated, backup)

        return migrated


# ── Tagged Resolution Store ───────────────────────────────────────

# Resolution categories
_RESOLUTION_CATEGORIES = {
    "deployment": ["deploy", "install", "setup", "configure", "service", "start", "run"],
    "networking": ["connection", "proxy", "port", "firewall", "timeout", "network", "dns", "ssl", "tls"],
    "packages": ["pip", "apt", "brew", "npm", "install", "package", "dependency", "module"],
    "docker": ["docker", "container", "compose", "image", "dockerfile"],
    "auth": ["auth", "login", "credential", "password", "token", "key", "permission", "denied"],
    "database": ["database", "sql", "sqlite", "mysql", "postgres", "redis", "migration"],
}


def classify_resolution(symptom: str) -> str:
    """Determine which resolution file a symptom belongs in."""
    symptom_lower = symptom.lower()
    for category, keywords in _RESOLUTION_CATEGORIES.items():
        if any(kw in symptom_lower for kw in keywords):
            return category
    return "general"


class TaggedResolutionStore:
    """Environment-tagged resolution storage.

    Resolutions stored in .opensculpt/resolutions/{category}.md files.
    Lookup is fingerprint-based: normalize symptom → scan index → read file.
    """

    def __init__(self, base_dir: Path | str | None = None):
        if base_dir is None:
            from agos.config import settings
            base_dir = settings.workspace_dir
        self._base = Path(base_dir)
        self._resolutions_dir = self._base / "resolutions"
        self._resolutions_dir.mkdir(parents=True, exist_ok=True)

        index = self._resolutions_dir / "_index.md"
        if not index.exists():
            index.write_text("# Resolution Index\n\n", encoding="utf-8")

    def add(self, symptom: str, fix: str, root_cause: str = "", source: str = "") -> bool:
        """Add a resolution pattern. Returns True if new, False if duplicate."""
        fp = fingerprint(symptom)
        category = classify_resolution(symptom)

        # Dedup via index fingerprint
        index_path = self._resolutions_dir / "_index.md"
        index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        if fp and fp in index_text:
            return False

        cat_file = self._resolutions_dir / f"{category}.md"
        header = f"# {category} resolutions\n" if not cat_file.exists() else ""

        with open(cat_file, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write(f"\n## {symptom[:80]}\n")
            if root_cause:
                f.write(f"- Root cause: {root_cause}\n")
            f.write(f"- Fix: {fix}\n")
            if source:
                f.write(f"- Source: {source}\n")
            f.write(f"- Confirmed: {datetime.now().isoformat()}\n")

        # Update index
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(f"- [{category}] {symptom[:60]} → {fix[:40]}  <!-- fp:{fp} -->\n")

        _logger.info("Resolution added [%s]: %s → %s", category, symptom[:40], fix[:40])
        return True

    def lookup(self, symptom: str) -> str | None:
        """Fast fingerprint-based resolution lookup.

        Returns the fix text if found, None if no match.
        Much more precise than the old 2-word overlap matching.
        """
        fp = fingerprint(symptom)
        if not fp:
            return None

        index_path = self._resolutions_dir / "_index.md"
        if not index_path.exists():
            return None

        index_text = index_path.read_text(encoding="utf-8")

        # Search by fingerprint in index
        for line in index_text.split("\n"):
            if f"fp:{fp}" in line:
                # Extract the fix hint from the index line
                arrow_idx = line.find("→")
                if arrow_idx > 0:
                    fix_hint = line[arrow_idx + 1:].split("<!--")[0].strip()
                    return fix_hint

        # Fallback: partial fingerprint match (4+ shared words)
        fp_words = set(fp.split("_"))
        if len(fp_words) < 3:
            return None

        for line in index_text.split("\n"):
            fp_match = re.search(r'fp:(\w+)', line)
            if fp_match:
                existing_words = set(fp_match.group(1).split("_"))
                overlap = len(fp_words & existing_words)
                if overlap >= 3 and overlap / max(len(fp_words), len(existing_words)) > 0.6:
                    arrow_idx = line.find("→")
                    if arrow_idx > 0:
                        return line[arrow_idx + 1:].split("<!--")[0].strip()

        return None

    def load_category(self, category: str, max_chars: int = 4000) -> str:
        """Load all resolutions in a category."""
        cat_file = self._resolutions_dir / f"{category}.md"
        if cat_file.exists():
            return cat_file.read_text(encoding="utf-8")[:max_chars]
        return ""

    def count(self) -> int:
        """Total resolutions across all files."""
        total = 0
        for f in self._resolutions_dir.glob("*.md"):
            if f.name == "_index.md":
                continue
            total += f.read_text(encoding="utf-8").count("\n## ")
        return total

    def migrate_flat_file(self, flat_path: Path | None = None) -> int:
        """Migrate existing flat resolutions.md into tagged files."""
        if flat_path is None:
            flat_path = self._base / "resolutions.md"
        if not flat_path.exists():
            return 0

        text = flat_path.read_text(encoding="utf-8")
        migrated = 0

        for section in text.split("\n## ")[1:]:
            lines = section.strip().split("\n")
            if not lines:
                continue
            symptom = lines[0].strip()
            fix = ""
            root_cause = ""
            for line in lines[1:]:
                if line.strip().startswith("- Fix:"):
                    fix = line.strip()[6:].strip()
                elif line.strip().startswith("- Root cause:"):
                    root_cause = line.strip()[13:].strip()

            if symptom and fix and self.add(symptom, fix, root_cause):
                migrated += 1

        if migrated > 0:
            backup = flat_path.with_suffix(".md.migrated")
            flat_path.rename(backup)
            _logger.info("Migrated %d resolutions from flat file, backup at %s", migrated, backup)

        return migrated
