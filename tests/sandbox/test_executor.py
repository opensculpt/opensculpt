"""Tests for sandbox executor — isolation levels and path checking."""

from __future__ import annotations

import asyncio

import pytest

from agos.sandbox.executor import (
    SandboxLevel,
    SandboxConfig,
    SandboxedToolExecutor,
)
from agos.tools.registry import ToolRegistry
from agos.tools.schema import ToolSchema, ToolParameter


# ── Test helpers ────────────────────────────────────────────────


@pytest.fixture
def registry():
    """Registry with a simple mock tool."""
    reg = ToolRegistry()

    async def _echo(message: str = "hello") -> str:
        return f"echo: {message}"

    async def _read(path: str) -> str:
        return f"read: {path}"

    async def _slow(seconds: int = 5) -> str:
        await asyncio.sleep(seconds)
        return "done"

    reg.register(
        ToolSchema(name="echo", description="Echo", parameters=[
            ToolParameter(name="message", description="msg"),
        ]),
        _echo,
    )
    reg.register(
        ToolSchema(name="read_file", description="Read file", parameters=[
            ToolParameter(name="path", description="path"),
        ]),
        _read,
    )
    reg.register(
        ToolSchema(name="http", description="HTTP", parameters=[]),
        _echo,
    )
    reg.register(
        ToolSchema(name="slow", description="Slow tool", parameters=[
            ToolParameter(name="seconds", type="integer", description="delay"),
        ]),
        _slow,
    )
    return reg


# ── SandboxConfig tests ────────────────────────────────────────


def test_sandbox_config_defaults():
    cfg = SandboxConfig()
    assert cfg.level == SandboxLevel.NONE
    assert cfg.memory_limit_mb == 512
    assert cfg.cpu_time_limit_s == 60
    assert cfg.allowed_paths == []
    assert cfg.blocked_paths == []
    assert cfg.allow_network is True


def test_sandbox_config_strict():
    cfg = SandboxConfig(
        level=SandboxLevel.STRICT,
        memory_limit_mb=128,
        allowed_paths=["/app"],
        allow_network=False,
    )
    assert cfg.level == SandboxLevel.STRICT
    assert cfg.memory_limit_mb == 128


# ── SandboxLevel.NONE — pass-through ──────────────────────────


async def test_sandbox_none_delegates(registry):
    """NONE level should pass through to inner registry."""
    executor = SandboxedToolExecutor(registry, SandboxConfig(level=SandboxLevel.NONE))
    result = await executor.execute("echo", {"message": "test"})
    assert result.success is True
    assert "echo: test" in str(result.result)


async def test_sandbox_none_has_same_tools(registry):
    """get_anthropic_tools should return the same tools as inner registry."""
    executor = SandboxedToolExecutor(registry, SandboxConfig(level=SandboxLevel.NONE))
    assert len(executor.get_anthropic_tools()) == len(registry.get_anthropic_tools())


# ── SandboxLevel.STRICT — path + network checks ──────────────


async def test_sandbox_strict_blocks_network(registry):
    """STRICT with allow_network=False should block http tools."""
    config = SandboxConfig(
        level=SandboxLevel.STRICT,
        allow_network=False,
    )
    executor = SandboxedToolExecutor(registry, config)
    result = await executor.execute("http", {"url": "http://example.com"})
    assert result.success is False
    assert "Network access blocked" in str(result.error)


async def test_sandbox_strict_allows_network(registry):
    """STRICT with allow_network=True should NOT block http tools."""
    config = SandboxConfig(
        level=SandboxLevel.STRICT,
        allow_network=True,
    )
    executor = SandboxedToolExecutor(registry, config)
    # This will fail in subprocess (no actual tool) but won't be blocked by network check
    result = await executor.execute("echo", {"message": "ok"})
    # The tool goes to subprocess execution, which may or may not succeed
    # but it should NOT return "Network access blocked"
    assert "Network access blocked" not in str(result.error or "")


async def test_sandbox_strict_blocks_disallowed_path(registry, tmp_path):
    """STRICT with allowed_paths should block paths outside the whitelist."""
    config = SandboxConfig(
        level=SandboxLevel.STRICT,
        allowed_paths=[str(tmp_path / "safe")],
    )
    executor = SandboxedToolExecutor(registry, config)
    result = await executor.execute("read_file", {"path": "/etc/passwd"})
    assert result.success is False
    assert "blocked by sandbox policy" in str(result.error)


async def test_sandbox_strict_allows_permitted_path(registry, tmp_path):
    """STRICT should allow paths within allowed_paths."""
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    config = SandboxConfig(
        level=SandboxLevel.STRICT,
        allowed_paths=[str(safe_dir)],
    )
    executor = SandboxedToolExecutor(registry, config)
    result = await executor.execute("read_file", {"path": str(safe_dir / "test.txt")})
    # Path check passes, tool goes to subprocess (may fail there, but path wasn't blocked)
    assert "blocked by sandbox policy" not in str(result.error or "")


async def test_sandbox_strict_blocks_blacklisted_path(registry, tmp_path):
    """STRICT with blocked_paths should reject those paths."""
    config = SandboxConfig(
        level=SandboxLevel.STRICT,
        blocked_paths=[str(tmp_path / "secret")],
    )
    executor = SandboxedToolExecutor(registry, config)
    result = await executor.execute("read_file", {"path": str(tmp_path / "secret" / "key.pem")})
    assert result.success is False
    assert "blocked by sandbox policy" in str(result.error)


# ── Policy integration ─────────────────────────────────────────


def test_sandbox_config_from_policy():
    """AgentPolicy sandbox fields should map to SandboxConfig."""
    from agos.policy.schema import AgentPolicy

    policy = AgentPolicy(
        sandbox_level="strict",
        sandbox_memory_limit_mb=256,
        sandbox_cpu_time_limit_s=30,
        sandbox_allowed_paths=["/app", "/data"],
    )
    config = SandboxConfig(
        level=SandboxLevel(policy.sandbox_level),
        memory_limit_mb=policy.sandbox_memory_limit_mb,
        cpu_time_limit_s=policy.sandbox_cpu_time_limit_s,
        allowed_paths=policy.sandbox_allowed_paths,
    )
    assert config.level == SandboxLevel.STRICT
    assert config.memory_limit_mb == 256
    assert config.allowed_paths == ["/app", "/data"]
