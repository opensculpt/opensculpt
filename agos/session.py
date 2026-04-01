"""Session management — pluggable context condensers.

Inspired by:
- OpenClaw's 4-layer compaction (count → token → TTL → smart)
- OpenHands' 9 pluggable condensers via registry
- OpenFang's memory flush before compact
- AutoGen's TransformMessages pipeline

The OS agent (or evolution engine) can switch condenser strategies
on the fly — like choosing a design pattern for context management.
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


# ── Condenser Registry (OpenHands pattern) ──────────────────────────

_CONDENSER_REGISTRY: dict[str, type["BaseCondenser"]] = {}


def register_condenser(name: str):
    """Decorator to register a condenser strategy."""
    def decorator(cls):
        _CONDENSER_REGISTRY[name] = cls
        return cls
    return decorator


def get_condenser(name: str, **kwargs) -> "BaseCondenser":
    """Get a condenser by name. OS agent can switch strategies at runtime.

    Filters kwargs to only pass parameters the condenser's __init__ accepts,
    preventing TypeError on unexpected keyword arguments.
    """
    import inspect
    cls = _CONDENSER_REGISTRY.get(name)
    if not cls:
        _logger.warning("Unknown condenser '%s', falling back to 'observation_masking'", name)
        cls = _CONDENSER_REGISTRY.get("observation_masking", ObservationMaskingCondenser)
    # Filter kwargs to what this condenser + BaseCondenser actually accept
    valid = set()
    for klass in cls.__mro__:
        if klass is object:
            continue
        try:
            valid.update(inspect.signature(klass.__init__).parameters.keys())
        except (ValueError, TypeError):
            pass
    valid.discard("self")
    filtered = {k: v for k, v in kwargs.items() if k in valid}
    return cls(**filtered)


def list_condensers() -> list[str]:
    """List available condenser strategies."""
    return list(_CONDENSER_REGISTRY.keys())


# ── Base Condenser ──────────────────────────────────────────────────

class BaseCondenser(ABC):
    """Base class for all context condensers."""

    def __init__(self, keep_first: bool = True, max_summary_chars: int = 1500):
        self._keep_first = keep_first  # OpenClaw/OpenHands: always preserve initial task
        self._max_summary = max_summary_chars
        self._compaction_count = 0
        self._total_compacted = 0

    @abstractmethod
    def should_compact(self, messages: list[dict]) -> bool:
        """Check if messages need compaction."""

    @abstractmethod
    def compact(self, messages: list[dict]) -> list[dict]:
        """Compact messages. Must preserve first message if keep_first=True."""

    @property
    def stats(self) -> dict:
        return {
            "compactions": self._compaction_count,
            "total_compacted": self._total_compacted,
            "strategy": type(self).__name__,
        }


# ── Strategy 1: Observation Masking (JetBrains/NeurIPS 2025) ───────
# Replace old tool outputs with short summaries. Zero LLM cost.
# "The Complexity Trap" paper: same 50% savings as LLM summarization.

@register_condenser("observation_masking")
class ObservationMaskingCondenser(BaseCondenser):
    """Replace old tool outputs with placeholders. Zero LLM cost, 50% savings."""

    def __init__(self, threshold: int = 8, keep_recent: int = 4, **kwargs):
        super().__init__(**kwargs)
        self._threshold = threshold
        self._keep_recent = keep_recent

    def should_compact(self, messages: list[dict]) -> bool:
        return len(messages) > self._threshold

    def compact(self, messages: list) -> list:
        """Compact messages. Handles both dict (OS agent) and LLMMessage (sub-agent) formats."""
        if not self.should_compact(messages):
            return messages

        first = [messages[0]] if self._keep_first and messages else []
        recent = messages[-self._keep_recent:]
        middle = messages[len(first):-self._keep_recent] if len(messages) > len(first) + self._keep_recent else []

        # Mask tool outputs in middle messages
        masked_middle = []
        for msg in middle:
            # Handle both LLMMessage (has .content attr) and dict formats
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if isinstance(content, list):
                # Tool result messages — mask verbose content in-place
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        c = item.get("content", "")
                        if isinstance(c, str) and len(c) > 150:
                            status = "error" if item.get("is_error") else "ok"
                            item["content"] = f"[{status}: {c[:100]}...]"
            elif isinstance(content, str) and len(content) > 300:
                # Text content — truncate (only for dicts, can't mutate LLMMessage)
                if isinstance(msg, dict):
                    key = "response" if "response" in msg else "content"
                    msg[key] = content[:200] + "...[masked]"
            masked_middle.append(msg)

        self._compaction_count += 1
        self._total_compacted += len(middle)
        _logger.info("Observation masking: compacted %d middle messages (kept first + %d recent)",
                     len(middle), len(recent))
        return first + masked_middle + recent


# ── Strategy 2: Recent (keep last N, drop rest) ────────────────────

@register_condenser("recent")
class RecentCondenser(BaseCondenser):
    """Keep only the N most recent messages. Simplest, most aggressive."""

    def __init__(self, max_messages: int = 10, **kwargs):
        super().__init__(**kwargs)
        self._max = max_messages

    def should_compact(self, messages: list[dict]) -> bool:
        return len(messages) > self._max

    def compact(self, messages: list[dict]) -> list[dict]:
        if not self.should_compact(messages):
            return messages
        first = [messages[0]] if self._keep_first and messages else []
        recent = messages[-(self._max - len(first)):]
        self._compaction_count += 1
        self._total_compacted += len(messages) - self._max
        return first + recent


# ── Strategy 3: Summary (OpenClaw rule-based) ──────────────────────

@register_condenser("summary")
class SummaryCondenser(BaseCondenser):
    """Compress old messages into a rule-based summary. No LLM cost."""

    def __init__(self, max_messages: int = 20, compact_to: int = 5, **kwargs):
        super().__init__(**kwargs)
        self._max = max_messages
        self._compact_to = compact_to

    def should_compact(self, messages: list[dict]) -> bool:
        return len(messages) > self._max

    def compact(self, messages: list[dict]) -> list[dict]:
        if not self.should_compact(messages):
            return messages

        first = [messages[0]] if self._keep_first and messages else []
        keep_count = self._compact_to
        old = messages[len(first):-keep_count]
        recent = messages[-keep_count:]

        summary = self._extract_summary(old)
        self._compaction_count += 1
        self._total_compacted += len(old)

        summary_entry = {
            "command": "[session context — compacted]",
            "response": summary,
            "ts": time.time(),
            "_compacted": True,
        }
        return first + [summary_entry] + recent

    def _extract_summary(self, messages: list[dict]) -> str:
        parts, tools_used, files_mentioned, errors = [], set(), set(), []
        for msg in messages:
            cmd = msg.get("command", "")
            resp = msg.get("response", "")
            if cmd and not cmd.startswith("[session context"):
                parts.append(f"- {cmd[:80]}")
            for tool in ["shell", "read_file", "write_file", "http", "python", "docker"]:
                if tool in str(resp).lower():
                    tools_used.add(tool)
            for p in re.findall(r'[\w./\\]+\.\w{1,4}', str(resp)[:500])[:3]:
                files_mentioned.add(p)
            if any(w in str(resp).lower() for w in ["error", "failed"]):
                errors.append(str(resp).split("\n")[0][:80])

        out = ["Summary:"]
        if parts:
            out.extend(parts[-8:])
        if tools_used:
            out.append(f"Tools: {', '.join(sorted(tools_used))}")
        if files_mentioned:
            out.append(f"Files: {', '.join(sorted(list(files_mentioned)[:5]))}")
        if errors:
            out.append(f"Errors: {len(errors)}")
            out.extend(f"  - {e}" for e in errors[-3:])
        return "\n".join(out)[:self._max_summary]


# ── Strategy 4: Noop (no compaction) ───────────────────────────────

@register_condenser("noop")
class NoopCondenser(BaseCondenser):
    """No compaction. For debugging or unlimited context models."""

    def should_compact(self, messages: list[dict]) -> bool:
        return False

    def compact(self, messages: list[dict]) -> list[dict]:
        return messages


# ── Strategy 5: Memory Flush + Summary (OpenClaw pattern) ──────────
# Flush durable facts to .md files before compacting.
# Knowledge survives compaction — the LLM-native way.

@register_condenser("memory_flush")
class MemoryFlushCondenser(SummaryCondenser):
    """Flush key facts to skill docs before compacting (OpenClaw pattern)."""

    def compact(self, messages: list[dict]) -> list[dict]:
        # Flush durable facts before they're lost
        self._flush_to_skills(messages)
        return super().compact(messages)

    def _flush_to_skills(self, messages: list[dict]) -> None:
        """Extract key operational facts and save as skill docs."""
        try:
            skills_dir = Path(".opensculpt/skills")
            skills_dir.mkdir(parents=True, exist_ok=True)

            facts = []
            for msg in messages:
                resp = str(msg.get("response", ""))
                # Extract service deployments
                if any(w in resp.lower() for w in ["deployed", "started", "running on port"]):
                    facts.append(resp.split("\n")[0][:200])
                # Extract credentials
                if any(w in resp.lower() for w in ["password", "credential", "api_key", "token"]):
                    facts.append(resp.split("\n")[0][:200])
                # Extract errors and their solutions
                if "fixed" in resp.lower() or "resolved" in resp.lower():
                    facts.append(resp.split("\n")[0][:200])

            if facts:
                ts = int(time.time())
                doc = f"# Session Facts (auto-flushed {ts})\n\n"
                doc += "\n".join(f"- {f}" for f in facts[:10])
                (skills_dir / f"session_flush_{ts}.md").write_text(doc, encoding="utf-8")
                _logger.info("Memory flush: saved %d facts before compaction", len(facts))
        except Exception as e:
            _logger.debug("Memory flush failed: %s", e)


# ── Backward-compatible SessionCompactor ────────────────────────────
# Wraps the pluggable system for existing code that uses SessionCompactor.

# ── Strategy 6: Microcompact (Claude Code pattern — FREE) ─────────
# Clear old tool results client-side. No LLM cost at all.
# Keeps last N tool results, replaces older ones with "[cleared]".
# Claude Code clears after 60-min gap; we clear based on count.

@register_condenser("microcompact")
class MicrocompactCondenser(BaseCondenser):
    """Free client-side compaction: clear old tool results, keep last N.

    Inspired by Claude Code's microcompact: eligible tool outputs older
    than keep_recent are replaced with a 1-line summary. Zero LLM cost.
    """

    # Tool types whose outputs are safe to clear (read-only or verbose)
    ELIGIBLE_TOOLS = {
        "shell", "read_file", "python", "http", "browse",
        "docker_ps", "docker_logs", "docker_run",
        "list_agents", "check_goals", "daemon_results",
        "think", "manage_agent",
    }

    def __init__(self, keep_recent: int = 5, **kwargs):
        super().__init__(**kwargs)
        self._keep_recent = keep_recent

    def should_compact(self, messages: list) -> bool:
        # Count tool result messages
        tool_count = 0
        for msg in messages:
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_count += 1
        return tool_count > self._keep_recent * 2

    def compact(self, messages: list) -> list:
        if not self.should_compact(messages):
            return messages

        # Find all tool_result positions (message index, item index)
        tool_positions: list[tuple[int, int]] = []
        for mi, msg in enumerate(messages):
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if isinstance(content, list):
                for ii, item in enumerate(content):
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_positions.append((mi, ii))

        if len(tool_positions) <= self._keep_recent:
            return messages

        # Clear all but the last keep_recent tool results
        to_clear = tool_positions[:-self._keep_recent]
        cleared = 0
        for mi, ii in to_clear:
            msg = messages[mi]
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if isinstance(content, list) and ii < len(content):
                item = content[ii]
                old_content = item.get("content", "")
                if isinstance(old_content, str) and len(old_content) > 100:
                    status = "error" if item.get("is_error") else "ok"
                    item["content"] = f"[{status}: output cleared]"
                    cleared += 1

        if cleared:
            self._compaction_count += 1
            self._total_compacted += cleared
            _logger.info("Microcompact: cleared %d old tool outputs (kept last %d)",
                         cleared, self._keep_recent)
        return messages


# ── Strategy 7: Tiered Compaction (Claude Code 4-tier pattern) ────
# Applies strategies in order: microcompact → observation_masking → summary
# with circuit breaker to prevent infinite compaction loops.

@register_condenser("tiered")
class TieredCondenser(BaseCondenser):
    """Four-tier compaction inspired by Claude Code.

    Tier 1: Microcompact (free, client-side)
    Tier 2: Observation masking (free, truncates middle)
    Tier 3: Summary (rule-based, compresses to summary)
    Circuit breaker: max consecutive compactions before giving up.
    """

    MAX_CONSECUTIVE = 3  # Claude Code pattern: prevent compaction storms

    def __init__(self, threshold: int = 12, **kwargs):
        super().__init__(**kwargs)
        self._threshold = threshold
        self._consecutive_compactions = 0
        self._micro = MicrocompactCondenser(keep_recent=5, **kwargs)
        self._masking = ObservationMaskingCondenser(threshold=8, keep_recent=4, **kwargs)
        self._summary = SummaryCondenser(max_messages=20, compact_to=5, **kwargs)

    def should_compact(self, messages: list) -> bool:
        if self._consecutive_compactions >= self.MAX_CONSECUTIVE:
            _logger.warning("Circuit breaker: %d consecutive compactions, pausing",
                            self._consecutive_compactions)
            self._consecutive_compactions = 0  # Reset after warning
            return False
        return len(messages) > self._threshold

    def compact(self, messages: list) -> list:
        if not self.should_compact(messages):
            return messages

        before = len(messages)
        self._consecutive_compactions += 1

        # Tier 1: Microcompact (free)
        messages = self._micro.compact(messages)

        # Tier 2: Observation masking (if still too long)
        if self._masking.should_compact(messages):
            messages = self._masking.compact(messages)

        # Tier 3: Summary (if still too long)
        if self._summary.should_compact(messages):
            messages = self._summary.compact(messages)

        after = len(messages)
        if after < before:
            self._compaction_count += 1
            self._total_compacted += before - after
            _logger.info("Tiered compaction: %d → %d messages", before, after)
        else:
            # Nothing changed — reset circuit breaker
            self._consecutive_compactions = 0

        return messages

    def reset_circuit_breaker(self):
        """Reset after successful non-compaction turn (user got a response)."""
        self._consecutive_compactions = 0


class SessionCompactor:
    """Backward-compatible wrapper. Uses pluggable condensers internally."""

    def __init__(
        self,
        max_messages: int = 20,
        compact_to: int = 5,
        max_summary_chars: int = 1500,
        strategy: str = "observation_masking",
    ) -> None:
        # Only pass kwargs the condenser accepts (different strategies have different params)
        kwargs = {
            "max_messages": max_messages,
            "compact_to": compact_to,
            "threshold": max_messages,
            "keep_recent": compact_to,
            "max_summary_chars": max_summary_chars,
            "keep_first": True,
        }
        # Get the condenser class to check its __init__ params
        cls = _CONDENSER_REGISTRY.get(strategy, _CONDENSER_REGISTRY.get("observation_masking"))
        import inspect
        valid_params = set(inspect.signature(cls.__init__).parameters.keys()) - {"self"}
        # Also include BaseCondenser params
        valid_params.update(inspect.signature(BaseCondenser.__init__).parameters.keys())
        valid_params.discard("self")
        filtered = {k: v for k, v in kwargs.items() if k in valid_params}
        self._condenser = get_condenser(strategy, **filtered)

    def should_compact(self, history: list[dict]) -> bool:
        return self._condenser.should_compact(history)

    def compact(self, history: list[dict], llm=None) -> list[dict]:
        return self._condenser.compact(history)

    def set_strategy(self, name: str, **kwargs) -> None:
        """Switch condenser strategy at runtime. OS agent can call this."""
        self._condenser = get_condenser(name, keep_first=True, **kwargs)
        _logger.info("Condenser strategy switched to: %s", name)

    @property
    def stats(self) -> dict:
        return self._condenser.stats
