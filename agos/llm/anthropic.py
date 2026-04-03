"""Anthropic Claude LLM provider."""

from __future__ import annotations

import anthropic

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", base_url: str = ""):
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # Build API-compatible messages
        api_messages = []
        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        # Prompt caching (Anthropic) — "Don't Break the Cache" pattern.
        # Cache system prompt + tool schemas (stable across turns).
        # Cached reads = 10% of normal input price → 50-80% savings.
        if system:
            # Prompt cache boundary (Claude Code pattern):
            # Split at __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__ so the static prefix
            # gets its own cache_control block (stable across turns = high hit rate)
            # and the dynamic suffix gets a separate ephemeral block.
            from agos.os_agent import CACHE_BOUNDARY
            if CACHE_BOUNDARY in system:
                static, dynamic = system.split(CACHE_BOUNDARY, 1)
                kwargs["system"] = [
                    {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": dynamic, "cache_control": {"type": "ephemeral"}},
                ]
            else:
                kwargs["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
                ]
        if tools:
            # Mark last tool schema as cache breakpoint
            cached_tools = [dict(t) for t in tools]
            if cached_tools:
                cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = cached_tools

        response = await self._client.messages.create(**kwargs)

        # Parse response
        tool_calls = []
        content_text = ""
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        # Track cache performance (if available)
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read > 0 or cache_create > 0:
            import logging
            logging.getLogger("agos.llm.anthropic").debug(
                "Prompt cache: read=%d created=%d input=%d (%.0f%% cached)",
                cache_read, cache_create, usage.input_tokens,
                (cache_read / max(usage.input_tokens + cache_read, 1)) * 100,
            )

        return LLMResponse(
            content=content_text or None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
