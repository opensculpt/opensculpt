"""A2A Server — exposes OpenSculpt agents via the A2A protocol.

Adds two routes to the dashboard FastAPI app:
  GET  /.well-known/agent-card.json  — agent discovery
  POST /a2a                          — JSON-RPC task endpoint

Every installed agent becomes a "skill" on OpenSculpt's Agent Card.
Incoming tasks are dispatched to the OSAgent or a matching agent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agos.a2a.models import (
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ATask,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    JsonRpcRequest,
    JsonRpcResponse,
    TaskState,
    TaskStatus,
)

_logger = logging.getLogger(__name__)

router = APIRouter()


class A2AServer:
    """Manages the A2A server state — tasks and agent card."""

    def __init__(self, os_agent=None, agent_registry=None, event_bus=None) -> None:
        self._os_agent = os_agent
        self._registry = agent_registry
        self._bus = event_bus
        self._tasks: dict[str, A2ATask] = {}
        self._base_url = ""

    def set_base_url(self, url: str) -> None:
        self._base_url = url.rstrip("/")

    def build_agent_card(self) -> AgentCard:
        """Build the Agent Card dynamically from installed agents."""
        skills: list[AgentSkill] = []

        # Add a general-purpose skill (OSAgent can handle anything)
        skills.append(AgentSkill(
            id="os_agent",
            name="General Purpose OS Agent",
            description=(
                "Can execute any task: run code, install software, "
                "manage files, call APIs, analyze data, build projects."
            ),
            tags=["general", "code", "shell", "files", "http", "python"],
            examples=[
                "Write a Python script that...",
                "Install nginx and configure it",
                "Analyze this CSV data",
                "Build a REST API with FastAPI",
            ],
        ))

        # Add installed agents as skills
        if self._registry:
            for agent in self._registry.list_agents():
                skills.append(AgentSkill(
                    id=agent["name"],
                    name=agent.get("display_name", agent["name"]),
                    description=agent.get("description", ""),
                    tags=[agent.get("runtime", "unknown"), agent["name"]],
                    examples=[],
                ))

        return AgentCard(
            name="OpenSculpt",
            description=(
                "The Self-Evolving Agentic OS — manages agents like an OS "
                "manages processes. Send any task and OpenSculpt will find "
                "or create the right agent to handle it."
            ),
            url=self._base_url,
            version="0.1.0",
            provider=AgentProvider(organization="OpenSculpt"),
            capabilities=AgentCapabilities(streaming=False),
            skills=skills,
        )

    async def handle_rpc(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Dispatch a JSON-RPC request to the right handler."""
        handlers = {
            "message/send": self._handle_message_send,
            "tasks/get": self._handle_tasks_get,
            "tasks/cancel": self._handle_tasks_cancel,
        }

        handler = handlers.get(request.method)
        if not handler:
            return JsonRpcResponse.err(
                request.id, -32601,
                f"Method not found: {request.method}",
            )

        try:
            result = await handler(request.params)
            return JsonRpcResponse.success(request.id, result)
        except Exception as e:
            _logger.exception("A2A RPC error: %s", e)
            return JsonRpcResponse.err(request.id, -32000, str(e))

    async def _handle_message_send(self, params: dict) -> dict:
        """Handle message/send — create a task and start processing."""
        msg_data = params.get("message", {})
        parts = [
            A2APart(**p) for p in msg_data.get("parts", [])
        ]
        message = A2AMessage(
            role=msg_data.get("role", "user"),
            parts=parts,
        )

        # Extract text from message parts
        text = " ".join(p.text for p in parts if p.text).strip()
        if not text:
            text = str(msg_data)

        # Create task
        task = A2ATask(
            messages=[message],
            status=TaskStatus(state=TaskState.WORKING),
        )
        self._tasks[task.task_id] = task

        if self._bus:
            await self._bus.emit("a2a.task.created", {
                "task_id": task.task_id, "text": text[:200],
            }, source="a2a_server")

        # Execute in background so we can return the task immediately
        asyncio.create_task(self._execute_task(task, text))

        return task.model_dump(by_alias=True, mode="json")

    async def _execute_task(self, task: A2ATask, text: str) -> None:
        """Execute a task by delegating to the OSAgent."""
        try:
            if not self._os_agent:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message=A2AMessage(
                        role="agent",
                        parts=[A2APart(text="OSAgent not available")],
                    ),
                )
                return

            result = await self._os_agent.execute(text)
            response_text = result.get("message", str(result))

            task.artifacts.append(A2AArtifact(
                name="result",
                parts=[A2APart(text=response_text)],
            ))
            task.status = TaskStatus(
                state=TaskState.COMPLETED,
                message=A2AMessage(
                    role="agent",
                    parts=[A2APart(text=response_text[:500])],
                ),
            )

            if self._bus:
                await self._bus.emit("a2a.task.completed", {
                    "task_id": task.task_id,
                }, source="a2a_server")

        except Exception as e:
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message=A2AMessage(
                    role="agent",
                    parts=[A2APart(text=f"Error: {e}")],
                ),
            )

    async def _handle_tasks_get(self, params: dict) -> dict:
        """Handle tasks/get — return current task state."""
        task_id = params.get("taskId") or params.get("task_id", "")
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        return task.model_dump(by_alias=True, mode="json")

    async def _handle_tasks_cancel(self, params: dict) -> dict:
        """Handle tasks/cancel — cancel a running task."""
        task_id = params.get("taskId") or params.get("task_id", "")
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        if not task.status.state.is_terminal:
            task.status = TaskStatus(state=TaskState.CANCELED)
        return task.model_dump(by_alias=True, mode="json")

    def list_tasks(self) -> list[dict]:
        """List all tracked tasks."""
        return [
            {
                "task_id": t.task_id,
                "state": t.status.state.value,
                "messages": len(t.messages),
                "artifacts": len(t.artifacts),
            }
            for t in self._tasks.values()
        ]


# ── Module-level state (injected by dashboard configure) ─────

_server: A2AServer | None = None


def set_server(server: A2AServer) -> None:
    global _server
    _server = server


# ── Routes ────────────────────────────────────────────────────


@router.get("/.well-known/agent-card.json")
async def agent_card() -> JSONResponse:
    """Serve the AGOS Agent Card for A2A discovery."""
    if _server is None:
        return JSONResponse({"error": "A2A not initialized"}, status_code=503)
    card = _server.build_agent_card()
    return JSONResponse(card.model_dump(by_alias=True))


@router.post("/a2a")
async def a2a_rpc(request: dict[str, Any]) -> JSONResponse:
    """JSON-RPC 2.0 endpoint for A2A protocol."""
    if _server is None:
        return JSONResponse({"error": "A2A not initialized"}, status_code=503)

    try:
        rpc = JsonRpcRequest(**request)
    except Exception as e:
        resp = JsonRpcResponse.err(None, -32700, f"Parse error: {e}")
        return JSONResponse(resp.model_dump(mode="json"))

    resp = await _server.handle_rpc(rpc)
    return JSONResponse(resp.model_dump(mode="json"))


@router.get("/api/a2a/tasks")
async def list_tasks() -> list[dict]:
    """List active A2A tasks."""
    if _server is None:
        return []
    return _server.list_tasks()
