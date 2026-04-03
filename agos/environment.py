"""Environment probe — detect what OpenSculpt can work with.

Used by sub-agents, evolution, GoalRunner, and dashboard to understand
the runtime environment. Cached after first probe since the environment
doesn't change during a session.

Detects: OS, containerization, package managers, runtimes, services,
networking, storage, and hardware.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import logging
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)
_cached: Optional["Environment"] = None


@dataclass
class Environment:
    """Snapshot of the runtime environment."""

    # OS
    os_name: str = ""          # Linux, Windows, Darwin
    os_version: str = ""       # 22.04, 10.0.19041, 14.2
    os_arch: str = ""          # x86_64, aarch64, arm64
    hostname: str = ""

    # Containerization
    in_container: bool = False
    container_runtime: str = ""  # docker, podman, lxc, none
    docker_available: bool = False  # daemon accessible, can run containers
    docker_cli_only: bool = False   # CLI exists but no daemon

    # Package managers
    apt: bool = False
    yum: bool = False
    dnf: bool = False
    apk: bool = False
    brew: bool = False
    choco: bool = False
    snap: bool = False

    # Runtimes
    python_version: str = ""
    pip: bool = False
    node: bool = False
    node_version: str = ""
    npm: bool = False
    go: bool = False
    rust: bool = False
    java: bool = False
    php: bool = False
    ruby: bool = False

    # Services / tools
    git: bool = False
    curl: bool = False
    wget: bool = False
    nginx: bool = False
    mysql: bool = False
    postgresql: bool = False
    redis: bool = False
    sqlite: bool = True  # Python stdlib, always available
    systemd: bool = False
    supervisor: bool = False

    # Networking
    internet: bool = False
    ports_available: list[int] = field(default_factory=list)
    ports_in_use: dict = field(default_factory=dict)  # {port: "process_name"}

    # Storage
    disk_free_gb: float = 0.0
    memory_total_mb: int = 0
    memory_free_mb: int = 0

    # Permissions & limits
    is_root: bool = False
    writable_paths: list[str] = field(default_factory=list)  # ["/app", "/tmp", "/home/user"]
    read_only_root: bool = False
    memory_limit_mb: int = 0  # container cgroup limit, 0 = unlimited
    cpu_limit: float = 0.0    # container cgroup limit, 0 = unlimited

    # Running services (discovered at probe time)
    running_services: list[dict] = field(default_factory=list)  # [{"name": "mysql", "port": 3306, "pid": 123}]

    # Capabilities summary
    can_install_packages: bool = False
    can_run_containers: bool = False
    can_run_services: bool = False
    can_build_from_source: bool = False

    # Vibe coding tools (detected by agos.vibe_tools)
    vibe_tools: list[dict] = field(default_factory=list)  # [{"name": "claude_code", "label": "Claude Code", ...}]

    # Deployment recommendation
    recommended_strategy: str = ""  # "docker", "apt_install", "pip_python", "minimal"


class EnvironmentProbe:
    """Probes the environment and caches the result."""

    @staticmethod
    def probe() -> Environment:
        """Full environment probe. Cached after first call."""
        global _cached
        if _cached is not None:
            return _cached

        env = Environment()

        # ── OS ──
        env.os_name = platform.system()
        env.os_version = platform.release()
        env.os_arch = platform.machine()
        env.hostname = platform.node()

        # ── Container detection ──
        env.in_container = (
            os.path.exists("/.dockerenv")
            or os.path.exists("/run/.containerenv")
            or _check_cgroup_container()
        )

        # ── Docker ──
        if shutil.which("docker"):
            if os.path.exists("/var/run/docker.sock"):
                # Socket exists — try to ping
                try:
                    r = subprocess.run(
                        ["docker", "info"], capture_output=True, timeout=5
                    )
                    env.docker_available = r.returncode == 0
                except Exception:
                    env.docker_available = False
                if not env.docker_available:
                    env.docker_cli_only = True
            else:
                env.docker_cli_only = True
        if shutil.which("podman"):
            env.container_runtime = "podman"
            if not env.docker_available:
                try:
                    r = subprocess.run(
                        ["podman", "info"], capture_output=True, timeout=5
                    )
                    env.can_run_containers = r.returncode == 0
                except Exception:
                    pass

        if env.docker_available:
            env.container_runtime = "docker"
            env.can_run_containers = True

        # ── Package managers ──
        env.apt = bool(shutil.which("apt-get"))
        env.yum = bool(shutil.which("yum"))
        env.dnf = bool(shutil.which("dnf"))
        env.apk = bool(shutil.which("apk"))
        env.brew = bool(shutil.which("brew"))
        env.choco = bool(shutil.which("choco"))
        env.snap = bool(shutil.which("snap"))
        env.can_install_packages = any([env.apt, env.yum, env.dnf, env.apk, env.brew, env.choco])

        # ── Runtimes ──
        env.python_version = platform.python_version()
        env.pip = bool(shutil.which("pip") or shutil.which("pip3"))
        env.node = bool(shutil.which("node"))
        if env.node:
            try:
                r = subprocess.run(["node", "--version"], capture_output=True, timeout=3)
                env.node_version = r.stdout.decode().strip()
            except Exception:
                pass
        env.npm = bool(shutil.which("npm"))
        env.go = bool(shutil.which("go"))
        env.rust = bool(shutil.which("cargo") or shutil.which("rustc"))
        env.java = bool(shutil.which("java"))
        env.php = bool(shutil.which("php"))
        env.ruby = bool(shutil.which("ruby"))

        # ── Services / tools ──
        env.git = bool(shutil.which("git"))
        env.curl = bool(shutil.which("curl"))
        env.wget = bool(shutil.which("wget"))
        env.nginx = bool(shutil.which("nginx"))
        env.mysql = bool(shutil.which("mysql") or shutil.which("mariadb"))
        env.postgresql = bool(shutil.which("psql"))
        env.redis = bool(shutil.which("redis-server") or shutil.which("redis-cli"))
        env.systemd = os.path.exists("/run/systemd/system") or shutil.which("systemctl") is not None
        env.supervisor = bool(shutil.which("supervisord"))

        # ── Can run services? ──
        env.can_run_services = env.systemd or env.supervisor or env.in_container

        # ── Can build from source? ──
        env.can_build_from_source = bool(
            shutil.which("gcc") or shutil.which("make") or shutil.which("cargo")
        )

        # ── Networking ──
        try:
            import socket
            s = socket.create_connection(("8.8.8.8", 53), timeout=3)
            s.close()
            env.internet = True
        except Exception:
            env.internet = False

        # Check common ports
        for port in [8080, 8081, 8082, 3000, 5432, 3306, 6379]:
            try:
                import socket as _sock
                s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                s.close()
                if result != 0:  # Port is free
                    env.ports_available.append(port)
            except Exception:
                pass

        # ── Storage ──
        try:
            import shutil as _sh
            usage = _sh.disk_usage("/")
            env.disk_free_gb = round(usage.free / (1024**3), 1)
        except Exception:
            pass

        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        env.memory_total_mb = int(line.split()[1]) // 1024
                    elif line.startswith("MemAvailable"):
                        env.memory_free_mb = int(line.split()[1]) // 1024
        except Exception:
            try:
                import psutil
                mem = psutil.virtual_memory()
                env.memory_total_mb = mem.total // (1024 * 1024)
                env.memory_free_mb = mem.available // (1024 * 1024)
            except Exception:
                pass

        # ── Permissions ──
        env.is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
        # Check writable paths
        for path in ["/app", "/opt", "/tmp", "/var", os.path.expanduser("~")]:
            if os.path.isdir(path) and os.access(path, os.W_OK):
                env.writable_paths.append(path)
        # Read-only root filesystem check
        try:
            test_file = "/tmp/.opensculpt_write_test"
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception:
            env.read_only_root = True

        # ── Container resource limits ──
        try:
            # cgroup v2
            mem_limit_path = "/sys/fs/cgroup/memory.max"
            if not os.path.exists(mem_limit_path):
                # cgroup v1
                mem_limit_path = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
            if os.path.exists(mem_limit_path):
                with open(mem_limit_path) as f:
                    val = f.read().strip()
                    if val != "max" and val.isdigit():
                        env.memory_limit_mb = int(val) // (1024 * 1024)
        except Exception:
            pass
        try:
            cpu_path = "/sys/fs/cgroup/cpu.max"
            if os.path.exists(cpu_path):
                with open(cpu_path) as f:
                    parts = f.read().strip().split()
                    if parts[0] != "max" and len(parts) == 2:
                        env.cpu_limit = round(int(parts[0]) / int(parts[1]), 2)
        except Exception:
            pass

        # ── Running services (what's already listening?) ──
        try:
            r = subprocess.run(
                ["ss", "-tlnp"], capture_output=True, timeout=5
            )
            if r.returncode == 0:
                for line in r.stdout.decode().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        addr = parts[3]
                        if ":" in addr:
                            port_str = addr.rsplit(":", 1)[-1]
                            if port_str.isdigit():
                                port = int(port_str)
                                proc = parts[-1] if len(parts) > 5 else ""
                                env.ports_in_use[port] = proc
                                # Identify known services
                                if port == 3306:
                                    env.running_services.append({"name": "mysql", "port": port})
                                elif port == 5432:
                                    env.running_services.append({"name": "postgresql", "port": port})
                                elif port == 6379:
                                    env.running_services.append({"name": "redis", "port": port})
                                elif port in (80, 443, 8080, 8081):
                                    env.running_services.append({"name": "http", "port": port})
        except Exception:
            pass

        # ── Vibe coding tools ──
        try:
            from agos.vibe_tools import detect_vibe_tools
            env.vibe_tools = [t.to_dict() for t in detect_vibe_tools()]
        except Exception:
            _logger.debug("Vibe tool detection failed", exc_info=True)

        # ── Deployment strategy recommendation ──
        if env.docker_available:
            env.recommended_strategy = "docker"
        elif env.can_install_packages and env.is_root:
            env.recommended_strategy = "apt_install"
        elif env.pip:
            env.recommended_strategy = "pip_python"
        else:
            env.recommended_strategy = "minimal"

        _cached = env
        _logger.info("Environment probed: %s %s, container=%s, docker=%s, apt=%s, pip=%s, root=%s, strategy=%s",
                     env.os_name, env.os_arch, env.in_container, env.docker_available,
                     env.apt, env.pip, env.is_root, env.recommended_strategy)
        return env

    @staticmethod
    def summary() -> str:
        """Human-readable summary for injection into LLM prompts."""
        env = EnvironmentProbe.probe()
        lines = []

        # OS
        lines.append(f"OS: {env.os_name} {env.os_version} ({env.os_arch})")

        # Container
        if env.in_container:
            lines.append("CONTAINER: Running inside a container.")
            if env.docker_available:
                lines.append("DOCKER: Docker daemon available — can run containers.")
            elif env.docker_cli_only:
                lines.append("DOCKER: CLI exists but NO daemon. CANNOT run containers. Install software directly.")
            else:
                lines.append("DOCKER: Not available. Install software directly with package manager or pip.")

        # How to install software
        install_methods = []
        if env.apt:
            install_methods.append("apt-get install (Debian/Ubuntu packages: php, mysql, nginx, redis, etc.)")
        if env.yum or env.dnf:
            install_methods.append("yum/dnf install (RHEL/CentOS packages)")
        if env.apk:
            install_methods.append("apk add (Alpine packages)")
        if env.pip:
            install_methods.append("pip install (Python packages, Flask, Django, etc.)")
        if env.npm:
            install_methods.append("npm install (Node.js packages, Express, etc.)")
        if env.brew:
            install_methods.append("brew install (macOS packages)")
        if install_methods:
            lines.append("INSTALL SOFTWARE WITH: " + " | ".join(install_methods))
        else:
            lines.append("INSTALL: No package manager found. Download binaries or use Python stdlib.")

        # Available runtimes
        runtimes = [f"Python {env.python_version}"]
        if env.node:
            runtimes.append(f"Node.js {env.node_version}")
        if env.php:
            runtimes.append("PHP")
        if env.go:
            runtimes.append("Go")
        if env.java:
            runtimes.append("Java")
        lines.append("RUNTIMES: " + ", ".join(runtimes))

        # Already installed services
        services = []
        if env.nginx:
            services.append("nginx")
        if env.mysql:
            services.append("mysql/mariadb")
        if env.postgresql:
            services.append("postgresql")
        if env.redis:
            services.append("redis")
        if services:
            lines.append("INSTALLED SERVICES: " + ", ".join(services))

        # Service management
        if env.systemd:
            lines.append("SERVICE MANAGER: systemd (use systemctl start/stop)")
        elif env.supervisor:
            lines.append("SERVICE MANAGER: supervisor")
        else:
            lines.append("SERVICE MANAGER: None — run processes in background with & or nohup")

        # Permissions
        lines.append(f"ROOT ACCESS: {'Yes' if env.is_root else 'No'}")
        if env.writable_paths:
            lines.append(f"WRITABLE PATHS: {', '.join(env.writable_paths[:5])}")
        if env.read_only_root:
            lines.append("WARNING: Root filesystem is read-only. Use /tmp or mounted volumes.")
        if env.memory_limit_mb:
            lines.append(f"CONTAINER MEMORY LIMIT: {env.memory_limit_mb} MB")

        # Running services already available
        if env.running_services:
            svc_list = [f"{s['name']} (port {s['port']})" for s in env.running_services]
            lines.append("ALREADY RUNNING: " + ", ".join(svc_list))
            lines.append("TIP: Reuse existing services instead of installing new ones.")

        # Network
        if env.internet:
            lines.append("INTERNET: Available — can download packages and access APIs.")
        else:
            lines.append("INTERNET: Not available — use only local resources.")

        if env.ports_available:
            lines.append(f"FREE PORTS: {', '.join(str(p) for p in env.ports_available[:5])}")
        if env.ports_in_use:
            used = [f"{p}" for p in sorted(env.ports_in_use.keys())[:5]]
            lines.append(f"PORTS IN USE: {', '.join(used)}")

        # Storage
        if env.disk_free_gb:
            lines.append(f"DISK: {env.disk_free_gb} GB free")
        if env.memory_total_mb:
            lines.append(f"MEMORY: {env.memory_free_mb}/{env.memory_total_mb} MB available")

        # Vibe coding tools
        installed_vibe = [t for t in env.vibe_tools if t.get("installed")]
        if installed_vibe:
            vibe_names = [t["label"] for t in installed_vibe]
            lines.append("VIBE CODING TOOLS: " + ", ".join(vibe_names))
        else:
            lines.append("VIBE CODING TOOLS: None detected")

        # Deployment recommendation
        strategies = {
            "docker": "STRATEGY: Use Docker Compose to deploy services.",
            "apt_install": "STRATEGY: Install software directly with apt-get (you have root + package manager).",
            "pip_python": "STRATEGY: Use Python-based alternatives (Flask+SQLite, etc.) installed via pip.",
            "minimal": "STRATEGY: Very limited environment. Use Python stdlib only (http.server, sqlite3).",
        }
        lines.append(strategies.get(env.recommended_strategy, "STRATEGY: Probe failed — try shell commands to discover capabilities."))

        return "\n".join(lines)

    @staticmethod
    def reset():
        """Clear cache — force re-probe on next call."""
        global _cached
        _cached = None

    @staticmethod
    def to_dict() -> dict:
        """For API/dashboard consumption."""
        env = EnvironmentProbe.probe()
        from dataclasses import asdict
        return asdict(env)


def _check_cgroup_container() -> bool:
    """Detect container via cgroup."""
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            return "docker" in content or "kubepods" in content or "lxc" in content
    except Exception:
        return False
