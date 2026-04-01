"""Policy schema — defines what agents can and cannot do."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentPolicy(BaseModel):
    """Security and resource policy for an agent.

    Every agent operates under a policy. The default policy is
    permissive; tighter policies can be assigned per-agent or globally.
    """

    # Identity
    agent_name: str = "*"  # "*" means applies to all agents

    # Tool access control
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Tool names the agent may use. ['*'] = all.",
    )
    denied_tools: list[str] = Field(
        default_factory=list,
        description="Tool names the agent is NEVER allowed to use.",
    )

    # Resource limits
    max_tokens: int = 200_000
    max_turns: int = 50
    max_concurrent_tools: int = 5

    # Action boundaries
    allow_network: bool = True
    allow_shell: bool = True
    allow_file_write: bool = True
    read_only: bool = False  # If True, blocks all write/exec tools

    # Rate limiting
    max_tool_calls_per_minute: int = 60

    # Sandbox isolation
    sandbox_level: str = "none"  # "none", "process", "strict"
    sandbox_memory_limit_mb: int = 512
    sandbox_cpu_time_limit_s: int = 60
    sandbox_allowed_paths: list[str] = Field(default_factory=list)

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if a tool is permitted under this policy."""
        if tool_name in self.denied_tools:
            return False
        if self.read_only and tool_name in (
            "file_write", "shell_exec", "python_exec",
        ):
            return False
        if not self.allow_shell and tool_name == "shell_exec":
            return False
        if not self.allow_network and tool_name in (
            "http_request", "web_search",
        ):
            return False
        if not self.allow_file_write and tool_name == "file_write":
            return False
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools
