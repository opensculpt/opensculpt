"""ArxivScout — searches arxiv for research papers relevant to agos.

Periodically scans arxiv's free API for papers on agentic AI,
memory systems, multi-agent coordination, and related topics.
No authentication needed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class Paper(BaseModel):
    """A research paper from arxiv."""

    arxiv_id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    categories: list[str] = Field(default_factory=list)
    published: datetime = Field(default_factory=datetime.utcnow)
    updated: datetime | None = None
    pdf_url: str = ""
    abs_url: str = ""


# Topics the evolution engine searches for
SEARCH_TOPICS = [
    # Knowledge & Memory
    "agentic memory systems",
    "knowledge graph reasoning agents",
    "retrieval augmented generation agents",
    # Intent & Intelligence
    "LLM intent classification planning",
    "agent persona role specialization",
    "proactive AI anomaly detection",
    # Orchestration & Coordination
    "multi-agent coordination LLM",
    "LLM agent workflow orchestration",
    "task scheduling distributed agents",
    # Policy & Governance
    "AI safety policy enforcement agents",
    "LLM rate limiting access control",
    "audit trail accountability AI systems",
    # Experience & Observability
    "agent observability distributed tracing",
    "event driven architecture AI agents",
    # Security & Hardening
    "LLM agent sandbox escape detection",
    "prompt injection defense techniques",
    "AI system vulnerability scanning",
    # Cross-cutting
    "autonomous agent self-improvement",
    "meta-learning agent architecture",
]

# Industry-specific topics per node role (OWASP, OpenID, domain experts)
INDUSTRY_TOPICS: dict[str, list[str]] = {
    "knowledge": [
        "RAG retrieval augmented generation optimization",
        "vector database indexing large scale",
        "episodic memory consolidation agents",
        "semantic memory graph neural networks",
    ],
    "intent": [
        "intent classification transformer architectures",
        "conversational AI multi-turn planning",
        "proactive agent anomaly detection systems",
        "user intent disambiguation LLM",
    ],
    "orchestration": [
        "agent-to-agent protocol interoperability",
        "workflow orchestration autonomous agents",
        "federated multi-agent task delegation",
        "distributed agent consensus protocols",
    ],
    "policy": [
        "OWASP LLM AI application security risks",
        "prompt injection detection defense LLM",
        "AI governance compliance frameworks",
        "LLM red teaming adversarial robustness",
        "AI agent audit trail accountability",
        "OpenID AI agent identity authentication",
        "Python sandbox escape prevention techniques",
        "code execution isolation containers security",
    ],
    "general": [
        "meta-learning agent self-improvement ALMA",
        "autonomous AI systems architecture design",
        "decentralized AI governance protocols",
        "OpenID foundation AI identity management",
        "AI agent vulnerability detection scanning",
        "secure code evolution validation techniques",
    ],
}


def get_topics_for_role(role: str) -> list[str]:
    """Return search topics for a node role: specialized + shared general."""
    role_specific = INDUSTRY_TOPICS.get(role, [])
    shared = SEARCH_TOPICS[:4]  # always include top 4 general topics
    if role == "general":
        return SEARCH_TOPICS + INDUSTRY_TOPICS.get("general", [])
    return role_specific + shared


# Arxiv categories we care about
CATEGORIES = ["cs.AI", "cs.MA", "cs.CL", "cs.SE", "cs.LG"]


class ArxivScout:
    """Searches arxiv for papers that could improve agos."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def search(self, query: str, max_results: int = 10) -> list[Paper]:
        """Search arxiv for papers matching a query.

        Uses ti: (title) and abs: (abstract) fields instead of all:
        to avoid matching papers that mention keywords only in passing.
        Restricts to CS categories.
        """
        cat_filter = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
        encoded_query = quote(query, safe="")
        # Search title + abstract only (not full text) for precision
        search_query = (
            f"(ti:{encoded_query}+OR+abs:{encoded_query})"
            f"+AND+({cat_filter})"
        )
        url = (
            f"{ARXIV_API}?search_query={search_query}"
            f"&max_results={max_results}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        return self._parse_atom(resp.text)

    async def search_recent(
        self, days: int = 7, max_results: int = 20
    ) -> list[Paper]:
        """Search across all topics for recent papers."""
        all_papers: dict[str, Paper] = {}

        for topic in SEARCH_TOPICS:
            try:
                papers = await self.search(topic, max_results=max_results // len(SEARCH_TOPICS) + 1)
                for p in papers:
                    # Deduplicate by arxiv_id
                    if p.arxiv_id not in all_papers:
                        all_papers[p.arxiv_id] = p
            except Exception:
                continue  # One failed topic shouldn't stop the rest

        # Filter to papers within the date window
        cutoff = datetime.utcnow() - timedelta(days=days)
        recent = [p for p in all_papers.values() if p.published >= cutoff]

        # Sort by most recent first
        recent.sort(key=lambda p: p.published, reverse=True)
        return recent[:max_results]

    def _parse_atom(self, xml_text: str) -> list[Paper]:
        """Parse arxiv Atom XML response into Paper objects."""
        papers = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return papers

        for entry in root.findall(f"{ATOM_NS}entry"):
            paper = self._parse_entry(entry)
            if paper:
                papers.append(paper)

        return papers

    def _parse_entry(self, entry: ET.Element) -> Paper | None:
        """Parse a single Atom entry into a Paper."""
        try:
            # ID
            id_el = entry.find(f"{ATOM_NS}id")
            arxiv_id = id_el.text.split("/abs/")[-1] if id_el is not None and id_el.text else ""

            # Title
            title_el = entry.find(f"{ATOM_NS}title")
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

            # Abstract
            summary_el = entry.find(f"{ATOM_NS}summary")
            abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""

            # Authors
            authors = []
            for author_el in entry.findall(f"{ATOM_NS}author"):
                name_el = author_el.find(f"{ATOM_NS}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text)

            # Categories
            categories = []
            for cat_el in entry.findall(f"{ARXIV_NS}primary_category"):
                term = cat_el.get("term", "")
                if term:
                    categories.append(term)
            for cat_el in entry.findall(f"{ATOM_NS}category"):
                term = cat_el.get("term", "")
                if term and term not in categories:
                    categories.append(term)

            # Published date
            pub_el = entry.find(f"{ATOM_NS}published")
            published = datetime.utcnow()
            if pub_el is not None and pub_el.text:
                try:
                    published = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass

            # Updated date
            upd_el = entry.find(f"{ATOM_NS}updated")
            updated = None
            if upd_el is not None and upd_el.text:
                try:
                    updated = datetime.fromisoformat(upd_el.text.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass

            # Links
            pdf_url = ""
            abs_url = ""
            for link_el in entry.findall(f"{ATOM_NS}link"):
                href = link_el.get("href", "")
                link_type = link_el.get("type", "")
                rel = link_el.get("rel", "")
                if link_type == "application/pdf" or link_el.get("title") == "pdf":
                    pdf_url = href
                elif rel == "alternate":
                    abs_url = href

            if not arxiv_id or not title:
                return None

            return Paper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                categories=categories,
                published=published,
                updated=updated,
                pdf_url=pdf_url,
                abs_url=abs_url,
            )

        except Exception:
            return None
