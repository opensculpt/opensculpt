"""Ollama provider — local LLM via Ollama HTTP API.

Connects to Ollama's local server (default http://localhost:11434).
Free after model download (e.g. ollama pull llama3).
"""

from __future__ import annotations

import logging

import httpx

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Local LLM provider via Ollama HTTP API."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=90)

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": self._model,
            "messages": api_messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        resp = await self._client.post(
            f"{self._base_url}/api/chat", json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        return LLMResponse(
            content=text,
            stop_reason="stop",
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    async def complete_prompt(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        resp = await self._client.post(
            f"{self._base_url}/api/generate", json=payload,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
