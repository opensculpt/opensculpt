"""Policy Engine — checks every action before execution.

The policy engine sits between agents and tools. Before any tool
call is executed, the engine checks if the agent's policy permits it.
"""

from __future__ import annotations

import time
from collections import defaultdict

from agos.policy.schema import AgentPolicy
from agos.exceptions import PolicyViolationError


class PolicyEngine:
    """Enforces policies on agent actions.

    Maintains per-agent policies with a global default fallback.
    Every tool call is checked against the relevant policy.
    """

    def __init__(self) -> None:
        self._policies: dict[str, AgentPolicy] = {}
        self._default = AgentPolicy()
        self._call_counts: dict[str, list[float]] = defaultdict(list)

    def set_default(self, policy: AgentPolicy) -> None:
        """Set the global default policy."""
        self._default = policy

    def assign(self, agent_name: str, policy: AgentPolicy) -> None:
        """Assign a policy to a specific agent."""
        self._policies[agent_name] = policy

    def remove(self, agent_name: str) -> None:
        """Remove a per-agent policy (falls back to default)."""
        self._policies.pop(agent_name, None)

    def get_policy(self, agent_name: str) -> AgentPolicy:
        """Get the effective policy for an agent."""
        return self._policies.get(agent_name, self._default)

    def check_tool(self, agent_name: str, tool_name: str) -> None:
        """Check if an agent can use a tool. Raises on violation."""
        policy = self.get_policy(agent_name)

        if not policy.can_use_tool(tool_name):
            raise PolicyViolationError(
                f"Agent '{agent_name}' is not allowed to use tool '{tool_name}'"
            )

        # Rate limiting
        self._enforce_rate_limit(agent_name, policy)

    def check_budget(self, agent_name: str, tokens_used: int) -> None:
        """Check if an agent is within its token budget."""
        policy = self.get_policy(agent_name)
        if tokens_used > policy.max_tokens:
            raise PolicyViolationError(
                f"Agent '{agent_name}' exceeded token budget: "
                f"{tokens_used}/{policy.max_tokens}"
            )

    def check_turns(self, agent_name: str, turns: int) -> None:
        """Check if an agent is within its turn limit."""
        policy = self.get_policy(agent_name)
        if turns > policy.max_turns:
            raise PolicyViolationError(
                f"Agent '{agent_name}' exceeded turn limit: "
                f"{turns}/{policy.max_turns}"
            )

    def _enforce_rate_limit(self, agent_name: str, policy: AgentPolicy) -> None:
        """Enforce per-minute tool call rate limit."""
        now = time.monotonic()
        window = 60.0
        key = agent_name

        # Prune old entries
        self._call_counts[key] = [
            t for t in self._call_counts[key] if now - t < window
        ]

        if len(self._call_counts[key]) >= policy.max_tool_calls_per_minute:
            raise PolicyViolationError(
                f"Agent '{agent_name}' exceeded rate limit: "
                f"{policy.max_tool_calls_per_minute} calls/minute"
            )

        self._call_counts[key].append(now)

    def list_policies(self) -> list[dict]:
        """List all assigned policies."""
        result = []
        for name, policy in self._policies.items():
            result.append({
                "agent_name": name,
                "allowed_tools": policy.allowed_tools,
                "denied_tools": policy.denied_tools,
                "max_tokens": policy.max_tokens,
                "read_only": policy.read_only,
            })
        return result
