"""Tests for LM Studio OpenAI-compatible provider."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from agos.evolution.providers.lmstudio_provider import (
    LMStudioProvider,
    _strip_think,
)


class TestStripThink:
    def test_strips_think_block(self):
        text = "<think>reasoning here</think>actual output"
        assert _strip_think(text) == "actual output"

    def test_strips_multiline_think(self):
        text = "<think>\nstep 1\nstep 2\n</think>\ncode here"
        assert _strip_think(text) == "code here"

    def test_no_think_block_unchanged(self):
        text = "just normal text"
        assert _strip_think(text) == "just normal text"

    def test_strips_multiple_think_blocks(self):
        text = "<think>a</think>middle<think>b</think>end"
        assert _strip_think(text) == "middleend"

    def test_strips_unclosed_think_block(self):
        """Truncated responses may not have closing </think> tag."""
        text = "<think>reasoning that got truncated without closing"
        assert _strip_think(text) == ""

    def test_strips_unclosed_think_with_trailing_content(self):
        """If somehow content appears before an unclosed think block."""
        text = "prefix<think>reasoning without end"
        # The unclosed regex matches from <think> to end
        assert _strip_think(text) == "prefix"


class TestLMStudioProvider:
    @pytest.mark.asyncio
    async def test_complete_prompt_parses_response(self):
        provider = LMStudioProvider(model="test-model")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "print('hello')"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.complete_prompt("write code", max_tokens=500)
        assert result == "print('hello')"

    @pytest.mark.asyncio
    async def test_complete_prompt_strips_think(self):
        provider = LMStudioProvider(model="deepseek-r1")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "<think>reasoning</think>x = 1"}}],
            "usage": {},
        }
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.complete_prompt("test")
        assert result == "x = 1"

    @pytest.mark.asyncio
    async def test_complete_returns_llm_response(self):
        from agos.llm.base import LLMMessage
        provider = LMStudioProvider(model="test")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.complete([LLMMessage(role="user", content="hi")])
        assert result.content == "response"
        assert result.input_tokens == 20

    @pytest.mark.asyncio
    async def test_auto_picks_model_skips_reasoning_and_vl(self):
        provider = LMStudioProvider(model="")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "microsoft/phi-4-mini-reasoning"},
                {"id": "deepseek/deepseek-r1-8b"},
                {"id": "qwen/qwen3-vl-8b"},
                {"id": "mistralai/ministral-3-3b"},
                {"id": "llama-3.2-3b-instruct"},
            ]
        }
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}], "usage": {},
        }
        provider._client.post = AsyncMock(return_value=post_resp)

        await provider.complete_prompt("test")
        # Should skip: reasoning (phi, deepseek-r1), VL (qwen-vl)
        # Picks ministral (first preference match)
        assert provider._model == "mistralai/ministral-3-3b"

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self):
        """On connection error, return empty string (graceful fallback)."""
        import httpx
        provider = LMStudioProvider(model="test")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await provider.complete_prompt("test")
        assert result == ""

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        """On timeout, return empty string (graceful fallback)."""
        import httpx
        provider = LMStudioProvider(model="test")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

        result = await provider.complete_prompt("test")
        assert result == ""
