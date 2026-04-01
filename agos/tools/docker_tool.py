"""Docker tool — native container management for the OS.

Not a shell workaround. Structured operations with proper error handling,
output parsing, and status tracking. Uses Docker CLI under the hood
but provides a clean interface for the OS agent.
"""
from __future__ import annotations

import asyncio
import json
import logging

_logger = logging.getLogger(__name__)


async def docker_run(image: str, name: str = "", ports: str = "",
                     env: str = "", network: str = "",
                     detach: bool = True, extra: str = "") -> str:
    """Run a Docker container.

    Args:
        image: Docker image (e.g. "espocrm/espocrm:latest")
        name: Container name
        ports: Port mapping (e.g. "8081:80")
        env: Environment variables as JSON object (e.g. {"MYSQL_ROOT_PASSWORD": "pass"})
        network: Docker network to join
        detach: Run in background (default True)
        extra: Additional docker run flags
    """
    cmd = ["docker", "run"]
    if detach:
        cmd.append("-d")
    if name:
        cmd.extend(["--name", name])
    if ports:
        for p in ports.split(","):
            cmd.extend(["-p", p.strip()])
    if network:
        cmd.extend(["--network", network])
    if env:
        try:
            env_dict = json.loads(env) if isinstance(env, str) else env
            for k, v in env_dict.items():
                cmd.extend(["-e", f"{k}={v}"])
        except (json.JSONDecodeError, AttributeError):
            cmd.extend(["-e", str(env)])
    # Enforce memory limit on all spawned containers to prevent OOM
    # Default 512MB per container — prevents runaway memory consumption
    if "--memory" not in extra and "-m " not in extra:
        cmd.extend(["--memory", "512m", "--memory-swap", "768m"])
    if extra:
        cmd.extend(extra.split())
    cmd.append(image)

    return await _run_cmd(cmd)


async def docker_ps(all_containers: bool = False) -> str:
    """List running containers (or all with all=True)."""
    cmd = ["docker", "ps", "--format", "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}\\t{{.Image}}"]
    if all_containers:
        cmd.append("-a")
    return await _run_cmd(cmd)


async def docker_stop(name: str) -> str:
    """Stop a container by name or ID."""
    return await _run_cmd(["docker", "stop", name])


async def docker_rm(name: str, force: bool = False) -> str:
    """Remove a container."""
    cmd = ["docker", "rm"]
    if force:
        cmd.append("-f")
    cmd.append(name)
    return await _run_cmd(cmd)


async def docker_logs(name: str, tail: int = 50) -> str:
    """Get container logs."""
    return await _run_cmd(["docker", "logs", "--tail", str(tail), name])


async def docker_pull(image: str) -> str:
    """Pull a Docker image."""
    return await _run_cmd(["docker", "pull", image])


async def docker_network(action: str, name: str) -> str:
    """Manage Docker networks. Action: create, rm, ls."""
    if action == "ls":
        return await _run_cmd(["docker", "network", "ls"])
    return await _run_cmd(["docker", "network", action, name])


async def docker_exec(container: str, command: str) -> str:
    """Execute a command inside a running container.

    Supports compound commands (&&, ||, ;, pipes) by wrapping with sh -c.
    """
    # If command contains shell operators, wrap with sh -c
    if any(op in command for op in ("&&", "||", ";", "|", ">", "<")):
        return await _run_cmd(["docker", "exec", container, "sh", "-c", command])
    return await _run_cmd(["docker", "exec", container] + command.split())


async def _run_cmd(cmd: list[str]) -> str:
    """Run a Docker CLI command and return output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0:
            return out if out else "OK"
        else:
            # Check if docker is available
            if "not found" in err.lower() or "not recognized" in err.lower():
                return "ERROR: Docker CLI not installed. The OS needs to install Docker first."
            return f"ERROR (exit {proc.returncode}): {err[:500]}" + (f"\n{out}" if out else "")
    except FileNotFoundError:
        return "ERROR: Docker CLI not installed. Run: curl -fsSL https://get.docker.com | sh"
    except asyncio.TimeoutError:
        return "ERROR: Command timed out after 120s"
    except Exception as e:
        return f"ERROR: {e}"
