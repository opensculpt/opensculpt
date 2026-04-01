"""Tests for built-in tools."""

import pytest
import tempfile
from pathlib import Path

from agos.tools.builtins import _file_read, _file_write, _shell_exec, _python_exec


@pytest.mark.asyncio
async def test_file_read():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello agos")
        path = f.name

    result = await _file_read(path)
    assert result == "hello agos"
    Path(path).unlink()


@pytest.mark.asyncio
async def test_file_read_not_found():
    result = await _file_read("/nonexistent/file.txt")
    assert "Error" in result


@pytest.mark.asyncio
async def test_file_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "test.txt")
        result = await _file_write(path, "hello world")
        assert "Written" in result
        assert Path(path).read_text() == "hello world"


@pytest.mark.asyncio
async def test_shell_exec():
    result = await _shell_exec("echo hello")
    assert "hello" in result
    assert "exit_code=0" in result


@pytest.mark.asyncio
async def test_shell_exec_timeout():
    result = await _shell_exec("sleep 10", timeout=1)
    assert "timed out" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_python_exec():
    result = await _python_exec("print(2 + 2)")
    assert "4" in result
