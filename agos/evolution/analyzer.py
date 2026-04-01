"""PaperAnalyzer — uses Claude to extract actionable techniques from papers.

Given a paper's abstract, the analyzer determines whether the paper
contains techniques that could improve agos, and extracts structured
insights if so.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.llm.base import BaseLLMProvider, LLMMessage
from agos.evolution.scout import Paper

AGOS_ARCHITECTURE = """agos is an Agentic Operating System with these modules:
- Intent Engine: natural language -> execution plans via LLM
- Agent Kernel: async agent lifecycle, state machine, token budgets
- Knowledge System: episodic memory (SQLite), semantic search (TF-IDF), knowledge graph
- Tool Bus: file_read, file_write, shell_exec, http_request, python_exec
- Triggers: file watch, cron schedule, webhooks for ambient intelligence
- Multi-Agent Coordination: channels, shared workspace, team strategies (solo/pipeline/parallel/debate)
- Policy Engine: tool ACLs, rate limits, audit trail
- Event Bus: pub/sub with wildcard topic matching
- Memory Notes: Zettelkasten-style linked notes with importance decay
- Memory Consolidation: compresses old memories into insights
- Working Memory: task-scoped ephemeral context with capacity limits

The system is Python 3.11+, async-native, uses aiosqlite for persistence,
httpx for HTTP, and Claude (Anthropic API) as its LLM backbone."""

ANALYSIS_PROMPT = """You are an AI systems architect analyzing research papers for techniques
that could improve an Agentic Operating System called "agos".

Given a paper's title and abstract, determine:
1. Is this paper relevant to improving agos? (yes/no)
2. If yes, extract ONE specific, actionable technique

Respond in JSON format:
{
    "relevant": true/false,
    "technique": "Name of the technique (3-8 words)",
    "description": "What the technique does and how it works (2-3 sentences)",
    "applicability": "Specifically how this could improve agos (2-3 sentences)",
    "priority": "high/medium/low",
    "agos_module": "Which agos module this improves (e.g., 'knowledge', 'coordination', 'intent', 'kernel', 'tools', 'evolution')",
    "implementation_hint": "High-level steps to implement this in agos (2-3 sentences)"
}

If the paper is NOT relevant, respond:
{"relevant": false}"""


class PaperInsight(BaseModel):
    """An actionable insight extracted from a research paper."""

    id: str = Field(default_factory=new_id)
    paper_id: str = ""
    paper_title: str = ""
    technique: str = ""
    description: str = ""
    applicability: str = ""
    priority: str = "medium"  # high, medium, low
    agos_module: str = ""
    implementation_hint: str = ""


class PaperAnalyzer:
    """Analyzes papers via Claude and extracts actionable improvements."""

    def __init__(self, llm: BaseLLMProvider) -> None:
        self._llm = llm

    async def analyze(self, paper: Paper) -> PaperInsight | None:
        """Analyze a single paper. Returns insight if relevant, None otherwise."""
        user_content = (
            f"Paper Title: {paper.title}\n"
            f"Authors: {', '.join(paper.authors[:5])}\n"
            f"Categories: {', '.join(paper.categories)}\n"
            f"Published: {paper.published.strftime('%Y-%m-%d')}\n\n"
            f"Abstract:\n{paper.abstract[:2000]}\n\n"
            f"agos Architecture:\n{AGOS_ARCHITECTURE}"
        )

        try:
            response = await self._llm.complete(
                messages=[LLMMessage(role="user", content=user_content)],
                system=ANALYSIS_PROMPT,
                max_tokens=1024,
            )

            if not response.content:
                return None

            data = self._parse_response(response.content)
            if not data or not data.get("relevant"):
                return None

            return PaperInsight(
                paper_id=paper.arxiv_id,
                paper_title=paper.title,
                technique=data.get("technique", ""),
                description=data.get("description", ""),
                applicability=data.get("applicability", ""),
                priority=data.get("priority", "medium"),
                agos_module=data.get("agos_module", ""),
                implementation_hint=data.get("implementation_hint", ""),
            )

        except Exception:
            return None

    async def analyze_batch(self, papers: list[Paper]) -> list[PaperInsight]:
        """Analyze multiple papers sequentially. Returns only relevant insights."""
        insights = []
        for paper in papers:
            insight = await self.analyze(paper)
            if insight:
                insights.append(insight)
        return insights

    def _parse_response(self, text: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
        return None
