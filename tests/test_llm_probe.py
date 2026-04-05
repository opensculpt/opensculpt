"""Tests for LLM capability probe."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from agos.llm.base import LLMMessage, LLMResponse, ToolCall
from agos.llm.probe import LLMProbe, LLMCapability, KNOWN_CONTEXT_WINDOWS


def _make_provider(complete_return=None, complete_side_effect=None):
    """Create a mock LLM provider."""
    provider = AsyncMock()
    if complete_side_effect:
        provider.complete.side_effect = complete_side_effect
    else:
        provider.complete.return_value = complete_return or LLMResponse(content="OK")
    return provider


class TestHeartbeat:
    def test_heartbeat_pass(self):
        provider = _make_provider(LLMResponse(content="OK", input_tokens=5, output_tokens=2))
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="test-model"))
        assert cap.reachable is True
        assert cap.latency_ms > 0
        assert cap.model_id == "test-model"

    def test_heartbeat_timeout(self):
        async def _timeout(*args, **kwargs):
            raise asyncio.TimeoutError()
        provider = _make_provider(complete_side_effect=_timeout)
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider))
        assert cap.reachable is False
        assert cap.tier == "dead"

    def test_heartbeat_http_error(self):
        async def _error(*args, **kwargs):
            raise ConnectionError("Connection refused")
        provider = _make_provider(complete_side_effect=_error)
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider))
        assert cap.reachable is False
        assert cap.tier == "dead"


class TestToolCalling:
    def _provider_with_tool_response(self, tool_calls=None, content=""):
        """Provider that returns different responses based on whether tools are passed."""
        async def _complete(messages, tools=None, max_tokens=4096, system=None):
            if tools:
                return LLMResponse(content=content, tool_calls=tool_calls or [], input_tokens=10, output_tokens=10)
            return LLMResponse(content="OK", input_tokens=5, output_tokens=2)
        provider = AsyncMock()
        provider.complete = _complete
        return provider

    def test_tool_calling_full_fidelity(self):
        tc = ToolCall(id="1", name="test_tool", arguments={"message": "hello"})
        provider = self._provider_with_tool_response(tool_calls=[tc])
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="test"))
        assert cap.tool_calling is True
        assert cap.tool_arg_fidelity is True

    def test_tool_calling_partial(self):
        tc = ToolCall(id="1", name="test_tool", arguments={"raw": "bad json"})
        provider = self._provider_with_tool_response(tool_calls=[tc])
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="test"))
        assert cap.tool_calling is True
        assert cap.tool_arg_fidelity is False

    def test_tool_calling_hallucinated(self):
        tc = ToolCall(id="1", name="fake_tool", arguments={"message": "hello"})
        provider = self._provider_with_tool_response(tool_calls=[tc])
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="test"))
        assert cap.tool_calling is False

    def test_tool_calling_no_call(self):
        provider = self._provider_with_tool_response(tool_calls=[], content="I'll help you!")
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="test"))
        assert cap.tool_calling is False


class TestContextWindow:
    def test_context_known_model(self):
        provider = _make_provider(LLMResponse(content="OK", input_tokens=5, output_tokens=2))
        cap = asyncio.get_event_loop().run_until_complete(LLMProbe.probe(provider, model_id="gemma-4-e4b-it"))
        assert cap.context_window == 8192


class TestTierDerivation:
    def test_dead(self):
        cap = LLMCapability(reachable=False)
        assert cap.tier == "dead"

    def test_chat_only(self):
        cap = LLMCapability(reachable=True, tool_calling=False)
        assert cap.tier == "chat_only"

    def test_basic_tools(self):
        cap = LLMCapability(reachable=True, tool_calling=True, tool_arg_fidelity=True, context_window=8192)
        assert cap.tier == "basic_tools"

    def test_full(self):
        cap = LLMCapability(reachable=True, tool_calling=True, tool_arg_fidelity=True, context_window=128000)
        assert cap.tier == "full"

    def test_basic_tools_no_fidelity(self):
        cap = LLMCapability(reachable=True, tool_calling=True, tool_arg_fidelity=False, context_window=128000)
        assert cap.tier == "basic_tools"
