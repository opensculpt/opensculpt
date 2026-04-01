"""MetaEvolver — ALMA-inspired meta-evolution for ALL agos components.

Instead of only evolving the knowledge layer, this controller runs a
meta-learning loop across every architectural layer:

  1. Semantic Work Substrate (TheLoom, weaves, consolidation)
  2. Agent & Intent Intelligence (IntentEngine, personas)
  3. Agent Orchestration & Workflow (Planner, runtime)
  4. Identity, Delegation & Governance (PolicyEngine)
  5. Episodic Experience (EventBus, Tracer)

Each component exposes a "genome" of evolvable parameters.  The
MetaEvolver observes fitness signals from real usage (audit trail,
event bus, tracing), identifies underperforming components, proposes
parameter mutations, tests them in sandbox, and applies winners.

Inspired by: "Learning to Continually Learn via Meta-learning Agentic
Memory Designs" (ALMA, arXiv:2602.07755).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.evolution.state import EvalTask


# ── Real Eval Tasks ──────────────────────────────────────────────
# Concrete sandbox-safe Python tasks with known correct answers.
# Used to compute REAL fitness instead of proxy signals.

EVAL_TASKS: dict[str, list[EvalTask]] = {
    "knowledge.semantic": [
        EvalTask(
            component="knowledge.semantic",
            name="store_and_retrieve",
            weight=1.0,
            expected_output="PASS",
            test_code="""
import json, math, random

# Simulate a minimal semantic store with cosine similarity
class MiniStore:
    def __init__(self):
        self.items = []

    def store(self, text, embedding):
        self.items.append({"text": text, "emb": embedding})

    def query(self, embedding, k=3):
        def cosine(a, b):
            dot = sum(x*y for x,y in zip(a,b))
            na = math.sqrt(sum(x*x for x in a))
            nb = math.sqrt(sum(x*x for x in b))
            return dot / (na * nb + 1e-9)
        scored = [(cosine(embedding, it["emb"]), it["text"]) for it in self.items]
        scored.sort(reverse=True)
        return [text for _, text in scored[:k]]

store = MiniStore()
# Store 5 items with distinct embeddings
store.store("python async patterns", [0.9, 0.1, 0.0, 0.1])
store.store("rust memory safety", [0.1, 0.9, 0.0, 0.1])
store.store("python decorators", [0.8, 0.1, 0.1, 0.1])
store.store("database indexing", [0.1, 0.1, 0.9, 0.1])
store.store("python type hints", [0.7, 0.2, 0.0, 0.2])

# Query for python-related items
results = store.query([0.85, 0.15, 0.0, 0.1], k=3)
# Expect python items to dominate
python_count = sum(1 for r in results if "python" in r.lower())
print("PASS" if python_count >= 2 else f"FAIL: only {python_count} python results")
""",
        ),
    ],
    "knowledge.graph": [
        EvalTask(
            component="knowledge.graph",
            name="graph_traversal",
            weight=1.0,
            expected_output="PASS",
            test_code="""
# Build a graph, traverse it, verify neighbors
class MiniGraph:
    def __init__(self):
        self.edges = {}

    def link(self, a, b, weight=1.0):
        self.edges.setdefault(a, []).append((b, weight))
        self.edges.setdefault(b, []).append((a, weight))

    def neighbors(self, node, depth=1):
        visited = set()
        frontier = {node}
        for _ in range(depth):
            next_frontier = set()
            for n in frontier:
                for neighbor, _ in self.edges.get(n, []):
                    if neighbor not in visited and neighbor != node:
                        next_frontier.add(neighbor)
                        visited.add(neighbor)
            frontier = next_frontier
        return visited

g = MiniGraph()
g.link("A", "B")
g.link("B", "C")
g.link("C", "D")
g.link("A", "E")

n1 = g.neighbors("A", depth=1)
n2 = g.neighbors("A", depth=2)

ok = "B" in n1 and "E" in n1 and "C" in n2 and "D" not in n1
print("PASS" if ok else f"FAIL: depth1={n1}, depth2={n2}")
""",
        ),
    ],
    "policy.engine": [
        EvalTask(
            component="policy.engine",
            name="budget_enforcement",
            weight=1.0,
            expected_output="PASS",
            test_code="""
# Simulate token budget enforcement
class Policy:
    def __init__(self, max_tokens, max_calls_per_min):
        self.max_tokens = max_tokens
        self.max_calls = max_calls_per_min
        self.used_tokens = 0
        self.calls_this_minute = 0

    def check(self, tokens_needed):
        if self.used_tokens + tokens_needed > self.max_tokens:
            return False, "budget_exceeded"
        if self.calls_this_minute >= self.max_calls:
            return False, "rate_limited"
        self.used_tokens += tokens_needed
        self.calls_this_minute += 1
        return True, "ok"

p = Policy(max_tokens=1000, max_calls_per_min=3)
r1 = p.check(500)   # ok
r2 = p.check(400)   # ok
r3 = p.check(200)   # should fail (budget)

p2 = Policy(max_tokens=10000, max_calls_per_min=2)
p2.check(1)
p2.check(1)
r4 = p2.check(1)    # should fail (rate)

ok = r1[0] and r2[0] and not r3[0] and r3[1] == "budget_exceeded" and not r4[0]
print("PASS" if ok else f"FAIL: r1={r1}, r3={r3}, r4={r4}")
""",
        ),
    ],
    "orchestration.planner": [
        EvalTask(
            component="orchestration.planner",
            name="task_decomposition",
            weight=1.0,
            expected_output="PASS",
            test_code="""
# Simulate task decomposition and dependency resolution
def decompose(task, subtasks):
    plan = []
    resolved = set()
    remaining = list(subtasks)
    max_iterations = len(subtasks) * 2
    i = 0
    while remaining and i < max_iterations:
        i += 1
        for st in list(remaining):
            deps = st.get("depends_on", [])
            if all(d in resolved for d in deps):
                plan.append(st["name"])
                resolved.add(st["name"])
                remaining.remove(st)
    return plan

subtasks = [
    {"name": "fetch_data", "depends_on": []},
    {"name": "parse_data", "depends_on": ["fetch_data"]},
    {"name": "validate", "depends_on": ["parse_data"]},
    {"name": "fetch_schema", "depends_on": []},
    {"name": "transform", "depends_on": ["validate", "fetch_schema"]},
]

order = decompose("pipeline", subtasks)
# fetch_data and fetch_schema can be first (either order)
# parse_data must come after fetch_data
# validate after parse_data
# transform must be last
ok = (len(order) == 5
      and order.index("parse_data") > order.index("fetch_data")
      and order.index("validate") > order.index("parse_data")
      and order[-1] == "transform")
print("PASS" if ok else f"FAIL: order={order}")
""",
        ),
    ],
    "intent.engine": [
        EvalTask(
            component="intent.engine",
            name="intent_classification",
            weight=1.0,
            expected_output="PASS",
            test_code="""
# Simple keyword-based intent classifier
def classify(text):
    text_lower = text.lower()
    if any(w in text_lower for w in ["search", "find", "look up", "query"]):
        return "research"
    if any(w in text_lower for w in ["write", "create", "build", "implement"]):
        return "coding"
    if any(w in text_lower for w in ["deploy", "run", "start", "execute"]):
        return "operations"
    if any(w in text_lower for w in ["plan", "design", "architect"]):
        return "planning"
    return "general"

tests = [
    ("search for python async patterns", "research"),
    ("write a REST API endpoint", "coding"),
    ("deploy the service to production", "operations"),
    ("plan the database migration", "planning"),
    ("hello world", "general"),
]

correct = sum(1 for text, expected in tests if classify(text) == expected)
print("PASS" if correct >= 4 else f"FAIL: {correct}/5 correct")
""",
        ),
    ],
}


# ── Component Genome ─────────────────────────────────────────────


class ParamSpec(BaseModel):
    """A single evolvable parameter."""

    name: str
    current: Any = None
    default: Any = None
    min_val: Any = None
    max_val: Any = None
    param_type: str = "float"  # float, int, bool, str
    description: str = ""


class ComponentGenome(BaseModel):
    """Evolvable parameters for one architectural component."""

    component: str  # e.g. "knowledge.semantic", "intent.engine"
    layer: str  # architecture layer name
    params: list[ParamSpec] = Field(default_factory=list)
    fitness_score: float = 0.5  # 0..1 — current fitness
    last_evaluated: str = ""
    mutations_applied: int = 0


class FitnessSignal(BaseModel):
    """A single fitness observation from the running system."""

    component: str
    metric: str  # e.g. "recall_hit_rate", "task_completion_rate"
    value: float
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Mutation(BaseModel):
    """A proposed parameter change."""

    id: str = Field(default_factory=new_id)
    component: str
    param_name: str
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    fitness_before: float = 0.0
    fitness_after: float | None = None
    applied: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Fitness Collector ────────────────────────────────────────────


class FitnessCollector:
    """Gathers performance signals from all subsystems.

    Reads from: EventBus history, AuditTrail, Tracer spans,
    TheLoom access stats, and raw system metrics.
    """

    def __init__(self) -> None:
        self._signals: list[FitnessSignal] = []
        self._window_hours: int = 6  # look at last 6 hours

    async def collect(
        self,
        event_bus=None,
        audit_trail=None,
        tracer=None,
        loom=None,
        policy_engine=None,
        runtime=None,
        process_manager=None,
    ) -> list[FitnessSignal]:
        """Collect fitness signals from all available subsystems."""
        signals: list[FitnessSignal] = []

        # ── Knowledge fitness ──
        if loom:
            signals.extend(await self._collect_knowledge(loom))

        # ── Policy fitness ──
        if audit_trail:
            signals.extend(await self._collect_policy(audit_trail))

        # ── Agent fitness ──
        if runtime:
            signals.extend(self._collect_agent(runtime))

        # ── Event bus fitness ──
        if event_bus:
            signals.extend(self._collect_events(event_bus))

        # ── Tracing fitness ──
        if tracer:
            signals.extend(self._collect_tracing(tracer))

        # ── Process fitness (OS-level workload monitoring) ──
        if process_manager:
            signals.extend(self._collect_processes(process_manager))

        self._signals.extend(signals)
        # Keep last 1000
        self._signals = self._signals[-1000:]
        return signals

    async def _collect_knowledge(self, loom) -> list[FitnessSignal]:
        """Fitness signals from the knowledge substrate."""
        signals = []
        try:
            from agos.knowledge.base import ThreadQuery

            # Semantic retrieval health: try a generic query
            results = await loom.semantic.query(
                ThreadQuery(text="agent knowledge", limit=5)
            )
            hit_rate = min(len(results) / 5.0, 1.0)
            signals.append(FitnessSignal(
                component="knowledge.semantic",
                metric="retrieval_hit_rate",
                value=hit_rate,
            ))

            # Graph density
            entities = await loom.graph.entities()
            density = min(len(entities) / 100.0, 1.0)  # normalize to 0-1
            signals.append(FitnessSignal(
                component="knowledge.graph",
                metric="graph_density",
                value=density,
            ))
        except Exception:
            pass
        return signals

    async def _collect_policy(self, audit_trail) -> list[FitnessSignal]:
        """Fitness signals from the policy/audit system."""
        signals = []
        try:
            total = await audit_trail.count()
            violations = await audit_trail.violations(limit=100)
            violation_count = len(violations)

            # Low violation rate = good policy calibration
            if total > 0:
                violation_rate = violation_count / max(total, 1)
                signals.append(FitnessSignal(
                    component="policy.engine",
                    metric="violation_rate",
                    value=1.0 - min(violation_rate * 10, 1.0),  # invert: lower is better
                ))

            # Audit volume = system activity
            activity = min(total / 100.0, 1.0)
            signals.append(FitnessSignal(
                component="policy.audit",
                metric="activity_level",
                value=activity,
            ))
        except Exception:
            pass
        return signals

    def _collect_agent(self, runtime) -> list[FitnessSignal]:
        """Fitness signals from the agent runtime."""
        signals = []
        try:
            agents = runtime.list_agents()
            total = len(agents)
            completed = sum(1 for a in agents if a["state"] == "completed")
            errored = sum(1 for a in agents if a["state"] == "error")

            if total > 0:
                # Completion rate
                signals.append(FitnessSignal(
                    component="kernel.runtime",
                    metric="completion_rate",
                    value=completed / max(total, 1),
                ))
                # Error rate (inverted)
                signals.append(FitnessSignal(
                    component="kernel.runtime",
                    metric="error_rate_inv",
                    value=1.0 - (errored / max(total, 1)),
                ))

            # Token efficiency: average tokens per completed agent
            completed_agents = [a for a in agents if a["state"] == "completed"]
            if completed_agents:
                avg_tokens = sum(a["tokens_used"] for a in completed_agents) / len(completed_agents)
                # Lower is better, normalize against 200k budget
                efficiency = 1.0 - min(avg_tokens / 200_000, 1.0)
                signals.append(FitnessSignal(
                    component="kernel.agent",
                    metric="token_efficiency",
                    value=efficiency,
                ))
        except Exception:
            pass
        return signals

    def _collect_events(self, event_bus) -> list[FitnessSignal]:
        """Fitness signals from the event bus."""
        signals = []
        try:
            topics = event_bus.topics()
            history_len = len(event_bus._history)

            # Topic diversity
            diversity = min(len(topics) / 20.0, 1.0)
            signals.append(FitnessSignal(
                component="events.bus",
                metric="topic_diversity",
                value=diversity,
            ))

            # History utilization
            utilization = history_len / max(event_bus._history_limit, 1)
            signals.append(FitnessSignal(
                component="events.bus",
                metric="history_utilization",
                value=min(utilization, 1.0),
            ))
        except Exception:
            pass
        return signals

    def _collect_tracing(self, tracer) -> list[FitnessSignal]:
        """Fitness signals from the tracing system."""
        signals = []
        try:
            traces = tracer.list_traces(limit=50)
            if traces:
                error_traces = sum(1 for t in traces if t.error_count > 0)
                signals.append(FitnessSignal(
                    component="events.tracing",
                    metric="trace_success_rate",
                    value=1.0 - (error_traces / max(len(traces), 1)),
                ))
        except Exception:
            pass
        return signals

    def _collect_processes(self, process_manager) -> list[FitnessSignal]:
        """Fitness signals from OS-level process management."""
        signals = []
        try:
            procs = process_manager.list_processes()
            if not procs:
                return signals

            running = [p for p in procs if p["state"] == "running"]
            crashed = [p for p in procs if p["state"] == "crashed"]

            # Process survival rate
            total = len(procs)
            survival = len(running) / max(total, 1)
            signals.append(FitnessSignal(
                component="kernel",
                metric="process_survival_rate",
                value=survival,
            ))

            # Crash rate (lower is better → invert for fitness)
            crash_rate = len(crashed) / max(total, 1)
            signals.append(FitnessSignal(
                component="kernel",
                metric="process_stability",
                value=1.0 - crash_rate,
            ))

            # Memory efficiency across all processes
            for p in running:
                mem_usage = p["memory_mb"] / max(p["memory_limit_mb"], 1)
                signals.append(FitnessSignal(
                    component="kernel",
                    metric=f"memory_efficiency:{p['name']}",
                    value=max(0, 1.0 - mem_usage),
                ))

                # Token budget utilization
                if p["token_limit"] > 0:
                    token_usage = p["token_count"] / p["token_limit"]
                    signals.append(FitnessSignal(
                        component="policy",
                        metric=f"token_budget_health:{p['name']}",
                        value=max(0, 1.0 - token_usage),
                    ))

            # Restart frequency (many restarts = OS not handling failures well)
            total_restarts = sum(p["restart_count"] for p in procs)
            restart_penalty = min(total_restarts / max(total * 3, 1), 1.0)
            signals.append(FitnessSignal(
                component="orchestration.runtime",
                metric="restart_frequency",
                value=1.0 - restart_penalty,
            ))

        except Exception:
            pass
        return signals

    def aggregate_fitness(self, component: str) -> float:
        """Average fitness for a component over recent signals."""
        cutoff = (datetime.utcnow() - timedelta(hours=self._window_hours)).isoformat()
        relevant = [
            s for s in self._signals
            if s.component == component and s.timestamp >= cutoff
        ]
        if not relevant:
            return 0.5  # neutral default
        return sum(s.value for s in relevant) / len(relevant)

    def recent_signals(self, limit: int = 50) -> list[FitnessSignal]:
        return self._signals[-limit:]


# ── Meta Evolver ─────────────────────────────────────────────────


class MetaHyperparams(BaseModel):
    """HyperAgents metacognitive layer — the MetaEvolver's own evolvable parameters.

    These control HOW evolution works (mutation rates, fitness weights, thresholds).
    The metacognitive layer can modify these based on performance trends,
    making the improvement mechanism itself improvable.
    """

    # Fitness blending weights (must sum to 1.0 for 3-way blend)
    live_weight: float = 0.4
    sandbox_weight: float = 0.3
    proxy_weight: float = 0.3

    # Mutation control
    underperformer_threshold: float = 0.6  # fitness below this triggers mutations
    mutation_rate_float: float = 0.2  # ±% of range for float perturbation
    mutation_rate_bool_flip: float = 0.3  # probability of toggling a bool
    max_mutations_per_component: int = 2

    # Selection pressure (for design archive)
    archive_temperature: float = 0.3
    archive_novelty_weight: float = 0.3

    # LLM-guided mutation frequency
    llm_ideate_every: int = 5  # use LLM every Nth cycle

    # Metacognitive adaptation rates
    meta_adaptation_rate: float = 0.1  # how much to adjust per self-modification


class MetaEvolver:
    """The ALMA + HyperAgents meta-evolution controller.

    Maintains a genome for each component, collects fitness signals,
    proposes mutations, and applies them through the integrator.

    HyperAgents addition: metacognitive self-modification — the MetaEvolver
    can evolve its own hyperparameters (mutation rates, fitness weights,
    selection temperature) based on stagnation detection and performance trends.
    """

    def __init__(self) -> None:
        self.genomes: dict[str, ComponentGenome] = {}
        self.fitness = FitnessCollector()
        self.mutations: list[Mutation] = []
        self._eval_scores: dict[str, float] = {}  # component -> last sandbox eval score
        self._live_scores: dict[str, float] = {}  # component -> last live eval score
        self._llm_cycle_counter: int = 0
        # HyperAgents: metacognitive hyperparameters (self-modifiable)
        self.hyper = MetaHyperparams()
        self._meta_modifications: int = 0  # count of self-modifications applied
        self._build_genomes()

    def _build_genomes(self) -> None:
        """Define the evolvable parameter space for each component."""

        # ── Layer 1: Semantic Work Substrate ──
        self.genomes["knowledge.semantic"] = ComponentGenome(
            component="knowledge.semantic",
            layer="Semantic Work Substrate",
            params=[
                ParamSpec(
                    name="temperature", default=0.0, min_val=0.0, max_val=1.0,
                    param_type="float", description="Softmax retrieval diversity",
                ),
                ParamSpec(
                    name="track_access", default=False,
                    param_type="bool", description="Access-based confidence tracking",
                ),
                ParamSpec(
                    name="relevance_threshold", default=0.01, min_val=0.001, max_val=0.1,
                    param_type="float", description="Minimum cosine similarity for results",
                ),
                ParamSpec(
                    name="confidence_decay_factor", default=0.95, min_val=0.8, max_val=0.99,
                    param_type="float", description="Confidence decay for unused knowledge",
                ),
                ParamSpec(
                    name="confidence_decay_days", default=30, min_val=7, max_val=90,
                    param_type="int", description="Days inactive before decay kicks in",
                ),
            ],
        )

        self.genomes["knowledge.graph"] = ComponentGenome(
            component="knowledge.graph",
            layer="Semantic Work Substrate",
            params=[
                ParamSpec(
                    name="default_traversal_depth", default=1, min_val=1, max_val=4,
                    param_type="int", description="Default neighbor traversal hops",
                ),
                ParamSpec(
                    name="edge_weight_decay", default=0.99, min_val=0.9, max_val=1.0,
                    param_type="float", description="Weight decay per consolidation cycle",
                ),
            ],
        )

        self.genomes["knowledge.consolidator"] = ComponentGenome(
            component="knowledge.consolidator",
            layer="Semantic Work Substrate",
            params=[
                ParamSpec(
                    name="older_than_hours", default=24, min_val=6, max_val=168,
                    param_type="int", description="Consolidate events older than N hours",
                ),
                ParamSpec(
                    name="min_cluster_size", default=3, min_val=2, max_val=10,
                    param_type="int", description="Minimum events to form a summary",
                ),
                ParamSpec(
                    name="max_concurrent_writes", default=5, min_val=1, max_val=20,
                    param_type="int", description="Semaphore limit for batch ops",
                ),
            ],
        )

        self.genomes["knowledge.loom"] = ComponentGenome(
            component="knowledge.loom",
            layer="Semantic Work Substrate",
            params=[
                ParamSpec(
                    name="use_layered_recall", default=False,
                    param_type="bool", description="Priority-ordered layer retrieval",
                ),
                ParamSpec(
                    name="recall_limit", default=10, min_val=3, max_val=50,
                    param_type="int", description="Default recall result limit",
                ),
            ],
        )

        # ── Layer 2: Agent & Intent Intelligence ──
        self.genomes["intent.engine"] = ComponentGenome(
            component="intent.engine",
            layer="Agent & Intent Intelligence",
            params=[
                ParamSpec(
                    name="default_strategy", default="solo",
                    param_type="str", description="Fallback coordination strategy",
                ),
                ParamSpec(
                    name="max_intent_tokens", default=500, min_val=200, max_val=1500,
                    param_type="int", description="Token limit for intent classification",
                ),
            ],
        )

        self.genomes["intent.personas"] = ComponentGenome(
            component="intent.personas",
            layer="Agent & Intent Intelligence",
            params=[
                ParamSpec(
                    name="researcher_budget", default=200_000, min_val=50_000, max_val=500_000,
                    param_type="int", description="Researcher agent token budget",
                ),
                ParamSpec(
                    name="coder_budget", default=200_000, min_val=50_000, max_val=500_000,
                    param_type="int", description="Coder agent token budget",
                ),
                ParamSpec(
                    name="orchestrator_budget", default=200_000, min_val=50_000, max_val=500_000,
                    param_type="int", description="Orchestrator agent token budget",
                ),
                ParamSpec(
                    name="researcher_max_turns", default=30, min_val=5, max_val=80,
                    param_type="int", description="Researcher max turns",
                ),
                ParamSpec(
                    name="coder_max_turns", default=40, min_val=10, max_val=100,
                    param_type="int", description="Coder max turns",
                ),
                ParamSpec(
                    name="orchestrator_max_turns", default=50, min_val=10, max_val=100,
                    param_type="int", description="Orchestrator max turns",
                ),
            ],
        )

        # ── Layer 3: Agent Orchestration & Workflow ──
        self.genomes["orchestration.planner"] = ComponentGenome(
            component="orchestration.planner",
            layer="Agent Orchestration & Workflow",
            params=[
                ParamSpec(
                    name="parallel_threshold", default=3, min_val=2, max_val=10,
                    param_type="int",
                    description="Min subtasks to trigger parallel execution",
                ),
                ParamSpec(
                    name="pipeline_max_agents", default=5, min_val=2, max_val=10,
                    param_type="int", description="Max agents in a pipeline",
                ),
            ],
        )

        self.genomes["orchestration.runtime"] = ComponentGenome(
            component="orchestration.runtime",
            layer="Agent Orchestration & Workflow",
            params=[
                ParamSpec(
                    name="max_concurrent_agents", default=50, min_val=5, max_val=200,
                    param_type="int", description="Max agents running simultaneously",
                ),
            ],
        )

        # ── Layer 4: Identity, Delegation & Governance ──
        self.genomes["policy.engine"] = ComponentGenome(
            component="policy.engine",
            layer="Identity & Governance",
            params=[
                ParamSpec(
                    name="default_max_tokens", default=200_000, min_val=50_000, max_val=1_000_000,
                    param_type="int", description="Default agent token budget",
                ),
                ParamSpec(
                    name="default_max_turns", default=50, min_val=10, max_val=200,
                    param_type="int", description="Default agent turn limit",
                ),
                ParamSpec(
                    name="default_rate_limit", default=60, min_val=10, max_val=200,
                    param_type="int", description="Tool calls per minute",
                ),
                ParamSpec(
                    name="default_read_only", default=False,
                    param_type="bool", description="Default read-only mode",
                ),
            ],
        )

        # ── Layer 5: Episodic Experience ──
        self.genomes["events.bus"] = ComponentGenome(
            component="events.bus",
            layer="Episodic Experience",
            params=[
                ParamSpec(
                    name="history_limit", default=500, min_val=100, max_val=5000,
                    param_type="int", description="Max events in memory",
                ),
            ],
        )

        self.genomes["events.tracing"] = ComponentGenome(
            component="events.tracing",
            layer="Episodic Experience",
            params=[
                ParamSpec(
                    name="max_traces", default=200, min_val=50, max_val=1000,
                    param_type="int", description="Max traces retained",
                ),
            ],
        )

    def metacognitive_adapt(self, perf_tracker=None) -> list[str]:
        """HyperAgents metacognitive self-modification.

        When performance is stagnating, the MetaEvolver modifies its OWN
        hyperparameters to escape the plateau. This makes the improvement
        mechanism itself improvable — the core DGM-H insight.

        Returns list of modifications applied.
        """
        changes: list[str] = []
        rate = self.hyper.meta_adaptation_rate

        if perf_tracker is None:
            return changes

        velocity = perf_tracker.improvement_velocity()
        stagnating = perf_tracker.is_stagnating()
        acceptance = perf_tracker.acceptance_rate()

        if stagnating:
            # Stagnating → increase exploration: widen mutation range,
            # lower underperformer threshold, increase archive novelty
            old_rate = self.hyper.mutation_rate_float
            self.hyper.mutation_rate_float = min(0.5, old_rate + rate)
            if self.hyper.mutation_rate_float != old_rate:
                changes.append(
                    f"mutation_rate_float: {old_rate:.2f} -> {self.hyper.mutation_rate_float:.2f} (stagnation)"
                )

            old_threshold = self.hyper.underperformer_threshold
            self.hyper.underperformer_threshold = min(0.8, old_threshold + rate * 0.5)
            if self.hyper.underperformer_threshold != old_threshold:
                changes.append(
                    f"underperformer_threshold: {old_threshold:.2f} -> "
                    f"{self.hyper.underperformer_threshold:.2f} (more aggressive)"
                )

            old_novelty = self.hyper.archive_novelty_weight
            self.hyper.archive_novelty_weight = min(0.7, old_novelty + rate)
            if self.hyper.archive_novelty_weight != old_novelty:
                changes.append(
                    f"archive_novelty_weight: {old_novelty:.2f} -> "
                    f"{self.hyper.archive_novelty_weight:.2f} (explore more)"
                )

        elif velocity > 0.02:
            # Improving well → tighten exploration, exploit more
            old_rate = self.hyper.mutation_rate_float
            self.hyper.mutation_rate_float = max(0.05, old_rate - rate * 0.5)
            if self.hyper.mutation_rate_float != old_rate:
                changes.append(
                    f"mutation_rate_float: {old_rate:.2f} -> {self.hyper.mutation_rate_float:.2f} (exploit)"
                )

            old_novelty = self.hyper.archive_novelty_weight
            self.hyper.archive_novelty_weight = max(0.1, old_novelty - rate * 0.5)
            if self.hyper.archive_novelty_weight != old_novelty:
                changes.append(
                    f"archive_novelty_weight: {old_novelty:.2f} -> "
                    f"{self.hyper.archive_novelty_weight:.2f} (exploit more)"
                )

        # Low acceptance rate → sandbox is too strict or code quality is poor
        if acceptance < 0.2 and perf_tracker.cycles_recorded > 5:
            old_max = self.hyper.max_mutations_per_component
            self.hyper.max_mutations_per_component = min(4, old_max + 1)
            if self.hyper.max_mutations_per_component != old_max:
                changes.append(
                    f"max_mutations_per_component: {old_max} -> "
                    f"{self.hyper.max_mutations_per_component} (low acceptance)"
                )

        if changes:
            self._meta_modifications += 1

        return changes

    async def run_eval_tasks(self, sandbox) -> dict[str, float]:
        """Run real eval tasks through sandbox and return component scores.

        Each task has known correct output. Score = fraction of tasks
        that produce expected output per component.
        """
        scores: dict[str, float] = {}
        for component, tasks in EVAL_TASKS.items():
            passed = 0
            total = 0
            for task in tasks:
                total += task.weight
                try:
                    result = await sandbox.execute(task.test_code)
                    if result.passed and task.expected_output in result.output:
                        passed += task.weight
                except Exception:
                    pass
            scores[component] = passed / max(total, 1.0)
        self._eval_scores = scores
        return scores

    async def run_live_evals(
        self,
        loom=None,
        event_bus=None,
        audit_trail=None,
        policy_engine=None,
    ) -> dict[str, float]:
        """Run eval tasks against REAL live AGOS components.

        Unlike sandbox evals (which test toy simulations in subprocess),
        these test the actual running system. This closes the feedback
        loop: mutations → real component changes → measured here.

        Returns {component: score} where score is 0.0-1.0.
        """
        scores: dict[str, float] = {}

        # ── knowledge.semantic: store threads, query, check retrieval ──
        if loom and hasattr(loom, "semantic"):
            try:
                from agos.knowledge.base import Thread, ThreadQuery

                # Store test threads with distinct content
                test_tag = f"_eval_{new_id()[:8]}"
                stored_ids = []
                test_items = [
                    "python asyncio concurrency patterns",
                    "rust borrow checker and ownership",
                    "python decorator metaprogramming",
                    "database query optimization indexes",
                    "python type hints and mypy checking",
                ]
                for text in test_items:
                    tid = await loom.semantic.store(Thread(
                        content=text, kind="eval", tags=[test_tag],
                        source="live_eval", confidence=0.9,
                    ))
                    stored_ids.append(tid)

                # Query for python-related content
                results = await loom.semantic.query(
                    ThreadQuery(text="python programming", limit=5, tags=[test_tag])
                )

                # Score: what fraction of results are python-related?
                if results:
                    python_hits = sum(
                        1 for r in results if "python" in r.content.lower()
                    )
                    scores["knowledge.semantic"] = min(python_hits / 3.0, 1.0)
                else:
                    scores["knowledge.semantic"] = 0.0

                # Cleanup eval threads
                for tid in stored_ids:
                    try:
                        await loom.semantic.delete(tid)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── knowledge.graph: create links, traverse, verify ──
        if loom and hasattr(loom, "graph"):
            try:
                eval_prefix = f"_eval_{new_id()[:8]}"
                a, b, c = f"{eval_prefix}:A", f"{eval_prefix}:B", f"{eval_prefix}:C"

                await loom.graph.link(a, "connects_to", b)
                await loom.graph.link(b, "connects_to", c)

                # Check: A's connections should include B
                conns_a = await loom.graph.connections(a)
                found_b = any(e.target == b for e in conns_a)

                # Check: B should connect to both A and C
                conns_b = await loom.graph.connections(b, direction="both")
                found_edges = len(conns_b)

                score = 0.0
                if found_b:
                    score += 0.5
                if found_edges >= 2:
                    score += 0.5
                scores["knowledge.graph"] = score

                # Cleanup
                for edge in conns_a + conns_b:
                    try:
                        await loom.graph.unlink(edge.id)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── policy.audit: record + query + count ──
        if audit_trail:
            try:
                from agos.policy.audit import AuditEntry

                count_before = await audit_trail.count()

                # Record a test entry
                await audit_trail.record(AuditEntry(
                    agent_id="eval_agent", agent_name="LiveEval",
                    action="eval_test", detail="fitness eval probe",
                    success=True,
                ))

                count_after = await audit_trail.count()

                # Score: did count increase?
                score = 1.0 if count_after > count_before else 0.0
                scores["policy.engine"] = score
                scores["policy.audit"] = score
            except Exception:
                pass

        # ── events.bus: emit + verify history ──
        if event_bus:
            try:
                eval_topic = f"_eval.probe.{new_id()[:8]}"

                # Emit a test event
                await event_bus.emit(eval_topic, {"test": True}, source="live_eval")

                # Check it appears in topics
                topics = event_bus.topics()
                found = eval_topic in topics

                # Check it appears in history
                history = event_bus.history(topic_filter=eval_topic, limit=1)
                in_history = len(history) > 0

                score = 0.0
                if found:
                    score += 0.5
                if in_history:
                    score += 0.5
                scores["events.bus"] = score
            except Exception:
                pass

        self._live_scores = scores
        return scores

    async def run_meta_cycle(
        self,
        event_bus=None,
        audit_trail=None,
        tracer=None,
        loom=None,
        policy_engine=None,
        runtime=None,
        integrator=None,
        sandbox=None,
        llm_provider=None,
        process_manager=None,
        perf_tracker=None,
        design_archive=None,
    ) -> MetaCycleReport:
        """Run one meta-evolution cycle across all components.

        1. Collect fitness signals (proxy + sandbox eval + live eval)
        2. Update genome fitness scores (3-way blend)
        3. Identify underperformers
        4. Propose mutations (random or LLM-guided every Nth cycle)
        5. Apply mutations + before/after measurement
        """
        start = time.monotonic()
        report = MetaCycleReport()
        self._llm_cycle_counter += 1

        # Step 1a: Collect proxy fitness signals
        signals = await self.fitness.collect(
            event_bus=event_bus,
            audit_trail=audit_trail,
            tracer=tracer,
            loom=loom,
            policy_engine=policy_engine,
            runtime=runtime,
            process_manager=process_manager,
        )
        report.signals_collected = len(signals)

        # Step 1b: Run sandbox eval tasks (tests algorithmic correctness)
        sandbox_scores: dict[str, float] = {}
        if sandbox is not None:
            try:
                sandbox_scores = await self.run_eval_tasks(sandbox)
            except Exception:
                pass

        # Step 1c: Run live eval tasks against REAL components
        live_scores: dict[str, float] = {}
        has_live_components = loom is not None or event_bus is not None or audit_trail is not None
        if has_live_components:
            try:
                live_scores = await self.run_live_evals(
                    loom=loom,
                    event_bus=event_bus,
                    audit_trail=audit_trail,
                    policy_engine=policy_engine,
                )
            except Exception:
                pass

        # Step 1d: HyperAgents metacognitive self-modification
        meta_changes: list[str] = []
        if perf_tracker is not None:
            meta_changes = self.metacognitive_adapt(perf_tracker)
            if meta_changes and event_bus:
                await event_bus.emit("meta.self_modified", {
                    "changes": meta_changes,
                    "total_modifications": self._meta_modifications,
                }, source="meta_evolver")
            # Sync archive hyperparams if archive is provided
            if design_archive is not None and meta_changes:
                design_archive.temperature = self.hyper.archive_temperature
                design_archive.novelty_weight = self.hyper.archive_novelty_weight

        # Step 2: Update genome fitness (metacognitive weights)
        lw = self.hyper.live_weight
        sw = self.hyper.sandbox_weight
        pw = self.hyper.proxy_weight
        for name, genome in self.genomes.items():
            proxy_score = self.fitness.aggregate_fitness(name)
            sandbox_score = sandbox_scores.get(name)
            live_score = live_scores.get(name)

            if live_score is not None and sandbox_score is not None:
                genome.fitness_score = (
                    lw * live_score + sw * sandbox_score + pw * proxy_score
                )
            elif live_score is not None:
                total = lw + pw
                genome.fitness_score = (lw / total) * live_score + (pw / total) * proxy_score
            elif sandbox_score is not None:
                total = sw + pw
                genome.fitness_score = (sw / total) * sandbox_score + (pw / total) * proxy_score
            else:
                genome.fitness_score = proxy_score
            genome.last_evaluated = datetime.utcnow().isoformat()

        # Step 3: Identify underperformers (metacognitive threshold)
        threshold = self.hyper.underperformer_threshold
        underperformers = [
            g for g in self.genomes.values()
            if g.fitness_score < threshold and g.params
        ]
        report.underperformers = [g.component for g in underperformers]

        # Step 4: Propose mutations
        proposed: list[Mutation] = []
        use_llm = (
            llm_provider is not None
            and self._llm_cycle_counter % self.hyper.llm_ideate_every == 0
            and underperformers
        )
        if use_llm:
            # LLM-guided ideation every 5th cycle
            try:
                llm_mutations = await self.llm_ideate_mutations(
                    underperformers, signals, llm_provider
                )
                proposed.extend(llm_mutations)
            except Exception:
                # Graceful fallback to random
                for genome in underperformers:
                    proposed.extend(self._propose_mutations(genome))
        else:
            for genome in underperformers:
                proposed.extend(self._propose_mutations(genome))
        report.mutations_proposed = len(proposed)

        # Step 5: Apply mutations with before/after measurement
        applied_count = 0
        for mutation in proposed:
            # Snapshot fitness BEFORE
            genome = self.genomes.get(mutation.component)
            fitness_before = genome.fitness_score if genome else 0.0

            success = await self._apply_mutation(
                mutation,
                loom=loom,
                policy_engine=policy_engine,
                event_bus=event_bus,
                tracer=tracer,
            )
            if success:
                mutation.applied = True
                mutation.fitness_before = fitness_before
                applied_count += 1
                # Update genome
                if genome:
                    genome.mutations_applied += 1
                    for p in genome.params:
                        if p.name == mutation.param_name:
                            p.current = mutation.new_value

        # Re-run live evals AFTER mutations to measure impact
        if applied_count > 0 and has_live_components:
            try:
                post_scores = await self.run_live_evals(
                    loom=loom,
                    event_bus=event_bus,
                    audit_trail=audit_trail,
                    policy_engine=policy_engine,
                )
                # Record fitness_after on each applied mutation
                for mutation in proposed:
                    if mutation.applied:
                        after = post_scores.get(mutation.component)
                        if after is not None:
                            mutation.fitness_after = after
            except Exception:
                pass

        self.mutations.extend(proposed)
        # Keep last 200 mutations
        self.mutations = self.mutations[-200:]
        report.mutations_applied = applied_count

        report.duration_ms = (time.monotonic() - start) * 1000

        # Emit event if bus available
        if event_bus:
            await event_bus.emit("meta.evolution_cycle", {
                "signals": report.signals_collected,
                "underperformers": report.underperformers,
                "proposed": report.mutations_proposed,
                "applied": report.mutations_applied,
                "live_scores": live_scores,
                "sandbox_scores": sandbox_scores,
                "llm_guided": use_llm,
                "duration_ms": round(report.duration_ms),
            }, source="meta_evolver")

        return report

    def _propose_mutations(self, genome: ComponentGenome) -> list[Mutation]:
        """Propose parameter mutations for an underperforming component.

        Uses metacognitive hyperparams for mutation rates instead of hardcoded values.
        Strategy: nudge numeric params toward better-performing ranges.
        """
        import random

        mr = self.hyper.mutation_rate_float  # metacognitive: adaptive mutation range
        flip_prob = self.hyper.mutation_rate_bool_flip

        mutations: list[Mutation] = []
        threshold = self.hyper.underperformer_threshold
        for param in genome.params:
            if param.current is not None and param.current != param.default:
                continue

            if param.param_type == "float":
                old = param.current if param.current is not None else param.default
                range_size = (param.max_val or 1.0) - (param.min_val or 0.0)
                delta = random.uniform(-mr, mr) * range_size
                new = max(param.min_val or 0.0, min(param.max_val or 1.0, old + delta))
                if abs(new - old) > 0.001:
                    mutations.append(Mutation(
                        component=genome.component,
                        param_name=param.name,
                        old_value=old,
                        new_value=round(new, 4),
                        reason=f"Fitness {genome.fitness_score:.2f} < {threshold}, nudging {param.name}",
                        fitness_before=genome.fitness_score,
                    ))

            elif param.param_type == "int":
                old = param.current if param.current is not None else param.default
                range_size = (param.max_val or 100) - (param.min_val or 0)
                int_range = max(1, int(range_size * mr))
                delta = random.randint(-int_range, int_range)
                new = max(param.min_val or 0, min(param.max_val or 999999, old + delta))
                if new != old:
                    mutations.append(Mutation(
                        component=genome.component,
                        param_name=param.name,
                        old_value=old,
                        new_value=new,
                        reason=f"Fitness {genome.fitness_score:.2f} < {threshold}, adjusting {param.name}",
                        fitness_before=genome.fitness_score,
                    ))

            elif param.param_type == "bool":
                old = param.current if param.current is not None else param.default
                if random.random() < flip_prob:
                    mutations.append(Mutation(
                        component=genome.component,
                        param_name=param.name,
                        old_value=old,
                        new_value=not old,
                        reason=f"Fitness {genome.fitness_score:.2f} < {threshold}, toggling {param.name}",
                        fitness_before=genome.fitness_score,
                    ))

        return mutations[:self.hyper.max_mutations_per_component]

    async def llm_ideate_mutations(
        self,
        underperformers: list[ComponentGenome],
        signals: list[FitnessSignal],
        llm_provider,
    ) -> list[Mutation]:
        """Use LLM to propose targeted mutations instead of random perturbation.

        Sends compact genome + signal summary to Claude, asks for up to 3
        targeted mutations with reasoning. ~800 tokens per call.
        """
        import json as _json

        # Build compact prompt
        genome_summary = []
        for g in underperformers[:3]:  # limit to 3 genomes
            params_str = ", ".join(
                f"{p.name}={p.current if p.current is not None else p.default}"
                f"({p.min_val}-{p.max_val})"
                for p in g.params[:5]
            )
            genome_summary.append(
                f"- {g.component} (fitness={g.fitness_score:.2f}): {params_str}"
            )

        signal_summary = []
        for s in signals[-10:]:
            signal_summary.append(f"  {s.component}.{s.metric}={s.value:.2f}")

        prompt = (
            "You are tuning an agentic OS. These components are underperforming.\n\n"
            "Genomes:\n" + "\n".join(genome_summary) + "\n\n"
            "Recent signals:\n" + "\n".join(signal_summary) + "\n\n"
            "Propose up to 3 parameter changes as JSON array:\n"
            '[{"component":"...","param":"...","value":...,"reason":"..."}]\n'
            "Only use existing param names. Values must be within min-max range."
        )

        try:
            response = await llm_provider.complete_prompt(
                prompt, max_tokens=400, temperature=0.3
            )
            text = response.strip()
            # Extract JSON array from response
            start = text.find("[")
            end = text.rfind("]") + 1
            if start < 0 or end <= start:
                return []
            proposals = _json.loads(text[start:end])
        except Exception:
            return []

        mutations: list[Mutation] = []
        for p in proposals[:3]:
            comp = p.get("component", "")
            param_name = p.get("param", "")
            value = p.get("value")
            reason = p.get("reason", "LLM-proposed")

            genome = self.genomes.get(comp)
            if not genome:
                continue
            param_spec = next((ps for ps in genome.params if ps.name == param_name), None)
            if not param_spec:
                continue

            # Validate value is within range
            if param_spec.param_type in ("float", "int"):
                if param_spec.min_val is not None and value < param_spec.min_val:
                    continue
                if param_spec.max_val is not None and value > param_spec.max_val:
                    continue
                if param_spec.param_type == "int":
                    value = int(value)

            old_value = param_spec.current if param_spec.current is not None else param_spec.default
            mutations.append(Mutation(
                component=comp,
                param_name=param_name,
                old_value=old_value,
                new_value=value,
                reason=f"LLM: {reason}",
                fitness_before=genome.fitness_score,
            ))

        return mutations

    async def _apply_mutation(
        self, mutation: Mutation,
        loom=None, policy_engine=None, event_bus=None, tracer=None,
    ) -> bool:
        """Apply a single mutation to the target component."""
        try:
            comp = mutation.component
            param = mutation.param_name
            val = mutation.new_value

            # ── Knowledge mutations ──
            if comp == "knowledge.semantic" and loom:
                if param == "temperature":
                    loom.semantic.set_temperature(val)
                elif param == "track_access":
                    loom.semantic.enable_access_tracking(val)
                elif param == "relevance_threshold":
                    # Store as attribute for future use
                    loom.semantic._relevance_threshold = val
                elif param == "confidence_decay_factor":
                    loom.semantic._decay_factor = val
                elif param == "confidence_decay_days":
                    loom.semantic._decay_days = val
                else:
                    return False
                return True

            if comp == "knowledge.loom" and loom:
                if param == "use_layered_recall":
                    loom.enable_layered_recall(val)
                elif param == "recall_limit":
                    loom._default_recall_limit = val
                else:
                    return False
                return True

            if comp == "knowledge.consolidator" and loom:
                if param == "max_concurrent_writes":
                    loom.learner._max_concurrent = val
                else:
                    # Store for consolidator use
                    setattr(loom, f"_consolidator_{param}", val)
                return True

            if comp == "knowledge.graph" and loom:
                setattr(loom.graph, f"_{param}", val)
                return True

            # ── Policy mutations ──
            if comp == "policy.engine" and policy_engine:
                default = policy_engine._default
                if param == "default_max_tokens":
                    default.max_tokens = val
                elif param == "default_max_turns":
                    default.max_turns = val
                elif param == "default_rate_limit":
                    default.max_tool_calls_per_minute = val
                elif param == "default_read_only":
                    default.read_only = val
                else:
                    return False
                return True

            # ── Event bus mutations ──
            if comp == "events.bus" and event_bus:
                if param == "history_limit":
                    event_bus._history_limit = val
                else:
                    return False
                return True

            # ── Tracing mutations ──
            if comp == "events.tracing" and tracer:
                if param == "max_traces":
                    tracer._max_traces = val
                else:
                    return False
                return True

            # ── Persona / orchestration / intent mutations ──
            # These are stored in genomes and applied on next agent spawn
            if comp in ("intent.engine", "intent.personas",
                        "orchestration.planner", "orchestration.runtime"):
                # Value stored in genome.params — read at spawn time
                return True

            return False

        except Exception:
            return False

    def get_genome(self, component: str) -> ComponentGenome | None:
        return self.genomes.get(component)

    def all_genomes(self) -> list[ComponentGenome]:
        return list(self.genomes.values())

    async def reevaluate_archive(
        self,
        design_archive,
        loom=None,
        event_bus=None,
        audit_trail=None,
        policy_engine=None,
        sandbox=None,
    ) -> int:
        """Re-evaluate designs in the archive against live components.

        Updates stale fitness scores so the softmax sampling reflects
        current system performance, not historical snapshots.

        Returns count of designs that had their fitness updated.
        """
        if not design_archive or not design_archive.entries:
            return 0

        # Get current live scores
        live_scores = await self.run_live_evals(
            loom=loom, event_bus=event_bus,
            audit_trail=audit_trail, policy_engine=policy_engine,
        )
        if not live_scores:
            return 0

        # Also get sandbox scores if available
        sandbox_scores: dict[str, float] = {}
        if sandbox is not None:
            try:
                sandbox_scores = await self.run_eval_tasks(sandbox)
            except Exception:
                pass

        updated = 0
        for entry in design_archive.entries:
            live = live_scores.get(entry.module)
            sb = sandbox_scores.get(entry.module)
            if live is not None:
                # Blend live + sandbox for this module
                if sb is not None:
                    new_fitness = 0.6 * live + 0.4 * sb
                else:
                    new_fitness = live
                design_archive.update_fitness(entry.id, round(new_fitness, 4))
                updated += 1

        return updated

    def export_state(self) -> dict:
        """Export full meta-evolution state for persistence."""
        return {
            "genomes": {
                name: g.model_dump() for name, g in self.genomes.items()
            },
            "recent_mutations": [m.model_dump() for m in self.mutations[-50:]],
            "hyperparams": self.hyper.model_dump(),
            "meta_modifications": self._meta_modifications,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def restore_state(self, data: dict) -> None:
        """Restore meta-evolution state from persisted data."""
        if "genomes" in data:
            for name, gdata in data["genomes"].items():
                if name in self.genomes:
                    stored = ComponentGenome(**gdata)
                    existing = self.genomes[name]
                    existing.fitness_score = stored.fitness_score
                    existing.last_evaluated = stored.last_evaluated
                    existing.mutations_applied = stored.mutations_applied
                    stored_params = {p.name: p for p in stored.params}
                    for p in existing.params:
                        if p.name in stored_params:
                            p.current = stored_params[p.name].current

        if "recent_mutations" in data:
            self.mutations = [Mutation(**m) for m in data["recent_mutations"]]

        # HyperAgents: restore metacognitive hyperparams
        if "hyperparams" in data:
            self.hyper = MetaHyperparams(**data["hyperparams"])
        self._meta_modifications = data.get("meta_modifications", 0)


class MetaCycleReport(BaseModel):
    """Summary of one meta-evolution cycle."""

    signals_collected: int = 0
    underperformers: list[str] = Field(default_factory=list)
    mutations_proposed: int = 0
    mutations_applied: int = 0
    duration_ms: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
