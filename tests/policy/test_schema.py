"""Tests for the policy schema."""

from agos.policy.schema import AgentPolicy


def test_default_policy_allows_all():
    p = AgentPolicy()
    assert p.can_use_tool("file_read")
    assert p.can_use_tool("shell_exec")
    assert p.can_use_tool("http_request")
    assert p.can_use_tool("anything")


def test_denied_tools():
    p = AgentPolicy(denied_tools=["shell_exec", "python_exec"])
    assert p.can_use_tool("file_read")
    assert not p.can_use_tool("shell_exec")
    assert not p.can_use_tool("python_exec")


def test_allowed_tools_whitelist():
    p = AgentPolicy(allowed_tools=["file_read", "file_write"])
    assert p.can_use_tool("file_read")
    assert p.can_use_tool("file_write")
    assert not p.can_use_tool("shell_exec")


def test_read_only_blocks_writes():
    p = AgentPolicy(read_only=True)
    assert p.can_use_tool("file_read")
    assert not p.can_use_tool("file_write")
    assert not p.can_use_tool("shell_exec")
    assert not p.can_use_tool("python_exec")
    assert p.can_use_tool("http_request")


def test_no_shell():
    p = AgentPolicy(allow_shell=False)
    assert not p.can_use_tool("shell_exec")
    assert p.can_use_tool("file_read")


def test_no_network():
    p = AgentPolicy(allow_network=False)
    assert not p.can_use_tool("http_request")
    assert not p.can_use_tool("web_search")
    assert p.can_use_tool("file_read")
    assert p.can_use_tool("shell_exec")


def test_no_file_write():
    p = AgentPolicy(allow_file_write=False)
    assert not p.can_use_tool("file_write")
    assert p.can_use_tool("file_read")


def test_denied_overrides_allowed():
    p = AgentPolicy(
        allowed_tools=["shell_exec", "file_read"],
        denied_tools=["shell_exec"],
    )
    assert not p.can_use_tool("shell_exec")
    assert p.can_use_tool("file_read")


def test_agent_name_default():
    p = AgentPolicy()
    assert p.agent_name == "*"

    p2 = AgentPolicy(agent_name="analyst")
    assert p2.agent_name == "analyst"
