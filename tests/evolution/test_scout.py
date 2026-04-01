"""Tests for the ArxivScout."""

from datetime import datetime


from agos.evolution.scout import ArxivScout, Paper, SEARCH_TOPICS, CATEGORIES


# Sample arxiv Atom XML for testing the parser
SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Agentic Memory Systems for Self-Improving AI</title>
    <summary>We propose a novel approach to agentic memory that enables
    autonomous agents to dynamically adapt their memory structures based
    on task requirements. Our method combines episodic and semantic
    memory with a meta-learning controller.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
    <category term="cs.CL" />
    <published>2024-01-15T00:00:00Z</published>
    <updated>2024-01-16T00:00:00Z</updated>
    <link href="http://arxiv.org/abs/2401.12345v1" rel="alternate" type="text/html" />
    <link href="http://arxiv.org/pdf/2401.12345v1" title="pdf" type="application/pdf" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.67890v1</id>
    <title>Multi-Agent Coordination via Shared Knowledge Graphs</title>
    <summary>This paper presents a framework for multi-agent systems where
    agents share a dynamic knowledge graph to coordinate actions and
    share learned experiences.</summary>
    <author><name>Carol White</name></author>
    <arxiv:primary_category term="cs.MA" />
    <category term="cs.MA" />
    <published>2024-01-14T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2401.67890v1" rel="alternate" type="text/html" />
    <link href="http://arxiv.org/pdf/2401.67890v1" title="pdf" type="application/pdf" />
  </entry>
</feed>"""

EMPTY_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>"""


def test_parse_atom_basic():
    scout = ArxivScout()
    papers = scout._parse_atom(SAMPLE_ATOM)

    assert len(papers) == 2

    p1 = papers[0]
    assert p1.arxiv_id == "2401.12345v1"
    assert "Agentic Memory" in p1.title
    assert len(p1.authors) == 2
    assert "Alice Smith" in p1.authors
    assert "cs.AI" in p1.categories
    assert "meta-learning" in p1.abstract
    assert p1.pdf_url == "http://arxiv.org/pdf/2401.12345v1"
    assert p1.abs_url == "http://arxiv.org/abs/2401.12345v1"


def test_parse_atom_second_paper():
    scout = ArxivScout()
    papers = scout._parse_atom(SAMPLE_ATOM)

    p2 = papers[1]
    assert p2.arxiv_id == "2401.67890v1"
    assert "Multi-Agent" in p2.title
    assert len(p2.authors) == 1
    assert "cs.MA" in p2.categories


def test_parse_empty_feed():
    scout = ArxivScout()
    papers = scout._parse_atom(EMPTY_ATOM)
    assert papers == []


def test_parse_invalid_xml():
    scout = ArxivScout()
    papers = scout._parse_atom("not xml at all")
    assert papers == []


def test_paper_model():
    paper = Paper(
        arxiv_id="2401.12345",
        title="Test Paper",
        authors=["Author A"],
        abstract="Abstract text here.",
        categories=["cs.AI"],
        published=datetime(2024, 1, 15),
        pdf_url="http://example.com/paper.pdf",
    )
    assert paper.arxiv_id == "2401.12345"
    assert paper.title == "Test Paper"
    assert paper.published.year == 2024


def test_search_topics_exist():
    assert len(SEARCH_TOPICS) >= 5
    assert any("memory" in t.lower() for t in SEARCH_TOPICS)
    assert any("agent" in t.lower() for t in SEARCH_TOPICS)


def test_categories_include_ai():
    assert "cs.AI" in CATEGORIES
    assert "cs.MA" in CATEGORIES
    assert "cs.CL" in CATEGORIES


def test_parse_atom_extracts_dates():
    scout = ArxivScout()
    papers = scout._parse_atom(SAMPLE_ATOM)

    p1 = papers[0]
    assert p1.published.year == 2024
    assert p1.published.month == 1
    assert p1.published.day == 15
    assert p1.updated is not None
    assert p1.updated.day == 16
