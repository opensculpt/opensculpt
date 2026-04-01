"""Heuristic analysis — keyword-based paper insight extraction and AST pattern mining.

These functions provide fast, LLM-free alternatives to the full
PaperAnalyzer and CodeAnalyzer when API keys are not available.
"""
from __future__ import annotations

import ast

from agos.evolution.scout import Paper, SEARCH_TOPICS
from agos.evolution.analyzer import PaperInsight
from agos.evolution.code_analyzer import CodePattern
from agos.evolution.seed_patterns import TECHNIQUE_PATTERNS, _ALL_SNIPPETS
from agos.config import settings as _settings


# Which modules each role is allowed to evolve
_ROLE_MODULES = {
    "knowledge": {"knowledge", "knowledge.semantic", "knowledge.manager", "knowledge.graph", "knowledge.consolidator"},
    "intent": {"intent", "intent.personas", "intent.proactive"},
    "orchestration": {"coordination", "orchestration.planner", "orchestration.runtime"},
    "policy": {"policy", "policy.audit"},
}


def _module_matches_role(module: str) -> bool:
    """Check if a module matches the node's assigned role (general allows all)."""
    role = _settings.node_role
    if role == "general":
        return True
    allowed = _ROLE_MODULES.get(role, set())
    return module in allowed or module.split(".")[0] in {m.split(".")[0] for m in allowed}


def _get_testable_snippet(module: str, cycle_num: int) -> CodePattern | None:
    """Get a testable snippet for the given module, rotating across cycles."""
    if not _module_matches_role(module):
        return None
    patterns = _ALL_SNIPPETS.get(module)
    if patterns is None and "." in module:
        patterns = _ALL_SNIPPETS.get(module.rsplit(".", 1)[0])
    if not patterns:
        return None
    return patterns[cycle_num % len(patterns)]


_IRRELEVANT_SIGNALS = frozenset({
    # Physics
    "quantum", "qubit", "photon", "boson", "fermion", "majorana",
    "superconductor", "spin liquid", "lattice", "ising model",
    "higgs", "hadron", "neutrino", "cosmolog", "dark matter",
    "black hole", "galaxy", "stellar", "hamiltonian",
    "schrödinger", "schrodinger", "wave function", "entangle",
    "tachyon", "inflation rate", "hubble", "di-top", "four-top",
    # Biology / Medicine
    "genome", "protein fold", "molecular dynamic", "crystal",
    "drug scout", "pharma", "clinical trial", "biomarker",
    "radiograph", "chest x-ray", "medical imag", "patholog",
    "tumor", "cancer",
    # Weather / Earth science
    "weather forecast", "meteorolog", "ensemble-size",
    "seismic", "earthquake",
    # Non-software domains
    "building semantic", "resolution spectra", "spectroscop",
    "dexterous manipul", "sim-to-real", "robot grasp",
    "autonomous driv", "self-driving", "lidar point cloud",
})

# The abstract must describe an implementable software technique.
# At least one of these signals must appear to prove the paper has
# methodology we can actually turn into code for the OS.
# NOTE: Signals must be specific to software engineering / ML methodology.
# Generic words like "agent", "model", "system" are NOT methodology signals.
_METHODOLOGY_SIGNALS = frozenset({
    "algorithm", "we implement", "our framework", "our architecture",
    "system design", "our pipeline", "our method",
    "we propose a", "we introduce a", "we present a method",
    "benchmark", "we evaluat", "our experiment", "dataset",
    "outperform", "baseline", "state-of-the-art", "sota",
    "codebase", "open source", "github.com", "our library",
    "we deploy", "inference speed", "fine-tun", "pre-train",
    "loss function", "gradient descent", "backprop",
    "end-to-end train", "ablation stud", "hyperparameter",
    "encoder-decoder", "embedding layer", "tokeniz",
    "latency", "throughput", "scalab",
    "retrieval augment", "vector index", "vector databas",
    "tool use", "function call", "api call",
    "in-context learn", "few-shot", "zero-shot",
    "reinforcement learn", "reward model", "policy gradient",
})

# Minimum keyword matches required (prevents junk papers sneaking through)
_MIN_KEYWORD_SCORE = 2


def heuristic_analyze(paper: Paper) -> PaperInsight | None:
    """Extract insight from paper using keyword matching (no LLM needed).

    Filters applied (all must pass):
    1. Paper must have cs.* arxiv category
    2. Paper must not contain physics/bio/math signals
    3. Paper abstract must describe implementable methodology
    4. At least 2 keyword matches from TECHNIQUE_PATTERNS
    """
    text = (paper.title + " " + paper.abstract).lower()

    # Reject papers from non-CS fields
    if paper.categories:
        has_cs = any(c.startswith("cs.") for c in paper.categories)
        if not has_cs:
            return None

    # Reject papers with physics/bio/math signals
    if any(sig in text for sig in _IRRELEVANT_SIGNALS):
        return None

    # Paper must describe implementable methodology — not just mention keywords.
    # A paper about "memory in fruit flies" matches "memory" but has no
    # software methodology we can use. Require at least one methodology signal.
    if not any(sig in text for sig in _METHODOLOGY_SIGNALS):
        return None

    best_match = None
    best_score = 0

    for keywords, module, priority in TECHNIQUE_PATTERNS:
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_match = (keywords, module, priority)

    if not best_match or best_score < _MIN_KEYWORD_SCORE:
        return None

    _, module, priority = best_match
    technique = paper.title[:65] + ("..." if len(paper.title) > 65 else "")

    sentences = paper.abstract.split(". ")
    desc = sentences[0] if sentences else paper.abstract[:200]
    for s in sentences:
        if any(kw in s.lower() for kw in best_match[0]):
            desc = s
            break

    return PaperInsight(
        paper_id=paper.arxiv_id,
        paper_title=paper.title,
        technique=technique,
        description=desc[:300],
        applicability=f"Could improve agos.{module}",
        priority=priority,
        agos_module=module,
        implementation_hint=f"Adapt technique for agos.{module}",
    )


def extract_ast_patterns(snapshot) -> list[CodePattern]:
    """Extract patterns from repo code using AST analysis."""
    patterns = []
    kws = ["memory", "retrieve", "store", "recall", "agent", "coordinate",
           "plan", "learn", "evolve", "embed", "search", "index"]

    for file in snapshot.files:
        if not file.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(file.content)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name_lower = node.name.lower()
            if not any(kw in name_lower for kw in kws):
                continue
            if not hasattr(node, "end_lineno") or not node.end_lineno:
                continue

            lines = file.content.splitlines()
            start = node.lineno - 1
            end = min(node.end_lineno, start + 35)
            snippet = "\n".join(lines[start:end])

            module = "knowledge"
            if any(kw in name_lower for kw in ["agent", "coordinate", "team"]):
                module = "coordination"

            patterns.append(CodePattern(
                name=node.name,
                description=f"Pattern from {file.path}",
                source_file=file.path,
                source_repo=snapshot.repo_url,
                code_snippet=snippet,
                agos_module=module,
                priority="medium",
            ))

    return patterns[:5]


# ── Role-biased topic selection for node specialization ──────────


def _select_topics(cycle_num: int) -> list[str]:
    """Pick 2 search topics from role-specific + industry topics."""
    from agos.evolution.scout import get_topics_for_role
    role = _settings.node_role
    all_topics = get_topics_for_role(role)
    if not all_topics:
        all_topics = SEARCH_TOPICS
    idx = ((cycle_num - 1) * 2) % len(all_topics)
    return [all_topics[idx], all_topics[(idx + 1) % len(all_topics)]]

