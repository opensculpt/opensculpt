"""LM Studio provider — OpenAI-compatible local LLM.

Connects to LM Studio's local server (default http://localhost:1234/v1).
Works with any OpenAI-compatible endpoint. Free after model download.

Handles busy/timeout gracefully: returns empty responses so callers fall
through to heuristic or template fallbacks instead of blocking.
"""

from __future__ import annotations

import logging
import re

import httpx

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)

# Models preferred for code generation (checked in order).
# Smallest/fastest instruct models first — evolution doesn't need huge models,
# and LM Studio is single-threaded so speed matters more than quality.
_CODE_MODEL_PREFS = ["ministral", "llama-3.2-3b", "llama-3.2-1b", "gemma", "mistral", "phi", "qwen"]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL)
# Qwen 3.5 uses plain-text thinking blocks instead of <think> tags
_QWEN_THINK_RE = re.compile(
    r"(?:Thinking Process|Thought Process|Let me think).*?(?=\n```|\n[A-Z]|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Skip reasoning models and vision-language models — they're slow for text code gen
_REASONING_SKIP = ["reasoning", "deepseek-r1", "think", "-vl-", "vl-", "flash"]


def _strip_think(text: str) -> str:
    """Strip thinking blocks from model outputs.

    Handles:
    - <think>...</think> tags (DeepSeek, Qwen with thinking enabled)
    - Plain-text "Thinking Process:" blocks (Qwen 3.5 default)
    - Truncated responses where </think> is missing
    """
    text = _THINK_RE.sub("", text)
    text = _THINK_UNCLOSED_RE.sub("", text)
    text = _QWEN_THINK_RE.sub("", text)
    return text.strip()


class LMStudioProvider(BaseLLMProvider):
    """OpenAI-compatible provider for LM Studio (and any compatible server).

    On timeout or error, returns empty responses instead of raising,
    so evolution callers can gracefully fall through to heuristic fallbacks.
    """

    name = "lmstudio"

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        # 180s timeout — 9B models need more time for codegen
        self._client = httpx.AsyncClient(timeout=180)

    async def _pick_model(self) -> str:
        """Auto-select best available model for code generation."""
        if self._model:
            return self._model
        try:
            resp = await self._client.get(f"{self._base_url}/models")
            resp.raise_for_status()
            models = [m["id"] for m in resp.json().get("data", [])]
            # Filter out reasoning models (waste tokens on <think> blocks)
            usable = [
                m for m in models
                if not any(skip in m.lower() for skip in _REASONING_SKIP)
            ]
            # Pick by preference order from usable models
            for pref in _CODE_MODEL_PREFS:
                for m in usable:
                    if pref in m.lower():
                        self._model = m
                        logger.info("LM Studio auto-selected model: %s", m)
                        return m
            # Fallback to first usable, or any available
            if usable:
                self._model = usable[0]
                return self._model
            if models:
                self._model = models[0]
                return self._model
        except Exception:
            pass
        return self._model or "default"

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        model = await self._pick_model()
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions", json=payload,
            )
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("LM Studio busy/timeout (%s), returning empty response", e)
            return LLMResponse(content=None, stop_reason="timeout")

        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        text = _strip_think(raw)
        usage = data.get("usage", {})
        return LLMResponse(
            content=text,
            stop_reason=data["choices"][0].get("finish_reason", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    async def complete_prompt(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        model = await self._pick_model()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions", json=payload,
            )
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("LM Studio busy/timeout (%s), returning empty", e)
            return ""

        raw = resp.json()["choices"][0]["message"]["content"]
        return _strip_think(raw)
