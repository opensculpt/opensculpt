"""Sandbox — safe execution environment for testing research code.

Research papers come with code snippets and patterns. The sandbox
provides a restricted environment to test these before integrating
them into agos. Uses subprocess isolation with strict timeouts
and resource limits.
"""

from __future__ import annotations

import ast
import asyncio
import os
import tempfile
import textwrap
from datetime import datetime

from pydantic import BaseModel, Field

from agos.types import new_id

# Modules that are NEVER allowed in sandbox.
# SECURITY: These can execute arbitrary commands, access the filesystem
# destructively, or exfiltrate data over the network.
BLOCKED_IMPORTS = {
    "ctypes",
    "signal",
    "importlib",  # no dynamic code loading
    "multiprocessing", "threading",  # no spawning threads/processes
    "subprocess",  # arbitrary command execution
    "os",  # os.system, os.popen, file manipulation
    "shutil",  # destructive file operations (rmtree, move)
    "socket",  # raw network access
    "http",  # network access
    "urllib",  # network access
    "requests",  # network access (third-party)
    "sys",  # sys.exit, sys.modules manipulation
}

# Safe modules that evolved tools commonly need.
# SECURITY: subprocess, os, shutil, socket are BLOCKED for community/evolved code.
# Evolved tools that need shell/network access must use the OS agent's tool system,
# not raw imports. This prevents community code from running arbitrary commands.
ALLOWED_IMPORTS = {
    "json", "re", "math", "random", "datetime", "time",
    "collections", "itertools", "functools", "typing",
    "dataclasses", "enum", "abc", "copy", "hashlib",
    "uuid", "textwrap", "string", "operator",
    "asyncio",  # needed for async patterns
    "pathlib",  # read-only path manipulation (no exec)
    "logging",  # tools need logging
}

DEFAULT_TIMEOUT = 10  # seconds
MAX_OUTPUT_SIZE = 50_000  # chars


class SandboxResult(BaseModel):
    """Result from executing code in the sandbox."""

    id: str = Field(default_factory=new_id)
    success: bool = False
    output: str = ""
    error: str = ""
    execution_time_ms: float = 0.0
    code_hash: str = ""
    blocked_imports: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def passed(self) -> bool:
        return self.success and not self.error


class SandboxValidation(BaseModel):
    """Static validation result before execution."""

    safe: bool = True
    issues: list[str] = Field(default_factory=list)
    blocked_imports: list[str] = Field(default_factory=list)
    has_syntax_errors: bool = False


class Sandbox:
    """Restricted execution environment for testing research code.

    Security layers:
    1. Static analysis — blocks dangerous imports and operations
    2. Subprocess isolation — code runs in a separate process
    3. Timeout — kills long-running code
    4. Output limits — truncates excessive output
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._results: list[SandboxResult] = []

    def validate(self, code: str) -> SandboxValidation:
        """Static analysis: check if code is safe to execute."""
        validation = SandboxValidation()

        # Check syntax
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            validation.safe = False
            validation.has_syntax_errors = True
            validation.issues.append(f"Syntax error: {e}")
            return validation

        # Walk the AST looking for dangerous patterns
        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in BLOCKED_IMPORTS:
                        validation.safe = False
                        validation.blocked_imports.append(module)
                        validation.issues.append(f"Blocked import: {module}")

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module in BLOCKED_IMPORTS:
                        validation.safe = False
                        validation.blocked_imports.append(module)
                        validation.issues.append(f"Blocked import: {module}")

            # Check for dangerous calls
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id in ("exec", "eval", "compile", "__import__", "open"):
                        validation.safe = False
                        validation.issues.append(f"Blocked builtin: {func.id}()")
                    elif func.id == "getattr":
                        validation.safe = False
                        validation.issues.append("Blocked: getattr() — no dynamic attribute access in sandbox")

            # Block dunder attribute access (__class__, __subclasses__, etc.)
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    if node.attr not in ("__init__", "__str__", "__repr__",
                                         "__len__", "__iter__", "__next__",
                                         "__enter__", "__exit__", "__eq__",
                                         "__hash__", "__bool__", "__name__"):
                        validation.safe = False
                        validation.issues.append(f"Blocked dunder: .{node.attr}")

        return validation

    async def execute(self, code: str, test_input: str = "") -> SandboxResult:
        """Execute code in a sandboxed subprocess."""
        import hashlib
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        # Step 1: Validate
        validation = self.validate(code)
        if not validation.safe:
            result = SandboxResult(
                success=False,
                error=f"Code failed safety check: {'; '.join(validation.issues)}",
                code_hash=code_hash,
                blocked_imports=validation.blocked_imports,
            )
            self._results.append(result)
            return result

        # Step 2: Write to temp file and execute in subprocess
        result = await self._run_isolated(code, code_hash, test_input)
        self._results.append(result)
        return result

    async def test_pattern(self, code_snippet: str, test_code: str = "") -> SandboxResult:
        """Test a code pattern by running it with optional test harness."""
        full_code = code_snippet
        if test_code:
            full_code = f"{code_snippet}\n\n# === Test Harness ===\n{test_code}"
        return await self.execute(full_code)

    def history(self, limit: int = 20) -> list[SandboxResult]:
        """Get recent sandbox execution results."""
        return list(reversed(self._results[-limit:]))

    def stats(self) -> dict:
        """Get sandbox execution statistics."""
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        failed = total - passed
        return {
            "total_executions": total,
            "passed": passed,
            "failed": failed,
            "success_rate": passed / total if total else 0,
        }

    async def _run_isolated(self, code: str, code_hash: str, test_input: str) -> SandboxResult:
        """Run code in an isolated subprocess with timeout."""
        import time
        start = time.monotonic()

        # Wrap code to capture output
        wrapper = textwrap.dedent("""\
        import sys
        import io
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            exec(open(sys.argv[1]).read())
            output = sys.stdout.getvalue()
            errors = sys.stderr.getvalue()
            if errors:
                print(f"STDERR: {errors}", file=sys.__stderr__)
            print(output, end="", file=sys.__stdout__)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.__stderr__)
            sys.exit(1)
        """)

        try:
            # Write code to temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="agos_sandbox_"
            ) as f:
                f.write(code)
                code_file = f.name

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="agos_runner_"
            ) as f:
                f.write(wrapper)
                runner_file = f.name

            # Execute in subprocess
            proc = await asyncio.create_subprocess_exec(
                "python", runner_file, code_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if test_input else None,
            )

            try:
                stdin_data = test_input.encode() if test_input else None
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_data),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                elapsed = (time.monotonic() - start) * 1000
                return SandboxResult(
                    success=False,
                    error=f"Timeout: code exceeded {self._timeout}s limit",
                    execution_time_ms=elapsed,
                    code_hash=code_hash,
                )

            elapsed = (time.monotonic() - start) * 1000
            output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]
            error = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]

            return SandboxResult(
                success=proc.returncode == 0,
                output=output,
                error=error,
                execution_time_ms=elapsed,
                code_hash=code_hash,
            )

        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return SandboxResult(
                success=False,
                error=f"Sandbox error: {type(e).__name__}: {e}",
                execution_time_ms=elapsed,
                code_hash=code_hash,
            )
        finally:
            # Cleanup temp files
            for path in (code_file, runner_file):
                try:
                    os.unlink(path)
                except Exception:
                    pass
