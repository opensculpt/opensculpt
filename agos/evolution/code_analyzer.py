"""CodeAnalyzer — uses Claude to analyze repository code for implementable patterns.

Given a RepoSnapshot, analyzes the code to extract specific, implementable
techniques with actual code snippets that can be adapted for agos.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.llm.base import BaseLLMProvider, LLMMessage
from agos.evolution.repo_scout import RepoSnapshot
from agos.evolution.analyzer import AGOS_ARCHITECTURE

CODE_ANALYSIS_PROMPT = """You are an AI systems architect analyzing source code from a research
paper's repository. Your job is to extract CONCRETE, IMPLEMENTABLE patterns
that could improve an Agentic OS called "agos".

Focus on:
1. Memory/knowledge management patterns (storage, retrieval, indexing)
2. Agent coordination patterns (communication, task delegation)
3. Self-improvement/meta-learning patterns
4. Tool use and planning patterns
5. Novel architectural patterns

For each pattern you find, provide:
- The specific technique and what it does
- A CONCRETE code snippet showing the core pattern (adapted for Python async)
- Which agos module it improves
- How to integrate it

Respond in JSON format:
{
    "patterns": [
        {
            "name": "Pattern Name (3-8 words)",
            "description": "What this pattern does (2-3 sentences)",
            "source_file": "which file in the repo this came from",
            "code_snippet": "Python code showing the core pattern (10-40 lines, async-compatible)",
            "agos_module": "knowledge/coordination/intent/kernel/tools/evolution",
            "integration_steps": "How to integrate into agos (2-3 concrete steps)",
            "priority": "high/medium/low"
        }
    ]
}

If no useful patterns found, respond: {"patterns": []}"""


class CodePattern(BaseModel):
    """A concrete, implementable code pattern extracted from a repository."""

    id: str = Field(default_factory=new_id)
    name: str = ""
    description: str = ""
    source_file: str = ""
    source_repo: str = ""
    code_snippet: str = ""
    agos_module: str = ""
    integration_steps: str = ""
    priority: str = "medium"


class CodeAnalysisResult(BaseModel):
    """Results from analyzing a repository's code."""

    id: str = Field(default_factory=new_id)
    repo_url: str = ""
    repo_name: str = ""
    patterns: list[CodePattern] = Field(default_factory=list)
    files_analyzed: int = 0
    total_code_size: int = 0


class CodeAnalyzer:
    """Analyzes repository code via Claude to extract implementable patterns."""

    def __init__(self, llm: BaseLLMProvider) -> None:
        self._llm = llm

    async def analyze_repo(self, snapshot: RepoSnapshot) -> CodeAnalysisResult:
        """Analyze a repository snapshot and extract patterns."""
        result = CodeAnalysisResult(
            repo_url=snapshot.repo_url,
            repo_name=snapshot.repo_name,
            files_analyzed=len(snapshot.files),
            total_code_size=snapshot.total_code_size,
        )

        if not snapshot.files:
            return result

        # Build context from repo files
        code_context = self._build_code_context(snapshot)

        try:
            response = await self._llm.complete(
                messages=[LLMMessage(role="user", content=code_context)],
                system=CODE_ANALYSIS_PROMPT,
                max_tokens=8192,
            )

            if not response.content:
                return result

            data = self._parse_response(response.content)
            if not data:
                return result

            for pat_data in data.get("patterns", []):
                pattern = CodePattern(
                    name=pat_data.get("name", ""),
                    description=pat_data.get("description", ""),
                    source_file=pat_data.get("source_file", ""),
                    source_repo=snapshot.repo_url,
                    code_snippet=pat_data.get("code_snippet", ""),
                    agos_module=pat_data.get("agos_module", ""),
                    integration_steps=pat_data.get("integration_steps", ""),
                    priority=pat_data.get("priority", "medium"),
                )
                if pattern.name and pattern.code_snippet:
                    result.patterns.append(pattern)

        except Exception:
            pass

        return result

    def _build_code_context(self, snapshot: RepoSnapshot) -> str:
        """Build a context string from the repo's code files."""
        parts = [
            f"Repository: {snapshot.owner}/{snapshot.repo_name}",
            f"Description: {snapshot.description}",
            f"Language: {snapshot.language}",
            f"Stars: {snapshot.stars}",
            "",
        ]

        if snapshot.readme:
            parts.append("=== README (excerpt) ===")
            parts.append(snapshot.readme[:2000])
            parts.append("")

        parts.append(f"=== File tree ({len(snapshot.file_tree)} files) ===")
        for f in snapshot.file_tree[:50]:
            parts.append(f"  {f}")
        parts.append("")

        # Add code files (truncate each to keep within token budget)
        max_per_file = 3000
        for rf in snapshot.files[:10]:
            parts.append(f"=== {rf.path} ({rf.size} bytes) ===")
            content = rf.content[:max_per_file]
            if len(rf.content) > max_per_file:
                content += "\n... (truncated)"
            parts.append(content)
            parts.append("")

        parts.append(f"\nagos Architecture:\n{AGOS_ARCHITECTURE}")

        return "\n".join(parts)

    def _parse_response(self, text: str) -> dict | None:
        """Extract JSON from LLM response."""
        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
        return None
