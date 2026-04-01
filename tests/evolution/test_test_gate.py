"""Tests for the evolution regression gate."""
from __future__ import annotations

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from agos.evolution.test_gate import RegressionGate, GateResult


def test_result_model_defaults():
    """GateResult has sensible defaults."""
    r = GateResult()
    assert r.id  # auto-generated
    assert r.passed is False
    assert r.output == ""
    assert r.error == ""
    assert r.execution_time_ms == 0.0


def test_result_model_with_values():
    """GateResult accepts explicit values."""
    r = GateResult(
        passed=True,
        output="655 passed in 30s",
        file_tested="foo.py",
        test_count="655 passed",
    )
    assert r.passed is True
    assert r.file_tested == "foo.py"
    assert r.test_count == "655 passed"


@pytest.mark.asyncio
async def test_gate_passes_on_success(tmp_path):
    """Gate returns passed=True when pytest exits 0."""
    test_file = tmp_path / "test_trivial.py"
    test_file.write_text("def test_ok(): assert True\n")

    gate = RegressionGate(timeout=30, test_path=str(tmp_path))
    result = await gate.check("some_evolved.py")

    assert result.passed is True
    assert result.execution_time_ms > 0
    assert result.file_tested == "some_evolved.py"


@pytest.mark.asyncio
async def test_gate_fails_on_test_failure(tmp_path):
    """Gate returns passed=False when pytest exits non-zero."""
    test_file = tmp_path / "test_failing.py"
    test_file.write_text("def test_bad(): assert False\n")

    gate = RegressionGate(timeout=30, test_path=str(tmp_path))
    result = await gate.check("broken_evolved.py")

    assert result.passed is False
    assert result.file_tested == "broken_evolved.py"


@pytest.mark.asyncio
async def test_gate_timeout():
    """Gate handles subprocess timeout gracefully."""
    gate = RegressionGate(timeout=1, test_path="tests/")

    # Mock subprocess to simulate a hang
    with patch("agos.evolution.test_gate.asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        result = await gate.check("slow_evolved.py")

    assert result.passed is False
    assert "Timeout" in result.error


@pytest.mark.asyncio
async def test_gate_history():
    """History tracks results, most recent first."""
    gate = RegressionGate(timeout=5, test_path="tests/")

    with patch("agos.evolution.test_gate.asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"1 passed", b"")
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        mock_exec.return_value = mock_proc

        await gate.check("file1.py")
        await gate.check("file2.py")

    history = gate.history()
    assert len(history) == 2
    assert history[0].file_tested == "file2.py"  # most recent first
    assert history[1].file_tested == "file1.py"


@pytest.mark.asyncio
async def test_gate_extracts_test_count():
    """Gate extracts the pytest summary line from output."""
    gate = RegressionGate(timeout=5, test_path="tests/")

    output = b"collected 655 items\n...\n655 passed in 30.2s\n"
    with patch("agos.evolution.test_gate.asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output, b"")
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        mock_exec.return_value = mock_proc

        result = await gate.check("evolved.py")

    assert result.passed is True
    assert "655 passed" in result.test_count


@pytest.mark.asyncio
async def test_gate_handles_subprocess_error():
    """Gate handles unexpected subprocess errors gracefully."""
    gate = RegressionGate(timeout=5, test_path="tests/")

    with patch("agos.evolution.test_gate.asyncio.create_subprocess_exec",
               side_effect=OSError("no pytest")):
        result = await gate.check("evolved.py")

    assert result.passed is False
    assert "OSError" in result.error


import asyncio  # noqa: E402 — needed for TimeoutError in test_gate_timeout
