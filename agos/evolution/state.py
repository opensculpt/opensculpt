"""Evolution state persistence — save/load/export evolved parameters.

Captures the runtime parameters modified by integration strategies,
persists them to .agos/evolution_state.json, and restores them on boot.

Includes ALMA-inspired DesignArchive for tracking strategy lineage and
softmax-based selection pressure.
"""

from __future__ import annotations

import logging
import math
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

from agos.types import new_id

if TYPE_CHECKING:
    from agos.knowledge.manager import TheLoom

logger = logging.getLogger(__name__)

# A strategy name MUST contain at least one of these signals to be exported
# in a PR. Signals are multi-word to avoid cross-domain false positives
# (e.g. "agent" appears in drug-scouting, "search" in particle physics).
_REQUIRED_RELEVANCE_SIGNALS = frozenset({
    # LLM / language model — the strongest signal
    "llm", "large language model", "language model",
    "gpt", "claude", "chatbot",
    # Agent OS concepts (multi-word to avoid "agent" matching drug scouts)
    "ai agent", "llm agent", "agentic", "multi-agent",
    "autonomous agent", "software agent",
    # Memory & knowledge (core OS modules)
    "memory system", "episodic memory", "semantic memory",
    "knowledge graph", "graph travers", "graph reason",
    "retrieval augment", "rag ", "vector databas", "vector search",
    "memory retriev", "memory consolidat",
    # Intent & planning
    "intent classif", "intent engine", "text classif",
    "task planner", "task planning", "execution plan",
    # Policy & security
    "policy engine", "access control", "rate limit",
    "sandbox", "audit trail", "runtime monitor",
    "jailbreak", "prompt injection", "red team",
    "alignment", "safety", "guardrail",
    # Coordination
    "coordinat", "orchestrat", "workflow",
    "message passing", "message channel", "message rout",
    # ML techniques clearly for LLM/agent systems
    "fine-tun", "in-context learn", "few-shot", "zero-shot",
    "reinforcement learn", "reward model", "rlhf",
    "token budget", "context window", "long context",
    "attention mechanism",
    "embedding layer", "embedding space",
    # Evolution / self-improvement (our own patterns)
    "self-improv", "meta-learn", "self-evolv",
    "fitness", "evolution engine",
    # Our seed pattern names (evolved code file names)
    "cosine similarity", "softmax diversity", "weighted graph",
    "confidence tracker", "cluster consolidat",
    "intent classifier", "persona", "capability matcher",
    "policy rule", "token budget enforcer",
    "ttl lru", "dag task", "layered memory",
    "channel router", "fitness proportionate",
    "differential privacy", "federat learn",
})


def _is_relevant_strategy(name: str) -> bool:
    """Return True only if the strategy name relates to agent OS concepts.

    Whitelist approach: the name must contain at least one relevance signal.
    This automatically rejects physics, math, biology, robotics, etc.
    without needing an ever-growing blocklist.
    """
    lower = name.lower()
    return any(sig in lower for sig in _REQUIRED_RELEVANCE_SIGNALS)


class AppliedStrategy(BaseModel):
    """Record of a single applied strategy."""

    name: str
    module: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_papers: list[dict[str, str]] = Field(default_factory=list)
    sandbox_passed: bool = False
    health_check_passed: bool = True
    applied_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    applied_count: int = 1


class DiscoveredPattern(BaseModel):
    """A code pattern discovered during evolution."""

    name: str
    module: str
    code_snippet: str = ""
    sandbox_output: str = ""
    source_paper: str = ""


# ── ALMA-inspired Design Archive ─────────────────────────────────


class DesignEntry(BaseModel):
    """A single design in the archive — tracks lineage for iterative improvement."""

    id: str = Field(default_factory=new_id)
    strategy_name: str
    module: str  # target agos module (e.g. "knowledge.semantic")
    code_hash: str = ""
    code_snippet: str = ""  # the actual pattern code (truncated for persistence)
    fitness_scores: list[float] = Field(default_factory=list)  # history
    current_fitness: float = 0.0
    generation: int = 0  # 0 = original, 1+ = iterated children
    parent_id: str = ""  # lineage tracking
    children_count: int = 0  # HyperAgents: novelty tracking (fewer children = more novel)
    source_paper: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # Federation scoring: demand this artifact resolved + composite score
    demand_key: str = ""        # which demand triggered creation
    artifact_score: float = 0.0  # composite score from scoring engine
    adopted_by: list[str] = Field(default_factory=list)  # node IDs that adopted this


class DesignArchive:
    """ALMA + HyperAgents population archive with novelty-weighted selection.

    Maintains a bounded set of designs. Sampling combines fitness (softmax)
    with a novelty bonus inversely proportional to children count — designs
    that haven't been explored yet get higher probability, balancing
    exploitation (high fitness) with exploration (under-explored lineages).

    Inspired by DGM-H (HyperAgents) parent selection: sigmoid scoring with
    novelty bonus = 1 / (1 + children_count).
    """

    def __init__(self, max_size: int = 50, temperature: float = 0.3,
                 novelty_weight: float = 0.3) -> None:
        self.entries: list[DesignEntry] = []
        self.max_size = max_size
        self.temperature = temperature
        self.novelty_weight = novelty_weight  # blend: (1-w)*fitness + w*novelty

    def add(self, entry: DesignEntry) -> None:
        """Add a design, increment parent's children_count, evict lowest if over capacity."""
        # Track lineage: increment parent's children counter
        if entry.parent_id:
            for e in self.entries:
                if e.id == entry.parent_id:
                    e.children_count += 1
                    break
        self.entries.append(entry)
        if len(self.entries) > self.max_size:
            self.entries.sort(key=lambda e: e.current_fitness)
            self.entries.pop(0)  # remove lowest fitness

    def _novelty_scores(self) -> list[float]:
        """HyperAgents novelty bonus: 1 / (1 + children_count).

        Designs with no children get novelty=1.0, heavily explored ones → 0.
        """
        return [1.0 / (1.0 + e.children_count) for e in self.entries]

    def sample(self, n: int) -> list[DesignEntry]:
        """HyperAgents-style sampling: softmax(fitness) * novelty bonus.

        Combines ALMA softmax selection with DGM-H novelty weighting.
        P(d) ~ exp(blended_score / temp), where
        blended_score = (1 - novelty_weight) * fitness + novelty_weight * novelty.
        """
        if not self.entries or n <= 0:
            return []
        n = min(n, len(self.entries))

        # Compute blended scores: fitness + novelty
        novelty = self._novelty_scores()
        nw = self.novelty_weight
        blended = [
            (1.0 - nw) * e.current_fitness + nw * nov
            for e, nov in zip(self.entries, novelty)
        ]

        # Softmax over blended scores
        temp = max(self.temperature, 0.01)
        max_score = max(blended) if blended else 0
        weights = [math.exp((s - max_score) / temp) for s in blended]
        total = sum(weights)
        if total == 0:
            return random.sample(self.entries, n)

        probs = [w / total for w in weights]

        # Weighted sample without replacement
        indices = list(range(len(self.entries)))
        selected: list[int] = []
        remaining_probs = list(probs)
        for _ in range(n):
            r = random.random() * sum(remaining_probs)
            cumulative = 0.0
            for i, idx in enumerate(indices):
                if idx in selected:
                    continue
                cumulative += remaining_probs[i]
                if cumulative >= r:
                    selected.append(idx)
                    remaining_probs[i] = 0.0
                    break
            else:
                for i, idx in enumerate(indices):
                    if idx not in selected:
                        selected.append(idx)
                        remaining_probs[i] = 0.0
                        break

        return [self.entries[i] for i in selected]

    def best(self, n: int = 5) -> list[DesignEntry]:
        """Top N designs by fitness."""
        return sorted(self.entries, key=lambda e: e.current_fitness, reverse=True)[:n]

    def by_module(self, module: str) -> list[DesignEntry]:
        """Filter designs by target module."""
        return [e for e in self.entries if e.module == module]

    def update_fitness(self, design_id: str, fitness: float) -> None:
        """Update a design's fitness score."""
        for entry in self.entries:
            if entry.id == design_id:
                entry.fitness_scores.append(fitness)
                entry.current_fitness = fitness
                return

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "max_size": self.max_size,
            "temperature": self.temperature,
            "novelty_weight": self.novelty_weight,
            "entries": [e.model_dump() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DesignArchive:
        """Restore from persisted data."""
        archive = cls(
            max_size=data.get("max_size", 50),
            temperature=data.get("temperature", 0.3),
            novelty_weight=data.get("novelty_weight", 0.3),
        )
        for entry_data in data.get("entries", []):
            archive.entries.append(DesignEntry(**entry_data))
        return archive


# ── HyperAgents-inspired Performance Tracker ────────────────────


class CycleMetrics(BaseModel):
    """Metrics recorded for a single evolution cycle."""

    cycle: int
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    papers_found: int = 0
    insights_extracted: int = 0
    strategies_loaded: int = 0
    strategies_rejected: int = 0
    sandbox_passed: int = 0
    sandbox_failed: int = 0
    fitness_avg: float = 0.0
    fitness_best: float = 0.0
    archive_size: int = 0
    mutations_applied: int = 0
    tokens_spent: int = 0


class PerformanceTracker:
    """HyperAgents-inspired tracker for evolution performance over time.

    Tracks per-cycle metrics, detects stagnation (fitness plateau),
    and computes improvement velocity. Used by the metacognitive layer
    to decide when to increase exploration vs exploitation.
    """

    def __init__(self, max_history: int = 200) -> None:
        self.history: list[CycleMetrics] = []
        self.max_history = max_history
        self._stagnation_window: int = 10  # cycles to check for plateau

    def record(self, metrics: CycleMetrics) -> None:
        """Record metrics for a completed cycle."""
        self.history.append(metrics)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    @property
    def cycles_recorded(self) -> int:
        return len(self.history)

    def improvement_velocity(self, window: int = 10) -> float:
        """Average fitness improvement per cycle over the last N cycles.

        Positive = improving, zero/negative = stagnating or degrading.
        """
        recent = self.history[-window:]
        if len(recent) < 2:
            return 0.0
        deltas = [
            recent[i].fitness_best - recent[i - 1].fitness_best
            for i in range(1, len(recent))
        ]
        return sum(deltas) / len(deltas)

    def is_stagnating(self) -> bool:
        """Detect fitness plateau — no improvement over the stagnation window."""
        if len(self.history) < self._stagnation_window:
            return False
        recent = self.history[-self._stagnation_window:]
        best_scores = [m.fitness_best for m in recent]
        # Stagnating if best fitness hasn't improved by more than 1%
        return (max(best_scores) - min(best_scores)) < 0.01

    def acceptance_rate(self, window: int = 20) -> float:
        """Fraction of sandbox-tested patterns that passed, over last N cycles."""
        recent = self.history[-window:]
        total = sum(m.sandbox_passed + m.sandbox_failed for m in recent)
        if total == 0:
            return 0.0
        return sum(m.sandbox_passed for m in recent) / total

    def summary(self) -> dict:
        """Summary stats for dashboard / logging."""
        if not self.history:
            return {"cycles": 0, "velocity": 0.0, "stagnating": False}
        latest = self.history[-1]
        return {
            "cycles": len(self.history),
            "latest_fitness_best": latest.fitness_best,
            "latest_fitness_avg": latest.fitness_avg,
            "velocity": round(self.improvement_velocity(), 4),
            "stagnating": self.is_stagnating(),
            "acceptance_rate": round(self.acceptance_rate(), 3),
            "total_strategies_loaded": sum(m.strategies_loaded for m in self.history),
            "total_mutations": sum(m.mutations_applied for m in self.history),
        }

    def to_dict(self) -> dict:
        return {
            "max_history": self.max_history,
            "stagnation_window": self._stagnation_window,
            "history": [m.model_dump() for m in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> PerformanceTracker:
        tracker = cls(max_history=data.get("max_history", 200))
        tracker._stagnation_window = data.get("stagnation_window", 10)
        for entry in data.get("history", []):
            tracker.history.append(CycleMetrics(**entry))
        return tracker


# ── HyperAgents-inspired Evolution Memory ────────────────────────


class EvolutionInsight(BaseModel):
    """A synthesized insight from a completed evolution cycle.

    HyperAgents stores causal hypotheses ("X worked because Y") and
    plans across iterations. This lets the evolution engine learn from
    past cycles instead of starting fresh each time.
    """

    cycle: int
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    what_tried: str  # technique or pattern name
    module: str  # target agos module
    outcome: str  # "success", "sandbox_failed", "test_gate_failed", "rejected"
    reason: str = ""  # why it worked or failed
    fitness_delta: float = 0.0  # improvement over baseline
    source_paper: str = ""
    # Principle fields — auto-sync via P2P when non-empty
    principle: str = ""       # "When Docker unavailable, install via apt-get"
    applies_when: str = ""    # "scheduling recurring cron container docker"
    scenario_type: str = ""   # "devops", "finance", "general"
    env_context: str = ""     # "Linux container, apt-get available"
    # Structured case fields for situation-matched retrieval
    what_worked: str = ""     # The operational fix (injected into sub-agent prompts)
    recommendation: str = ""  # Same as what_worked (backward compat)
    environment_match: str = "any"  # "container", "baremetal", "docker", "any"
    error_pattern: str = ""   # Key error string to match (e.g. "crontab: not found")
    confidence: float = 1.0   # Starts 1.0, decays on failure, grows on success


class EvolutionMemory:
    """Persistent cross-cycle memory for the evolution engine.

    Stores synthesized insights about what worked, what failed, and why.
    Fed into future code generation prompts so the system doesn't repeat
    mistakes or re-explore dead ends.
    """

    def __init__(self, max_insights: int = 500) -> None:
        self.insights: list[EvolutionInsight] = []
        self.max_insights = max_insights

    def record(self, insight: EvolutionInsight) -> None:
        self.insights.append(insight)
        if len(self.insights) > self.max_insights:
            self.insights = self.insights[-self.max_insights:]

    def successes(self, module: str | None = None, limit: int = 10) -> list[EvolutionInsight]:
        """Recent successful insights, optionally filtered by module."""
        filtered = [i for i in self.insights if i.outcome == "success"]
        if module:
            filtered = [i for i in filtered if i.module == module]
        return filtered[-limit:]

    def failures(self, module: str | None = None, limit: int = 10) -> list[EvolutionInsight]:
        """Recent failures — patterns to avoid."""
        filtered = [i for i in self.insights if i.outcome != "success"]
        if module:
            filtered = [i for i in filtered if i.module == module]
        return filtered[-limit:]

    def context_prompt(self, module: str) -> str:
        """Generate a context string for LLM code generation prompts.

        Tells the LLM what has worked and what to avoid for this module.
        """
        good = self.successes(module, limit=5)
        bad = self.failures(module, limit=5)
        parts = []
        if good:
            parts.append("Previously successful approaches for this module:")
            for g in good:
                parts.append(f"  - {g.what_tried}: {g.reason} (delta={g.fitness_delta:+.3f})")
        if bad:
            parts.append("Approaches that failed (avoid repeating):")
            for b in bad:
                parts.append(f"  - {b.what_tried}: {b.reason} [{b.outcome}]")
        return "\n".join(parts) if parts else ""

    def principles_for(self, scenario_type: str = "", env_context: str = "") -> list[str]:
        """Retrieve distilled principles matching the scenario/environment.

        Returns principle strings to inject into sub-agent prompts.
        Cost: 0 LLM calls (pure filtering).
        """
        principles = []
        for i in self.insights:
            if not i.principle:
                continue
            # Match by scenario type if specified
            if scenario_type and i.scenario_type and scenario_type.lower() not in i.scenario_type.lower():
                continue
            # Match by environment keywords if specified
            if env_context and i.applies_when:
                # Simple keyword match — could be upgraded to expression eval
                conditions = i.applies_when.lower().split(" and ")
                if not all(c.strip().split("=")[0].strip() in env_context.lower() for c in conditions if "=" in c):
                    continue
            principles.append(i.principle)
        return principles

    def persist_durable(self, threshold: float = 0.8) -> int:
        """Promote high-confidence insights to .opensculpt/insights/ as .md files.

        OpenSeed lesson: Eve discovered workspace/ survives rollbacks but self/
        doesn't. High-value insights must be durable. We write them as markdown
        (LLM-native — the LLM reads these directly as context).

        Returns count of newly persisted insights.
        """
        insights_dir = Path(".opensculpt/insights")
        insights_dir.mkdir(parents=True, exist_ok=True)

        # Build set of already-persisted insight keys to avoid duplicates
        existing = set()
        for f in insights_dir.glob("*.md"):
            existing.add(f.stem)

        persisted = 0
        for i in self.insights:
            if i.confidence < threshold or not i.what_worked:
                continue
            # Deterministic filename from content
            key = f"{i.module}_{i.what_tried[:30]}".replace(" ", "_").replace("/", "_")
            key = re.sub(r'[^a-zA-Z0-9_-]', '', key)[:60]
            if key in existing:
                continue

            content = (
                f"# Insight: {i.what_tried[:80]}\n\n"
                f"**Module**: {i.module}\n"
                f"**Outcome**: {i.outcome}\n"
                f"**Confidence**: {i.confidence}\n"
                f"**When**: {i.timestamp}\n\n"
                f"## What Worked\n\n{i.what_worked}\n\n"
            )
            if i.reason:
                content += f"## Why\n\n{i.reason}\n\n"
            if i.principle:
                content += f"## Principle\n\n{i.principle}\n\n"
            if i.applies_when:
                content += f"## Applies When\n\n{i.applies_when}\n\n"
            if i.env_context:
                content += f"## Environment\n\n{i.env_context}\n\n"

            (insights_dir / f"{key}.md").write_text(content, encoding="utf-8")
            existing.add(key)
            persisted += 1

        if persisted:
            logger.info("Persisted %d durable insights to .opensculpt/insights/", persisted)
        return persisted

    def restore_from_durable(self) -> int:
        """Restore insights from .opensculpt/insights/*.md on startup.

        Reads markdown files and reconstructs EvolutionInsight objects
        so the LLM has access to them even after state.json is reset.

        Returns count of restored insights.
        """
        insights_dir = Path(".opensculpt/insights")
        if not insights_dir.exists():
            return 0

        # Build dedup set from existing in-memory insights
        existing_keys = {(i.what_tried, i.module) for i in self.insights}
        restored = 0

        for f in sorted(insights_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                # Parse the markdown back into an insight
                what_tried = ""
                module = ""
                what_worked = ""
                principle = ""
                confidence = 0.8

                for line in text.split("\n"):
                    if line.startswith("# Insight: "):
                        what_tried = line[11:].strip()
                    elif line.startswith("**Module**: "):
                        module = line[12:].strip()
                    elif line.startswith("**Confidence**: "):
                        try:
                            confidence = float(line[16:].strip())
                        except ValueError:
                            pass

                # Extract sections
                for section_name, field in [("What Worked", "what_worked"),
                                             ("Principle", "principle")]:
                    marker = f"## {section_name}"
                    if marker in text:
                        start = text.index(marker) + len(marker)
                        end = text.find("\n## ", start)
                        val = text[start:end].strip() if end > 0 else text[start:].strip()
                        if field == "what_worked":
                            what_worked = val
                        elif field == "principle":
                            principle = val

                if not what_tried or (what_tried, module) in existing_keys:
                    continue

                self.insights.append(EvolutionInsight(
                    cycle=0,
                    what_tried=what_tried,
                    module=module,
                    outcome="success",
                    reason="restored from durable storage",
                    what_worked=what_worked,
                    principle=principle,
                    confidence=confidence,
                ))
                existing_keys.add((what_tried, module))
                restored += 1
            except Exception as e:
                logger.debug("Failed to restore insight from %s: %s", f.name, e)

        if restored:
            logger.info("Restored %d insights from .opensculpt/insights/", restored)
        return restored

    def merge_remote(self, remote_data: dict, source_instance: str = "") -> int:
        """Merge insights from another node's evolution memory.

        Deduplicates by content hash of (what_tried, module, principle) to avoid bloat.
        Returns count of new insights merged.
        """
        if not remote_data:
            return 0
        remote = EvolutionMemory.from_dict(remote_data)

        # Build dedup set using content that doesn't change across syncs
        def _dedup_key(i):
            # Strip any [from X] prefixes for dedup
            reason = i.reason or ""
            while reason.startswith("[from "):
                idx = reason.find("] ")
                if idx > 0:
                    reason = reason[idx + 2:]
                else:
                    break
            return (i.what_tried, i.module, i.principle or reason[:50])

        existing = {_dedup_key(i) for i in self.insights}
        merged = 0
        for insight in remote.insights:
            key = _dedup_key(insight)
            if key not in existing:
                self.insights.append(insight)
                existing.add(key)
                merged += 1
        # Trim to max
        if len(self.insights) > self.max_insights:
            self.insights = self.insights[-self.max_insights:]
        return merged

    def to_dict(self) -> dict:
        return {
            "max_insights": self.max_insights,
            "insights": [i.model_dump() for i in self.insights],
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvolutionMemory:
        mem = cls(max_insights=data.get("max_insights", 500))
        for entry in data.get("insights", []):
            mem.insights.append(EvolutionInsight(**entry))
        return mem


class EvalTask(BaseModel):
    """A concrete evaluation task with known correct answers.

    Run in sandbox to produce real fitness scores instead of proxy signals.
    """

    component: str  # target component (e.g. "knowledge.semantic")
    name: str
    test_code: str  # Python code to execute in sandbox
    expected_output: str = ""  # substring expected in output
    weight: float = 1.0  # importance weight for fitness blending


class EvolutionStateData(BaseModel):
    """The full persisted evolution state."""

    instance_id: str = Field(default_factory=new_id)
    agos_version: str = "0.1.0"
    last_saved: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    cycles_completed: int = 0
    strategies_applied: list[AppliedStrategy] = Field(default_factory=list)
    discovered_patterns: list[DiscoveredPattern] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Meta-evolution state (ALMA-style all-component evolution)
    meta_evolution: dict[str, Any] = Field(default_factory=dict)
    meta_cycles_completed: int = 0
    # ALMA design archive (persisted across restarts)
    design_archive: dict[str, Any] = Field(default_factory=dict)
    # HyperAgents performance tracker (per-cycle metrics, stagnation detection)
    performance_tracker: dict[str, Any] = Field(default_factory=dict)
    # HyperAgents evolution memory (cross-cycle learning insights)
    evolution_memory: dict[str, Any] = Field(default_factory=dict)
    # Hashes of evolved files already included in a PR (dedup tracking)
    shared_file_hashes: list[str] = Field(default_factory=list)
    # Paper insights waiting for code generation (survives restarts)
    pending_insights: list[dict] = Field(default_factory=list)
    # Paper IDs for which code has already been generated (avoid repeats)
    codegen_done_paper_ids: list[str] = Field(default_factory=list)


class EvolutionState:
    """Manages persistence of evolution state to disk.

    save_path defaults to .agos/evolution_state.json
    """

    def __init__(self, save_path: Path | str | None = None) -> None:
        self._path = Path(save_path) if save_path else Path(".agos/evolution_state.json")
        self._data = EvolutionStateData()

    @property
    def data(self) -> EvolutionStateData:
        return self._data

    # ── Capture live parameters ──────────────────────────────────

    def capture_parameters(self, loom: TheLoom) -> dict[str, Any]:
        """Snapshot all evolved parameters from live objects."""
        params: dict[str, Any] = {}
        params["semantic.temperature"] = loom.semantic._temperature
        params["semantic.track_access"] = loom.semantic._track_access
        params["loom.use_layered_recall"] = loom._use_layered_recall
        params["loom.layers"] = [
            {"name": ly.name, "priority": ly.priority, "enabled": ly.enabled}
            for ly in loom._layers
        ]
        return params

    # ── Record events ────────────────────────────────────────────

    def record_integration(
        self,
        strategy_name: str,
        module: str,
        parameters: dict[str, Any] | None = None,
        source_papers: list[dict[str, str]] | None = None,
        sandbox_passed: bool = False,
    ) -> None:
        """Record that a strategy was applied (dedupes by name)."""
        for existing in self._data.strategies_applied:
            if existing.name == strategy_name:
                existing.applied_count += 1
                if parameters:
                    existing.parameters = parameters
                existing.applied_at = datetime.utcnow().isoformat()
                if source_papers:
                    existing.source_papers.extend(source_papers)
                return
        self._data.strategies_applied.append(
            AppliedStrategy(
                name=strategy_name,
                module=module,
                parameters=parameters or {},
                source_papers=source_papers or [],
                sandbox_passed=sandbox_passed,
            )
        )

    def record_pattern(
        self,
        name: str,
        module: str,
        code_snippet: str = "",
        sandbox_output: str = "",
        source_paper: str = "",
    ) -> None:
        """Record a discovered code pattern."""
        # Avoid duplicates
        for existing in self._data.discovered_patterns:
            if existing.name == name and existing.module == module:
                return
        self._data.discovered_patterns.append(
            DiscoveredPattern(
                name=name,
                module=module,
                code_snippet=code_snippet,
                sandbox_output=sandbox_output,
                source_paper=source_paper,
            )
        )

    def increment_cycle(self) -> None:
        self._data.cycles_completed += 1

    # ── Save / Load ─────────────────────────────────────────────

    def save(self, loom: TheLoom | None = None) -> None:
        """Persist current state to disk."""
        if loom is not None:
            self._data.parameters = self.capture_parameters(loom)
        self._data.last_saved = datetime.utcnow().isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            self._data.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info("Evolution state saved to %s", self._path)

    def load(self) -> bool:
        """Load state from disk. Returns True if loaded successfully."""
        if not self._path.exists():
            return False
        try:
            raw = self._path.read_text(encoding="utf-8")
            self._data = EvolutionStateData.model_validate_json(raw)
            logger.info(
                "Loaded evolution state: %d strategies, %d cycles",
                len(self._data.strategies_applied),
                self._data.cycles_completed,
            )
            return True
        except Exception as e:
            logger.warning("Failed to load evolution state: %s", e)
            return False

    def restore_parameters(self, loom: TheLoom) -> list[str]:
        """Re-apply persisted parameters to live objects."""
        changes: list[str] = []
        params = self._data.parameters
        if not params:
            return changes

        if "semantic.temperature" in params:
            temp = float(params["semantic.temperature"])
            if temp > 0 and temp != loom.semantic._temperature:
                loom.semantic.set_temperature(temp)
                changes.append(f"Restored semantic temperature={temp}")

        if "semantic.track_access" in params:
            tracking = bool(params["semantic.track_access"])
            if tracking and not loom.semantic._track_access:
                loom.semantic.enable_access_tracking(tracking)
                changes.append(f"Restored access tracking={tracking}")

        if "loom.use_layered_recall" in params:
            layered = bool(params["loom.use_layered_recall"])
            if layered and not loom._use_layered_recall:
                loom.enable_layered_recall(layered)
                changes.append(f"Restored layered recall={layered}")

        if "loom.layers" in params and params["loom.layers"] and not loom._layers:
            for ld in params["loom.layers"]:
                name = ld.get("name", "")
                priority = ld.get("priority", 0)
                if name == "semantic":
                    loom.add_layer("semantic", loom.semantic, priority=priority)
                elif name == "episodic":
                    loom.add_layer("episodic", loom.episodic, priority=priority)
                changes.append(f"Restored layer '{name}' (priority={priority})")

        return changes

    # ── Meta-evolution state ────────────────────────────────────

    def save_meta_state(self, meta_evolver) -> None:
        """Persist meta-evolution genomes and mutations."""
        self._data.meta_evolution = meta_evolver.export_state()
        self._data.meta_cycles_completed = sum(
            g.mutations_applied for g in meta_evolver.all_genomes()
        )

    def restore_meta_state(self, meta_evolver) -> int:
        """Restore meta-evolution state. Returns count of restored genomes."""
        if not self._data.meta_evolution:
            return 0
        meta_evolver.restore_state(self._data.meta_evolution)
        restored = sum(
            1 for g in meta_evolver.all_genomes()
            if g.mutations_applied > 0
        )
        logger.info("Restored meta-evolution: %d genomes with mutations", restored)
        return restored

    # ── Performance Tracker (HyperAgents) ──────────────────────────

    def save_performance_tracker(self, tracker: PerformanceTracker) -> None:
        """Persist the performance tracker into state data."""
        self._data.performance_tracker = tracker.to_dict()

    def restore_performance_tracker(self) -> PerformanceTracker:
        """Restore performance tracker from persisted state."""
        if self._data.performance_tracker:
            return PerformanceTracker.from_dict(self._data.performance_tracker)
        return PerformanceTracker()

    # ── Evolution Memory (HyperAgents) ────────────────────────────

    def save_evolution_memory(self, memory: EvolutionMemory) -> None:
        """Persist the evolution memory into state data + durable .md files."""
        self._data.evolution_memory = memory.to_dict()
        # OpenSeed lesson: high-value insights must survive state resets
        try:
            memory.persist_durable()
        except Exception as e:
            logger.debug("Failed to persist durable insights: %s", e)

    def restore_evolution_memory(self) -> EvolutionMemory:
        """Restore evolution memory from persisted state + durable .md files."""
        if self._data.evolution_memory:
            mem = EvolutionMemory.from_dict(self._data.evolution_memory)
        else:
            mem = EvolutionMemory()
        # Restore any insights from .opensculpt/insights/ that survived a reset
        try:
            mem.restore_from_durable()
        except Exception as e:
            logger.debug("Failed to restore durable insights: %s", e)
        return mem

    # ── Design Archive ─────────────────────────────────────────────

    def save_design_archive(self, archive: DesignArchive) -> None:
        """Persist the design archive into state data."""
        self._data.design_archive = archive.to_dict()

    def restore_design_archive(self) -> DesignArchive:
        """Restore design archive from persisted state."""
        if self._data.design_archive:
            return DesignArchive.from_dict(self._data.design_archive)
        return DesignArchive()

    # ── Generic JSON persistence (for demand signals, etc.) ────────

    def save_json(self, name: str, data: dict) -> None:
        """Save arbitrary JSON data alongside evolution state."""
        import json
        path = self._path.parent / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, default=str), encoding="utf-8")

    def load_json(self, name: str) -> dict | None:
        """Load arbitrary JSON data from alongside evolution state."""
        import json
        path = self._path.parent / f"{name}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── PR dedup tracking ─────────────────────────────────────────

    @staticmethod
    def _file_content_hash(content: str) -> str:
        """Hash file content for dedup tracking."""
        import hashlib
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def mark_shared(self, file_hashes: list[str]) -> None:
        """Record that these file hashes were included in a PR."""
        existing = set(self._data.shared_file_hashes)
        for h in file_hashes:
            if h not in existing:
                self._data.shared_file_hashes.append(h)
                existing.add(h)

    # ── Pending insight queue (for paper-inspired code generation) ──

    def store_insight(self, insight_dict: dict) -> None:
        """Queue a paper insight for future code generation."""
        paper_id = insight_dict.get("paper_id", "")
        if paper_id in self._data.codegen_done_paper_ids:
            return  # already generated code for this paper
        # Avoid duplicates in the queue
        existing_ids = {d.get("paper_id") for d in self._data.pending_insights}
        if paper_id not in existing_ids:
            self._data.pending_insights.append(insight_dict)

    def pop_pending_insight(self) -> dict | None:
        """Get the next insight that hasn't had code generated yet."""
        done = set(self._data.codegen_done_paper_ids)
        for i, ins in enumerate(self._data.pending_insights):
            if ins.get("paper_id") not in done:
                self._data.pending_insights.pop(i)
                return ins
        return None

    def mark_codegen_done(self, paper_id: str) -> None:
        """Record that code was generated for this paper insight."""
        if paper_id not in self._data.codegen_done_paper_ids:
            self._data.codegen_done_paper_ids.append(paper_id)

    def reset_codegen_done(self) -> int:
        """Clear codegen_done list so papers can be retried.

        Returns the number of paper IDs cleared.
        """
        count = len(self._data.codegen_done_paper_ids)
        self._data.codegen_done_paper_ids.clear()
        return count

    # ── Export for community contribution ────────────────────────

    def export_contribution(self, evolved_dir: Path | None = None) -> dict:
        """Export aggregate stats for dashboard display.

        No raw code, no file contents, no private data.
        For sharing code, users open PRs from their fork (standard git).
        For sharing knowledge, use curator.export_contribution() which anonymizes.
        """
        clean_strategies = [
            s for s in self._data.strategies_applied
            if _is_relevant_strategy(s.name)
        ]

        return {
            "instance_id": self._data.instance_id,
            "agos_version": self._data.agos_version,
            "contributed_at": datetime.utcnow().isoformat(),
            "cycles_completed": self._data.cycles_completed,
            "strategies_applied": [
                {"name": s.name, "module": s.module} for s in clean_strategies
            ],
            "meta_cycles_completed": self._data.meta_cycles_completed,
        }
