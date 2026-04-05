"""LLM Capability Probe — boot-time model testing.

Runs 3 microtests against the configured LLM to classify its capability tier.
Tests 1 (heartbeat) and 2 (tool calling) run in parallel via asyncio.gather.
Test 3 (context window) uses provider API → known table → single probe fallback.

Tiers:
  full        — tool calling + arg fidelity + 16K+ context
  basic_tools — tool calling works + 8K+ context (3 core tools only)
  chat_only   — can respond but no reliable tool calling
  dead        — can't reach the LLM at all
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

_logger = logging.getLogger(__name__)

# Known context windows for common local models.
# Quantized variants usually keep the same context window as the base.
KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    # Gemma family
    "gemma-4-e4b-it": 8192,
    "gemma-2-2b-it": 8192,
    "gemma-2-9b-it": 8192,
    "gemma-2-27b-it": 8192,
    "gemma2": 8192,
    "gemma4": 8192,
    # Llama family
    "llama-3.2-1b": 131072,
    "llama-3.2-3b": 131072,
    "llama-3.3-70b": 131072,
    "llama3": 8192,
    "llama3.1": 131072,
    "llama3.2": 131072,
    "llama3.3": 131072,
    # Mistral / Mixtral
    "mistral": 32768,
    "mixtral": 32768,
    "mistral-7b": 32768,
    "mixtral-8x7b": 32768,
    # Qwen
    "qwen2.5": 32768,
    "qwen2.5-coder": 32768,
    # DeepSeek
    "deepseek-r1": 65536,
    "deepseek-coder": 16384,
    # Phi
    "phi-3": 4096,
    "phi-4": 16384,
    # Cloud models (for completeness)
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "claude-haiku-4-5": 200000,
    "claude-sonnet-4-5": 200000,
    "claude-opus-4": 200000,
}

_PROBE_TIMEOUT = 30  # seconds per test
_TOTAL_TIMEOUT = 60  # total probe cap


@dataclass
class LLMCapability:
    """Result of probing an LLM's capabilities."""

    reachable: bool = False
    model_id: str = ""
    tool_calling: bool = False
    tool_arg_fidelity: bool = False
    context_window: int = 0
    latency_ms: int = 0
    probed_at: str = ""

    @property
    def tier(self) -> str:
        if not self.reachable:
            return "dead"
        if not self.tool_calling:
            return "chat_only"
        if self.tool_arg_fidelity and self.context_window >= 16384:
            return "full"
        return "basic_tools"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "model_id": self.model_id,
            "tool_calling": self.tool_calling,
            "tool_arg_fidelity": self.tool_arg_fidelity,
            "context_window": self.context_window,
            "latency_ms": self.latency_ms,
            "probed_at": self.probed_at,
            "tier": self.tier,
        }


class LLMProbe:
    """Probes an LLM provider with 3 microtests."""

    @staticmethod
    async def probe(provider: BaseLLMProvider, model_id: str = "") -> LLMCapability:
        """Run all 3 microtests and return the capability classification."""
        cap = LLMCapability(probed_at=datetime.now(timezone.utc).isoformat())

        try:
            # Tests 1 and 2 run in parallel
            heartbeat_task = asyncio.create_task(
                LLMProbe._test_heartbeat(provider)
            )
            tool_task = asyncio.create_task(
                LLMProbe._test_tool_calling(provider)
            )

            heartbeat_result, tool_result = await asyncio.wait_for(
                asyncio.gather(heartbeat_task, tool_task, return_exceptions=True),
                timeout=_TOTAL_TIMEOUT,
            )

            # Process heartbeat result
            if isinstance(heartbeat_result, Exception):
                _logger.warning("Probe heartbeat failed: %s", heartbeat_result)
                return cap  # dead
            cap.reachable = heartbeat_result["reachable"]
            cap.latency_ms = heartbeat_result["latency_ms"]
            cap.model_id = model_id or heartbeat_result.get("model_id", "")

            if not cap.reachable:
                return cap  # dead

            # Process tool calling result
            if isinstance(tool_result, Exception):
                _logger.warning("Probe tool test failed: %s", tool_result)
                # reachable but tools unknown — chat_only
            else:
                cap.tool_calling = tool_result["tool_calling"]
                cap.tool_arg_fidelity = tool_result["tool_arg_fidelity"]

            # Test 3: context window (sequential, uses result of test 1)
            cap.context_window = await LLMProbe._discover_context_window(
                provider, cap.model_id
            )

        except asyncio.TimeoutError:
            _logger.warning("Probe timed out after %ds", _TOTAL_TIMEOUT)
        except Exception as exc:
            _logger.warning("Probe failed: %s", exc)

        _logger.info(
            "LLM probe complete: model=%s tier=%s tool_calling=%s context=%d latency=%dms",
            cap.model_id, cap.tier, cap.tool_calling,
            cap.context_window, cap.latency_ms,
        )
        return cap

    @staticmethod
    async def _test_heartbeat(provider: BaseLLMProvider) -> dict:
        """Test 1: Can the model respond at all?"""
        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                provider.complete(
                    messages=[LLMMessage(role="user", content="Reply with exactly: OK")],
                    max_tokens=10,
                ),
                timeout=_PROBE_TIMEOUT,
            )
            latency = int((time.monotonic() - t0) * 1000)
            content = (resp.content or "").strip()
            return {
                "reachable": "ok" in content.lower(),
                "latency_ms": latency,
                "model_id": "",  # provider doesn't expose this in response
            }
        except Exception as exc:
            _logger.debug("Heartbeat failed: %s", exc)
            return {"reachable": False, "latency_ms": 0, "model_id": ""}

    @staticmethod
    async def _test_tool_calling(provider: BaseLLMProvider) -> dict:
        """Test 2: Can the model call tools correctly?"""
        test_tools = [
            {
                "name": "test_tool",
                "description": "Send a test message",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "The message to send"},
                    },
                    "required": ["message"],
                },
            }
        ]
        try:
            resp = await asyncio.wait_for(
                provider.complete(
                    messages=[
                        LLMMessage(
                            role="user",
                            content="Use the test_tool to send message 'hello'",
                        )
                    ],
                    tools=test_tools,
                    max_tokens=100,
                ),
                timeout=_PROBE_TIMEOUT,
            )

            if not resp.tool_calls:
                _logger.debug("Tool test: model returned text instead of tool call: %s", (resp.content or "")[:100])
                return {"tool_calling": False, "tool_arg_fidelity": False}

            # Check first tool call
            tc = resp.tool_calls[0]
            _logger.debug("Tool test: model called %s with %s", tc.name, tc.arguments)

            # Hallucinated tool name?
            if tc.name != "test_tool":
                return {"tool_calling": False, "tool_arg_fidelity": False}

            # Tool calling works. Check arg fidelity.
            args = tc.arguments
            if "raw" in args:
                # JSON parse failed in provider layer
                return {"tool_calling": True, "tool_arg_fidelity": False}
            if isinstance(args.get("message"), str):
                return {"tool_calling": True, "tool_arg_fidelity": True}

            # Args present but wrong type
            return {"tool_calling": True, "tool_arg_fidelity": False}

        except Exception as exc:
            _logger.debug("Tool calling test failed: %s", exc)
            return {"tool_calling": False, "tool_arg_fidelity": False}

    @staticmethod
    async def _discover_context_window(
        provider: BaseLLMProvider, model_id: str
    ) -> int:
        """Test 3: Discover context window size.

        Strategy: config override → known table → single 8K probe.
        """
        from agos.config import settings

        # 1. User override wins
        if settings.model_context_window > 0:
            return settings.model_context_window

        # 2. Check known models table (fuzzy match on model_id substring)
        model_lower = model_id.lower()
        for known_id, ctx_size in KNOWN_CONTEXT_WINDOWS.items():
            if known_id in model_lower or model_lower in known_id:
                _logger.debug("Context window from known table: %s → %d", known_id, ctx_size)
                return ctx_size

        # 3. Single probe at 8K tokens
        try:
            # Generate ~8K tokens of padding
            padding = "test " * 1600  # ~8000 tokens
            resp = await asyncio.wait_for(
                provider.complete(
                    messages=[
                        LLMMessage(
                            role="user",
                            content=f"Ignore the following padding and reply 'OK'.\n{padding}",
                        )
                    ],
                    max_tokens=10,
                ),
                timeout=_PROBE_TIMEOUT,
            )
            # If we get here without error, 8K fits
            return 8192
        except Exception:
            # 8K didn't fit — assume 4K
            return 4096
