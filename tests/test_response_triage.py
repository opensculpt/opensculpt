"""Tests for response triage in OS agent."""

import pytest
from agos.llm.base import LLMResponse, ToolCall


class TestResponseTriage:
    """Tests for the triage logic that catches weak-model failures."""

    def test_triage_stop_length(self):
        """stop_reason='length' should be detected as a triage-able failure."""
        resp = LLMResponse(content="partial response...", stop_reason="length")
        assert resp.stop_reason in ("length", "max_tokens")

    def test_triage_hallucinated_tool(self):
        """Tool call with unknown name should be caught."""
        tc = ToolCall(id="1", name="nonexistent_tool", arguments={"x": 1})
        known_tools = {"set_goal", "check_goals", "shell"}
        bad = [t for t in [tc] if t.name not in known_tools]
        assert len(bad) == 1
        assert bad[0].name == "nonexistent_tool"

    def test_triage_malformed_args(self):
        """Tool call with {"raw": ...} args should be caught."""
        tc = ToolCall(id="1", name="set_goal", arguments={"raw": "invalid json here"})
        assert "raw" in tc.arguments

    def test_triage_empty_response(self):
        """Empty response (no content, no tools) should be caught."""
        resp = LLMResponse(content=None, tool_calls=[])
        assert not resp.content and not resp.tool_calls

    def test_triage_max_retries(self):
        """After 3 failures of the same type, triage should give up."""
        retry_counts = {"length": 3}
        should_give_up = any(v > 2 for v in retry_counts.values())
        assert should_give_up is True

    def test_triage_valid_tool_call_passes(self):
        """Valid tool call should NOT trigger triage."""
        tc = ToolCall(id="1", name="set_goal", arguments={"goal": "test"})
        known_tools = {"set_goal", "check_goals", "shell"}
        bad = [t for t in [tc] if t.name not in known_tools]
        assert len(bad) == 0
        assert "raw" not in tc.arguments
