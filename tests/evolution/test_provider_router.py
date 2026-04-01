"""Tests for the evolution provider router."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from agos.evolution.providers.router import build_evolution_provider


def _make_settings(**overrides):
    """Create a minimal settings-like object."""
    defaults = {
        "evolution_llm_provider": "auto",
        "lmstudio_base_url": "http://localhost:1234/v1",
        "lmstudio_model": "",
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "llama3",
        "anthropic_api_key": "",
        "default_model": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)

    class FakeSettings:
        pass

    s = FakeSettings()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestProviderRouter:
    @pytest.mark.asyncio
    async def test_explicit_template_selection(self):
        from agos.evolution.providers.template_provider import TemplateProvider
        settings = _make_settings(evolution_llm_provider="template")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, TemplateProvider)

    @pytest.mark.asyncio
    async def test_explicit_lmstudio_selection(self):
        from agos.evolution.providers.lmstudio_provider import LMStudioProvider
        settings = _make_settings(evolution_llm_provider="lmstudio")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, LMStudioProvider)

    @pytest.mark.asyncio
    async def test_explicit_ollama_selection(self):
        from agos.evolution.providers.ollama_provider import OllamaProvider
        settings = _make_settings(evolution_llm_provider="ollama")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, OllamaProvider)

    @pytest.mark.asyncio
    @patch("agos.evolution.providers.router._probe_lmstudio", return_value=True)
    async def test_auto_detects_lmstudio(self, mock_probe):
        from agos.evolution.providers.lmstudio_provider import LMStudioProvider
        settings = _make_settings(evolution_llm_provider="auto")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, LMStudioProvider)

    @pytest.mark.asyncio
    @patch("agos.evolution.providers.router._probe_lmstudio", return_value=False)
    @patch("agos.evolution.providers.router._probe_ollama", return_value=True)
    async def test_auto_falls_to_ollama(self, mock_ollama, mock_lm):
        from agos.evolution.providers.ollama_provider import OllamaProvider
        settings = _make_settings(evolution_llm_provider="auto")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, OllamaProvider)

    @pytest.mark.asyncio
    @patch("agos.evolution.providers.router._probe_lmstudio", return_value=False)
    @patch("agos.evolution.providers.router._probe_ollama", return_value=False)
    async def test_auto_falls_to_template_no_key(self, mock_ollama, mock_lm):
        from agos.evolution.providers.template_provider import TemplateProvider
        settings = _make_settings(evolution_llm_provider="auto", anthropic_api_key="")
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, TemplateProvider)

    @pytest.mark.asyncio
    @patch("agos.evolution.providers.router._probe_lmstudio", return_value=False)
    @patch("agos.evolution.providers.router._probe_ollama", return_value=False)
    async def test_auto_uses_anthropic_when_key_set(self, mock_ollama, mock_lm):
        from agos.evolution.llm_provider import LLMProvider
        settings = _make_settings(
            evolution_llm_provider="auto",
            anthropic_api_key="sk-ant-test-key",
        )
        provider = await build_evolution_provider(settings)
        assert isinstance(provider, LLMProvider)
