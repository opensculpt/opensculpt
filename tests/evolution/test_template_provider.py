"""Tests for zero-cost template provider."""

import json

import pytest

from agos.evolution.providers.template_provider import TemplateProvider
from agos.evolution.sandbox import Sandbox


class TestTemplateProvider:
    @pytest.mark.asyncio
    async def test_complete_returns_none_content(self):
        from agos.llm.base import LLMMessage
        provider = TemplateProvider()
        resp = await provider.complete([LLMMessage(role="user", content="hi")])
        assert resp.content is None

    @pytest.mark.asyncio
    async def test_iterate_prompt_returns_code(self):
        provider = TemplateProvider()
        prompt = (
            "You are improving a Python code pattern.\n"
            "Current fitness: 0.5\n"
            "```python\nx = 1\n```\n"
        )
        result = await provider.complete_prompt(prompt)
        assert len(result) > 0
        # Should be valid Python
        import ast
        ast.parse(result)

    @pytest.mark.asyncio
    async def test_insight_prompt_returns_module_snippet(self):
        provider = TemplateProvider()
        prompt = (
            "You are writing a Python code pattern for an agentic OS component.\n\n"
            "Research technique: Cosine Similarity Search\n"
            "Target module: knowledge.semantic\n"
        )
        result = await provider.complete_prompt(prompt)
        assert len(result) > 20
        # Should contain code from seed patterns
        assert "def " in result or "class " in result

    @pytest.mark.asyncio
    async def test_reflection_prompt_strips_bad_imports(self):
        provider = TemplateProvider()
        prompt = (
            "The code failed sandbox:\n"
            "Error: Blocked import: os\n\n"
            "```python\nimport os\nimport math\nx = math.sqrt(4)\n```"
        )
        result = await provider.complete_prompt(prompt)
        assert "import os" not in result
        assert "import math" in result

    @pytest.mark.asyncio
    async def test_ideation_prompt_returns_json(self):
        provider = TemplateProvider()
        prompt = "Propose up to 3 parameter changes for the genome."
        result = await provider.complete_prompt(prompt)
        mutations = json.loads(result)
        assert isinstance(mutations, list)
        assert len(mutations) > 0
        assert "param" in mutations[0]

    @pytest.mark.asyncio
    async def test_fallback_snippet_passes_sandbox(self):
        provider = TemplateProvider()
        sandbox = Sandbox(timeout=10)
        code = await provider.complete_prompt("unknown prompt type")
        result = await sandbox.test_pattern(code)
        assert result.passed

    @pytest.mark.asyncio
    async def test_insight_snippet_passes_sandbox(self):
        provider = TemplateProvider()
        sandbox = Sandbox(timeout=10)
        prompt = (
            "You are writing a Python code pattern for an agentic OS component.\n\n"
            "Research technique: Graph Traversal\n"
            "Target module: knowledge.graph\n"
        )
        code = await provider.complete_prompt(prompt)
        result = await sandbox.test_pattern(code)
        assert result.passed
