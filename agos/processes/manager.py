"""ProcessManager — the OS process table for real subprocesses.

Spawns agent workloads as real OS processes, monitors their lifecycle,
enforces resource limits, and emits events on the EventBus.

Think of this as the kernel scheduler + process supervisor.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agos.types import new_id
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail


class ProcessState(str, Enum):
    DISCOVERED = "discovered"
    INSTALLING = "installing"
    READY = "ready"
    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"
    KILLED = "killed"
    RESTARTING = "restarting"


@dataclass
class ProcessInfo:
    """Tracks a managed OS process."""

    pid: str  # AGOS process ID (not OS PID)
    name: str
    command: list[str]
    workdir: str
    state: ProcessState = ProcessState.READY
    os_pid: int | None = None  # Real OS PID once spawned
    started_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    restart_count: int = 0
    max_restarts: int = 3
    # Resource tracking
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    memory_limit_mb: float = 512.0
    token_count: int = 0
    token_limit: int = 100_000
    files_accessed: list[str] = field(default_factory=list)
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    # Metadata
    kind: str = "agent"  # agent|service|worker
    tags: dict[str, str] = field(default_factory=dict)


class ProcessManager:
    """OS-level process supervisor.

    Spawns real subprocesses, monitors them via background tasks,
    enforces resource limits, and restarts on crash.
    """

    def __init__(self, event_bus: EventBus, audit_trail: AuditTrail) -> None:
        self._bus = event_bus
        self._audit = audit_trail
        self._processes: dict[str, ProcessInfo] = {}
        self._os_processes: dict[str, asyncio.subprocess.Process] = {}
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._output_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        name: str,
        command: list[str],
        workdir: str = ".",
        env: dict[str, str] | None = None,
        memory_limit_mb: float = 512.0,
        token_limit: int = 100_000,
        kind: str = "agent",
        tags: dict[str, str] | None = None,
        auto_restart: bool = True,
    ) -> ProcessInfo:
        """Spawn a real OS process and begin monitoring it."""
        pid = new_id()
        info = ProcessInfo(
            pid=pid,
            name=name,
            command=command,
            workdir=workdir,
            memory_limit_mb=memory_limit_mb,
            token_limit=token_limit,
            kind=kind,
            tags=tags or {},
            max_restarts=3 if auto_restart else 0,
        )

        async with self._lock:
            self._processes[pid] = info

        await self._start_process(pid, env)
        return info

    async def _start_process(self, pid: str, env: dict[str, str] | None = None) -> None:
        """Actually start the subprocess."""
        info = self._processes[pid]
        proc_env = {**os.environ, **(env or {})}

        try:
            proc = await asyncio.create_subprocess_exec(
                *info.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=info.workdir,
                env=proc_env,
            )
            info.os_pid = proc.pid
            info.state = ProcessState.RUNNING
            info.started_at = time.time()

            async with self._lock:
                self._os_processes[pid] = proc

            await self._bus.emit("process.spawned", {
                "pid": pid,
                "name": info.name,
                "os_pid": proc.pid,
                "command": " ".join(info.command),
                "kind": info.kind,
            }, source="process_manager")

            await self._audit.log_state_change(pid, info.name, "ready", "running")

            # Start background monitoring
            self._monitor_tasks[pid] = asyncio.create_task(self._monitor_loop(pid))
            self._output_tasks[pid] = asyncio.create_task(self._read_output(pid))

        except Exception as e:
            info.state = ProcessState.CRASHED
            await self._bus.emit("process.error", {
                "pid": pid,
                "name": info.name,
                "error": str(e)[:300],
            }, source="process_manager")

    async def _read_output(self, pid: str) -> None:
        """Read stdout/stderr from the process in real time."""
        proc = self._os_processes.get(pid)
        info = self._processes.get(pid)
        if not proc or not info:
            return

        async def _read_stream(stream, is_stderr: bool):
            target = info.stderr_lines if is_stderr else info.stdout_lines
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                target.append(decoded)
                # Keep last 200 lines
                if len(target) > 200:
                    del target[:100]
                # Emit output event
                await self._bus.emit("process.output", {
                    "pid": pid,
                    "name": info.name,
                    "line": decoded[:500],
                    "stderr": is_stderr,
                }, source="process_manager")

                # Detect token usage patterns in output
                if "token" in decoded.lower() and any(c.isdigit() for c in decoded):
                    self._parse_token_usage(info, decoded)

        await asyncio.gather(
            _read_stream(proc.stdout, False),
            _read_stream(proc.stderr, True),
            return_exceptions=True,
        )

    def _parse_token_usage(self, info: ProcessInfo, line: str) -> None:
        """Try to detect token consumption from process output."""
        import re
        match = re.search(r"tokens?[\s:=]+(\d+)", line, re.IGNORECASE)
        if match:
            tokens = int(match.group(1))
            info.token_count = max(info.token_count, tokens)

    async def _monitor_loop(self, pid: str) -> None:
        """Background monitor: check process health, resources, liveness."""
        info = self._processes.get(pid)
        if not info:
            return

        while info.state == ProcessState.RUNNING:
            await asyncio.sleep(5)

            proc = self._os_processes.get(pid)
            if not proc:
                break

            # Check if process is still alive
            if proc.returncode is not None:
                await self._handle_exit(pid, proc.returncode)
                return

            # Read /proc stats on Linux
            if info.os_pid and os.path.exists(f"/proc/{info.os_pid}/status"):
                self._read_proc_stats(info)

            # Emit heartbeat
            await self._bus.emit("process.heartbeat", {
                "pid": pid,
                "name": info.name,
                "os_pid": info.os_pid,
                "memory_mb": round(info.memory_mb, 1),
                "cpu_percent": round(info.cpu_percent, 1),
                "uptime_s": int(time.time() - (info.started_at or time.time())),
                "token_count": info.token_count,
            }, source="process_manager")

            # Check resource limits
            if info.memory_mb > info.memory_limit_mb:
                await self._bus.emit("process.memory_exceeded", {
                    "pid": pid,
                    "name": info.name,
                    "memory_mb": round(info.memory_mb, 1),
                    "limit_mb": info.memory_limit_mb,
                }, source="process_manager")
                await self.kill(pid, reason="memory limit exceeded")
                return

            if info.token_count > info.token_limit:
                await self._bus.emit("process.token_hoarding", {
                    "pid": pid,
                    "name": info.name,
                    "tokens": info.token_count,
                    "limit": info.token_limit,
                }, source="process_manager")
                await self.kill(pid, reason="token limit exceeded")
                return

            # Memory warning at 80%
            if info.memory_mb > info.memory_limit_mb * 0.8:
                await self._bus.emit("process.memory_warning", {
                    "pid": pid,
                    "name": info.name,
                    "memory_mb": round(info.memory_mb, 1),
                    "limit_mb": info.memory_limit_mb,
                    "usage_pct": round(info.memory_mb / info.memory_limit_mb * 100, 1),
                }, source="process_manager")

    def _read_proc_stats(self, info: ProcessInfo) -> None:
        """Read memory/CPU from /proc filesystem (Linux only)."""
        try:
            status_path = f"/proc/{info.os_pid}/status"
            with open(status_path) as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS is in kB
                        kb = int(line.split()[1])
                        info.memory_mb = kb / 1024.0
                    elif line.startswith("Threads:"):
                        info.tags["threads"] = line.split()[1]
        except (FileNotFoundError, PermissionError, ValueError):
            pass

        try:
            stat_path = f"/proc/{info.os_pid}/stat"
            with open(stat_path) as f:
                parts = f.read().split()
                if len(parts) > 14:
                    utime = int(parts[13])
                    stime = int(parts[14])
                    total_ticks = utime + stime
                    info.tags["cpu_ticks"] = str(total_ticks)
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            pass

        # Track open file descriptors
        try:
            fd_path = f"/proc/{info.os_pid}/fd"
            if os.path.isdir(fd_path):
                fds = os.listdir(fd_path)
                info.tags["open_fds"] = str(len(fds))
        except (PermissionError, FileNotFoundError):
            pass

    async def _handle_exit(self, pid: str, exit_code: int) -> None:
        """Handle process exit — log, emit event, maybe restart."""
        info = self._processes.get(pid)
        if not info:
            return

        info.exit_code = exit_code
        info.stopped_at = time.time()

        if exit_code == 0:
            info.state = ProcessState.STOPPED
            await self._bus.emit("process.completed", {
                "pid": pid,
                "name": info.name,
                "exit_code": 0,
                "uptime_s": int(info.stopped_at - (info.started_at or info.stopped_at)),
            }, source="process_manager")
            await self._audit.log_state_change(pid, info.name, "running", "completed")
        else:
            info.state = ProcessState.CRASHED
            stderr_tail = "\n".join(info.stderr_lines[-10:])
            await self._bus.emit("process.crashed", {
                "pid": pid,
                "name": info.name,
                "exit_code": exit_code,
                "stderr": stderr_tail[:500],
            }, source="process_manager")
            await self._audit.log_state_change(pid, info.name, "running", "crashed")

            # Auto-restart if allowed
            if info.restart_count < info.max_restarts:
                info.restart_count += 1
                info.state = ProcessState.RESTARTING
                await self._bus.emit("process.restarting", {
                    "pid": pid,
                    "name": info.name,
                    "attempt": info.restart_count,
                    "max": info.max_restarts,
                }, source="process_manager")
                await asyncio.sleep(2 ** info.restart_count)  # Exponential backoff
                await self._start_process(pid)

    async def kill(self, pid: str, reason: str = "user request") -> None:
        """Kill a running process."""
        info = self._processes.get(pid)
        proc = self._os_processes.get(pid)
        if not info or not proc:
            return

        info.state = ProcessState.KILLED
        info.stopped_at = time.time()
        info.max_restarts = 0  # Prevent auto-restart

        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        except ProcessLookupError:
            pass

        await self._bus.emit("process.killed", {
            "pid": pid,
            "name": info.name,
            "reason": reason,
        }, source="process_manager")
        await self._audit.log_state_change(pid, info.name, "running", "killed")

    async def restart(self, pid: str) -> None:
        """Force restart a process."""
        info = self._processes.get(pid)
        if not info:
            return
        info.max_restarts = info.restart_count + 1  # Allow one more restart
        if info.state == ProcessState.RUNNING:
            await self.kill(pid, reason="restart requested")
        info.restart_count += 1
        info.state = ProcessState.RESTARTING
        await asyncio.sleep(1)
        await self._start_process(pid)

    def list_processes(self) -> list[dict[str, Any]]:
        """Return process table for API/dashboard."""
        result = []
        for info in self._processes.values():
            uptime = 0
            if info.started_at:
                end = info.stopped_at or time.time()
                uptime = int(end - info.started_at)
            result.append({
                "pid": info.pid,
                "name": info.name,
                "state": info.state.value,
                "os_pid": info.os_pid,
                "kind": info.kind,
                "memory_mb": round(info.memory_mb, 1),
                "cpu_percent": round(info.cpu_percent, 1),
                "token_count": info.token_count,
                "token_limit": info.token_limit,
                "memory_limit_mb": info.memory_limit_mb,
                "uptime_s": uptime,
                "exit_code": info.exit_code,
                "restart_count": info.restart_count,
                "tags": info.tags,
                "command": " ".join(info.command),
            })
        return result

    def get_process(self, pid: str) -> ProcessInfo | None:
        return self._processes.get(pid)

    def get_output(self, pid: str, lines: int = 50) -> dict:
        """Get recent stdout/stderr for a process."""
        info = self._processes.get(pid)
        if not info:
            return {"stdout": [], "stderr": []}
        return {
            "stdout": info.stdout_lines[-lines:],
            "stderr": info.stderr_lines[-lines:],
        }

    async def shutdown(self) -> None:
        """Kill all processes and cancel monitor tasks."""
        for pid in list(self._processes):
            if self._processes[pid].state == ProcessState.RUNNING:
                await self.kill(pid, reason="OS shutdown")
        for task in self._monitor_tasks.values():
            task.cancel()
        for task in self._output_tasks.values():
            task.cancel()
