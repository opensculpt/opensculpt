"""LLM providers — 27 providers via OpenAI-compatible and native APIs.

All providers implement BaseLLMProvider. Most use httpx directly to avoid
requiring heavy SDKs. Providers that speak the OpenAI chat/completions
format share the _OpenAICompatible base.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

_logger = logging.getLogger(__name__)
_TIMEOUT = 120


# ── OpenAI-Compatible Base ───────────────────────────────────


class _OpenAICompatible(BaseLLMProvider):
    """Base for any provider that speaks OpenAI's chat/completions format."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        api_msgs: list[dict[str, Any]] = []
        if system:
            api_msgs.append({"role": "system", "content": system})
        for m in messages:
            # Handle Anthropic-format content blocks (tool_use / tool_result)
            if isinstance(m.content, list):
                if m.role == "assistant":
                    # Convert Anthropic tool_use blocks → OpenAI assistant + tool_calls
                    text_parts = [b["text"] for b in m.content if b.get("type") == "text"]
                    tc_blocks = [b for b in m.content if b.get("type") == "tool_use"]
                    oai_msg: dict[str, Any] = {"role": "assistant", "content": " ".join(text_parts) or None}
                    if tc_blocks:
                        import json as _json
                        oai_msg["tool_calls"] = [
                            {"id": b["id"], "type": "function", "function": {
                                "name": b["name"],
                                "arguments": _json.dumps(b.get("input", {})),
                            }} for b in tc_blocks
                        ]
                    api_msgs.append(oai_msg)
                elif m.role == "user":
                    # Convert Anthropic tool_result blocks → OpenAI tool messages
                    for b in m.content:
                        if b.get("type") == "tool_result":
                            api_msgs.append({
                                "role": "tool",
                                "tool_call_id": b["tool_use_id"],
                                "content": str(b.get("content", "")),
                            })
                        else:
                            api_msgs.append({"role": "user", "content": str(b)})
                else:
                    api_msgs.append({"role": m.role, "content": str(m.content)})
            else:
                api_msgs.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
        }
        if tools:
            # Convert Anthropic tool format to OpenAI format
            oai_tools = []
            for t in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                })
            payload["tools"] = oai_tools

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        import asyncio as _asyncio
        data = None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for _attempt in range(5):
                resp = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = min(60, 2 ** _attempt)
                    _logger.warning("LLM 429 rate limited, retrying in %ds (attempt %d)", wait, _attempt + 1)
                    await _asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = min(30, 2 ** _attempt)
                    _logger.warning("LLM %d server error, retrying in %ds", resp.status_code, wait)
                    await _asyncio.sleep(wait)
                    continue
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    raise RuntimeError(f"LLM provider returned HTTP {resp.status_code}: {body}")
                try:
                    data = resp.json()
                except Exception:
                    raise RuntimeError(f"LLM provider returned non-JSON: {resp.text[:300]}")
                break
            else:
                raise RuntimeError("LLM provider rate limited after 5 retries")
        if data is None:
            raise RuntimeError("LLM provider returned no data")

        if not data.get("choices"):
            error_msg = data.get("error", {}).get("message", "") if isinstance(data.get("error"), dict) else str(data.get("error", ""))
            raise RuntimeError(f"LLM returned no choices: {error_msg or data}")

        choice = data["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""

        tool_calls = []
        for tc in msg.get("tool_calls", []):
            import json
            args = tc["function"].get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


# ── 1. OpenAI ────────────────────────────────────────────────


class OpenAIProvider(_OpenAICompatible):
    name = "openai"
    description = "OpenAI GPT models"

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        super().__init__(api_key, model, "https://api.openai.com/v1")


# ── 2. Groq ──────────────────────────────────────────────────


class GroqProvider(_OpenAICompatible):
    name = "groq"
    description = "Groq ultra-fast inference"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        super().__init__(api_key, model, "https://api.groq.com/openai/v1")


# ── 3. Together AI ───────────────────────────────────────────


class TogetherProvider(_OpenAICompatible):
    name = "together"
    description = "Together AI inference"

    def __init__(self, api_key: str, model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"):
        super().__init__(api_key, model, "https://api.together.xyz/v1")


# ── 4. Mistral AI ────────────────────────────────────────────


class MistralProvider(_OpenAICompatible):
    name = "mistral"
    description = "Mistral AI models"

    def __init__(self, api_key: str, model: str = "mistral-large-latest"):
        super().__init__(api_key, model, "https://api.mistral.ai/v1")


# ── 5. Fireworks AI ──────────────────────────────────────────


class FireworksProvider(_OpenAICompatible):
    name = "fireworks"
    description = "Fireworks AI inference"

    def __init__(self, api_key: str, model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct"):
        super().__init__(api_key, model, "https://api.fireworks.ai/inference/v1")


# ── 6. Perplexity ────────────────────────────────────────────


class PerplexityProvider(_OpenAICompatible):
    name = "perplexity"
    description = "Perplexity online models with search"

    def __init__(self, api_key: str, model: str = "sonar-pro"):
        super().__init__(api_key, model, "https://api.perplexity.ai")


# ── 7. DeepSeek ──────────────────────────────────────────────


class DeepSeekProvider(_OpenAICompatible):
    name = "deepseek"
    description = "DeepSeek reasoning models"

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        super().__init__(api_key, model, "https://api.deepseek.com/v1")


# ── 8. xAI (Grok) ───────────────────────────────────────────


class XAIProvider(_OpenAICompatible):
    name = "xai"
    description = "xAI Grok models"

    def __init__(self, api_key: str, model: str = "grok-2-latest"):
        super().__init__(api_key, model, "https://api.x.ai/v1")


# ── 9. OpenRouter ────────────────────────────────────────────


class OpenRouterProvider(_OpenAICompatible):
    name = "openrouter"
    description = "OpenRouter multi-model gateway"

    def __init__(self, api_key: str, model: str = "anthropic/claude-sonnet-4"):
        super().__init__(api_key, model, "https://openrouter.ai/api/v1")


# ── 10. Cerebras ─────────────────────────────────────────────


class CerebrasProvider(_OpenAICompatible):
    name = "cerebras"
    description = "Cerebras ultra-fast inference"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b"):
        super().__init__(api_key, model, "https://api.cerebras.ai/v1")


# ── 11. SambaNova ────────────────────────────────────────────


class SambaNovaProvider(_OpenAICompatible):
    name = "sambanova"
    description = "SambaNova fast inference"

    def __init__(self, api_key: str, model: str = "Meta-Llama-3.3-70B-Instruct"):
        super().__init__(api_key, model, "https://api.sambanova.ai/v1")


# ── 12. Cohere ───────────────────────────────────────────────


class CohereProvider(BaseLLMProvider):
    name = "cohere"
    description = "Cohere Command models"

    def __init__(self, api_key: str, model: str = "command-r-plus"):
        self._api_key = api_key
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        chat_history = []
        message = ""
        for m in messages:
            content = m.content if isinstance(m.content, str) else str(m.content)
            if m.role == "user":
                message = content
            else:
                chat_history.append({"role": m.role.upper(), "message": content})

        payload: dict[str, Any] = {
            "model": self._model,
            "message": message,
            "chat_history": chat_history,
            "max_tokens": max_tokens,
        }
        if system:
            payload["preamble"] = system

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://api.cohere.ai/v1/chat",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload)
            resp.raise_for_status()
            data = resp.json()

        return LLMResponse(
            content=data.get("text", ""),
            stop_reason="stop",
            input_tokens=data.get("meta", {}).get("tokens", {}).get("input_tokens", 0),
            output_tokens=data.get("meta", {}).get("tokens", {}).get("output_tokens", 0),
        )


# ── 13. Google Gemini ────────────────────────────────────────


class GeminiProvider(BaseLLMProvider):
    name = "gemini"
    description = "Google Gemini models"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self._api_key = api_key
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        contents = []
        for m in messages:
            content = m.content if isinstance(m.content, str) else str(m.content)
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": content}]})

        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        payload["generationConfig"] = {"maxOutputTokens": max_tokens}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent?key={self._api_key}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            content=text or None,
            stop_reason="stop",
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )


# ── 14. Hugging Face Inference ───────────────────────────────


class HuggingFaceProvider(_OpenAICompatible):
    name = "huggingface"
    description = "Hugging Face Inference API"

    def __init__(self, api_key: str, model: str = "meta-llama/Llama-3.3-70B-Instruct"):
        super().__init__(api_key, model, "https://api-inference.huggingface.co/v1")


# ── 15. NVIDIA NIM ───────────────────────────────────────────


class NVIDIANIMProvider(_OpenAICompatible):
    name = "nvidia_nim"
    description = "NVIDIA NIM inference"

    def __init__(self, api_key: str, model: str = "meta/llama-3.3-70b-instruct"):
        super().__init__(api_key, model, "https://integrate.api.nvidia.com/v1")


# ── 16. AI21 ─────────────────────────────────────────────────


class AI21Provider(_OpenAICompatible):
    name = "ai21"
    description = "AI21 Jamba models"

    def __init__(self, api_key: str, model: str = "jamba-1.5-large"):
        super().__init__(api_key, model, "https://api.ai21.com/studio/v1")


# ── 17. Replicate ────────────────────────────────────────────


class ReplicateProvider(BaseLLMProvider):
    name = "replicate"
    description = "Replicate hosted models"

    def __init__(self, api_key: str, model: str = "meta/meta-llama-3-70b-instruct"):
        self._api_key = api_key
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        prompt = ""
        if system:
            prompt += f"<|system|>\n{system}\n"
        for m in messages:
            content = m.content if isinstance(m.content, str) else str(m.content)
            prompt += f"<|{m.role}|>\n{content}\n"
        prompt += "<|assistant|>\n"

        payload = {
            "version": self._model,
            "input": {"prompt": prompt, "max_tokens": max_tokens},
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://api.replicate.com/v1/predictions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload)
            resp.raise_for_status()
            data = resp.json()

            # Poll for result
            poll_url = data.get("urls", {}).get("get", "")
            if poll_url:
                import asyncio
                for _ in range(60):
                    await asyncio.sleep(2)
                    r = await client.get(poll_url, headers={"Authorization": f"Bearer {self._api_key}"})
                    result = r.json()
                    if result.get("status") in ("succeeded", "failed"):
                        output = result.get("output", "")
                        if isinstance(output, list):
                            output = "".join(output)
                        return LLMResponse(content=output or None, stop_reason="stop")

        return LLMResponse(content=None, stop_reason="error")


# ── 18. Ollama ───────────────────────────────────────────────


class OllamaProvider(_OpenAICompatible):
    name = "ollama"
    description = "Ollama local models"

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        super().__init__("ollama", model, f"{base}/v1")


# ── 19. LM Studio ───────────────────────────────────────────


class LMStudioProvider(_OpenAICompatible):
    name = "lmstudio"
    description = "LM Studio local models"

    def __init__(self, model: str = "auto", base_url: str = "http://localhost:1234"):
        # Strip /v1 suffix if user already included it (setup wizard saves full URL)
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        super().__init__("lm-studio", model, f"{base}/v1")


# ── 20. vLLM ─────────────────────────────────────────────────


class VLLMProvider(_OpenAICompatible):
    name = "vllm"
    description = "vLLM self-hosted inference"

    def __init__(self, model: str = "default", base_url: str = "http://localhost:8000"):
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        super().__init__("vllm", model, f"{base}/v1")


# ── 21. Anyscale ─────────────────────────────────────────────


class AnyscaleProvider(_OpenAICompatible):
    name = "anyscale"
    description = "Anyscale Endpoints"

    def __init__(self, api_key: str, model: str = "meta-llama/Llama-3-70b-chat-hf"):
        super().__init__(api_key, model, "https://api.endpoints.anyscale.com/v1")


# ── 22. Lepton AI ────────────────────────────────────────────


class LeptonProvider(_OpenAICompatible):
    name = "lepton"
    description = "Lepton AI serverless inference"

    def __init__(self, api_key: str, model: str = "llama3-70b"):
        super().__init__(api_key, model, f"https://{model}.lepton.run/api/v1")


# ── 23. Azure OpenAI ────────────────────────────────────────


class AzureOpenAIProvider(BaseLLMProvider):
    name = "azure_openai"
    description = "Azure OpenAI Service"

    def __init__(self, api_key: str, endpoint: str, deployment: str, api_version: str = "2024-06-01"):
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._api_version = api_version

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        api_msgs: list[dict] = []
        if system:
            api_msgs.append({"role": "system", "content": system})
        for m in messages:
            api_msgs.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {"messages": api_msgs, "max_tokens": max_tokens}

        url = f"{self._endpoint}/openai/deployments/{self._deployment}/chat/completions?api-version={self._api_version}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers={"api-key": self._api_key})
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"].get("content"),
            stop_reason=choice.get("finish_reason", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


# ── 24. AWS Bedrock ──────────────────────────────────────────


class BedrockProvider(BaseLLMProvider):
    name = "bedrock"
    description = "AWS Bedrock (Claude, Llama, etc.)"

    def __init__(self, model: str = "anthropic.claude-3-sonnet-20240229-v1:0", region: str = "us-east-1"):
        self._model = model
        self._region = region

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        try:
            import boto3
            client = boto3.client("bedrock-runtime", region_name=self._region)
            import json

            body: dict[str, Any] = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            }
            if system:
                body["system"] = system

            response = client.invoke_model(modelId=self._model, body=json.dumps(body))
            data = json.loads(response["body"].read())
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResponse(
                content=text or None,
                stop_reason=data.get("stop_reason", ""),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
        except ImportError:
            return LLMResponse(content="Error: boto3 not installed", stop_reason="error")
        except Exception as e:
            return LLMResponse(content=f"Error: {e}", stop_reason="error")


# ── 25. Cloudflare Workers AI ────────────────────────────────


class CloudflareAIProvider(_OpenAICompatible):
    name = "cloudflare_ai"
    description = "Cloudflare Workers AI"

    def __init__(self, api_key: str, account_id: str, model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"):
        super().__init__(api_key, model, f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1")


# ── 26. GitHub Models ────────────────────────────────────────


class GitHubModelsProvider(_OpenAICompatible):
    name = "github_models"
    description = "GitHub Models marketplace"

    def __init__(self, token: str, model: str = "gpt-4o"):
        super().__init__(token, model, "https://models.inference.ai.azure.com")


# ── 27. Custom OpenAI-Compatible ─────────────────────────────


class CustomOpenAIProvider(_OpenAICompatible):
    name = "custom_openai"
    description = "Any OpenAI-compatible endpoint"

    def __init__(self, api_key: str, model: str, base_url: str):
        super().__init__(api_key, model, base_url)


# ── Registry ─────────────────────────────────────────────────

try:
    from agos.llm.anthropic import AnthropicProvider as _AnthropicProvider
except ImportError:
    _AnthropicProvider = None

try:
    from agos.llm.claude_code import ClaudeCodeProvider as _ClaudeCodeProvider
except (ImportError, FileNotFoundError):
    _ClaudeCodeProvider = None

ALL_PROVIDERS: dict[str, type[BaseLLMProvider]] = {
    **({"anthropic": _AnthropicProvider} if _AnthropicProvider else {}),
    **({"claude_code": _ClaudeCodeProvider} if _ClaudeCodeProvider else {}),
    "openai": OpenAIProvider,
    "groq": GroqProvider,
    "together": TogetherProvider,
    "mistral": MistralProvider,
    "fireworks": FireworksProvider,
    "perplexity": PerplexityProvider,
    "deepseek": DeepSeekProvider,
    "xai": XAIProvider,
    "openrouter": OpenRouterProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "cohere": CohereProvider,
    "gemini": GeminiProvider,
    "huggingface": HuggingFaceProvider,
    "nvidia_nim": NVIDIANIMProvider,
    "ai21": AI21Provider,
    "replicate": ReplicateProvider,
    "ollama": OllamaProvider,
    "lmstudio": LMStudioProvider,
    "vllm": VLLMProvider,
    "anyscale": AnyscaleProvider,
    "lepton": LeptonProvider,
    "azure_openai": AzureOpenAIProvider,
    "bedrock": BedrockProvider,
    "cloudflare_ai": CloudflareAIProvider,
    "github_models": GitHubModelsProvider,
    "custom_openai": CustomOpenAIProvider,
}

# ── Config fields for setup UI ──────────────────────────────

_API_KEY_FIELD = {"key": "api_key", "label": "API Key", "type": "password", "required": True}
def _MODEL_FIELD(default):
    return {"key": "model", "label": "Model", "type": "text", "default": default}

def _MODEL_SELECT(default, options):
    return {"key": "model", "label": "Model", "type": "select", "default": default, "options": options}

def _BASE_URL_FIELD(default):
    return {"key": "base_url", "label": "Base URL", "type": "text", "default": default}

_PROVIDER_CONFIG_FIELDS: dict[str, list[dict]] = {
    # Anthropic (native API)
    "anthropic":    [_API_KEY_FIELD, _MODEL_SELECT("claude-haiku-4-5-20251001", [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
    ])],
    # Cloud providers (api_key + model)
    "openai":       [_API_KEY_FIELD, _MODEL_FIELD("gpt-4o")],
    "groq":         [_API_KEY_FIELD, _MODEL_FIELD("llama-3.3-70b-versatile")],
    "together":     [_API_KEY_FIELD, _MODEL_FIELD("meta-llama/Llama-3.3-70B-Instruct-Turbo")],
    "mistral":      [_API_KEY_FIELD, _MODEL_FIELD("mistral-large-latest")],
    "fireworks":    [_API_KEY_FIELD, _MODEL_FIELD("accounts/fireworks/models/llama-v3p3-70b-instruct")],
    "perplexity":   [_API_KEY_FIELD, _MODEL_FIELD("sonar-pro")],
    "deepseek":     [_API_KEY_FIELD, _MODEL_FIELD("deepseek-chat")],
    "xai":          [_API_KEY_FIELD, _MODEL_FIELD("grok-2-latest")],
    "openrouter":   [_API_KEY_FIELD, _MODEL_FIELD("anthropic/claude-sonnet-4")],
    "cerebras":     [_API_KEY_FIELD, _MODEL_FIELD("llama-3.3-70b")],
    "sambanova":    [_API_KEY_FIELD, _MODEL_FIELD("Meta-Llama-3.3-70B-Instruct")],
    "cohere":       [_API_KEY_FIELD, _MODEL_FIELD("command-r-plus")],
    "gemini":       [_API_KEY_FIELD, _MODEL_FIELD("gemini-2.0-flash")],
    "huggingface":  [_API_KEY_FIELD, _MODEL_FIELD("meta-llama/Llama-3.3-70B-Instruct")],
    "nvidia_nim":   [_API_KEY_FIELD, _MODEL_FIELD("meta/llama-3.3-70b-instruct")],
    "ai21":         [_API_KEY_FIELD, _MODEL_FIELD("jamba-1.5-large")],
    "replicate":    [_API_KEY_FIELD, _MODEL_FIELD("meta/meta-llama-3-70b-instruct")],
    "anyscale":     [_API_KEY_FIELD, _MODEL_FIELD("meta-llama/Llama-3-70b-chat-hf")],
    "lepton":       [_API_KEY_FIELD, _MODEL_FIELD("llama3-70b")],
    # Local providers (no api_key, just base_url + model)
    "ollama":       [_MODEL_FIELD("llama3"), _BASE_URL_FIELD("http://localhost:11434")],
    "lmstudio":     [_MODEL_FIELD("auto"), _BASE_URL_FIELD("http://localhost:1234")],
    "vllm":         [_MODEL_FIELD("default"), _BASE_URL_FIELD("http://localhost:8000")],
    # Special providers
    "azure_openai": [
        _API_KEY_FIELD,
        {"key": "endpoint", "label": "Endpoint URL", "type": "text", "required": True},
        {"key": "deployment", "label": "Deployment Name", "type": "text", "required": True},
        {"key": "api_version", "label": "API Version", "type": "text", "default": "2024-06-01"},
    ],
    "bedrock":      [
        _MODEL_FIELD("anthropic.claude-3-sonnet-20240229-v1:0"),
        {"key": "region", "label": "AWS Region", "type": "text", "default": "us-east-1"},
    ],
    "cloudflare_ai": [
        _API_KEY_FIELD,
        {"key": "account_id", "label": "Account ID", "type": "text", "required": True},
        _MODEL_FIELD("@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    ],
    "github_models": [
        {"key": "api_key", "label": "GitHub Token", "type": "password", "required": True},
        _MODEL_FIELD("gpt-4o"),
    ],
    "custom_openai": [
        _API_KEY_FIELD,
        _MODEL_FIELD(""),
        _BASE_URL_FIELD(""),
    ],
    # Claude Code (no API key needed — uses your subscription)
    "claude_code": [
        _MODEL_SELECT("sonnet", ["haiku", "sonnet", "opus"]),
        {"key": "mode", "label": "Mode", "type": "select", "default": "cli",
         "options": ["cli", "oauth"],
         "description": "cli = free (uses subscription), oauth = direct API (faster but may cost)"},
    ],
}


def provider_config_fields(name: str) -> list[dict]:
    """Return config fields for a provider by name (for setup UI)."""
    return _PROVIDER_CONFIG_FIELDS.get(name, [_API_KEY_FIELD, _MODEL_FIELD("")])
