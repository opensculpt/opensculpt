"""Pattern Registry — evolvable agentic design patterns with fitness tracking.

Every task gets executed using a design pattern (or combination). Patterns are
rated after each execution based on tokens used, turns, time, and success rate.
Over time, the registry learns which patterns work best for which task types.

This is the evolution engine for HOW agents work, not WHAT tools they have.

Patterns are inspired by: https://zeljkoavramovic.github.io/agentic-design-patterns
"""

from __future__ import annotations

import math
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class PatternOutcome:
    """Recorded outcome of using a pattern for a task."""
    pattern_ids: list[str]
    task_summary: str
    task_type: str  # install, review, research, monitor, automate, etc.
    success: bool
    tokens_used: int
    turns: int
    time_ms: int
    tools_used: list[str] = field(default_factory=list)
    errors_encountered: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class PatternEntry:
    """A single evolvable design pattern."""
    id: str
    name: str
    category: str  # core, reasoning, orchestration, infrastructure, reliability
    description: str
    instructions: str  # injected into agent system prompt
    # Task type affinity — learned from usage, not hardcoded
    # Keys are task types, values are fitness scores for that type
    task_affinity: dict[str, float] = field(default_factory=dict)
    # Global fitness tracking
    fitness_scores: list[float] = field(default_factory=list)
    current_fitness: float = 0.5  # start neutral
    usage_count: int = 0
    success_count: int = 0
    avg_tokens: float = 0.0
    avg_turns: float = 0.0
    avg_time_ms: float = 0.0
    # Evolution lineage
    composite_of: list[str] = field(default_factory=list)
    generation: int = 0  # 0=builtin, 1+=evolved
    parent_id: str = ""
    children_count: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def novelty_bonus(self) -> float:
        """Unexplored patterns get a bonus (ALMA-style)."""
        return 1.0 / (1.0 + self.children_count)

    def fitness_for(self, task_type: str) -> float:
        """Get fitness for a specific task type, with fallback to global."""
        if task_type in self.task_affinity:
            return self.task_affinity[task_type]
        return self.current_fitness

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "description": self.description, "instructions": self.instructions,
            "task_affinity": self.task_affinity,
            "fitness_scores": self.fitness_scores[-20:],  # keep last 20
            "current_fitness": self.current_fitness,
            "usage_count": self.usage_count, "success_count": self.success_count,
            "avg_tokens": self.avg_tokens, "avg_turns": self.avg_turns,
            "avg_time_ms": self.avg_time_ms,
            "composite_of": self.composite_of,
            "generation": self.generation, "parent_id": self.parent_id,
            "children_count": self.children_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PatternEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class PatternRegistry:
    """Living population of design patterns that evolve via fitness feedback.

    Selection uses softmax-weighted sampling with task-type affinity and
    novelty bonus — same algorithm as DesignArchive but for execution patterns.
    """

    def __init__(self, temperature: float = 0.3):
        self._patterns: dict[str, PatternEntry] = {}
        self._temperature = temperature
        self._outcomes: list[PatternOutcome] = []  # recent outcomes for analysis
        self._max_outcomes = 200

    def seed_builtins(self) -> None:
        """Seed the 16 builtin patterns from agentic design patterns research."""
        builtins = [
            ("prompt_chaining", "core",
             "Break tasks into sequential steps with validation between each",
             "PATTERN: Prompt Chaining — break this into sequential stages. "
             "Each stage validates output before feeding into the next. "
             "Stage 1: gather. Stage 2: process. Stage 3: validate."),

            ("routing", "core",
             "Route requests to specialized agents based on intent",
             "PATTERN: Routing — analyze the request, identify the right specialist, "
             "and delegate. Don't try to handle everything yourself."),

            ("parallelization", "core",
             "Split jobs into independent chunks processed by parallel agents",
             "PATTERN: Parallelization — identify independent subtasks and spawn "
             "multiple agents to work simultaneously. Merge results at the end."),

            ("tool_use", "core",
             "Use external tools aggressively to accomplish tasks",
             "PATTERN: Tool Use — use tools aggressively. Don't explain — DO. "
             "Use shell, docker, http, python as needed. Verify after each action."),

            ("reflection", "reasoning",
             "Generate draft, self-critique, then revise iteratively",
             "PATTERN: Reflection — do the work, then critique your own output. "
             "Ask: What did I miss? What could be better? Fix it. "
             "Minimum 2 passes: draft → review → revise."),

            ("planning", "reasoning",
             "Decompose goals into steps with dependencies before executing",
             "PATTERN: Planning — before doing anything, plan your steps: "
             "1) List all steps 2) Identify dependencies 3) Execute in order "
             "4) Verify each step before proceeding. If blocked, replan."),

            ("multi_agent", "orchestration",
             "Assemble specialized agents coordinated by a manager",
             "PATTERN: Multi-Agent — you are the coordinator. Spawn specialized "
             "agents for different parts of the task. Monitor their progress. "
             "Merge their results into a coherent output."),

            ("goal_monitoring", "orchestration",
             "Define SMART goals and continuously track progress",
             "PATTERN: Goal Monitoring — define what success looks like with "
             "measurable criteria. Track progress. Alert if off-track. "
             "Adjust plan if needed."),

            ("inter_agent_comm", "orchestration",
             "Coordinate multiple agents via shared memory and protocols",
             "PATTERN: Inter-Agent Communication — agents share results via "
             "TheLoom memory. Each agent reads previous agents' outputs before "
             "starting. Use skill docs for knowledge transfer."),

            ("memory_management", "infrastructure",
             "Classify info into short/episodic/long-term memory stores",
             "PATTERN: Memory Management — before acting, recall relevant "
             "knowledge from TheLoom. After acting, save important results. "
             "Use WorkingMemory to focus on what matters."),

            ("learning_adaptation", "infrastructure",
             "Collect feedback and improve prompts/policies over time",
             "PATTERN: Learning — after completing the task, record what "
             "worked and what didn't. Update skill docs. This helps future "
             "agents avoid the same mistakes."),

            ("mcp_integration", "infrastructure",
             "Discover and connect external tool servers via MCP",
             "PATTERN: MCP Integration — if you need a capability you don't "
             "have, check if there's an MCP server that provides it. "
             "Connect before building from scratch."),

            ("rag_retrieval", "infrastructure",
             "Query knowledge bases to ground responses in facts",
             "PATTERN: RAG Retrieval — search TheLoom and skill docs first. "
             "Ground your response in facts from memory, not guesses. "
             "Cite what you found."),

            ("stop_hook", "reliability",
             "Validate output with deterministic checks before completing",
             "PATTERN: Stop Hook — before reporting results, run a validation "
             "check. Test the code. Verify the API returns 200. Confirm the "
             "file exists. Don't report success without proof."),

            ("exception_handling", "reliability",
             "Anticipate errors, retry with backoff, fall back gracefully",
             "PATTERN: Exception Handling — expect failures. If a tool fails, "
             "retry once. If it fails again, try a different approach. "
             "Never give up on first error."),

            ("human_in_the_loop", "reliability",
             "Insert human checkpoints for high-risk decisions",
             "PATTERN: Human-in-the-Loop — for dangerous or irreversible "
             "actions (delete, deploy to prod, send email), ask for confirmation "
             "before proceeding."),
        ]

        for name, category, description, instructions in builtins:
            if name not in self._patterns:
                self._patterns[name] = PatternEntry(
                    id=name, name=name, category=category,
                    description=description, instructions=instructions,
                    current_fitness=0.5, generation=0,
                )

    def add(self, entry: PatternEntry) -> None:
        self._patterns[entry.id] = entry

    def get(self, pattern_id: str) -> PatternEntry | None:
        return self._patterns.get(pattern_id)

    def all_patterns(self) -> list[PatternEntry]:
        return list(self._patterns.values())

    def select_for_task(self, task: str, task_type: str = "",
                        count: int = 2) -> list[PatternEntry]:
        """Select the best patterns for a task using fitness-weighted softmax.

        Returns 1-3 patterns that combine well for the task type.
        This replaces the old static keyword matching.
        """
        if not self._patterns:
            return []

        # Infer task type from keywords if not provided
        if not task_type:
            task_type = self._infer_task_type(task)

        candidates = list(self._patterns.values())

        # Score each pattern
        scores: list[tuple[PatternEntry, float]] = []
        for p in candidates:
            fitness = p.fitness_for(task_type)
            novelty = p.novelty_bonus
            # Blend fitness (70%) with novelty (30%) — explore vs exploit
            score = 0.7 * fitness + 0.3 * novelty
            scores.append((p, score))

        # Softmax selection
        if not scores:
            return []

        max_score = max(s for _, s in scores)
        weights = []
        for p, s in scores:
            w = math.exp((s - max_score) / max(self._temperature, 0.01))
            weights.append(w)

        total = sum(weights)
        if total == 0:
            return [scores[0][0]]

        probs = [w / total for w in weights]

        # Sample without replacement
        selected = []
        indices = list(range(len(scores)))
        for _ in range(min(count, len(scores))):
            if not indices:
                break
            r = random.random()
            cumsum = 0.0
            for i, idx in enumerate(indices):
                cumsum += probs[idx]
                if r <= cumsum:
                    selected.append(scores[idx][0])
                    indices.pop(i)
                    # Renormalize
                    remaining_total = sum(probs[j] for j in indices)
                    if remaining_total > 0:
                        probs = [probs[j] / remaining_total if j in indices else 0 for j in range(len(scores))]
                    break
            else:
                # Fallback: pick highest scoring remaining
                if indices:
                    best_idx = max(indices, key=lambda i: scores[i][1])
                    selected.append(scores[best_idx][0])
                    indices.remove(best_idx)

        return selected

    def update_fitness(self, outcome: PatternOutcome) -> None:
        """Update pattern fitness from an execution outcome.

        Fitness formula:
          fitness = 0.4 * success + 0.3 * token_efficiency + 0.2 * turn_efficiency + 0.1 * error_rate
        """
        MAX_TOKENS = 200_000
        MAX_TURNS = 40

        success_score = 1.0 if outcome.success else 0.0
        token_eff = max(0, 1.0 - outcome.tokens_used / MAX_TOKENS)
        turn_eff = max(0, 1.0 - outcome.turns / MAX_TURNS)
        error_rate = 1.0 - min(1.0, outcome.errors_encountered / max(outcome.turns, 1))

        fitness = (0.4 * success_score + 0.3 * token_eff
                   + 0.2 * turn_eff + 0.1 * error_rate)

        for pid in outcome.pattern_ids:
            p = self._patterns.get(pid)
            if not p:
                continue

            p.usage_count += 1
            if outcome.success:
                p.success_count += 1

            p.fitness_scores.append(fitness)
            if len(p.fitness_scores) > 20:
                p.fitness_scores = p.fitness_scores[-20:]

            # Exponential moving average for current fitness
            alpha = 0.3  # weight of new observation
            p.current_fitness = alpha * fitness + (1 - alpha) * p.current_fitness

            # Update task-type affinity
            if outcome.task_type:
                old = p.task_affinity.get(outcome.task_type, p.current_fitness)
                p.task_affinity[outcome.task_type] = alpha * fitness + (1 - alpha) * old

            # Update running averages
            n = p.usage_count
            p.avg_tokens = p.avg_tokens + (outcome.tokens_used - p.avg_tokens) / n
            p.avg_turns = p.avg_turns + (outcome.turns - p.avg_turns) / n
            p.avg_time_ms = p.avg_time_ms + (outcome.time_ms - p.avg_time_ms) / n

        # Store outcome for analysis
        self._outcomes.append(outcome)
        if len(self._outcomes) > self._max_outcomes:
            self._outcomes = self._outcomes[-self._max_outcomes:]

    def best_for(self, task_type: str, top_n: int = 3) -> list[PatternEntry]:
        """Get the top-N patterns for a task type by fitness."""
        scored = [(p, p.fitness_for(task_type)) for p in self._patterns.values()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored[:top_n]]

    def underperformers(self, threshold: float = 0.4, min_usage: int = 5) -> list[PatternEntry]:
        """Find patterns that consistently underperform."""
        return [
            p for p in self._patterns.values()
            if p.usage_count >= min_usage and p.current_fitness < threshold
        ]

    def stats(self) -> dict[str, Any]:
        """Registry statistics for the dashboard."""
        patterns = list(self._patterns.values())
        if not patterns:
            return {"total": 0}
        return {
            "total": len(patterns),
            "avg_fitness": round(sum(p.current_fitness for p in patterns) / len(patterns), 3),
            "total_usage": sum(p.usage_count for p in patterns),
            "total_outcomes": len(self._outcomes),
            "by_category": {
                cat: len([p for p in patterns if p.category == cat])
                for cat in set(p.category for p in patterns)
            },
            "top_3": [
                {"name": p.name, "fitness": round(p.current_fitness, 3), "usage": p.usage_count}
                for p in sorted(patterns, key=lambda x: x.current_fitness, reverse=True)[:3]
            ],
        }

    def to_dict(self) -> dict:
        """Serialize for persistence in EvolutionState."""
        return {
            "patterns": {k: v.to_dict() for k, v in self._patterns.items()},
            "temperature": self._temperature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PatternRegistry":
        """Restore from persisted state."""
        reg = cls(temperature=data.get("temperature", 0.3))
        for k, v in data.get("patterns", {}).items():
            reg._patterns[k] = PatternEntry.from_dict(v)
        return reg

    @staticmethod
    def _infer_task_type(task: str) -> str:
        """Infer task type from natural language."""
        task_lower = task.lower()
        type_keywords = {
            "install": ["install", "set up", "deploy", "configure", "docker", "run"],
            "review": ["review", "audit", "analyze", "check", "assess", "evaluate"],
            "research": ["research", "find", "search", "look up", "what is", "explain"],
            "monitor": ["monitor", "watch", "alert", "track", "check every"],
            "automate": ["automate", "schedule", "run daily", "background", "recurring"],
            "build": ["build", "create", "write", "develop", "implement", "code"],
            "fix": ["fix", "debug", "repair", "resolve", "troubleshoot"],
            "query": ["show me", "list", "how many", "status", "report"],
        }
        for task_type, keywords in type_keywords.items():
            if any(kw in task_lower for kw in keywords):
                return task_type
        return "general"
