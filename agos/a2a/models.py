"""A2A protocol data models — follows the A2A v0.3 specification.

Covers Agent Cards, Messages, Tasks, and JSON-RPC 2.0 wrappers.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id


# ── Agent Card ────────────────────────────────────────────────


class AgentSkill(BaseModel):
    """A capability that an agent advertises."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="inputModes",
    )
    output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="outputModes",
    )

    model_config = {"populate_by_name": True}


class AgentCapabilities(BaseModel):
    """Protocol features the agent supports."""

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False

    model_config = {"populate_by_name": True}


class AgentProvider(BaseModel):
    """Who provides this agent."""

    organization: str = "OpenSculpt"
    url: str = ""


class AgentCard(BaseModel):
    """A2A Agent Card — the identity document of an agent.

    Published at /.well-known/agent-card.json so other agents
    can discover this agent's capabilities.
    """

    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    provider: AgentProvider = Field(default_factory=AgentProvider)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: list[AgentSkill] = Field(default_factory=list)
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="defaultInputModes",
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="defaultOutputModes",
    )

    model_config = {"populate_by_name": True}


# ── Messages & Parts ─────────────────────────────────────────


class A2APart(BaseModel):
    """Atomic content unit within a message."""

    kind: str = "text"  # "text" | "data" | "file"
    text: str = ""
    data: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AMessage(BaseModel):
    """A single communication turn."""

    role: str = "user"  # "user" | "agent"
    parts: list[A2APart] = Field(default_factory=list)
    message_id: str = Field(default_factory=new_id, alias="messageId")

    model_config = {"populate_by_name": True}


class A2AArtifact(BaseModel):
    """A tangible output produced by an agent."""

    artifact_id: str = Field(default_factory=new_id, alias="artifactId")
    name: str = ""
    parts: list[A2APart] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# ── Tasks ─────────────────────────────────────────────────────


class TaskState(str, Enum):
    """A2A task lifecycle states."""

    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input_required"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED)


class TaskStatus(BaseModel):
    """Current status of a task."""

    state: TaskState = TaskState.WORKING
    message: A2AMessage | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class A2ATask(BaseModel):
    """A unit of work delegated to an agent."""

    task_id: str = Field(default_factory=new_id, alias="taskId")
    context_id: str = Field(default_factory=new_id, alias="contextId")
    status: TaskStatus = Field(default_factory=TaskStatus)
    messages: list[A2AMessage] = Field(default_factory=list)
    artifacts: list[A2AArtifact] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# ── JSON-RPC 2.0 ─────────────────────────────────────────────


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    @classmethod
    def success(cls, id: int | str | None, result: Any) -> JsonRpcResponse:
        return cls(id=id, result=result)

    @classmethod
    def err(cls, id: int | str | None, code: int, message: str) -> JsonRpcResponse:
        return cls(id=id, error={"code": code, "message": message})
