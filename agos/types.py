"""Core types shared across all agos subsystems."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, TypeAlias

from pydantic import BaseModel, Field

# ── ID Types ──────────────────────────────────────────────────────────────────

AgentId: TypeAlias = str
TaskId: TypeAlias = str
ToolName: TypeAlias = str
ChannelId: TypeAlias = str


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Agent States ──────────────────────────────────────────────────────────────


class AgentState(str, Enum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    ERROR = "error"


# ── Agent Definition ─────────────────────────────────────────────────────────


class AgentDefinition(BaseModel):
    """Describes what an agent is and how it behaves."""

    name: str
    role: str = ""
    system_prompt: str
    model: str = "claude-sonnet-4-20250514"
    tools: list[str] = Field(default_factory=list)
    token_budget: int = 100_000
    max_turns: int = 50


# ── Messages ─────────────────────────────────────────────────────────────────


class AgentMessage(BaseModel):
    id: str = Field(default_factory=new_id)
    sender: AgentId
    recipient: AgentId | None = None
    content: Any
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── Intent ───────────────────────────────────────────────────────────────────


class IntentType(str, Enum):
    RESEARCH = "research"
    CODE = "code"
    REVIEW = "review"
    ANALYZE = "analyze"
    MONITOR = "monitor"
    AUTOMATE = "automate"
    ANSWER = "answer"
    CREATE = "create"


class CoordinationStrategy(str, Enum):
    SOLO = "solo"
    PIPELINE = "pipeline"
    PARALLEL = "parallel"
    DEBATE = "debate"


class ExecutionPlan(BaseModel):
    """What the Intent Engine produces — a plan for fulfilling user intent."""

    intent_type: IntentType
    description: str
    agents: list[AgentDefinition]
    strategy: CoordinationStrategy = CoordinationStrategy.SOLO
    tool_assignments: dict[str, list[str]] = Field(default_factory=dict)
