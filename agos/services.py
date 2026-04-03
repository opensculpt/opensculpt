"""Agentic Service Lifecycle — the OS that fixes itself.

Every deployed service gets a Service Card (.md with YAML frontmatter).
The ServiceKeeper daemon monitors health, spawns debug agents on failure,
and restores services on boot. This is NOT supervisord — it's an LLM-powered
DevOps engineer that diagnoses root causes instead of blindly replaying commands.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

SERVICES_DIR = Path(".opensculpt/services")
LOGS_DIR = SERVICES_DIR / "logs"
ARCHIVED_DIR = SERVICES_DIR / "archived"


# ── Service Card ──────────────────────────────────────────────────

@dataclass
class ServiceCard:
    """Parsed from .opensculpt/services/{name}.md — YAML frontmatter + markdown body."""

    name: str = ""
    goal_id: str = ""
    type: str = "flask_app"  # flask_app | node_app | docker_container | script | cron
    port: int = 0
    health_check: str = ""
    start_command: str = ""
    working_dir: str = ""
    status: str = "starting"  # deploying|starting|healthy|crashed|debugging|needs_user|decommissioned
    pid: int = 0
    depends_on: list[str] = field(default_factory=list)
    restart_count: int = 0
    max_restarts: int = 3
    last_healthy: str = ""
    created_at: str = ""
    # Not in frontmatter — the full markdown body
    body: str = ""
    # File path on disk
    _path: str = ""
    # Consecutive health check failures (not persisted, runtime only)
    _consecutive_failures: int = 0

    @classmethod
    def from_file(cls, path: Path) -> ServiceCard:
        """Parse a service card .md file with YAML frontmatter."""
        text = path.read_text(encoding="utf-8")
        card = cls(_path=str(path))
        # Split YAML frontmatter from body
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    meta = yaml.safe_load(parts[1]) or {}
                    card.body = parts[2].strip()
                except Exception:
                    meta = {}
                    card.body = text
                for k, v in meta.items():
                    if hasattr(card, k) and not k.startswith("_"):
                        setattr(card, k, v)
        else:
            card.body = text
        if not card.name:
            card.name = path.stem
        return card

    def save(self) -> None:
        """Write card back to disk as .md with YAML frontmatter."""
        path = Path(self._path) if self._path else SERVICES_DIR / f"{self.name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "name": self.name,
            "goal_id": self.goal_id,
            "type": self.type,
            "port": self.port,
            "health_check": self.health_check,
            "start_command": self.start_command,
            "working_dir": self.working_dir,
            "status": self.status,
            "pid": self.pid,
            "depends_on": self.depends_on,
            "restart_count": self.restart_count,
            "max_restarts": self.max_restarts,
            "last_healthy": self.last_healthy,
            "created_at": self.created_at,
        }
        frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        content = f"---\n{frontmatter}\n---\n\n{self.body}"
        path.write_text(content, encoding="utf-8")
        self._path = str(path)

    def append_debug_history(self, entry: str) -> None:
        """Append a line to the Debug History section."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = f"- {ts}: {entry}"
        if "## Debug History" in self.body:
            self.body = self.body.replace("## Debug History", f"## Debug History\n{line}", 1)
        else:
            self.body += f"\n\n## Debug History\n{line}"
        self.save()


# ── Service Card Scanner ──────────────────────────────────────────

def scan_service_cards() -> list[ServiceCard]:
    """Read all service cards from .opensculpt/services/."""
    cards = []
    if not SERVICES_DIR.exists():
        return cards
    for path in SERVICES_DIR.glob("*.md"):
        try:
            cards.append(ServiceCard.from_file(path))
        except Exception as e:
            _logger.warning("Failed to parse service card %s: %s", path, e)
    return cards


def _topo_sort(cards: list[ServiceCard]) -> list[ServiceCard]:
    """Topological sort by depends_on. Services with no deps first."""
    name_map = {c.name: c for c in cards}
    visited, order = set(), []

    def visit(name):
        if name in visited:
            return
        visited.add(name)
        card = name_map.get(name)
        if card:
            for dep in card.depends_on:
                visit(dep)
            order.append(card)

    for c in cards:
        visit(c.name)
    # Add cards not in name_map (broken deps)
    for c in cards:
        if c not in order:
            order.append(c)
    return order


# ── Health Checking ───────────────────────────────────────────────

def run_health_check(health_check: str, timeout: int = 5) -> tuple[bool, str]:
    """Run a health check command. Returns (healthy, output)."""
    if not health_check:
        return True, "no health check configured"
    try:
        result = subprocess.run(
            health_check, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()[:500]
    except subprocess.TimeoutExpired:
        return False, f"health check timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def find_pid_on_port(port: int) -> int:
    """Find PID listening on a port. Returns 0 if not found."""
    try:
        result = subprocess.run(
            f"ss -tlnp 2>/dev/null | grep :{port} || lsof -ti :{port} 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=5
        )
        # Try to extract PID from ss output: pid=1234
        m = re.search(r'pid=(\d+)', result.stdout)
        if m:
            return int(m.group(1))
        # Try lsof output (just a number)
        for line in result.stdout.strip().split('\n'):
            if line.strip().isdigit():
                return int(line.strip())
    except Exception:
        pass
    return 0


# ── Process Management ────────────────────────────────────────────

_MANAGED_PROCESSES: dict[str, subprocess.Popen] = {}


def start_service_process(card: ServiceCard) -> bool:
    """Start a service process from its card. Non-blocking."""
    if not card.start_command:
        _logger.warning("No start_command for service %s", card.name)
        return False

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{card.name}.log"

    try:
        log_file = open(log_path, "a")
        proc = subprocess.Popen(
            card.start_command,
            shell=True,
            cwd=card.working_dir or None,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # Don't die when parent dies
        )
        _MANAGED_PROCESSES[card.name] = proc
        card.pid = proc.pid
        card.status = "starting"
        card.save()
        _logger.info("Started service %s (PID %d): %s", card.name, proc.pid, card.start_command)
        return True
    except Exception as e:
        _logger.error("Failed to start service %s: %s", card.name, e)
        return False


def stop_service_process(card: ServiceCard) -> bool:
    """Stop a service process. Kill by PID + kill by port (catches child processes)."""
    import signal
    import os

    killed_any = False

    # Kill by stored PID
    pid = card.pid
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
            killed_any = True
        except ProcessLookupError:
            pass
        except Exception:
            pass

    # Kill by port — catches child processes the PID kill missed
    if card.port:
        try:
            result = subprocess.run(
                f"lsof -ti :{card.port} 2>/dev/null || fuser {card.port}/tcp 2>/dev/null | tr -s ' '",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split('\n'):
                for token in line.strip().split():
                    token = token.strip()
                    if token.isdigit():
                        try:
                            os.kill(int(token), signal.SIGKILL)
                            killed_any = True
                            _logger.info("Killed PID %s on port %d for %s", token, card.port, card.name)
                        except (ProcessLookupError, PermissionError):
                            pass
        except Exception:
            pass

    # Also kill by managed process handle
    proc = _MANAGED_PROCESSES.pop(card.name, None)
    if proc and proc.poll() is None:
        try:
            proc.kill()
            killed_any = True
        except Exception:
            pass

    card.pid = 0
    card.status = "stopped"
    card.save()
    if killed_any:
        _logger.info("Stopped service %s", card.name)
    return True


# ── ServiceKeeper Daemon ──────────────────────────────────────────

class ServiceKeeper:
    """System daemon that monitors all service cards and spawns debug agents on failure.

    NOT a Daemon subclass — it's wired directly into the boot sequence and
    runs as an asyncio task alongside the other daemons.
    """

    def __init__(self, os_agent: Any = None):
        self._os_agent = os_agent
        self._cards: dict[str, ServiceCard] = {}
        self._debugging: set[str] = set()  # services currently being debugged
        self._task: asyncio.Task | None = None

    def load_cards(self) -> int:
        """Scan and load all service cards. Preserves runtime state across reloads."""
        # Save runtime state before clearing
        old_failures = {name: card._consecutive_failures for name, card in self._cards.items()}
        self._cards.clear()
        for card in scan_service_cards():
            # Restore consecutive failure counter from previous tick
            if card.name in old_failures:
                card._consecutive_failures = old_failures[card.name]
            self._cards[card.name] = card
        return len(self._cards)

    async def boot_restore(self) -> list[str]:
        """On boot: health-check all services, mark crashed ones for debug."""
        self.load_cards()
        sorted_cards = _topo_sort(list(self._cards.values()))
        restored, needs_debug = [], []

        for card in sorted_cards:
            if card.status == "decommissioned":
                continue

            healthy, output = run_health_check(card.health_check)
            if healthy:
                card.status = "healthy"
                card.pid = find_pid_on_port(card.port) if card.port else 0
                card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                card._consecutive_failures = 0
                card.save()
                restored.append(card.name)
                _logger.info("Service %s already healthy on boot", card.name)
            else:
                # Try simple start first
                if card.start_command:
                    started = start_service_process(card)
                    if started:
                        # Wait a moment, then check health
                        await asyncio.sleep(3)
                        healthy2, _ = run_health_check(card.health_check)
                        if healthy2:
                            card.status = "healthy"
                            card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            card._consecutive_failures = 0
                            card.save()
                            restored.append(card.name)
                            _logger.info("Service %s restored on boot via start_command", card.name)
                            continue
                # Simple start didn't work — mark for debug
                card.status = "crashed"
                card.save()
                needs_debug.append(card.name)
                _logger.info("Service %s needs debug on boot", card.name)

        _logger.info("Boot restore: %d healthy, %d need debug", len(restored), len(needs_debug))
        return restored

    async def start(self) -> None:
        """Start the keeper loop."""
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        """Main loop — check health every 30s."""
        while True:
            try:
                await self._tick()
            except Exception as e:
                _logger.error("ServiceKeeper tick error: %s", e)
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        """One health check cycle."""
        self.load_cards()  # Re-read cards (they may have been updated by debug agents)

        for name, card in self._cards.items():
            if card.status in ("decommissioned", "needs_user", "deploying"):
                continue
            if name in self._debugging:
                continue  # Debug agent is working on it

            if card.status in ("healthy", "starting"):
                healthy, output = run_health_check(card.health_check)
                if healthy:
                    if card.status == "starting":
                        card.status = "healthy"
                    card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    card._consecutive_failures = 0
                    card.save()
                else:
                    card._consecutive_failures += 1
                    if card._consecutive_failures >= 3:
                        card.status = "crashed"
                        card.save()
                        _logger.warning("Service %s crashed (3 consecutive failures): %s",
                                       name, output[:200])

            if card.status == "crashed":
                if card.restart_count >= card.max_restarts:
                    card.status = "needs_user"
                    card.save()
                    _logger.warning("Service %s exceeded max restarts (%d), needs user help",
                                   name, card.max_restarts)
                    if self._os_agent and hasattr(self._os_agent, '_event_bus'):
                        try:
                            await self._os_agent._event_bus.emit(
                                "evolution.user_action_needed",
                                {"message": f"Service '{name}' failed {card.max_restarts} times. "
                                            f"Last error in service card.",
                                 "service": name},
                                source="service_keeper"
                            )
                        except Exception:
                            pass
                else:
                    # Step 1: Try simple restart FIRST (free, instant, works 90% of the time)
                    if card.start_command:
                        _logger.info("Service %s: attempting simple restart first", name)
                        stop_service_process(card)  # kill stale process if any
                        started = start_service_process(card)
                        if started:
                            await asyncio.sleep(4)
                            healthy, _ = run_health_check(card.health_check)
                            if healthy:
                                card.status = "healthy"
                                card.restart_count += 1
                                card._consecutive_failures = 0
                                card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                                card.append_debug_history("Simple restart succeeded")
                                _logger.info("Service %s restored via simple restart", name)
                                continue
                        _logger.info("Service %s: simple restart failed, spawning debug agent", name)

                    # Step 2: Simple restart failed → debug agent (LLM diagnoses root cause)
                    await self._spawn_debug_agent(card)

    async def _spawn_debug_agent(self, card: ServiceCard) -> None:
        """Spawn a debug sub-agent to diagnose and fix the service."""
        if not self._os_agent:
            # No OS agent — try simple restart
            _logger.info("No OS agent, attempting simple restart for %s", card.name)
            if start_service_process(card):
                await asyncio.sleep(3)
                healthy, _ = run_health_check(card.health_check)
                if healthy:
                    card.status = "healthy"
                    card.restart_count += 1
                    card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    card.append_debug_history("Simple restart succeeded (no OS agent)")
                    return
            card.restart_count += 1
            card.save()
            return

        self._debugging.add(card.name)
        card.status = "debugging"
        card.save()

        try:
            # Gather context for the debug agent
            health_ok, health_output = run_health_check(card.health_check)
            ps_output = ""
            port_output = ""
            try:
                ps_result = subprocess.run(
                    "ps aux 2>/dev/null | head -30", shell=True,
                    capture_output=True, text=True, timeout=5
                )
                ps_output = ps_result.stdout[:1000]
            except Exception:
                pass
            if card.port:
                try:
                    port_result = subprocess.run(
                        f"ss -tlnp 2>/dev/null | grep :{card.port} || echo 'port {card.port} not in use'",
                        shell=True, capture_output=True, text=True, timeout=5
                    )
                    port_output = port_result.stdout[:500]
                except Exception:
                    pass

            # Read service log if exists
            log_tail = ""
            log_path = LOGS_DIR / f"{card.name}.log"
            if log_path.exists():
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="replace").split('\n')
                    log_tail = '\n'.join(lines[-30:])  # Last 30 lines
                except Exception:
                    pass

            # Build env summary
            env_summary = ""
            try:
                from agos.environment import EnvironmentProbe
                env_summary = EnvironmentProbe.summary()
            except Exception:
                env_summary = "Environment probe unavailable"

            prompt = f"""SERVICE DOWN: "{card.name}" is not responding.

HEALTH CHECK OUTPUT:
{health_output}

SERVICE CARD:
{card.body}

Start command: {card.start_command}
Working dir: {card.working_dir}
Port: {card.port}
Type: {card.type}

ENVIRONMENT:
{env_summary}

RECENT LOGS:
{log_tail[-1500:] if log_tail else 'No logs found'}

RUNNING PROCESSES:
{ps_output}

PORT STATUS:
{port_output}

YOUR JOB:
1. Figure out WHY it's down (check processes, ports, deps, database, logs)
2. Fix the root cause — don't just restart blindly
3. Start the service: {card.start_command}
4. Verify: {card.health_check}
5. If you fix it, update the file {card._path} — append to "## Debug History"

TOOLS: shell, read_file, write_file, http, python
"""

            _logger.info("Spawning debug agent for service %s (attempt %d/%d)",
                        card.name, card.restart_count + 1, card.max_restarts)

            # Use OS agent's sub-agent mechanism
            result = await self._os_agent._run_sub_agent(
                task=prompt,
                tools=["shell", "read_file", "write_file", "http", "python"],
                category="debug",
                goal_id=card.goal_id,
            )

            # Check if service is now healthy
            await asyncio.sleep(3)
            healthy, output = run_health_check(card.health_check)
            card.restart_count += 1

            if healthy:
                card.status = "healthy"
                card.pid = find_pid_on_port(card.port) if card.port else 0
                card.last_healthy = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                card._consecutive_failures = 0
                summary = (result or "")[:200] if isinstance(result, str) else "Debug agent fixed it"
                card.append_debug_history(f"Fixed by debug agent: {summary}")
                _logger.info("Service %s restored by debug agent", card.name)
            else:
                card.status = "crashed"
                summary = (result or "")[:200] if isinstance(result, str) else "Debug agent failed"
                card.append_debug_history(f"Debug attempt {card.restart_count} failed: {summary}")
                _logger.warning("Debug agent failed to restore %s: %s", card.name, output[:200])

            card.save()

        except Exception as e:
            _logger.error("Debug agent crashed for %s: %s", card.name, e)
            card.status = "crashed"
            card.restart_count += 1
            card.append_debug_history(f"Debug agent crashed: {type(e).__name__}: {e}")
        finally:
            self._debugging.discard(card.name)

    # ── Public API ────────────────────────────────────────────────

    def get_services(self) -> list[dict]:
        """Return all services as dicts for the API."""
        self.load_cards()
        result = []
        for card in self._cards.values():
            d = {
                "name": card.name,
                "goal_id": card.goal_id,
                "type": card.type,
                "port": card.port,
                "health_check": card.health_check,
                "start_command": card.start_command,
                "status": card.status,
                "pid": card.pid,
                "restart_count": card.restart_count,
                "max_restarts": card.max_restarts,
                "last_healthy": card.last_healthy,
                "url": f"http://localhost:{card.port}" if card.port else "",
            }
            # Extract credentials from body
            _creds = {}  # noqa: F841
            for line in card.body.split('\n'):
                if 'login' in line.lower() or 'password' in line.lower() or 'credential' in line.lower():
                    d["credentials_hint"] = line.strip().lstrip('- *')
                    break
            result.append(d)
        return result

    async def restart_service(self, name: str) -> dict:
        """Manually restart a service (from API/dashboard)."""
        card = self._cards.get(name)
        if not card:
            return {"ok": False, "error": f"Service '{name}' not found"}

        # Reset circuit breaker on manual restart
        card.restart_count = 0
        card.status = "crashed"
        card._consecutive_failures = 3  # Force debug on next tick
        card.save()
        return {"ok": True, "message": f"Service '{name}' queued for debug restart"}

    async def stop_service(self, name: str) -> dict:
        """Manually stop a service."""
        self.load_cards()  # Fresh read from disk
        card = self._cards.get(name)
        if not card:
            return {"ok": False, "error": f"Service '{name}' not found"}
        _logger.info("Stopping service %s (pid=%d, port=%d)", name, card.pid, card.port)
        stop_service_process(card)
        return {"ok": True, "message": f"Service '{name}' stopped"}

    async def decommission_service(self, name: str) -> dict:
        """Remove a service entirely."""
        card = self._cards.get(name)
        if not card:
            return {"ok": False, "error": f"Service '{name}' not found"}
        stop_service_process(card)
        card.status = "decommissioned"
        # Move card to archived
        ARCHIVED_DIR.mkdir(parents=True, exist_ok=True)
        src = Path(card._path)
        if src.exists():
            dst = ARCHIVED_DIR / src.name
            src.rename(dst)
            card._path = str(dst)
        card.save()
        self._cards.pop(name, None)
        return {"ok": True, "message": f"Service '{name}' decommissioned"}


# ── GoalRunner Integration Helper ─────────────────────────────────

SERVICE_CARD_EXTRACTION_PROMPT = """A deployment phase just completed. Extract service information.

Goal: {goal_description}
Phase: {phase_name}
Phase result: {phase_result}
Verify command: {verify}
Creates daemon: {creates_daemon}

Write a service card. Use EXACTLY this format:

---
name: (human-readable service name, e.g. "Sales CRM API")
goal_id: {goal_id}
type: (flask_app|node_app|docker_container|script|cron)
port: (number, or 0 if no port)
health_check: {verify}
start_command: (the exact shell command to start this service)
working_dir: (the directory to cd into before starting)
status: starting
depends_on: []
restart_count: 0
max_restarts: 3
created_at: {created_at}
---

## Access
- **URL**: http://localhost:(port)
- (credentials if any were set during deployment)

## What This Service Does
(one paragraph describing the service)

## Dependencies
(list packages, databases, external services)

## Files
(list key files created, with full paths)

## How to Start
```bash
(commands to start from scratch — install deps + start)
```
"""


async def extract_service_card(llm: Any, goal: dict, phase: dict) -> ServiceCard | None:
    """Ask LLM to extract a service card from a completed phase."""
    try:
        from agos.llm.base import LLMMessage
    except ImportError:
        return None

    prompt = SERVICE_CARD_EXTRACTION_PROMPT.format(
        goal_description=goal.get("description", ""),
        phase_name=phase.get("name", ""),
        phase_result=(phase.get("result", "") or "")[:2000],
        verify=phase.get("verify", ""),
        creates_daemon=phase.get("creates_daemon") or phase.get("creates_hand", ""),
        goal_id=goal.get("id", ""),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    try:
        resp = await llm.complete(
            messages=[LLMMessage(role="user", content=prompt)],
            max_tokens=800,
        )
        card_text = (resp.content or "").strip()
        if not card_text or "---" not in card_text:
            _logger.warning("LLM returned invalid service card format")
            return None

        # Save to disk
        safe_name = re.sub(r'[^a-z0-9_]', '_', phase.get("name", "service").lower())
        card_path = SERVICES_DIR / f"{goal.get('id', 'unknown')}_{safe_name}.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(card_text, encoding="utf-8")

        card = ServiceCard.from_file(card_path)
        _logger.info("Extracted service card: %s (port %d)", card.name, card.port)
        return card

    except Exception as e:
        _logger.error("Failed to extract service card: %s", e)
        return None
