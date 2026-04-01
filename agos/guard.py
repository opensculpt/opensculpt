"""Loop Guard — detect and break infinite tool call loops.

Inspired by OpenFang's SHA256-based tool-call ping-pong detection.
Hashes recent tool call sequences and detects when the agent is
repeating the same pattern (stuck in a loop).

Also includes capability gates — tool-level permissions per agent.
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Any

_logger = logging.getLogger(__name__)


class LoopGuard:
    """Detects when an agent is stuck in a repeating tool-call loop.

    Maintains a sliding window of tool call signatures (name + args hash).
    If a pattern of length N repeats K times, the guard trips.

    Example loop: shell(ls) -> read_file(x) -> shell(ls) -> read_file(x)
    Pattern "shell:abc,read_file:def" repeats — loop detected.
    """

    # Tools that only read state — not harmful alone, but circular re-reads
    # of the same targets indicate a rabbit hole (OpenSeed lesson: creature
    # spent 560+ actions / 8.7% of lifetime in read-only loops).
    READONLY_TOOLS = frozenset({
        "read_file", "http", "daemon_results", "check_agent",
        "list_agents", "docker_ps", "docker_logs", "browse",
        "check_goals", "check_service",
    })

    def __init__(
        self,
        window_size: int = 20,
        min_pattern_len: int = 2,
        max_pattern_len: int = 6,
        repeat_threshold: int = 3,
    ) -> None:
        self._window: deque[str] = deque(maxlen=window_size)
        self._outputs: deque[str] = deque(maxlen=window_size)
        self._errors: deque[bool] = deque(maxlen=window_size)
        self._min_pat = min_pattern_len
        self._max_pat = max_pattern_len
        self._threshold = repeat_threshold
        self._tripped = False
        self._trip_reason = ""
        # Circular re-read tracking (OpenSeed rabbit-hole pattern)
        # Tracks (tool, target) pairs — trips when the SAME targets
        # are re-read, not just any consecutive reads.
        self._read_targets: deque[str] = deque(maxlen=20)
        self._read_target_set: dict[str, int] = {}  # target -> count

    def record(self, tool_name: str, arguments: dict[str, Any],
               output: str = "", is_error: bool = False) -> None:
        """Record a tool call and its outcome."""
        sig = self._signature(tool_name, arguments)
        self._window.append(sig)
        # Track outputs and errors for advanced stuck detection
        self._outputs.append(output[:200] if output else "")
        self._errors.append(is_error)
        # Track circular re-reads: same target read multiple times
        if tool_name in self.READONLY_TOOLS:
            target = self._read_target_key(tool_name, arguments)
            self._read_targets.append(target)
            self._read_target_set[target] = self._read_target_set.get(target, 0) + 1
        else:
            # Write tool resets — agent is making progress
            self._read_targets.clear()
            self._read_target_set.clear()

    def is_looping(self) -> bool:
        """5-pattern stuck detector (OpenHands pattern).

        Detects:
        1. Repeating tool pattern (A→B→A→B) — original
        2. Identical consecutive outputs (same error 3x)
        3. Error-only loops (last N calls all errors)
        4. No-progress stall (output unchanged across N calls)
        5. Ping-pong oscillation (tool A, undo A, tool A, undo A)
        """
        if self._tripped:
            return True

        calls = list(self._window)
        n = len(calls)

        # Pattern 1: Repeating tool call sequence (original)
        for pat_len in range(self._min_pat, min(self._max_pat + 1, n // 2 + 1)):
            needed = pat_len * self._threshold
            if n < needed:
                continue
            recent = calls[-needed:]
            pattern = recent[:pat_len]
            repeats = sum(1 for i in range(0, needed, pat_len)
                         if recent[i:i + pat_len] == pattern)
            if repeats >= self._threshold:
                self._tripped = True
                self._trip_reason = (
                    f"Pattern loop: {pat_len} calls repeated {repeats}x. "
                    f"Pattern: {' -> '.join(s.split(':')[0] for s in pattern)}"
                )
                _logger.warning("Loop guard [pattern]: %s", self._trip_reason)
                return True

        # Pattern 2: Identical consecutive outputs (same error repeated)
        outputs = list(self._outputs)
        if len(outputs) >= 3:
            last3 = outputs[-3:]
            if last3[0] and all(o == last3[0] for o in last3):
                self._tripped = True
                self._trip_reason = f"Identical output 3x: {last3[0][:80]}..."
                _logger.warning("Loop guard [identical output]: %s", self._trip_reason)
                return True

        # Pattern 3: Error-only loop (last 4+ calls all errors)
        errors = list(self._errors)
        if len(errors) >= 4 and all(errors[-4:]):
            self._tripped = True
            self._trip_reason = "Last 4 tool calls all returned errors"
            _logger.warning("Loop guard [error loop]: %s", self._trip_reason)
            return True

        # Pattern 4: A-B-A-B ping-pong (2 tools alternating)
        if n >= 6:
            last6 = calls[-6:]
            tools_only = [s.split(":")[0] for s in last6]
            if (tools_only[0] == tools_only[2] == tools_only[4] and
                    tools_only[1] == tools_only[3] == tools_only[5] and
                    tools_only[0] != tools_only[1]):
                self._tripped = True
                self._trip_reason = f"Ping-pong: {tools_only[0]} <-> {tools_only[1]} (3 cycles)"
                _logger.warning("Loop guard [ping-pong]: %s", self._trip_reason)
                return True

        # Pattern 5: Circular re-reads — same targets read 3+ times
        # without any write in between (OpenSeed rabbit-hole detection).
        # Unlike a flat "consecutive reads" counter, this only trips when
        # the agent is re-reading the SAME files/endpoints — legitimate
        # research phases read many DIFFERENT targets.
        if len(self._read_targets) >= 6:
            reread_count = sum(1 for c in self._read_target_set.values() if c >= 3)
            if reread_count >= 2:
                top = sorted(self._read_target_set.items(), key=lambda x: -x[1])[:3]
                targets = ", ".join(f"{t}({c}x)" for t, c in top)
                self._tripped = True
                self._trip_reason = f"Circular re-reads: {targets}"
                _logger.warning("Loop guard [rabbit-hole]: %s", self._trip_reason)
                return True

        return False

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    def reset(self) -> None:
        """Reset the guard (new command)."""
        self._window.clear()
        self._outputs.clear()
        self._errors.clear()
        self._read_targets.clear()
        self._read_target_set.clear()
        self._tripped = False
        self._trip_reason = ""

    @staticmethod
    def _read_target_key(tool_name: str, arguments: dict) -> str:
        """Extract the meaningful target from a read-only tool call.

        Returns a short human-readable key like "read_file:config.py"
        so the trip reason is informative.
        """
        # Pick the most meaningful argument as the target identifier
        for key in ("path", "file", "url", "agent_id", "goal_id", "command"):
            if key in arguments:
                val = str(arguments[key])
                # Truncate long values but keep them identifiable
                return f"{tool_name}:{val[:60]}"
        # Fallback: hash the arguments
        args_str = str(sorted(arguments.items())) if arguments else ""
        h = hashlib.sha256(args_str.encode()).hexdigest()[:8]
        return f"{tool_name}:{h}"

    @staticmethod
    def _signature(tool_name: str, arguments: dict) -> str:
        """Create a deterministic signature for a tool call."""
        args_str = str(sorted(arguments.items())) if arguments else ""
        h = hashlib.sha256(args_str.encode()).hexdigest()[:12]
        return f"{tool_name}:{h}"


class CapabilityGate:
    """Tool-level permissions for agents.

    Each agent/sub-agent declares what tools it needs. The gate
    enforces that agents can only use their declared tools.

    Dangerous tools (shell, write_file, python) require explicit grant.
    """

    # Tools that can modify the system — require explicit permission
    DANGEROUS = {"shell", "write_file", "python", "manage_agent"}
    # Tools that are safe for any agent
    SAFE = {"read_file", "http", "daemon_results", "check_agent", "list_agents"}

    def __init__(self) -> None:
        self._grants: dict[str, set[str]] = {}  # agent_id -> allowed tools
        self._denials: list[dict] = []  # audit trail of denied calls

    def grant(self, agent_id: str, tools: set[str] | list[str]) -> None:
        """Grant tool access to an agent."""
        self._grants[agent_id] = set(tools) | self.SAFE

    def grant_all(self, agent_id: str) -> None:
        """Grant all tool access (for the main OS agent)."""
        self._grants[agent_id] = {"*"}

    def check(self, agent_id: str, tool_name: str) -> bool:
        """Check if an agent is allowed to use a tool.

        Returns True if allowed, False if denied.
        Unknown agents get SAFE tools only.
        """
        allowed = self._grants.get(agent_id, self.SAFE)
        if "*" in allowed:
            return True
        if tool_name in allowed:
            return True

        self._denials.append({
            "agent": agent_id, "tool": tool_name,
            "reason": f"Agent '{agent_id}' not granted access to '{tool_name}'",
        })
        _logger.info("Capability gate denied: %s -> %s", agent_id, tool_name)
        return False

    def recent_denials(self, limit: int = 10) -> list[dict]:
        return self._denials[-limit:]

    def permissions_for(self, agent_id: str) -> set[str]:
        return self._grants.get(agent_id, self.SAFE)
