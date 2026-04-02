"""Vibe coding tool detection — find what AI coding tools are installed.

Detects CLI tools on PATH, IDE extensions, config directories, install paths,
and .app bundles. Cross-platform: Windows, macOS, Linux, Docker/container.

Used by the setup wizard, nudge endpoint, and environment probe
to know which tools the user actually has available for meta-evolution.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)
_cached: Optional[list["VibeTool"]] = None

_SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"


@dataclass
class VibeTool:
    """A detected vibe coding tool."""
    name: str               # e.g. "claude_code", "cursor", "aider"
    label: str              # e.g. "Claude Code", "Cursor", "Aider"
    category: str           # "cli", "ide", "extension"
    installed: bool = False
    confidence: str = ""    # "high" (exe/process), "medium" (extension), "low" (config dir only)
    path: str = ""          # binary path or extension dir
    version: str = ""
    how_to_use: str = ""    # instruction for the nudge UI
    config_dir: str = ""    # e.g. ~/.claude, ~/.cursor

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "category": self.category,
            "installed": self.installed,
            "confidence": self.confidence,
            "path": self.path,
            "version": self.version,
            "how_to_use": self.how_to_use,
            "config_dir": self.config_dir,
        }


# ── Tool definitions ────────────────────────────────────────────

_TOOL_DEFS: list[dict] = [
    {
        "name": "claude_code",
        "label": "Claude Code",
        "category": "cli",
        "cli_names": ["claude"],
        "config_dirs": [".claude"],
        "win_paths": ["{APPDATA}/Claude/claude-code"],
        "mac_paths": [
            "~/Library/Application Support/Claude/claude-code",
            "/usr/local/bin/claude",
        ],
        "linux_paths": ["/usr/local/bin/claude"],
        "exe_stems": ["claude"],
        "how_to_use": "Open Claude Code in this repo. Paste the prompt below.",
        "safe_version": True,  # --version won't launch GUI
    },
    {
        "name": "cursor",
        "label": "Cursor",
        "category": "ide",
        "cli_names": ["cursor"],
        "config_dirs": [".cursor"],
        "win_paths": ["{LOCALAPPDATA}/Programs/cursor"],
        "mac_apps": ["Cursor.app"],
        "linux_paths": [
            "/opt/cursor", "/opt/Cursor",
            "/snap/cursor/current",
            "{HOME}/cursor.AppImage",
            "/usr/share/cursor",
        ],
        "exe_stems": ["cursor"],
        "how_to_use": "Open Cursor. Press Cmd+L (or Ctrl+L). Paste the prompt.",
        "safe_version": False,  # may launch GUI on macOS
    },
    {
        "name": "windsurf",
        "label": "Windsurf",
        "category": "ide",
        "cli_names": ["windsurf"],
        "config_dirs": [".windsurf", ".codeium/windsurf"],
        "win_paths": ["{LOCALAPPDATA}/Programs/windsurf"],
        "mac_apps": ["Windsurf.app"],
        "linux_paths": [
            "/opt/windsurf", "/opt/Windsurf",
            "/snap/windsurf/current",
            "/usr/share/windsurf",
        ],
        "exe_stems": ["windsurf"],
        "how_to_use": "Open Windsurf. Use the AI panel. Paste the prompt.",
        "safe_version": False,
    },
    {
        "name": "aider",
        "label": "Aider",
        "category": "cli",
        "cli_names": ["aider"],
        "config_dirs": [".aider"],
        "how_to_use": "Run: aider --read .opensculpt/DEMANDS.md",
        "safe_version": True,
    },
    {
        "name": "codex",
        "label": "OpenAI Codex CLI",
        "category": "cli",
        "cli_names": ["codex"],
        "config_dirs": [".codex"],
        "how_to_use": "Run: codex 'Fix OpenSculpt demands' in this directory.",
        "safe_version": True,
    },
    {
        "name": "github_copilot",
        "label": "GitHub Copilot",
        "category": "extension",
        "cli_names": [],
        "vscode_ext_ids": ["github.copilot", "github.copilot-chat"],
        "how_to_use": "Open VS Code with Copilot. Use @workspace with the prompt.",
    },
    {
        "name": "cline",
        "label": "Cline",
        "category": "extension",
        "cli_names": [],
        "vscode_ext_ids": ["saoudrizwan.claude-dev"],
        "how_to_use": "Open VS Code. Open Cline panel. Paste the prompt.",
    },
    {
        "name": "roo_code",
        "label": "Roo Code",
        "category": "extension",
        "cli_names": ["roo"],
        "vscode_ext_ids": ["rooveterinaryinc.roo-cline"],
        "how_to_use": "Open VS Code. Open Roo Code panel. Paste the prompt.",
        "safe_version": True,
    },
    {
        "name": "continue_dev",
        "label": "Continue",
        "category": "extension",
        "cli_names": [],
        "vscode_ext_ids": ["continue.continue"],
        "config_dirs": [".continue"],
        "how_to_use": "Open VS Code. Open Continue panel. Paste the prompt.",
    },
    {
        "name": "copilot_cli",
        "label": "GitHub Copilot CLI",
        "category": "cli",
        "cli_names": ["gh copilot"],  # gh extension — special case
        "how_to_use": "Run: gh copilot suggest 'Fix OpenSculpt demands'",
    },
    {
        "name": "gemini_cli",
        "label": "Gemini CLI",
        "category": "cli",
        "cli_names": ["gemini"],
        "config_dirs": [".gemini"],
        "how_to_use": "Run: gemini in this directory. Paste the prompt.",
        "safe_version": True,
    },
    {
        "name": "amp",
        "label": "Amp (Sourcegraph)",
        "category": "cli",
        "cli_names": ["amp"],
        "how_to_use": "Run: amp in this directory. Paste the prompt.",
        "safe_version": True,
    },
]


# ── Platform-aware detection helpers ────────────────────────────


def _get_extension_dirs() -> list[Path]:
    """Find all IDE extension directories (VS Code, Cursor, Windsurf)."""
    home = Path.home()
    candidates = [
        home / ".vscode" / "extensions",
        home / ".vscode-insiders" / "extensions",
        home / ".cursor" / "extensions",
        home / ".windsurf" / "extensions",
    ]
    # VS Code on macOS can also be here via Homebrew
    if _SYSTEM == "Darwin":
        candidates.append(home / ".vscode-server" / "extensions")
    # Linux remote-SSH / container scenario
    if _SYSTEM == "Linux":
        candidates.append(home / ".vscode-server" / "extensions")
    return [p for p in candidates if p.is_dir()]


def _check_vscode_extension(ext_ids: list[str]) -> tuple[bool, str, str]:
    """Check if a VS Code/Cursor/Windsurf extension is installed.

    Returns (found, path, version).
    """
    import re
    for ext_dir in _get_extension_dirs():
        try:
            for entry in ext_dir.iterdir():
                name_lower = entry.name.lower()
                for ext_id in ext_ids:
                    if name_lower.startswith(ext_id.lower()):
                        # Extract version: "github.copilot-chat-0.42.2" -> "0.42.2"
                        # Match the last segment that looks like a semver
                        version = ""
                        m = re.search(r'-(\d+\.\d+[\.\d]*)', name_lower)
                        if m:
                            version = m.group(1)
                        return True, str(entry), version
        except (PermissionError, OSError):
            continue
    return False, "", ""


def _check_cli(cli_names: list[str], safe_version: bool = True) -> tuple[bool, str, str]:
    """Check if a CLI tool is on PATH. Returns (found, path, version)."""
    for name in cli_names:
        if " " in name:
            # Special case like "gh copilot" — check gh exists + extension
            parts = name.split()
            base = shutil.which(parts[0])
            if base:
                try:
                    r = subprocess.run(
                        [base, *parts[1:], "--help"],
                        capture_output=True, timeout=5,
                    )
                    if r.returncode == 0:
                        return True, base, ""
                except Exception:
                    pass
            continue

        found = shutil.which(name)
        if found:
            version = ""
            if safe_version:
                try:
                    r = subprocess.run(
                        [found, "--version"],
                        capture_output=True, timeout=5,
                    )
                    if r.returncode == 0:
                        version = r.stdout.decode("utf-8", errors="replace").strip()[:100]
                except Exception:
                    pass
            return True, found, version
    return False, "", ""


def _check_config_dir(dir_names: list[str]) -> tuple[bool, str]:
    """Check if a config directory exists in home."""
    home = Path.home()
    for name in dir_names:
        p = home / name
        if p.is_dir():
            return True, str(p)
    return False, ""


def _expand_path(tmpl: str) -> Path:
    """Expand env vars and ~ in a path template."""
    expanded = tmpl.format(
        APPDATA=os.environ.get("APPDATA", ""),
        LOCALAPPDATA=os.environ.get("LOCALAPPDATA", ""),
        HOME=str(Path.home()),
    )
    return Path(os.path.expanduser(expanded))


def _check_platform_paths(paths: list[str], exe_stems: list[str]) -> tuple[bool, str]:
    """Check platform-specific install paths. Case-insensitive on Windows."""
    for tmpl in paths:
        p = _expand_path(tmpl)

        # Direct file check (e.g. /usr/local/bin/claude, ~/cursor.AppImage)
        if p.is_file():
            return True, str(p)

        # Directory check
        if p.is_dir():
            exe_path = _find_exe_in_dir(p, exe_stems)
            return True, exe_path or str(p)

        # Windows case-insensitive fallback: list parent and match
        if _SYSTEM == "Windows":
            parent = p.parent
            target = p.name.lower()
            if parent.is_dir():
                try:
                    for entry in parent.iterdir():
                        if entry.name.lower() == target and entry.is_dir():
                            exe_path = _find_exe_in_dir(entry, exe_stems)
                            return True, exe_path or str(entry)
                except (PermissionError, OSError):
                    pass
    return False, ""


def _check_mac_apps(app_names: list[str]) -> tuple[bool, str]:
    """Check /Applications and ~/Applications for .app bundles on macOS."""
    if _SYSTEM != "Darwin":
        return False, ""
    for app_name in app_names:
        for apps_dir in [Path("/Applications"), Path.home() / "Applications"]:
            app_path = apps_dir / app_name
            if app_path.exists():
                # Try to find the CLI inside the .app bundle
                macos_dir = app_path / "Contents" / "Resources" / "app" / "bin"
                if macos_dir.is_dir():
                    # e.g. Cursor.app/Contents/Resources/app/bin/cursor
                    for f in macos_dir.iterdir():
                        if f.is_file() and os.access(str(f), os.X_OK):
                            return True, str(f)
                # Fallback: MacOS binary
                macos_bin = app_path / "Contents" / "MacOS"
                if macos_bin.is_dir():
                    for f in macos_bin.iterdir():
                        if f.is_file() and os.access(str(f), os.X_OK):
                            return True, str(f)
                # Just report the .app path
                return True, str(app_path)
    return False, ""


def _find_exe_in_dir(directory: Path, exe_stems: list[str]) -> str:
    """Find an executable in a directory, checking versioned subdirs too."""
    if not exe_stems:
        return ""
    stems_lower = {s.lower() for s in exe_stems}
    ext = ".exe" if _SYSTEM == "Windows" else ""

    # Check versioned subdirs first (e.g., claude-code/2.1.87/claude.exe)
    try:
        subdirs = sorted(
            [d for d in directory.iterdir() if d.is_dir()],
            reverse=True,
        )
        for subdir in subdirs[:5]:
            result = _scan_dir_for_exe(subdir, stems_lower, ext)
            if result:
                return result
    except (PermissionError, OSError):
        pass

    # Check top-level
    result = _scan_dir_for_exe(directory, stems_lower, ext)
    return result or ""


def _scan_dir_for_exe(directory: Path, stems_lower: set[str], ext: str) -> str:
    """Scan a single directory for a matching executable."""
    try:
        for candidate in directory.iterdir():
            if not candidate.is_file():
                continue
            if ext and candidate.suffix.lower() == ext and candidate.stem.lower() in stems_lower:
                return str(candidate)
            if not ext and candidate.stem.lower() in stems_lower and os.access(str(candidate), os.X_OK):
                return str(candidate)
    except (PermissionError, OSError):
        pass
    return ""


# ── Main detection ──────────────────────────────────────────────


def detect_vibe_tools(use_cache: bool = True) -> list[VibeTool]:
    """Detect all vibe coding tools installed on this machine.

    Cross-platform: Windows, macOS, Linux, Docker/container.
    Cached after first call (pass use_cache=False to force re-scan).
    """
    global _cached
    if use_cache and _cached is not None:
        return _cached

    tools: list[VibeTool] = []

    for defn in _TOOL_DEFS:
        tool = VibeTool(
            name=defn["name"],
            label=defn["label"],
            category=defn["category"],
            how_to_use=defn.get("how_to_use", ""),
        )
        safe_version = defn.get("safe_version", False)
        exe_stems = defn.get("exe_stems", [])

        # 1. CLI on PATH (cross-platform) — HIGH confidence
        cli_names = defn.get("cli_names", [])
        if cli_names:
            found, path, version = _check_cli(cli_names, safe_version)
            if found:
                tool.installed = True
                tool.confidence = "high"
                tool.path = path
                tool.version = version

        # 2. VS Code / Cursor / Windsurf extensions (cross-platform) — MEDIUM confidence
        ext_ids = defn.get("vscode_ext_ids", [])
        if ext_ids and not tool.installed:
            found, path, version = _check_vscode_extension(ext_ids)
            if found:
                tool.installed = True
                tool.confidence = "medium"
                tool.path = path
                if version:
                    tool.version = version

        # 3. macOS .app bundles — HIGH confidence
        mac_apps = defn.get("mac_apps", [])
        if mac_apps and not tool.installed:
            found, path = _check_mac_apps(mac_apps)
            if found:
                tool.installed = True
                tool.confidence = "high"
                tool.path = path

        # 4. Platform-specific install paths — HIGH if exe found, LOW if dir only
        #    A directory with no exe is likely an uninstall remnant
        platform_paths: list[str] = []
        if _SYSTEM == "Windows":
            platform_paths = defn.get("win_paths", [])
        elif _SYSTEM == "Darwin":
            platform_paths = defn.get("mac_paths", [])
        elif _SYSTEM == "Linux":
            platform_paths = defn.get("linux_paths", [])
        if platform_paths and not tool.installed:
            found, path = _check_platform_paths(platform_paths, exe_stems)
            if found:
                tool.installed = True
                tool.confidence = "high" if Path(path).is_file() else "low"
                tool.path = path

        # 5. Config directories (cross-platform, last — weakest signal)
        #    Config dir alone = LOW confidence (could be leftover from uninstall)
        config_dirs = defn.get("config_dirs", [])
        if config_dirs:
            found, path = _check_config_dir(config_dirs)
            if found:
                tool.config_dir = path
                if not tool.installed:
                    tool.installed = True
                    tool.confidence = "low"

        tools.append(tool)

    installed = [t for t in tools if t.installed]
    _logger.info(
        "Vibe tools detected: %d/%d installed [%s] — %s",
        len(installed), len(tools), _SYSTEM,
        ", ".join(t.label for t in installed) or "none",
    )

    _cached = tools
    return tools


def get_installed_tools(min_confidence: str = "medium") -> list[VibeTool]:
    """Return tools that are installed with at least the given confidence.

    Confidence levels: "high" > "medium" > "low"
    Default filters out "low" (config-dir-only detections that may be uninstall leftovers).
    """
    levels = {"high": 3, "medium": 2, "low": 1, "": 0}
    min_level = levels.get(min_confidence, 0)
    return [
        t for t in detect_vibe_tools()
        if t.installed and levels.get(t.confidence, 0) >= min_level
    ]


def get_tool_by_name(name: str) -> VibeTool | None:
    """Lookup a specific tool by its internal name."""
    for t in detect_vibe_tools():
        if t.name == name:
            return t
    return None


def summary() -> str:
    """Human-readable summary for LLM prompts."""
    installed = get_installed_tools(min_confidence="medium")
    if not installed:
        return "VIBE CODING TOOLS: None detected."

    lines = ["VIBE CODING TOOLS:"]
    for t in installed:
        parts = [f"  - {t.label}"]
        if t.version:
            parts.append(f"v{t.version}")
        if t.category == "extension":
            parts.append("[VS Code extension]")
        lines.append(" ".join(parts))

    # Mention low-confidence ones separately
    maybe = [t for t in detect_vibe_tools() if t.installed and t.confidence == "low"]
    if maybe:
        lines.append("  Maybe installed (config dir found, binary not): " +
                      ", ".join(t.label for t in maybe))
    return "\n".join(lines)


def reset_cache():
    """Force re-detection on next call."""
    global _cached
    _cached = None
