"""SecurityAgent — continuous vulnerability scanning for AGOS.

Runs as a system agent in the boot cycle. Scans:
1. Evolved code for sandbox-bypass patterns
2. Workspace files for secret leaks
3. Configuration for insecure defaults
4. Community contributions for malicious patterns
5. Running system for known vulnerability signatures

Findings are emitted via EventBus and recorded in the audit trail.
The evolution pipeline also reads security research papers so the
security system improves over time.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re

from agos.config import settings as _settings
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail, AuditEntry

SRC = pathlib.Path("/app/agos")
if not SRC.exists():
    SRC = pathlib.Path("agos")

def _get_evolved_dir() -> pathlib.Path:
    from agos.config import settings
    return pathlib.Path(settings.workspace_dir) / "evolved"

def _get_community_dir() -> pathlib.Path:
    from agos.config import settings
    return pathlib.Path(settings.workspace_dir) / "community" / "evolved"

EVOLVED_DIR = _get_evolved_dir()
COMMUNITY_DIR = _get_community_dir()

# ── Dangerous AST patterns that sandbox-bypass attacks use ─────

_BYPASS_PATTERNS: list[tuple[str, str]] = [
    (r'__subclasses__', "class hierarchy traversal (sandbox escape)"),
    (r'__mro__', "MRO traversal (sandbox escape)"),
    (r'__globals__', "globals access (sandbox escape)"),
    (r'__builtins__\s*\[', "builtins dict access (sandbox escape)"),
    (r'__import__\s*\(', "dynamic import (sandbox escape)"),
    (r'getattr\s*\(\s*__builtins__', "builtins getattr (sandbox escape)"),
    (r'type\s*\(\s*["\']', "dynamic type creation"),
    (r'vars\s*\(\s*\)', "vars() access (namespace leak)"),
    (r'globals\s*\(\s*\)', "globals() access (namespace leak)"),
    (r'locals\s*\(\s*\)', "locals() access (namespace leak)"),
    (r'compile\s*\(', "dynamic compilation"),
    (r'pickle\.loads', "pickle deserialization (RCE risk)"),
    (r'marshal\.loads', "marshal deserialization (RCE risk)"),
    (r'os\.system\s*\(', "os.system call"),
    (r'subprocess\.\w+\s*\(', "subprocess call"),
    (r'eval\s*\(', "eval call"),
    (r'exec\s*\(', "exec call"),
]

# Insecure configuration patterns
_CONFIG_ISSUES: list[tuple[str, str, str]] = [
    ("approval_mode", "auto", "approval_mode is 'auto' — all tool calls auto-approved"),
    ("dashboard_api_key", "", "no dashboard API key set — dashboard is unauthenticated"),
]


async def scan_vulnerabilities(
    aid, name, bus: EventBus, audit: AuditTrail,
) -> list[str]:
    """Full vulnerability scan — evolved code, config, and codebase."""
    findings: list[str] = []

    # Phase 1: Scan evolved code for bypass patterns
    evolved_findings = await _scan_evolved_code(bus, name)
    findings.extend(evolved_findings)

    # Phase 2: Scan community contributions
    community_findings = await _scan_community_code(bus, name)
    findings.extend(community_findings)

    # Phase 3: Check configuration security
    config_findings = await _scan_config(bus, name)
    findings.extend(config_findings)

    # Phase 4: Scan codebase for injection risks
    injection_findings = await _scan_injection_risks(bus, name)
    findings.extend(injection_findings)

    # Phase 5: Scan for exposed credentials in workspace
    cred_findings = await _scan_workspace_secrets(bus, name)
    findings.extend(cred_findings)

    # Record summary
    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        for sev in severity_counts:
            if f.startswith(f"[{sev}]"):
                severity_counts[sev] += 1

    await audit.record(AuditEntry(
        agent_id=aid, agent_name=name, action="vulnerability_scan",
        detail=(
            f"Scan complete: {len(findings)} findings "
            f"(C:{severity_counts['CRITICAL']} H:{severity_counts['HIGH']} "
            f"M:{severity_counts['MEDIUM']} L:{severity_counts['LOW']})"
        ),
        success=True,
    ))

    if not findings:
        findings.append("No vulnerabilities detected — system is clean")
        await bus.emit("security.vuln_clear", {"status": "PASS"}, source=name)
    else:
        await bus.emit("security.vuln_report", {
            "total": len(findings),
            **severity_counts,
        }, source=name)

    return findings


async def _scan_evolved_code(bus: EventBus, source: str) -> list[str]:
    """Scan .agos/evolved/ for sandbox-bypass patterns."""
    findings: list[str] = []
    if not EVOLVED_DIR.exists():
        return findings

    for py_file in EVOLVED_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            code = py_file.read_text(errors="ignore")
            file_findings = _check_bypass_patterns(code, str(py_file))
            for finding in file_findings:
                findings.append(finding)
                await bus.emit("security.evolved_vuln", {
                    "severity": "CRITICAL",
                    "file": str(py_file),
                    "detail": finding,
                }, source=source)
        except Exception:
            pass
        await asyncio.sleep(0.02)

    return findings


async def _scan_community_code(bus: EventBus, source: str) -> list[str]:
    """Scan community contributed code for malicious patterns."""
    findings: list[str] = []
    if not COMMUNITY_DIR.exists():
        return findings

    for py_file in COMMUNITY_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            code = py_file.read_text(errors="ignore")
            file_findings = _check_bypass_patterns(code, str(py_file))
            for finding in file_findings:
                findings.append(finding)
                await bus.emit("security.community_vuln", {
                    "severity": "CRITICAL",
                    "file": str(py_file),
                    "detail": finding,
                }, source=source)

            # Also check for network calls in community code
            net_patterns = [
                (r'requests\.\w+\s*\(', "HTTP request in community code"),
                (r'urllib\.\w+', "urllib usage in community code"),
                (r'socket\.\w+', "socket usage in community code"),
                (r'httpx\.\w+', "httpx usage in community code"),
            ]
            for pat, desc in net_patterns:
                for match in re.finditer(pat, code):
                    line = code[:match.start()].count("\n") + 1
                    finding = f"[HIGH] {desc} at {py_file}:{line}"
                    findings.append(finding)
                    await bus.emit("security.community_vuln", {
                        "severity": "HIGH",
                        "file": str(py_file),
                        "line": line,
                        "detail": desc,
                    }, source=source)
        except Exception:
            pass
        await asyncio.sleep(0.02)

    return findings


async def _scan_config(bus: EventBus, source: str) -> list[str]:
    """Check for insecure configuration."""
    findings: list[str] = []

    for attr, insecure_value, msg in _CONFIG_ISSUES:
        val = getattr(_settings, attr, None)
        if val == insecure_value:
            finding = f"[MEDIUM] Config: {msg}"
            findings.append(finding)
            await bus.emit("security.config_issue", {
                "severity": "MEDIUM",
                "setting": attr,
                "detail": msg,
            }, source=source)

    # Check if test gate is disabled
    if not _settings.evolution_test_gate:
        finding = "[HIGH] Config: evolution_test_gate is disabled — evolved code not regression-tested"
        findings.append(finding)
        await bus.emit("security.config_issue", {
            "severity": "HIGH",
            "setting": "evolution_test_gate",
            "detail": "Test gate disabled",
        }, source=source)

    return findings


async def _scan_injection_risks(bus: EventBus, source: str) -> list[str]:
    """Scan source code for injection vulnerabilities."""
    findings: list[str] = []

    injection_patterns = [
        (r'create_subprocess_shell\s*\(', "shell injection risk (use create_subprocess_exec)"),
        (r'f["\']SELECT\s', "potential SQL injection via f-string"),
        (r'f["\']INSERT\s', "potential SQL injection via f-string"),
        (r'f["\']DELETE\s', "potential SQL injection via f-string"),
        (r'\.format\(.*\).*(?:SELECT|INSERT|DELETE)', "SQL injection via str.format"),
    ]

    for f in SRC.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        try:
            content = f.read_text(errors="ignore")
            rel = str(f.relative_to(SRC.parent))
            for pat, desc in injection_patterns:
                for match in re.finditer(pat, content):
                    line = content[:match.start()].count("\n") + 1
                    finding = f"[MEDIUM] {desc} at {rel}:{line}"
                    findings.append(finding)
                    await bus.emit("security.injection_risk", {
                        "severity": "MEDIUM",
                        "file": rel,
                        "line": line,
                        "detail": desc,
                    }, source=source)
        except Exception:
            pass
        await asyncio.sleep(0.02)

    return findings


async def _scan_workspace_secrets(bus: EventBus, source: str) -> list[str]:
    """Scan workspace (.agos/) for leaked credentials."""
    findings: list[str] = []
    workspace = pathlib.Path(".agos")
    if not workspace.exists():
        return findings

    secret_patterns = [
        (r'(?i)sk-ant-[a-zA-Z0-9\-_]{20,}', "Anthropic API key"),
        (r'(?i)sk-[a-zA-Z0-9]{20,}', "OpenAI-style API key"),
        (r'(?i)ghp_[a-zA-Z0-9]{36}', "GitHub PAT"),
        (r'(?i)AKIA[0-9A-Z]{16}', "AWS access key"),
        (r'(?i)-----BEGIN (RSA |EC )?PRIVATE KEY', "private key"),
    ]

    for f in workspace.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix in (".db", ".sqlite", ".pyc"):
            continue
        try:
            content = f.read_text(errors="ignore")
            for pat, desc in secret_patterns:
                if re.search(pat, content):
                    finding = f"[CRITICAL] {desc} found in {f}"
                    findings.append(finding)
                    await bus.emit("security.workspace_secret", {
                        "severity": "CRITICAL",
                        "file": str(f),
                        "detail": desc,
                    }, source=source)
        except Exception:
            pass

    return findings


def _check_bypass_patterns(code: str, filepath: str) -> list[str]:
    """Check code for known sandbox-bypass patterns."""
    findings: list[str] = []
    for pat, desc in _BYPASS_PATTERNS:
        for match in re.finditer(pat, code):
            line = code[:match.start()].count("\n") + 1
            findings.append(f"[CRITICAL] {desc} at {filepath}:{line}")

    # AST-level checks for dynamic attribute access chains
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in ("__subclasses__", "__mro__", "__globals__",
                                 "__code__", "__func__", "__self__",
                                 "__dict__", "__class__"):
                    line = getattr(node, "lineno", 0)
                    findings.append(
                        f"[CRITICAL] AST: dunder access .{node.attr} at {filepath}:{line}"
                    )
    except SyntaxError:
        pass

    return findings
