"""Built-in system agent tasks — security scanning, profiling, cleanup.

Each function is a standalone async task that receives (aid, name, bus, audit)
and returns a list of finding strings. They are scheduled by the boot loop.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import socket
import subprocess
import time

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry


SRC = pathlib.Path("/app/agos")
if not SRC.exists():
    SRC = pathlib.Path("agos")


# ══════════════════════════════════════════════════════════════════
# REAL AGENT TASKS — each does actual work
# ══════════════════════════════════════════════════════════════════


async def scan_secrets(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Scan source code for hardcoded secrets, API keys, passwords."""
    patterns = [
        (r'(?i)(api[_-]?key|secret[_-]?key|password|token)\s*=\s*["\'][^"\']{8,}', "hardcoded secret"),
        (r'(?i)sk-[a-zA-Z0-9]{20,}', "OpenAI-style API key"),
        (r'(?i)AKIA[0-9A-Z]{16}', "AWS access key"),
        (r'(?i)ghp_[a-zA-Z0-9]{36}', "GitHub personal access token"),
        (r'(?i)-----BEGIN (RSA |EC )?PRIVATE KEY', "private key"),
    ]
    findings = []
    files_scanned = 0
    for f in SRC.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        files_scanned += 1
        try:
            content = f.read_text(errors="ignore")
            for pat, desc in patterns:
                for match in re.finditer(pat, content):
                    line_num = content[:match.start()].count("\n") + 1
                    finding = f"{desc} in {f.relative_to(SRC.parent)}:{line_num}"
                    findings.append(finding)
                    await bus.emit("security.finding", {
                        "severity": "HIGH", "type": desc,
                        "file": str(f.relative_to(SRC.parent)), "line": line_num,
                    }, source=name)
                    await audit.record(AuditEntry(
                        agent_id=aid, agent_name=name, action="security_scan",
                        detail=finding, success=True,
                    ))
        except Exception:
            pass
        await asyncio.sleep(0.05)

    if not findings:
        findings.append("No secrets found — code is clean")
        await bus.emit("security.clear", {"files_scanned": files_scanned, "status": "PASS"}, source=name)

    await audit.record(AuditEntry(
        agent_id=aid, agent_name=name, action="scan_complete",
        detail=f"Scanned {files_scanned} files, found {len(findings)} issues", success=True,
    ))
    return findings


async def scan_code_quality(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Find real code quality issues."""
    findings = []
    files_analyzed = 0

    for f in SRC.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        files_analyzed += 1
        try:
            lines = f.read_text(errors="ignore").splitlines()
            rel = str(f.relative_to(SRC.parent))

            func_start = None
            func_name = ""
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    if func_start is not None and (i - func_start) > 50:
                        finding = f"Long function '{func_name}' ({i - func_start} lines) in {rel}:{func_start+1}"
                        findings.append(finding)
                        await bus.emit("quality.long_function", {
                            "file": rel, "function": func_name,
                            "lines": i - func_start, "line": func_start + 1,
                        }, source=name)
                    func_name = stripped.split("(")[0].replace("def ", "").replace("async ", "")
                    func_start = i
            if func_start is not None and (len(lines) - func_start) > 50:
                finding = f"Long function '{func_name}' ({len(lines) - func_start} lines) in {rel}:{func_start+1}"
                findings.append(finding)
                await bus.emit("quality.long_function", {
                    "file": rel, "function": func_name, "lines": len(lines) - func_start,
                }, source=name)

            if lines and not lines[0].strip().startswith('"""') and not lines[0].strip().startswith("'''"):
                if len(lines) > 5:
                    findings.append(f"Missing module docstring: {rel}")
                    await bus.emit("quality.missing_docstring", {"file": rel}, source=name)

            for i, line in enumerate(lines):
                if re.match(r'\s*except\s*:', line) or re.match(r'\s*except\s+Exception\s*:', line):
                    findings.append(f"Broad except at {rel}:{i+1}")
                    await bus.emit("quality.broad_except", {"file": rel, "line": i + 1}, source=name)

        except Exception:
            pass
        await asyncio.sleep(0.03)

    await audit.record(AuditEntry(
        agent_id=aid, agent_name=name, action="quality_scan",
        detail=f"Analyzed {files_analyzed} files, found {len(findings)} issues", success=True,
    ))
    return findings


async def scan_disk_waste(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Find reclaimable disk space."""
    findings = []
    pycache_bytes = 0
    pycache_count = 0
    large_files = []
    root = pathlib.Path("/app") if pathlib.Path("/app").exists() else pathlib.Path(".")

    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
            rel = str(f)
            if "__pycache__" in rel or rel.endswith(".pyc"):
                pycache_bytes += size
                pycache_count += 1
            if size > 500_000:
                large_files.append((rel, size))
        except Exception:
            pass

    if pycache_bytes > 0:
        mb = round(pycache_bytes / 1_048_576, 2)
        finding = f"__pycache__ waste: {pycache_count} files, {mb} MB reclaimable"
        findings.append(finding)
        await bus.emit("disk.waste_found", {"type": "__pycache__", "files": pycache_count, "mb": mb}, source=name)

    large_files.sort(key=lambda x: x[1], reverse=True)
    for path, size in large_files[:10]:
        mb = round(size / 1_048_576, 2)
        findings.append(f"Large file: {path} ({mb} MB)")
        await bus.emit("disk.large_file", {"file": path, "mb": mb}, source=name)

    if not findings:
        findings.append("Disk is clean")
        await bus.emit("disk.clean", {"status": "PASS"}, source=name)
    return findings


async def audit_dependencies(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Check installed packages."""
    findings = []
    try:
        out = subprocess.check_output(["pip", "list", "--format=json"], text=True, timeout=15)
        import json
        packages = json.loads(out)
        await bus.emit("deps.scan_start", {"packages": len(packages)}, source=name)

        try:
            outdated_out = subprocess.check_output(
                ["pip", "list", "--outdated", "--format=json"], text=True, timeout=30
            )
            outdated = json.loads(outdated_out)
            for pkg in outdated[:10]:
                finding = f"{pkg['name']} {pkg['version']} -> {pkg['latest_version']}"
                findings.append(finding)
                await bus.emit("deps.update_available", {
                    "package": pkg["name"], "current": pkg["version"],
                    "latest": pkg["latest_version"],
                }, source=name)
        except Exception:
            pass

        if not findings:
            findings.append(f"All {len(packages)} packages healthy")
            await bus.emit("deps.healthy", {"packages": len(packages)}, source=name)
    except Exception as e:
        findings.append(f"Dependency scan error: {e}")
    return findings


async def profile_system(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Real system profiling."""
    findings = []
    samples = []
    for i in range(5):
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
                total = sum(int(x) for x in parts[1:])
                idle = int(parts[4])
                cpu = round(100 * (1 - idle / max(total, 1)), 1)
                samples.append(cpu)
                await bus.emit("profile.cpu_sample", {"sample": i + 1, "cpu_percent": cpu}, source=name)
        except Exception:
            pass
        await asyncio.sleep(1)

    if samples:
        avg = round(sum(samples) / len(samples), 1)
        findings.append(f"CPU: avg {avg}%, peak {max(samples)}%")
        await bus.emit("profile.cpu_result", {"avg": avg, "peak": max(samples)}, source=name)

    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                meminfo[k.strip()] = int(v.strip().split()[0])
        total_mb = meminfo.get("MemTotal", 0) // 1024
        avail_mb = meminfo.get("MemAvailable", 0) // 1024
        findings.append(f"Memory: {total_mb - avail_mb}/{total_mb} MB used")
        await bus.emit("profile.memory", {"total_mb": total_mb, "available_mb": avail_mb}, source=name)
    except Exception:
        pass

    try:
        procs = len([p for p in os.listdir("/proc") if p.isdigit()])
        findings.append(f"Processes: {procs}")
        await bus.emit("profile.processes", {"count": procs}, source=name)
    except Exception:
        pass

    await audit.record(AuditEntry(
        agent_id=aid, agent_name=name, action="profile_complete",
        detail=f"System profile: {len(findings)} metrics", success=True,
    ))
    return findings


async def scan_network(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Network connectivity check."""
    findings = []
    for host in ["pypi.org", "github.com", "arxiv.org"]:
        try:
            start = time.time()
            ip = socket.gethostbyname(host)
            ms = round((time.time() - start) * 1000, 1)
            findings.append(f"DNS {host} -> {ip} ({ms}ms)")
            await bus.emit("network.dns", {"host": host, "ip": ip, "ms": ms}, source=name)
        except Exception as e:
            findings.append(f"DNS FAIL: {host}")
            await bus.emit("network.dns_fail", {"host": host, "error": str(e)[:100]}, source=name)
        await asyncio.sleep(0.2)

    try:
        import httpx
        start = time.time()
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:8420/api/status", timeout=5)
            ms = round((time.time() - start) * 1000, 1)
            findings.append(f"Self-check: {resp.status_code} in {ms}ms")
            await bus.emit("network.self_check", {"status": resp.status_code, "ms": ms}, source=name)
    except Exception:
        pass
    return findings


async def cleanup_task(aid, name, bus: EventBus, audit: AuditTrail) -> list[str]:
    """Clean up __pycache__."""
    findings = []
    cleaned_count = 0
    cleaned_bytes = 0
    root = pathlib.Path("/app") if pathlib.Path("/app").exists() else pathlib.Path(".")

    for d in list(root.rglob("__pycache__")):
        if d.is_dir():
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                count = sum(1 for f in d.rglob("*") if f.is_file())
                import shutil
                shutil.rmtree(d)
                cleaned_count += count
                cleaned_bytes += size
                await bus.emit("cleanup.removed", {"path": str(d), "files": count}, source=name)
            except Exception:
                pass

    if cleaned_count > 0:
        mb = round(cleaned_bytes / 1_048_576, 2)
        findings.append(f"Cleaned {cleaned_count} files, freed {mb} MB")
        await bus.emit("cleanup.complete", {"files_removed": cleaned_count, "mb_freed": mb}, source=name)
    else:
        findings.append("Nothing to clean")
        await bus.emit("cleanup.nothing", {"status": "clean"}, source=name)
    return findings
