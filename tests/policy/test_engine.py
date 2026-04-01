"""Tests for the policy engine."""

import pytest

from agos.policy.schema import AgentPolicy
from agos.policy.engine import PolicyEngine
from agos.exceptions import PolicyViolationError


def test_default_policy_allows_all():
    engine = PolicyEngine()
    engine.check_tool("any-agent", "file_read")  # should not raise


def test_deny_tool_raises():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(denied_tools=["shell_exec"]))

    with pytest.raises(PolicyViolationError, match="shell_exec"):
        engine.check_tool("analyst", "shell_exec")


def test_per_agent_policy():
    engine = PolicyEngine()
    engine.assign("coder", AgentPolicy(denied_tools=["web_search"]))

    # Coder can't use web_search
    with pytest.raises(PolicyViolationError):
        engine.check_tool("coder", "web_search")

    # Other agents can
    engine.check_tool("researcher", "web_search")


def test_remove_policy_falls_back():
    engine = PolicyEngine()
    engine.assign("coder", AgentPolicy(denied_tools=["shell_exec"]))

    with pytest.raises(PolicyViolationError):
        engine.check_tool("coder", "shell_exec")

    engine.remove("coder")
    engine.check_tool("coder", "shell_exec")  # falls back to default


def test_check_budget():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(max_tokens=10_000))

    engine.check_budget("agent", 5_000)  # OK

    with pytest.raises(PolicyViolationError, match="token budget"):
        engine.check_budget("agent", 15_000)


def test_check_turns():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(max_turns=10))

    engine.check_turns("agent", 5)  # OK

    with pytest.raises(PolicyViolationError, match="turn limit"):
        engine.check_turns("agent", 15)


def test_rate_limit():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(max_tool_calls_per_minute=3))

    engine.check_tool("fast-agent", "file_read")
    engine.check_tool("fast-agent", "file_read")
    engine.check_tool("fast-agent", "file_read")

    with pytest.raises(PolicyViolationError, match="rate limit"):
        engine.check_tool("fast-agent", "file_read")


def test_rate_limit_per_agent():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(max_tool_calls_per_minute=2))

    engine.check_tool("agent-a", "file_read")
    engine.check_tool("agent-a", "file_read")

    # agent-b has its own counter
    engine.check_tool("agent-b", "file_read")


def test_list_policies():
    engine = PolicyEngine()
    engine.assign("coder", AgentPolicy(denied_tools=["shell_exec"], max_tokens=50_000))
    engine.assign("analyst", AgentPolicy(read_only=True))

    policies = engine.list_policies()
    assert len(policies) == 2
    names = {p["agent_name"] for p in policies}
    assert names == {"coder", "analyst"}


def test_get_policy_returns_specific_over_default():
    engine = PolicyEngine()
    engine.set_default(AgentPolicy(max_tokens=100_000))
    engine.assign("coder", AgentPolicy(max_tokens=50_000))

    assert engine.get_policy("coder").max_tokens == 50_000
    assert engine.get_policy("researcher").max_tokens == 100_000
