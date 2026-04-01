"""ResourceRegistry — Linux-style process table for agentic OS resources.

In Linux, every process has a PID in the process table. When a process dies,
its resources (file handles, memory, child processes) are freed.

In OpenSculpt, every goal creates resources (Docker containers, files, ports,
networks, databases). When a goal is undone, all its resources must be
cascade-deleted — like killing a process group.

This module tracks every resource the OS deploys and enables:
- "What resources does goal X own?" (like /proc/[pid]/fd/)
- "Undo goal X" → cascade destroy all resources
- "Find orphans" → resources whose goal/agent died (zombie reaping)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class ResourceType(str, Enum):
    CONTAINER = "container"
    PORT = "port"
    VOLUME = "volume"
    NETWORK = "network"
    FILE = "file"
    DATABASE = "database"
    HAND = "hand"
    AGENT = "agent"
    # AWS external resources
    EC2_INSTANCE = "ec2_instance"
    S3_BUCKET = "s3_bucket"
    S3_OBJECT = "s3_object"
    LAMBDA_FUNCTION = "lambda_function"
    ECS_TASK = "ecs_task"
    ECS_SERVICE = "ecs_service"
    RDS_INSTANCE = "rds_instance"
    SQS_QUEUE = "sqs_queue"
    SNS_TOPIC = "sns_topic"
    CLOUDWATCH_ALARM = "cloudwatch_alarm"
    IAM_ROLE = "iam_role"
    SECURITY_GROUP = "security_group"
    ELB = "elb"
    ECR_IMAGE = "ecr_image"


@dataclass
class Resource:
    """A single tracked resource — container, file, port, etc."""
    id: str
    type: ResourceType
    name: str
    goal_id: str = ""
    phase_name: str = ""
    agent_id: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "active"  # active | stopped | destroyed | orphaned
    metadata: dict[str, Any] = field(default_factory=dict)
    cleanup_command: str = ""
    cleanup_order: int = 10  # lower = destroy first

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type.value, "name": self.name,
            "goal_id": self.goal_id, "phase_name": self.phase_name,
            "agent_id": self.agent_id, "created_at": self.created_at,
            "status": self.status, "metadata": self.metadata,
            "cleanup_command": self.cleanup_command,
            "cleanup_order": self.cleanup_order,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Resource":
        d = dict(d)
        d["type"] = ResourceType(d["type"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ResourceRegistry:
    """Tracks all resources deployed by the OS.

    Like Linux's process table — every container, file, port, and network
    is registered here with its owning goal/agent. When a goal is undone,
    all its resources are cascade-deleted.
    """

    def __init__(self, state_path: Path | None = None):
        self._resources: dict[str, Resource] = {}
        self._state_path = state_path or Path(".opensculpt/resource_registry.json")
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def register(self, resource: Resource) -> None:
        """Record a new resource. Called after docker_run, write_file, etc."""
        self._resources[resource.id] = resource
        _logger.info("Resource registered: %s %s (%s) for goal=%s",
                      resource.type.value, resource.name, resource.id[:12],
                      resource.goal_id[:20] if resource.goal_id else "none")
        self.save()

    def by_goal(self, goal_id: str) -> list[Resource]:
        """All resources owned by a goal (like /proc/[pid]/fd/)."""
        return [r for r in self._resources.values()
                if r.goal_id == goal_id and r.status == "active"]

    def by_type(self, rtype: ResourceType) -> list[Resource]:
        """All resources of a type (like 'docker ps')."""
        return [r for r in self._resources.values()
                if r.type == rtype and r.status == "active"]

    def active(self) -> list[Resource]:
        """All living resources."""
        return [r for r in self._resources.values() if r.status == "active"]

    def all_resources(self) -> list[Resource]:
        """Everything, including destroyed."""
        return list(self._resources.values())

    def get(self, resource_id: str) -> Resource | None:
        return self._resources.get(resource_id)

    def orphans(self, active_goal_ids: set[str]) -> list[Resource]:
        """Resources whose goal no longer exists (zombie reaping)."""
        return [r for r in self._resources.values()
                if r.status == "active" and r.goal_id
                and r.goal_id not in active_goal_ids]

    async def destroy(self, resource_id: str) -> bool:
        """Destroy a single resource and mark it destroyed."""
        resource = self._resources.get(resource_id)
        if not resource:
            return False

        if resource.cleanup_command:
            try:
                proc = await asyncio.create_subprocess_shell(
                    resource.cleanup_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
                _logger.info("Resource destroyed: %s %s (%s)",
                              resource.type.value, resource.name, resource.id[:12])
            except Exception as e:
                _logger.warning("Failed to destroy %s: %s", resource.name, e)

        resource.status = "destroyed"
        self.save()
        return True

    async def undo_goal(self, goal_id: str) -> list[str]:
        """Cascade delete ALL resources for a goal, in reverse creation order.

        Like killing a process group in Linux — everything the goal
        created gets cleaned up.
        """
        resources = self.by_goal(goal_id)
        if not resources:
            return []

        # Sort: destroy containers first (order 10), then networks (order 20)
        resources.sort(key=lambda r: r.cleanup_order)

        destroyed = []
        for resource in resources:
            success = await self.destroy(resource.id)
            if success:
                destroyed.append(f"{resource.type.value}: {resource.name}")

        return destroyed

    async def cleanup_orphans(self, active_goal_ids: set[str]) -> list[str]:
        """Find and destroy orphaned resources (zombie reaping)."""
        orphaned = self.orphans(active_goal_ids)
        cleaned = []
        for resource in orphaned:
            resource.status = "orphaned"
            success = await self.destroy(resource.id)
            if success:
                cleaned.append(f"{resource.type.value}: {resource.name}")
        return cleaned

    async def reconcile(self) -> dict:
        """Reality check: verify tracked resources are actually alive.

        Like Linux checking /proc — compares what we THINK is running
        against what's ACTUALLY running. Updates status accordingly.
        Returns a report of what changed.
        """
        import subprocess

        changes = {"alive": 0, "dead": 0, "updated": []}
        active = self.active()

        # Get actual running Docker containers
        try:
            result = await asyncio.create_subprocess_exec(
                "docker", "ps", "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10)
            running_containers = set(stdout.decode().strip().splitlines())
        except Exception:
            running_containers = set()

        for resource in active:
            was_active = resource.status == "active"

            if resource.type == ResourceType.CONTAINER:
                if resource.name in running_containers:
                    resource.status = "active"
                    changes["alive"] += 1
                else:
                    resource.status = "down"
                    changes["dead"] += 1
                    if was_active:
                        changes["updated"].append(
                            f"CONTAINER {resource.name}: active -> down"
                        )

            elif resource.type == ResourceType.FILE:
                if Path(resource.id).exists():
                    resource.status = "active"
                    changes["alive"] += 1
                else:
                    resource.status = "down"
                    changes["dead"] += 1
                    if was_active:
                        changes["updated"].append(
                            f"FILE {resource.name}: active -> down"
                        )

            elif resource.type == ResourceType.PORT:
                # Check if port is listening
                port = resource.metadata.get("number", 0)
                if port:
                    try:
                        import socket
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(2)
                        result = s.connect_ex(("localhost", int(port)))
                        s.close()
                        if result == 0:
                            resource.status = "active"
                            changes["alive"] += 1
                        else:
                            resource.status = "down"
                            changes["dead"] += 1
                    except Exception:
                        pass

        if changes["updated"]:
            self.save()
            _logger.info("Reality check: %d alive, %d dead. Changes: %s",
                          changes["alive"], changes["dead"],
                          "; ".join(changes["updated"]))

        return changes

    def save(self) -> None:
        """Persist to disk."""
        try:
            data = {rid: r.to_dict() for rid, r in self._resources.items()}
            self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            _logger.debug("Failed to save resource registry: %s", e)

    def load(self) -> None:
        """Restore from disk."""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                for rid, rd in data.items():
                    self._resources[rid] = Resource.from_dict(rd)
                _logger.info("Loaded %d resources from registry", len(self._resources))
        except Exception as e:
            _logger.debug("Failed to load resource registry: %s", e)

    def stats(self) -> dict:
        """Stats for the dashboard."""
        active = self.active()
        return {
            "total": len(self._resources),
            "active": len(active),
            "by_type": {
                t.value: len([r for r in active if r.type == t])
                for t in ResourceType if any(r.type == t for r in active)
            },
            "goals_with_resources": len(set(r.goal_id for r in active if r.goal_id)),
        }

    def summary(self) -> list[dict]:
        """Active resources for API response."""
        return [r.to_dict() for r in self.active()]
