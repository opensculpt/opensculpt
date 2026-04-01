"""Tests for the Sandbox execution environment."""

import pytest

from agos.evolution.sandbox import (
    Sandbox, SandboxResult, SandboxValidation,
    BLOCKED_IMPORTS, ALLOWED_IMPORTS,
    DEFAULT_TIMEOUT, MAX_OUTPUT_SIZE,
)


# ── Static validation tests ─────────────────────────────────────

def test_validate_safe_code():
    sandbox = Sandbox()
    result = sandbox.validate("x = 1 + 2\nprint(x)")
    assert result.safe is True
    assert result.issues == []
    assert not result.has_syntax_errors


def test_validate_blocks_os_import():
    sandbox = Sandbox()
    result = sandbox.validate("import os\nos.system('rm -rf /')")
    assert result.safe is False
    assert "os" in result.blocked_imports
    assert any("os" in issue for issue in result.issues)


def test_validate_blocks_subprocess():
    sandbox = Sandbox()
    result = sandbox.validate("import subprocess\nsubprocess.run(['ls'])")
    assert result.safe is False
    assert "subprocess" in result.blocked_imports


def test_validate_blocks_from_import():
    sandbox = Sandbox()
    result = sandbox.validate("from os import path")
    assert result.safe is False
    assert "os" in result.blocked_imports


def test_validate_blocks_socket():
    sandbox = Sandbox()
    result = sandbox.validate("import socket\nsocket.socket()")
    assert result.safe is False
    assert "socket" in result.blocked_imports


def test_validate_blocks_exec():
    sandbox = Sandbox()
    result = sandbox.validate("exec('print(1)')")
    assert result.safe is False
    assert any("exec" in issue for issue in result.issues)


def test_validate_blocks_eval():
    sandbox = Sandbox()
    result = sandbox.validate("x = eval('1+2')")
    assert result.safe is False
    assert any("eval" in issue for issue in result.issues)


def test_validate_blocks_compile():
    sandbox = Sandbox()
    result = sandbox.validate("compile('pass', '<string>', 'exec')")
    assert result.safe is False
    assert any("compile" in issue for issue in result.issues)


def test_validate_blocks_dunder_import():
    sandbox = Sandbox()
    result = sandbox.validate("m = __import__('os')")
    assert result.safe is False
    assert any("__import__" in issue for issue in result.issues)


def test_validate_syntax_error():
    sandbox = Sandbox()
    result = sandbox.validate("def broken(:\n    pass")
    assert result.safe is False
    assert result.has_syntax_errors is True
    assert any("Syntax" in issue for issue in result.issues)


def test_validate_allows_safe_imports():
    sandbox = Sandbox()
    result = sandbox.validate("import json\nimport math\nimport re")
    assert result.safe is True


def test_validate_allows_asyncio():
    sandbox = Sandbox()
    result = sandbox.validate("import asyncio\nasync def main(): pass")
    assert result.safe is True


def test_validate_multiple_blocked():
    sandbox = Sandbox()
    result = sandbox.validate("import os\nimport subprocess\nimport shutil")
    assert result.safe is False
    assert len(result.blocked_imports) == 3


# ── Execution tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_safe_code():
    sandbox = Sandbox(timeout=5)
    result = await sandbox.execute("print('hello world')")
    assert result.success is True
    assert "hello world" in result.output
    assert result.execution_time_ms > 0
    assert result.code_hash


@pytest.mark.asyncio
async def test_execute_blocked_code():
    sandbox = Sandbox()
    result = await sandbox.execute("import os\nprint(os.getcwd())")
    assert result.success is False
    assert "safety check" in result.error.lower() or "blocked" in result.error.lower()
    assert "os" in result.blocked_imports


@pytest.mark.asyncio
async def test_execute_math():
    sandbox = Sandbox(timeout=5)
    result = await sandbox.execute("import math\nprint(math.pi)")
    assert result.success is True
    assert "3.14" in result.output


@pytest.mark.asyncio
async def test_execute_runtime_error():
    sandbox = Sandbox(timeout=5)
    result = await sandbox.execute("raise ValueError('oops')")
    assert result.success is False
    assert result.error  # should contain error info


@pytest.mark.asyncio
async def test_execute_timeout():
    sandbox = Sandbox(timeout=2)
    result = await sandbox.execute("import time\ntime.sleep(10)")
    assert result.success is False
    assert "timeout" in result.error.lower() or "Timeout" in result.error


@pytest.mark.asyncio
async def test_test_pattern():
    sandbox = Sandbox(timeout=5)
    code = "def add(a, b): return a + b"
    test_code = "assert add(2, 3) == 5\nprint('PASS')"
    result = await sandbox.test_pattern(code, test_code)
    assert result.success is True
    assert "PASS" in result.output


@pytest.mark.asyncio
async def test_test_pattern_failure():
    sandbox = Sandbox(timeout=5)
    code = "def add(a, b): return a - b"  # bug!
    test_code = "assert add(2, 3) == 5"
    result = await sandbox.test_pattern(code, test_code)
    assert result.success is False


@pytest.mark.asyncio
async def test_history():
    sandbox = Sandbox(timeout=5)
    await sandbox.execute("print('one')")
    await sandbox.execute("print('two')")

    history = sandbox.history()
    assert len(history) == 2
    # Most recent first
    assert "two" in history[0].output
    assert "one" in history[1].output


@pytest.mark.asyncio
async def test_stats():
    sandbox = Sandbox(timeout=5)
    await sandbox.execute("print('ok')")
    await sandbox.execute("import os")  # blocked

    stats = sandbox.stats()
    assert stats["total_executions"] == 2
    assert stats["passed"] == 1
    assert stats["failed"] == 1
    assert stats["success_rate"] == 0.5


def test_sandbox_result_passed():
    r = SandboxResult(success=True, output="ok", error="")
    assert r.passed is True

    r2 = SandboxResult(success=True, error="some warning")
    assert r2.passed is False

    r3 = SandboxResult(success=False)
    assert r3.passed is False


def test_sandbox_result_model():
    r = SandboxResult(
        success=True,
        output="hello",
        execution_time_ms=42.0,
        code_hash="abc123",
    )
    assert r.id
    assert r.output == "hello"
    assert r.execution_time_ms == 42.0


def test_validation_model():
    v = SandboxValidation(
        safe=False,
        issues=["Blocked import: os"],
        blocked_imports=["os"],
        has_syntax_errors=False,
    )
    assert not v.safe
    assert len(v.issues) == 1


def test_blocked_imports_list():
    assert "os" in BLOCKED_IMPORTS
    assert "subprocess" in BLOCKED_IMPORTS
    assert "socket" in BLOCKED_IMPORTS
    assert "shutil" in BLOCKED_IMPORTS
    assert "sys" in BLOCKED_IMPORTS


def test_allowed_imports_list():
    assert "json" in ALLOWED_IMPORTS
    assert "math" in ALLOWED_IMPORTS
    assert "asyncio" in ALLOWED_IMPORTS
    assert "re" in ALLOWED_IMPORTS


def test_default_constants():
    assert DEFAULT_TIMEOUT == 10
    assert MAX_OUTPUT_SIZE == 50_000
