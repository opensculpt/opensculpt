"""Task Planner — persistent multi-step task execution with checkpoints.

When the OS agent receives a complex task (e.g., "Install CRM and set up sales"),
the planner breaks it into steps, tracks progress, and can resume from
the last checkpoint if interrupted.

Steps are persisted to disk so tasks survive restarts.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

_logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskStep:
    """A single step in a multi-step task."""
    id: int
    description: str
    command: str  # What to tell the OS agent to do
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    started_at: float = 0
    completed_at: float = 0
    error: str = ""
    retries: int = 0


@dataclass
class TaskPlan:
    """A complete task plan with steps and progress tracking."""
    id: str
    name: str
    description: str
    steps: list[TaskStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending, running, completed, failed
    current_step: int = 0

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0
        done = sum(1 for s in self.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        return done / len(self.steps)

    @property
    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.status == StepStatus.DONE)
        failed = sum(1 for s in self.steps if s.status == StepStatus.FAILED)
        total = len(self.steps)
        return f"{self.name}: {done}/{total} done, {failed} failed ({self.progress:.0%})"


class TaskPlanner:
    """Manages multi-step task plans with persistence."""

    def __init__(self, workspace_dir: Path | str = ".opensculpt") -> None:
        self._dir = Path(workspace_dir) / "tasks"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._plans: dict[str, TaskPlan] = {}
        self._load_plans()

    def create_plan(self, plan_id: str, name: str, description: str,
                    steps: list[dict[str, str]]) -> TaskPlan:
        """Create a new task plan with steps.

        Args:
            plan_id: Unique plan identifier
            name: Short name (e.g., "install-crm")
            description: What this plan achieves
            steps: List of {"description": ..., "command": ...} dicts
        """
        task_steps = [
            TaskStep(id=i, description=s["description"], command=s["command"])
            for i, s in enumerate(steps)
        ]
        plan = TaskPlan(
            id=plan_id, name=name, description=description, steps=task_steps,
        )
        self._plans[plan_id] = plan
        self._save_plan(plan)
        _logger.info("Task plan created: %s (%d steps)", name, len(task_steps))
        return plan

    def get_plan(self, plan_id: str) -> TaskPlan | None:
        return self._plans.get(plan_id)

    def list_plans(self) -> list[dict]:
        return [
            {"id": p.id, "name": p.name, "status": p.status,
             "progress": p.progress, "summary": p.summary}
            for p in self._plans.values()
        ]

    def next_step(self, plan_id: str) -> TaskStep | None:
        """Get the next pending step."""
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        for step in plan.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None

    def mark_step_done(self, plan_id: str, step_id: int, result: str = "") -> None:
        """Mark a step as completed."""
        plan = self._plans.get(plan_id)
        if not plan:
            return
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.DONE
                step.result = result[:500]
                step.completed_at = time.time()
                break
        # Check if plan is complete
        if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in plan.steps):
            plan.status = "completed"
        self._save_plan(plan)

    def mark_step_failed(self, plan_id: str, step_id: int, error: str = "") -> None:
        """Mark a step as failed."""
        plan = self._plans.get(plan_id)
        if not plan:
            return
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.FAILED
                step.error = error[:500]
                step.retries += 1
                break
        plan.status = "failed"
        self._save_plan(plan)

    def retry_step(self, plan_id: str, step_id: int) -> None:
        """Reset a failed step to pending for retry."""
        plan = self._plans.get(plan_id)
        if not plan:
            return
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.PENDING
                step.error = ""
                break
        plan.status = "running"
        self._save_plan(plan)

    def get_context_for_step(self, plan_id: str, step_id: int) -> str:
        """Build context string from completed steps for the current step."""
        plan = self._plans.get(plan_id)
        if not plan:
            return ""
        parts = [f"Task: {plan.name}", f"Progress: {plan.summary}", ""]
        parts.append("Completed steps:")
        for step in plan.steps:
            if step.status == StepStatus.DONE:
                parts.append(f"  [{step.id}] {step.description} -> {step.result[:100]}")
            elif step.status == StepStatus.FAILED:
                parts.append(f"  [{step.id}] {step.description} -> FAILED: {step.error[:100]}")
        current = next((s for s in plan.steps if s.id == step_id), None)
        if current:
            parts.append(f"\nCurrent step [{step_id}]: {current.description}")
            parts.append(f"Command: {current.command}")
        return "\n".join(parts)

    def _save_plan(self, plan: TaskPlan) -> None:
        path = self._dir / f"{plan.id}.json"
        data = {
            "id": plan.id, "name": plan.name, "description": plan.description,
            "status": plan.status, "created_at": plan.created_at,
            "current_step": plan.current_step,
            "steps": [asdict(s) for s in plan.steps],
        }
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _load_plans(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                steps = [
                    TaskStep(
                        id=s["id"], description=s["description"], command=s["command"],
                        status=StepStatus(s.get("status", "pending")),
                        result=s.get("result", ""), error=s.get("error", ""),
                        started_at=s.get("started_at", 0),
                        completed_at=s.get("completed_at", 0),
                        retries=s.get("retries", 0),
                    )
                    for s in data.get("steps", [])
                ]
                plan = TaskPlan(
                    id=data["id"], name=data["name"],
                    description=data.get("description", ""),
                    steps=steps, status=data.get("status", "pending"),
                    created_at=data.get("created_at", 0),
                )
                self._plans[plan.id] = plan
            except Exception as e:
                _logger.warning("Failed to load task plan %s: %s", path, e)
