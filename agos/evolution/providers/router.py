"""Provider router — auto-selects the best available LLM provider.

Reads setup.json to use whatever provider the user already configured.
Priority: explicit setting → setup.json active provider → local servers → template.
"""

from __future__ import annotations

import logging
import os as _os

import httpx

from agos.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


async def _probe_lmstudio(base_url: str) -> bool:
    """Check if LM Studio is reachable and has models loaded."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{base_url}/models")
            resp.raise_for_status()
            models = resp.json().get("data", [])
            return len(models) > 0
    except Exception:
        return False


async def _probe_ollama(base_url: str) -> bool:
    """Check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            return True
    except Exception:
        return False


def _load_from_setup(settings) -> BaseLLMProvider | None:
    """Load provider from setup.json — uses whatever the user configured."""
    try:
        from agos.setup_store import load_setup
        from agos.llm.providers import ALL_PROVIDERS

        for ws in [str(settings.workspace_dir),
                   _os.path.join(_os.getcwd(), ".opensculpt")]:
            if not _os.path.isdir(ws):
                continue
            data = load_setup(ws)
            providers = data.get("providers", {})

            # Try active provider first (user explicitly selected)
            active = data.get("active_provider", "")
            if active and active in providers:
                cfg = providers[active]
                if cfg.get("enabled") or cfg.get("api_key"):
                    p = _build_provider(active, cfg, ALL_PROVIDERS)
                    if p:
                        logger.info("Evolution LLM: %s (from setup.json active_provider)", active)
                        return p

            # Try all enabled providers
            for name, cfg in providers.items():
                if not cfg.get("enabled"):
                    continue
                p = _build_provider(name, cfg, ALL_PROVIDERS)
                if p:
                    logger.info("Evolution LLM: %s (from setup.json)", name)
                    return p
    except Exception as e:
        logger.debug("setup.json provider load failed: %s", e)
    return None


def _build_provider(name: str, cfg: dict, all_providers: dict) -> BaseLLMProvider | None:
    """Instantiate a provider by name + config dict."""
    cls = all_providers.get(name)
    if not cls:
        return None
    kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
    try:
        return cls(**kwargs)
    except Exception:
        return None


async def build_evolution_provider(settings) -> BaseLLMProvider:
    """Build the best available LLM provider based on config.

    Returns a provider that is always non-None. TemplateProvider
    is the final fallback (zero-cost, no network).
    """
    choice = settings.evolution_llm_provider

    # ── Explicit provider selection ──
    if choice == "lmstudio":
        from agos.evolution.providers.lmstudio_provider import LMStudioProvider
        provider = LMStudioProvider(
            base_url=settings.lmstudio_base_url,
            model=settings.lmstudio_model,
        )
        logger.info("Evolution LLM: LM Studio (%s)", settings.lmstudio_base_url)
        return provider

    if choice == "ollama":
        from agos.evolution.providers.ollama_provider import OllamaProvider
        provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
        logger.info("Evolution LLM: Ollama (%s/%s)", settings.ollama_base_url, settings.ollama_model)
        return provider

    if choice == "anthropic":
        if settings.anthropic_api_key:
            from agos.llm.anthropic import AnthropicProvider
            provider = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.default_model,
            )
            logger.info("Evolution LLM: Anthropic (%s)", settings.default_model)
            return provider
        logger.warning("Anthropic selected but no API key — falling back to auto")

    if choice == "template":
        from agos.evolution.providers.template_provider import TemplateProvider
        logger.info("Evolution LLM: Template (zero-cost, no network)")
        return TemplateProvider()

    # ── Auto-detection ──

    # 1. setup.json — use whatever provider the user already configured
    setup_provider = _load_from_setup(settings)
    if setup_provider is not None:
        return setup_provider

    # 2. Probe local servers (free)
    if await _probe_lmstudio(settings.lmstudio_base_url):
        from agos.evolution.providers.lmstudio_provider import LMStudioProvider
        provider = LMStudioProvider(
            base_url=settings.lmstudio_base_url,
            model=settings.lmstudio_model,
        )
        logger.info("Evolution LLM: auto-detected LM Studio at %s", settings.lmstudio_base_url)
        return provider

    if await _probe_ollama(settings.ollama_base_url):
        from agos.evolution.providers.ollama_provider import OllamaProvider
        provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
        logger.info("Evolution LLM: auto-detected Ollama at %s", settings.ollama_base_url)
        return provider

    # 3. Anthropic direct (if key is set via env var)
    if settings.anthropic_api_key:
        from agos.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.default_model,
        )
        logger.info("Evolution LLM: Anthropic (%s)", settings.default_model)
        return provider

    # Final fallback — always works, zero cost
    from agos.evolution.providers.template_provider import TemplateProvider
    logger.info("Evolution LLM: Template (no LLM available — zero-cost fallback)")
    return TemplateProvider()
