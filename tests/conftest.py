"""Shared test fixtures — MockLLMProvider for testing without API calls."""

from __future__ import annotations

import pytest

from agos.llm.base import BaseLLMProvider, LLMResponse
from agos.tools.registry import ToolRegistry
from agos.tools.builtins import register_builtin_tools
from agos.kernel.runtime import AgentRuntime


class MockLLMProvider(BaseLLMProvider):
    """LLM provider that returns canned responses. No API calls."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self._responses = responses or []
        self._call_count = 0
        self.calls: list[dict] = []  # record all calls for assertions

    async def complete(self, messages, system=None, tools=None, max_tokens=4096):
        self.calls.append({
            "messages": messages,
            "system": system,
            "tools": tools,
            "max_tokens": max_tokens,
        })
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return LLMResponse(
            content="Done.",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


@pytest.fixture
def mock_llm():
    return MockLLMProvider()


@pytest.fixture
def mock_llm_with_responses():
    def _factory(responses: list[LLMResponse]) -> MockLLMProvider:
        return MockLLMProvider(responses=responses)
    return _factory


@pytest.fixture
def tool_registry():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest.fixture
def runtime(mock_llm, tool_registry):
    return AgentRuntime(llm_provider=mock_llm, tool_registry=tool_registry)
