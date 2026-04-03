"""WorkloadDiscovery — auto-detect and auto-install agent workloads.

Scans /workloads/ for agent projects, detects their type (Node.js, Go,
Python, Rust), installs dependencies, and registers them with the
ProcessManager for supervised execution.

This is the OS equivalent of an init system / systemd service discovery.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail


@dataclass
class WorkloadManifest:
    """Describes a discovered agent workload."""

    name: str
    path: str
    runtime: str  # nodejs|go|python|rust|unknown
    entry_point: list[str]  # Command to run
    install_cmd: list[str] | None  # Dependency install command
    installed: bool = False
    install_error: str = ""
    memory_limit_mb: float = 256.0
    description: str = ""


# Detection rules for each runtime
_RUNTIME_DETECTORS = [
    {
        "runtime": "nodejs",
        "markers": ["package.json"],
        "install": lambda p: ["npm", "install", "--ignore-scripts"],
        "entry": lambda p: _node_entry(p),
        "memory_limit": 384.0,
    },
    {
        "runtime": "go",
        "markers": ["go.mod"],
        "install": lambda p: ["go", "build", "./..."],
        "entry": lambda p: _go_entry(p),
        "memory_limit": 128.0,
    },
    {
        "runtime": "python",
        "markers": ["pyproject.toml", "setup.py", "requirements.txt"],
        "install": lambda p: _python_install(p),
        "entry": lambda p: _python_entry(p),
        "memory_limit": 256.0,
    },
    {
        "runtime": "rust",
        "markers": ["Cargo.toml"],
        "install": lambda p: ["cargo", "build", "--release"],
        "entry": lambda p: _rust_entry(p),
        "memory_limit": 128.0,
    },
]


def _node_entry(path: Path) -> list[str]:
    """Determine Node.js entry point."""
    pkg_json = path / "package.json"
    if pkg_json.exists():
        import json
        try:
            pkg = json.loads(pkg_json.read_text())
            # Check for start script
            scripts = pkg.get("scripts", {})
            if "start" in scripts:
                return ["npm", "start"]
            # Check for main field
            main = pkg.get("main", "")
            if main:
                return ["node", main]
        except Exception:
            pass
    # Fallback: look for index.js or server.js
    for candidate in ["index.js", "server.js", "src/index.js", "dist/index.js"]:
        if (path / candidate).exists():
            return ["node", candidate]
    return ["node", "."]


def _go_entry(path: Path) -> list[str]:
    """Determine Go entry point — run the built binary."""
    name = path.name
    # After go build, binary is in current dir
    binary = path / name
    if binary.exists():
        return [str(binary)]
    # Try to find any binary
    for f in path.iterdir():
        if f.is_file() and os.access(f, os.X_OK) and not f.suffix:
            return [str(f)]
    return ["go", "run", "."]


def _python_install(path: Path) -> list[str]:
    """Determine Python install command."""
    import sys
    pip = [sys.executable, "-m", "pip"]
    req = path / "requirements.txt"
    if req.exists():
        # Skip if requirements.txt is empty or comments-only
        content = req.read_text(encoding="utf-8", errors="replace").strip()
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            return []  # Nothing to install
        return pip + ["install", "--no-cache-dir", "-r", "requirements.txt"]
    if (path / "pyproject.toml").exists():
        return pip + ["install", "--no-cache-dir", "-e", "."]
    return []  # No install needed


def _python_entry(path: Path) -> list[str]:
    """Determine Python entry point."""
    if (path / "main.py").exists():
        return ["python", "main.py"]
    if (path / "__main__.py").exists():
        return ["python", "."]
    # Check for module with __main__
    name = path.name.replace("-", "_")
    if (path / name / "__main__.py").exists():
        return ["python", "-m", name]
    return ["python", "-c", "print('No entry point found')"]


def _rust_entry(path: Path) -> list[str]:
    """Determine Rust entry point."""
    # After cargo build, binary is in target/release/
    name = path.name
    release = path / "target" / "release" / name
    debug = path / "target" / "debug" / name
    if release.exists():
        return [str(release)]
    if debug.exists():
        return [str(debug)]
    return ["cargo", "run", "--release"]


class WorkloadDiscovery:
    """Auto-discovers agent workloads and prepares them for execution."""

    def __init__(
        self,
        event_bus: EventBus,
        audit_trail: AuditTrail,
        workload_dir: str = "/workloads",
    ) -> None:
        self._bus = event_bus
        self._audit = audit_trail
        self._workload_dir = Path(workload_dir)
        self._manifests: dict[str, WorkloadManifest] = {}

    async def scan(self) -> list[WorkloadManifest]:
        """Scan workload directory for agent projects."""
        if not self._workload_dir.exists():
            return []

        discovered = []
        for entry in sorted(self._workload_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue

            manifest = self._detect_workload(entry)
            if manifest:
                self._manifests[manifest.name] = manifest
                discovered.append(manifest)

                await self._bus.emit("process.discovered", {
                    "name": manifest.name,
                    "runtime": manifest.runtime,
                    "path": manifest.path,
                    "entry_point": " ".join(manifest.entry_point),
                }, source="workload_discovery")

        return discovered

    def _detect_workload(self, path: Path) -> WorkloadManifest | None:
        """Detect runtime and build manifest for a workload directory."""
        for detector in _RUNTIME_DETECTORS:
            for marker in detector["markers"]:
                if (path / marker).exists():
                    install_cmd = detector["install"](path)
                    entry_point = detector["entry"](path)
                    return WorkloadManifest(
                        name=path.name,
                        path=str(path),
                        runtime=detector["runtime"],
                        entry_point=entry_point,
                        install_cmd=install_cmd,
                        memory_limit_mb=detector["memory_limit"],
                        description=self._read_description(path),
                    )
        return None

    def _read_description(self, path: Path) -> str:
        """Try to read a description from the workload."""
        for readme in ["README.md", "README", "readme.md"]:
            f = path / readme
            if f.exists():
                try:
                    text = f.read_text(errors="replace")
                    # Return first non-empty line
                    for line in text.split("\n"):
                        line = line.strip().lstrip("#").strip()
                        if line and len(line) > 10:
                            return line[:200]
                except Exception:
                    pass
        return ""

    async def install(self, name: str) -> bool:
        """Install dependencies for a discovered workload."""
        manifest = self._manifests.get(name)
        if not manifest:
            return False
        if not manifest.install_cmd:
            # No deps to install — mark as success
            manifest.installed = True
            return True

        await self._bus.emit("process.installing", {
            "name": name,
            "runtime": manifest.runtime,
            "command": " ".join(manifest.install_cmd),
        }, source="workload_discovery")

        try:
            proc = await asyncio.create_subprocess_exec(
                *manifest.install_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=manifest.path,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode == 0:
                manifest.installed = True
                await self._bus.emit("process.installed", {
                    "name": name,
                    "runtime": manifest.runtime,
                }, source="workload_discovery")
                return True
            else:
                manifest.install_error = stderr.decode("utf-8", errors="replace")[:500]
                await self._bus.emit("process.install_failed", {
                    "name": name,
                    "error": manifest.install_error[:300],
                }, source="workload_discovery")
                return False

        except asyncio.TimeoutError:
            manifest.install_error = "Install timed out after 120s"
            await self._bus.emit("process.install_failed", {
                "name": name,
                "error": manifest.install_error,
            }, source="workload_discovery")
            return False
        except Exception as e:
            manifest.install_error = str(e)[:300]
            return False

    async def install_all(self) -> dict[str, bool]:
        """Install all discovered workloads."""
        results = {}
        for name in self._manifests:
            results[name] = await self.install(name)
        return results

    def list_workloads(self) -> list[dict[str, Any]]:
        """List all discovered workloads."""
        return [
            {
                "name": m.name,
                "path": m.path,
                "runtime": m.runtime,
                "entry_point": " ".join(m.entry_point),
                "installed": m.installed,
                "install_error": m.install_error,
                "memory_limit_mb": m.memory_limit_mb,
                "description": m.description,
            }
            for m in self._manifests.values()
        ]

    def get_manifest(self, name: str) -> WorkloadManifest | None:
        return self._manifests.get(name)
