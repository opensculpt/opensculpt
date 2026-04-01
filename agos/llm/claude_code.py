"""Claude Code CLI as LLM provider — uses your subscription, zero API cost.

Two modes:
  1. CLI mode (default): shells out to `claude -p` — uses your subscription quota
  2. OAuth mode: reads token from ~/.claude/.credentials.json — direct API (may incur cost)

The CLI mode uses a semaphore to prevent stacking multiple claude.exe processes
(which caused freezing). Only 1 call at a time, others queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

_logger = logging.getLogger(__name__)

# Model shortcuts
_MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
}


def _find_claude_exe() -> str | None:
    """Find claude CLI binary."""
    found = shutil.which("claude")
    if found:
        return found
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            cc_dir = Path(appdata) / "Claude" / "claude-code"
            if cc_dir.exists():
                for v in sorted(cc_dir.iterdir(), reverse=True):
                    exe = v / "claude.exe"
                    if exe.exists():
                        return str(exe)
    if sys.platform == "darwin":
        mac_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
        if mac_path.exists():
            for v in sorted(mac_path.iterdir(), reverse=True):
                exe = v / "claude"
                if exe.exists():
                    return str(exe)
    return None


def _load_oauth_token() -> dict | None:
    """Read OAuth credentials from Claude Code's credential store."""
    paths = [Path.home() / ".claude" / ".credentials.json"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            paths.insert(0, Path(appdata) / "Claude" / ".credentials.json")
    for path in paths:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                oauth = data.get("claudeAiOauth", {})
                if oauth.get("accessToken") and time.time() < oauth.get("expiresAt", 0) / 1000:
                    return oauth
            except Exception:
                pass
    return None


class ClaudeCodeProvider(BaseLLMProvider):
    """LLM provider using Claude Code — CLI mode (free) or OAuth mode (direct API).

    CLI mode: 1 subprocess at a time via semaphore. No freezing.
    OAuth mode: direct httpx calls. Faster but uses API quota.
    """

    name = "claude_code"
    description = "Claude Code subscription (free via CLI)"

    def __init__(self, model: str = "sonnet", mode: str = "cli"):
        """
        Args:
            model: "haiku", "sonnet", or "opus" (or full model ID)
            mode: "cli" (free, uses claude.exe) or "oauth" (direct API)
        """
        self._model_short = model
        self._model = _MODEL_MAP.get(model, model)
        self._mode = mode

        # Semaphore created lazily on first call (must be inside running event loop)
        self._semaphore: asyncio.Semaphore | None = None
        self._sem_limit = 1  # CLI: 1 at a time to prevent freezing

        if mode == "cli":
            self._exe = _find_claude_exe()
            if not self._exe:
                raise FileNotFoundError("Claude Code CLI not found")
            _logger.info("Claude Code CLI provider: %s (model=%s)", self._exe, model)
        else:
            oauth = _load_oauth_token()
            if not oauth:
                raise RuntimeError("Claude Code OAuth token not found or expired")
            self._access_token = oauth["accessToken"]
            self._sem_limit = 3  # OAuth can handle concurrency
            _logger.info("Claude Code OAuth provider: model=%s, sub=%s", model, oauth.get("subscriptionType"))

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazy init — semaphore must be created inside a running event loop."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._sem_limit)
        return self._semaphore

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        if self._mode == "cli":
            return await self._complete_cli(messages, system, tools, max_tokens)
        else:
            return await self._complete_oauth(messages, system, tools, max_tokens)

    # ── CLI mode (free, 1-at-a-time) ─────────────────────────────

    async def _complete_cli(
        self,
        messages: list[LLMMessage],
        system: str | None,
        tools: list[dict] | None,
        max_tokens: int,
    ) -> LLMResponse:
        prompt = self._build_prompt(messages, system, tools)

        cmd = [self._exe, "--output-format", "json", "-p", prompt]
        if self._model_short:
            cmd.extend(["--model", self._model_short])

        _logger.debug("CLI call queued (%d chars)", len(prompt))

        # Only 1 subprocess at a time — prevents the freeze
        async with self._get_semaphore():
            _logger.debug("CLI call started")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            except asyncio.TimeoutError:
                proc.kill()
                return LLMResponse(content="Error: Claude Code CLI timed out (3 min)", stop_reason="error")
            except Exception as e:
                return LLMResponse(content=f"Error: {e}", stop_reason="error")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()[:300]
            return LLMResponse(content=f"Error: CLI failed: {err}", stop_reason="error")

        raw = stdout.decode("utf-8", errors="replace").strip()

        # Parse JSON output
        try:
            data = json.loads(raw)
            content = data.get("result", data.get("content", data.get("text", raw)))
            if isinstance(content, dict):
                content = json.dumps(content)
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", len(prompt) // 4)
            output_tokens = usage.get("output_tokens", len(str(content)) // 4)
        except json.JSONDecodeError:
            content = raw
            input_tokens = len(prompt) // 4
            output_tokens = len(raw) // 4

        # Parse tool calls if tools provided
        tool_calls = self._parse_tool_calls(str(content), tools) if tools else []
        if tool_calls:
            content = self._strip_tool_json(str(content)) or None

        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            stop_reason="stop",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # ── OAuth mode (direct API, faster) ──────────────────────────

    async def _complete_oauth(
        self,
        messages: list[LLMMessage],
        system: str | None,
        tools: list[dict] | None,
        max_tokens: int,
    ) -> LLMResponse:
        api_messages = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }

        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if tools:
            cached_tools = [dict(t) for t in tools]
            if cached_tools:
                cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = cached_tools

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with self._get_semaphore():
            async with httpx.AsyncClient(timeout=180) as client:
                for attempt in range(5):
                    try:
                        resp = await client.post("https://api.anthropic.com/v1/messages", json=kwargs, headers=headers)
                    except httpx.TimeoutException:
                        await asyncio.sleep(min(30, 2 ** attempt))
                        continue
                    if resp.status_code == 429:
                        await asyncio.sleep(min(60, 2 ** attempt))
                        continue
                    if resp.status_code == 401:
                        return LLMResponse(content="Error: OAuth token expired. Re-login to Claude Code.", stop_reason="error")
                    if resp.status_code >= 400:
                        return LLMResponse(content=f"API error {resp.status_code}: {resp.text[:300]}", stop_reason="error")
                    break
                else:
                    return LLMResponse(content="Error: API failed after 5 retries", stop_reason="error")

        data = resp.json()
        tool_calls = []
        content_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input", {})))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content_text or None,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    # ── Prompt building (CLI mode only) ──────────────────────────

    def _build_prompt(self, messages: list[LLMMessage], system: str | None, tools: list[dict] | None) -> str:
        parts: list[str] = []
        if system:
            parts.append(f"<system>\n{system}\n</system>\n")
        if tools:
            tool_desc = json.dumps(
                [{"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})} for t in tools],
                indent=2,
            )
            parts.append(
                f"<available_tools>\n{tool_desc}\n</available_tools>\n"
                'When you need to use a tool, respond with a JSON block:\n'
                '```json\n{"tool_calls": [{"name": "tool_name", "arguments": {...}}]}\n```\n'
            )
        for msg in messages:
            content = msg.content
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            status = "ERROR" if block.get("is_error") else "OK"
                            text_parts.append(f"[Tool Result ({status})]: {block.get('content', '')}")
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[Called {block['name']}({json.dumps(block.get('input', {}))})]")
                    else:
                        text_parts.append(str(block))
                content = "\n".join(text_parts)
            parts.append(f"<{msg.role}>\n{content}\n</{msg.role}>\n")
        return "\n".join(parts)

    def _parse_tool_calls(self, content: str, tools: list[dict]) -> list[ToolCall]:
        import re
        tool_calls = []
        tool_names = {t["name"] for t in tools}
        for block in re.findall(r"```json\s*\n?(.*?)\n?```", content, re.DOTALL):
            try:
                data = json.loads(block.strip())
                calls = data.get("tool_calls", [data] if "name" in data else [])
                for call in calls:
                    if call.get("name") in tool_names:
                        tool_calls.append(ToolCall(
                            id=f"cc_{call['name']}_{len(tool_calls)}",
                            name=call["name"],
                            arguments=call.get("arguments", call.get("input", {})),
                        ))
            except (json.JSONDecodeError, AttributeError):
                continue
        return tool_calls

    def _strip_tool_json(self, content: str) -> str:
        import re
        return re.sub(r"```json\s*\n?\{.*?\"tool_calls\".*?\}\n?```", "", content, flags=re.DOTALL).strip()

    async def complete_prompt(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.3) -> str:
        response = await self.complete(messages=[LLMMessage(role="user", content=prompt)], max_tokens=max_tokens)
        return response.content or ""
