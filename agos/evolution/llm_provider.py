"""Thin LLM provider for ALMA evolution features.

Wraps the Anthropic SDK for use by MetaEvolver (ideation),
CodeEvolver (self-reflection, iterate-on-strategy), and
PaperAnalyzer (paper relevance analysis).

Implements BaseLLMProvider so it works with PaperAnalyzer,
CodeAnalyzer, and all other components that use the standard interface.
"""

from __future__ import annotations

import logging

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class LLMProvider(BaseLLMProvider):
    """LLM provider implementing BaseLLMProvider for all evolution features."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Full BaseLLMProvider interface — messages + system + tools."""
        api_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        content = None
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                from agos.llm.base import ToolCall
                tool_calls.append(ToolCall(
                    id=block.id, name=block.name, arguments=block.input,
                ))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
        )

    async def complete_prompt(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        """Simple single-turn prompt → text convenience method."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
