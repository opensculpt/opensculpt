"""Template provider — zero-cost evolution with no LLM calls.

Uses seed patterns and deterministic mutations as a fallback when
no local or cloud LLM is available. All returned code is pre-validated
and passes the sandbox by construction.
"""

from __future__ import annotations

import json
import random
import re

from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse


class TemplateProvider(BaseLLMProvider):
    """Zero-cost provider using seed patterns and deterministic mutations."""

    name = "template"

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # PaperAnalyzer / CodeAnalyzer call this.
        # Return None content so callers fall through to heuristic paths.
        return LLMResponse(content=None, stop_reason="end_turn")

    async def complete_prompt(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        self._call_count += 1
        lower = prompt.lower()

        # Dispatch based on prompt structure
        if "you are improving" in lower or "current fitness" in lower:
            return self._handle_iterate(prompt)
        if "you are writing a python code pattern" in lower:
            return self._handle_insight(prompt)
        if "failed sandbox" in lower:
            return self._handle_reflection(prompt)
        if "propose up to" in lower and "parameter" in lower:
            return self._handle_ideation(prompt)

        # Unknown prompt — return a minimal valid snippet
        return self._fallback_snippet()

    # ── Handlers ─────────────────────────────────────────────────

    def _handle_iterate(self, prompt: str) -> str:
        """Iterate on existing code: apply deterministic mutations."""
        # Extract existing code from the prompt
        code = self._extract_code(prompt)
        if not code:
            return self._fallback_snippet()
        return self._mutate(code)

    def _handle_insight(self, prompt: str) -> str:
        """Generate code from paper insight: pick a seed snippet."""
        module = self._extract_module(prompt)
        return self._snippet_for_module(module)

    def _handle_reflection(self, prompt: str) -> str:
        """Fix failed code: try stripping blocked imports."""
        code = self._extract_code(prompt)
        if not code:
            return self._fallback_snippet()
        # Strip lines with blocked imports
        fixed_lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                # Keep safe imports only
                safe = ("json", "re", "math", "random", "collections",
                        "hashlib", "functools", "itertools", "typing",
                        "dataclasses", "abc", "enum", "time", "copy")
                if any(s in stripped for s in safe):
                    fixed_lines.append(line)
                # else: drop the import
            else:
                fixed_lines.append(line)
        return "\n".join(fixed_lines)

    def _handle_ideation(self, prompt: str) -> str:
        """Propose parameter mutations as JSON."""
        mutations = []
        params = [
            ("decay_factor", random.uniform(0.05, 0.3)),
            ("temperature", random.uniform(0.5, 2.0)),
            ("top_k", random.randint(3, 20)),
        ]
        for name, value in random.sample(params, min(2, len(params))):
            mutations.append({"component": "knowledge", "param": name,
                              "value": round(value, 3), "reason": "exploration"})
        return json.dumps(mutations)

    # ── Helpers ───────────────────────────────────────────────────

    def _extract_code(self, prompt: str) -> str:
        """Extract Python code from a prompt (between ``` fences or after 'Code:')."""
        if "```python" in prompt:
            start = prompt.index("```python") + 9
            end = prompt.index("```", start)
            return prompt[start:end].strip()
        if "```" in prompt:
            start = prompt.index("```") + 3
            try:
                end = prompt.index("```", start)
                return prompt[start:end].strip()
            except ValueError:
                return prompt[start:].strip()
        return ""

    def _extract_module(self, prompt: str) -> str:
        """Extract target module from prompt."""
        m = re.search(r"Target module:\s*(\S+)", prompt)
        return m.group(1) if m else "knowledge"

    def _snippet_for_module(self, module: str) -> str:
        """Pick a seed snippet for the given module."""
        from agos.evolution.seed_patterns import _ALL_SNIPPETS
        patterns = _ALL_SNIPPETS.get(module)
        if not patterns:
            # Try parent module (e.g. "knowledge.semantic" -> "knowledge")
            parent = module.split(".")[0]
            patterns = _ALL_SNIPPETS.get(parent)
        if patterns:
            idx = self._call_count % len(patterns)
            return patterns[idx].code_snippet
        return self._fallback_snippet()

    def _mutate(self, code: str) -> str:
        """Apply deterministic mutations to numeric constants."""
        def _tweak(match: re.Match) -> str:
            val = float(match.group())
            # Shift by ±10-20%
            factor = 1.0 + random.uniform(-0.2, 0.2)
            new_val = val * factor
            if val == int(val) and abs(new_val) > 1:
                return str(int(round(new_val)))
            return f"{new_val:.4f}"

        # Only mutate numbers in assignments, not in imports/prints
        lines = code.splitlines()
        mutated = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ", "print(", "assert ")):
                mutated.append(line)
            else:
                mutated.append(re.sub(r"(?<![a-zA-Z_])\d+\.\d+(?![a-zA-Z_])", _tweak, line))
        return "\n".join(mutated)

    def _fallback_snippet(self) -> str:
        """Minimal valid snippet that passes sandbox."""
        return (
            "import math\n\n"
            "def evolved_function(data):\n"
            "    if not data:\n"
            "        return []\n"
            "    total = sum(data)\n"
            "    return [x / max(total, 1e-9) for x in data]\n\n"
            "result = evolved_function([1, 2, 3, 4])\n"
            "assert abs(sum(result) - 1.0) < 1e-6\n"
            "print('PASS: normalization')\n"
        )
