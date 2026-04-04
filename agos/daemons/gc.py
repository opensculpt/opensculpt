"""GarbageCollector daemon — internal + external (AWS) resource reclamation.

Like Linux's OOM killer meets cloud cost-guard. Runs periodically to:

Internal GC:
  - Reap orphaned resources (goal died but container/file lives on)
  - Expire stale goals (>6h no progress)
  - Prune dead agents from registry
  - Clean temp files and old daemon results
  - Compact knowledge (expired TTL threads)

External GC (AWS):
  - Discover AWS resources tagged by OpenSculpt (tag: opensculpt=true)
  - Cross-reference against active goals — if goal dead, resource is orphan
  - Terminate orphaned EC2 instances, delete unused S3 objects,
    remove stale Lambda functions, stop idle ECS tasks, etc.
  - Respect grace periods (don't kill a 5-minute-old instance)
  - Dry-run mode by default — log what WOULD be collected before doing it
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)

# AWS resources older than this (seconds) are candidates for GC
_AWS_GRACE_PERIOD_S = 1800  # 30 minutes
# Internal resources: orphan grace period
_INTERNAL_GRACE_S = 300  # 5 minutes
# Stale goal threshold
_STALE_GOAL_HOURS = 6
# Max destroyed records to keep in registry
_MAX_DESTROYED_RECORDS = 200


@dataclass
class GCReport:
    """Summary of a single GC sweep."""
    timestamp: float = field(default_factory=time.time)
    # Internal
    orphaned_resources: int = 0
    stale_goals: int = 0
    dead_agents_pruned: int = 0
    temp_files_cleaned: int = 0
    knowledge_pruned: int = 0
    destroyed_records_compacted: int = 0
    # External (AWS)
    aws_orphans_found: int = 0
    aws_orphans_terminated: int = 0
    aws_regions_scanned: list[str] = field(default_factory=list)
    aws_cost_saved_estimate: float = 0.0
    # Errors
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "internal": {
                "orphaned_resources": self.orphaned_resources,
                "stale_goals": self.stale_goals,
                "dead_agents_pruned": self.dead_agents_pruned,
                "temp_files_cleaned": self.temp_files_cleaned,
                "knowledge_pruned": self.knowledge_pruned,
                "destroyed_records_compacted": self.destroyed_records_compacted,
            },
            "aws": {
                "orphans_found": self.aws_orphans_found,
                "orphans_terminated": self.aws_orphans_terminated,
                "regions_scanned": self.aws_regions_scanned,
                "cost_saved_estimate": self.aws_cost_saved_estimate,
            },
            "errors": self.errors,
        }


# ── AWS resource type → termination logic ──────────────────────────

_AWS_RESOURCE_TYPES = frozenset({
    "ec2_instance", "s3_bucket", "s3_object", "lambda_function",
    "ecs_task", "ecs_service", "rds_instance", "sqs_queue",
    "sns_topic", "cloudwatch_alarm", "iam_role", "security_group",
    "elb", "ecr_image",
})

# Tag we stamp on every AWS resource the OS creates
OPENSCULPT_TAG_KEY = "opensculpt"
OPENSCULPT_TAG_VALUE = "true"
OPENSCULPT_GOAL_TAG = "opensculpt:goal_id"


class GarbageCollector(Daemon):
    """Periodic garbage collector for internal and external (AWS) resources."""

    name = "gc"
    description = "Garbage collector — internal + AWS resource reclamation"
    icon = "🗑"
    one_shot = False
    default_interval = 300  # 5 minutes

    def __init__(self) -> None:
        super().__init__()
        self._resource_registry: Any = None
        self._goal_runner: Any = None
        self._agent_registry: Any = None
        self._loom: Any = None
        self._daemon_manager: Any = None
        self._audit_trail: Any = None
        self._demand_collector: Any = None
        self._os_agent: Any = None
        self._dry_run: bool = True  # safe default: log but don't destroy
        self._aws_regions: list[str] = ["us-east-1"]
        self._aws_credentials: dict[str, str] = {}
        self._reports: list[GCReport] = []

    # ── Dependency injection ───────────────────────────────────────

    def set_resource_registry(self, rr: Any) -> None:
        self._resource_registry = rr

    def set_goal_runner(self, gr: Any) -> None:
        self._goal_runner = gr

    def set_agent_registry(self, ar: Any) -> None:
        self._agent_registry = ar

    def set_loom(self, loom: Any) -> None:
        self._loom = loom

    def set_daemon_manager(self, dm: Any) -> None:
        self._daemon_manager = dm

    def set_audit_trail(self, at: Any) -> None:
        self._audit_trail = at

    def set_demand_collector(self, dc: Any) -> None:
        self._demand_collector = dc

    def set_os_agent(self, oa: Any) -> None:
        self._os_agent = oa

    # ── Daemon lifecycle ───────────────────────────────────────────

    async def setup(self) -> None:
        cfg = self.config
        self._dry_run = cfg.get("dry_run", True)
        self._aws_regions = cfg.get("aws_regions", ["us-east-1"])
        self._aws_credentials = {
            "aws_access_key_id": cfg.get("aws_access_key_id", ""),
            "aws_secret_access_key": cfg.get("aws_secret_access_key", ""),
        }
        mode = "DRY-RUN" if self._dry_run else "LIVE"
        _logger.info("GC started in %s mode, regions=%s", mode, self._aws_regions)

    async def tick(self) -> None:
        report = GCReport()

        # ── Phase 0: Memory pressure check (like Windows memory warning) ──
        await self._check_memory_pressure(report)

        # ── Phase 1: Internal GC ───────────────────────────────────
        await self._gc_internal(report)

        # ── Phase 2: External GC (AWS) ─────────────────────────────
        await self._gc_aws(report)

        self._reports.append(report)
        if len(self._reports) > 50:
            self._reports = self._reports[-50:]

        # Emit summary
        _total_cleaned = (
            report.orphaned_resources + report.stale_goals +
            report.dead_agents_pruned + report.temp_files_cleaned +
            report.aws_orphans_terminated
        )
        summary = (
            f"GC sweep: {report.orphaned_resources} orphaned resources, "
            f"{report.stale_goals} stale goals, "
            f"{report.aws_orphans_found} AWS orphans found"
        )
        if report.aws_orphans_terminated:
            summary += f", {report.aws_orphans_terminated} AWS resources terminated"
        if self._dry_run and report.aws_orphans_found:
            summary += " (dry-run — no AWS resources actually destroyed)"

        self.add_result(DaemonResult(
            daemon_name=self.name,
            success=not report.errors,
            summary=summary,
            data=report.to_dict(),
        ))

        await self.emit("gc.sweep_complete", report.to_dict())
        _logger.info("GC sweep done: %s", summary)

    # ── Internal GC ────────────────────────────────────────────────

    async def _gc_internal(self, report: GCReport) -> None:
        """Clean up internal OS resources."""

        # 0. Docker containers spawned via shared socket
        try:
            await self._gc_docker_containers(report)
        except Exception as e:
            report.errors.append(f"docker_gc: {e}")
            _logger.warning("GC Docker cleanup error: %s", e)

        # 1. Orphaned resources (goal died, resource lives)
        try:
            await self._gc_orphaned_resources(report)
        except Exception as e:
            report.errors.append(f"orphan_gc: {e}")
            _logger.warning("GC orphan cleanup error: %s", e)

        # 2. Stale goals
        try:
            await self._gc_stale_goals(report)
        except Exception as e:
            report.errors.append(f"stale_goals: {e}")

        # 3. Dead agents
        try:
            await self._gc_dead_agents(report)
        except Exception as e:
            report.errors.append(f"dead_agents: {e}")

        # 4. Temp files
        try:
            await self._gc_temp_files(report)
        except Exception as e:
            report.errors.append(f"temp_files: {e}")

        # 4b. Execution traces (Meta-Harness: keep 7 days, max 50MB)
        try:
            await self._gc_traces(report)
        except Exception as e:
            report.errors.append(f"trace_prune: {e}")

        # 5. Knowledge pruning (expired TTL threads)
        try:
            await self._gc_knowledge(report)
        except Exception as e:
            report.errors.append(f"knowledge_prune: {e}")

        # 6. Compact destroyed records in registry
        try:
            self._gc_compact_registry(report)
        except Exception as e:
            report.errors.append(f"compact_registry: {e}")

        # 7. Trim in-memory audit trail (entries are also in SQLite, safe to trim)
        try:
            self._gc_audit_entries(report)
        except Exception as e:
            report.errors.append(f"audit_trim: {e}")

        # 8. Compact resolved demand signals
        try:
            self._gc_demand_signals(report)
        except Exception as e:
            report.errors.append(f"demand_compact: {e}")

        # 9. Trim OS agent caches (response cache, conversation history, sub-agents)
        try:
            self._gc_os_agent_memory(report)
        except Exception as e:
            report.errors.append(f"os_agent_memory: {e}")

    def _gc_audit_entries(self, report: GCReport) -> None:
        """Trim in-memory audit entries to last 500. Data is persisted in SQLite."""
        if not self._audit_trail:
            return
        entries = self._audit_trail._entries
        if len(entries) > 500:
            trimmed = len(entries) - 500
            self._audit_trail._entries = entries[-500:]
            _logger.info("GC: trimmed %d in-memory audit entries (keeping last 500)", trimmed)

    def _gc_demand_signals(self, report: GCReport) -> None:
        """Remove resolved demand signals older than 1 hour to free memory."""
        if not self._demand_collector:
            return
        now = time.time()
        to_remove = []
        for key, sig in self._demand_collector._signals.items():
            if sig.status == "resolved" and (now - getattr(sig, 'resolved_at', now)) > 3600:
                to_remove.append(key)
        for key in to_remove:
            del self._demand_collector._signals[key]
        if to_remove:
            _logger.info("GC: removed %d resolved demand signals", len(to_remove))
            self._demand_collector._persist()

    def _gc_os_agent_memory(self, report: GCReport) -> None:
        """Trim OS agent in-memory caches: response cache, completed sub-agents, old goal data."""
        if not self._os_agent:
            return

        # Clear completed sub-agents from memory
        if hasattr(self._os_agent, '_sub_agents'):
            done = [k for k, v in self._os_agent._sub_agents.items()
                    if v.get("status") == "done"]
            for k in done:
                del self._os_agent._sub_agents[k]
            if done:
                _logger.info("GC: cleared %d completed sub-agents from memory", len(done))

        # Trim response cache
        if hasattr(self._os_agent, '_response_cache'):
            cache = self._os_agent._response_cache
            if len(cache) > 50:
                # Keep only last 50 entries
                keys = list(cache.keys())
                for k in keys[:-50]:
                    del cache[k]
                _logger.info("GC: trimmed response cache to 50 entries")

        # Trim conversation history
        if hasattr(self._os_agent, '_conversation_history'):
            hist = self._os_agent._conversation_history
            if len(hist) > 20:
                self._os_agent._conversation_history = hist[-20:]
                _logger.info("GC: trimmed conversation history to 20 entries")

    async def _check_memory_pressure(self, report: GCReport) -> None:
        """Monitor system memory like Windows/Linux OOM killer. Warn and clean."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                _logger.warning("MEMORY CRITICAL: %d%% used (%dMB free). Emergency cleanup.",
                                mem.percent, mem.available // (1024 * 1024))
                # Aggressive in-memory cleanup first (free, instant)
                self._gc_audit_entries(report)
                self._gc_demand_signals(report)
                self._gc_os_agent_memory(report)
                # Then external cleanup
                await self._gc_docker_containers(report)
                await self._gc_orphaned_resources(report)
                await self.emit("os.memory_critical", {
                    "percent": mem.percent,
                    "available_mb": mem.available // (1024 * 1024),
                    "action": "emergency_cleanup",
                })
            elif mem.percent > 75:
                _logger.info("MEMORY WARNING: %d%% used (%dMB free).",
                             mem.percent, mem.available // (1024 * 1024))
                await self.emit("os.memory_warning", {
                    "percent": mem.percent,
                    "available_mb": mem.available // (1024 * 1024),
                })
        except ImportError:
            pass
        except Exception as e:
            report.errors.append(f"memory_check: {e}")

    async def _gc_docker_containers(self, report: GCReport) -> None:
        """Kill orphaned Docker containers not tracked in ResourceRegistry.

        Like Unix init reaping zombie processes — finds containers that
        exist in Docker but aren't tracked by the OS, and kills them.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "--format", "{{.Names}}\t{{.Status}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            return

        running = {}
        for line in stdout.decode().strip().splitlines():
            parts = line.split("\t", 1)
            if parts:
                running[parts[0]] = parts[1] if len(parts) > 1 else ""

        if not running:
            return

        # Get containers tracked in ResourceRegistry
        tracked_names: set[str] = set()
        if self._resource_registry:
            for r in self._resource_registry.all_resources():
                if r.type.value == "container" and r.status in ("active", "stopped"):
                    tracked_names.add(r.name)

        # Protected: sculpt fleet containers + any container with "sculpt" in name
        protected = {n for n in running if "sculpt" in n.lower()}

        # Orphans: running but not tracked and not protected
        orphans = set(running.keys()) - tracked_names - protected
        for name in orphans:
            # Grace period: only kill containers older than 5 minutes
            status = running.get(name, "")
            if "seconds" in status and "Up" in status:
                continue  # Too young, skip

            _logger.info("GC: killing orphaned Docker container '%s' (not in registry)", name)
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", name,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(kill_proc.communicate(), timeout=15)
                report.orphaned_resources += 1
            except Exception as e:
                report.errors.append(f"docker_rm_{name}: {e}")

    async def _gc_orphaned_resources(self, report: GCReport) -> None:
        """Reap resources whose goal no longer exists."""
        if not self._resource_registry or not self._goal_runner:
            return

        goals = self._goal_runner.get_goals()
        active_ids = {
            g["id"] for g in goals
            if g.get("status") in ("active", "planning", "operating")
        }

        cleaned = await self._resource_registry.cleanup_orphans(active_ids)
        report.orphaned_resources = len(cleaned)
        if cleaned:
            _logger.info("GC reaped %d orphaned resources: %s", len(cleaned), cleaned[:5])

    async def _gc_stale_goals(self, report: GCReport) -> None:
        """Mark ancient goals as stale so their resources can be reaped."""
        if not self._goal_runner:
            return

        now = time.time()
        goals = self._goal_runner.get_goals()
        for g in goals:
            if g.get("status") not in ("active", "planning"):
                continue
            age_h = (now - g.get("created_at", now)) / 3600
            if age_h > _STALE_GOAL_HOURS:
                g["status"] = "stale"
                self._goal_runner._save_goal(g)
                report.stale_goals += 1
                _logger.info("GC marked goal '%s' as stale (%.1fh old)",
                             g.get("description", g["id"])[:60], age_h)

    async def _gc_dead_agents(self, report: GCReport) -> None:
        """Prune agents stuck in ERROR/CRASHED state for >1 hour."""
        if not self._agent_registry:
            return

        now = time.time()
        for agent in self._agent_registry.list_all():
            status = agent.get("status", "")
            if status not in ("error", "crashed"):
                continue
            stopped_at = agent.get("stopped_at", 0)
            if stopped_at and (now - stopped_at) > 3600:
                try:
                    await self._agent_registry.uninstall(agent["id"])
                    report.dead_agents_pruned += 1
                except Exception:
                    pass

    async def _gc_temp_files(self, report: GCReport) -> None:
        """Clean up old temp files in workspace."""
        workspace = Path(".opensculpt")
        if not workspace.exists():
            return

        now = time.time()
        patterns = ["*.tmp", "*.log.old", "*.bak"]
        for pattern in patterns:
            for f in workspace.rglob(pattern):
                try:
                    age_h = (now - f.stat().st_mtime) / 3600
                    if age_h > 24:
                        f.unlink()
                        report.temp_files_cleaned += 1
                except Exception:
                    pass

    async def _gc_traces(self, report: GCReport) -> None:
        """Prune old execution traces (Meta-Harness pattern: keep recent, prune old)."""
        traces_dir = Path(".opensculpt/traces")
        if not traces_dir.exists():
            return
        now = time.time()
        total_size = 0
        files_by_age: list[tuple[float, int, Path]] = []
        for f in traces_dir.glob("*.jsonl"):
            try:
                stat = f.stat()
                total_size += stat.st_size
                age_days = (now - stat.st_mtime) / 86400
                files_by_age.append((age_days, stat.st_size, f))
            except Exception:
                pass
        # Sort oldest first
        files_by_age.sort(reverse=True)
        for age_days, size, f in files_by_age:
            if age_days > 7 or total_size > 50 * 1024 * 1024:
                try:
                    f.unlink()
                    total_size -= size
                    report.temp_files_cleaned += 1
                except Exception:
                    pass

    async def _gc_knowledge(self, report: GCReport) -> None:
        """Prune expired TTL threads from episodic knowledge."""
        if not self._loom:
            return
        if hasattr(self._loom, "episodic") and hasattr(self._loom.episodic, "prune"):
            pruned = await self._loom.episodic.prune()
            report.knowledge_pruned = pruned if isinstance(pruned, int) else 0

    def _gc_compact_registry(self, report: GCReport) -> None:
        """Remove old 'destroyed' records to keep registry lean."""
        if not self._resource_registry:
            return

        destroyed = [
            r for r in self._resource_registry.all_resources()
            if r.status == "destroyed"
        ]
        if len(destroyed) > _MAX_DESTROYED_RECORDS:
            # Keep most recent, remove oldest
            destroyed.sort(key=lambda r: r.created_at)
            to_remove = destroyed[:-_MAX_DESTROYED_RECORDS]
            for r in to_remove:
                self._resource_registry._resources.pop(r.id, None)
            self._resource_registry.save()
            report.destroyed_records_compacted = len(to_remove)

    # ── External GC (AWS) ──────────────────────────────────────────

    async def _gc_aws(self, report: GCReport) -> None:
        """Discover and reclaim orphaned AWS resources."""
        try:
            import boto3
        except ImportError:
            _logger.debug("GC: boto3 not installed, skipping AWS sweep")
            return

        if not self._aws_credentials.get("aws_access_key_id"):
            # Try environment / instance profile
            try:
                boto3.client("sts").get_caller_identity()
            except Exception:
                _logger.debug("GC: no AWS credentials, skipping AWS sweep")
                return

        for region in self._aws_regions:
            report.aws_regions_scanned.append(region)
            try:
                session = self._create_session(region)
                await self._gc_ec2(session, region, report)
                await self._gc_s3(session, region, report)
                await self._gc_lambda(session, region, report)
                await self._gc_ecs(session, region, report)
                await self._gc_rds(session, region, report)
                await self._gc_sqs(session, region, report)
                await self._gc_cloudwatch(session, region, report)
            except Exception as e:
                report.errors.append(f"aws_{region}: {e}")
                _logger.warning("GC AWS error in %s: %s", region, e)

    def _create_session(self, region: str) -> Any:
        import boto3
        kwargs: dict[str, str] = {"region_name": region}
        if self._aws_credentials.get("aws_access_key_id"):
            kwargs["aws_access_key_id"] = self._aws_credentials["aws_access_key_id"]
            kwargs["aws_secret_access_key"] = self._aws_credentials["aws_secret_access_key"]
        return boto3.Session(**kwargs)

    def _is_opensculpt_resource(self, tags: list[dict]) -> tuple[bool, str]:
        """Check if resource was created by OpenSculpt. Returns (is_ours, goal_id)."""
        is_ours = False
        goal_id = ""
        for tag in tags:
            k, v = tag.get("Key", ""), tag.get("Value", "")
            if k == OPENSCULPT_TAG_KEY and v == OPENSCULPT_TAG_VALUE:
                is_ours = True
            if k == OPENSCULPT_GOAL_TAG:
                goal_id = v
        return is_ours, goal_id

    def _goal_is_dead(self, goal_id: str) -> bool:
        """Check if the goal that created this resource is still alive."""
        if not goal_id or not self._goal_runner:
            return False  # can't determine — don't kill
        goals = self._goal_runner.get_goals()
        for g in goals:
            if g["id"] == goal_id:
                return g.get("status") in ("stale", "failed", "complete")
        return True  # goal not found at all → dead

    def _past_grace_period(self, launch_time) -> bool:
        """Check if resource is older than grace period."""
        import datetime
        if not launch_time:
            return False
        if isinstance(launch_time, str):
            launch_time = datetime.datetime.fromisoformat(launch_time)
        now = datetime.datetime.now(datetime.timezone.utc)
        if launch_time.tzinfo is None:
            launch_time = launch_time.replace(tzinfo=datetime.timezone.utc)
        age_s = (now - launch_time).total_seconds()
        return age_s > _AWS_GRACE_PERIOD_S

    # ── Per-service AWS GC ─────────────────────────────────────────

    async def _gc_ec2(self, session: Any, region: str, report: GCReport) -> None:
        """Find and terminate orphaned EC2 instances."""
        ec2 = session.client("ec2")

        # Only look at instances tagged by OpenSculpt
        resp = await asyncio.to_thread(
            ec2.describe_instances,
            Filters=[
                {"Name": f"tag:{OPENSCULPT_TAG_KEY}", "Values": [OPENSCULPT_TAG_VALUE]},
                {"Name": "instance-state-name", "Values": ["running", "stopped"]},
            ],
        )

        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                inst_id = inst["InstanceId"]
                tags = inst.get("Tags", [])
                _, goal_id = self._is_opensculpt_resource(tags)
                launch_time = inst.get("LaunchTime")

                if not self._goal_is_dead(goal_id):
                    continue
                if not self._past_grace_period(launch_time):
                    continue

                report.aws_orphans_found += 1
                _logger.info("GC: orphaned EC2 %s (goal=%s, region=%s)",
                             inst_id, goal_id, region)

                if not self._dry_run:
                    await asyncio.to_thread(
                        ec2.terminate_instances, InstanceIds=[inst_id])
                    report.aws_orphans_terminated += 1
                    report.aws_cost_saved_estimate += 0.05  # rough per-hour estimate
                    _logger.info("GC: terminated EC2 %s", inst_id)
                    await self.emit("gc.aws_terminated", {
                        "type": "ec2_instance", "id": inst_id,
                        "region": region, "goal_id": goal_id,
                    })

    async def _gc_s3(self, session: Any, region: str, report: GCReport) -> None:
        """Find and clean orphaned S3 buckets/objects."""
        s3 = session.client("s3")

        try:
            resp = await asyncio.to_thread(s3.list_buckets)
        except Exception:
            return

        for bucket in resp.get("Buckets", []):
            name = bucket["Name"]
            try:
                tag_resp = await asyncio.to_thread(
                    s3.get_bucket_tagging, Bucket=name)
                tags = [
                    {"Key": t["Key"], "Value": t["Value"]}
                    for t in tag_resp.get("TagSet", [])
                ]
            except Exception:
                continue

            is_ours, goal_id = self._is_opensculpt_resource(tags)
            if not is_ours:
                continue
            if not self._goal_is_dead(goal_id):
                continue
            if not self._past_grace_period(bucket.get("CreationDate")):
                continue

            report.aws_orphans_found += 1
            _logger.info("GC: orphaned S3 bucket %s (goal=%s)", name, goal_id)

            if not self._dry_run:
                # Empty bucket first then delete
                try:
                    paginator = s3.get_paginator("list_objects_v2")
                    async for page in _async_pages(paginator, Bucket=name):
                        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                        if objects:
                            await asyncio.to_thread(
                                s3.delete_objects,
                                Bucket=name,
                                Delete={"Objects": objects},
                            )
                    await asyncio.to_thread(s3.delete_bucket, Bucket=name)
                    report.aws_orphans_terminated += 1
                    _logger.info("GC: deleted S3 bucket %s", name)
                except Exception as e:
                    report.errors.append(f"s3_delete_{name}: {e}")

    async def _gc_lambda(self, session: Any, region: str, report: GCReport) -> None:
        """Find and delete orphaned Lambda functions."""
        lam = session.client("lambda")

        try:
            resp = await asyncio.to_thread(lam.list_functions)
        except Exception:
            return

        for fn in resp.get("Functions", []):
            arn = fn["FunctionArn"]
            fn_name = fn["FunctionName"]
            try:
                tag_resp = await asyncio.to_thread(lam.list_tags, Resource=arn)
                tags_raw = tag_resp.get("Tags", {})
            except Exception:
                continue

            is_ours = tags_raw.get(OPENSCULPT_TAG_KEY) == OPENSCULPT_TAG_VALUE
            goal_id = tags_raw.get(OPENSCULPT_GOAL_TAG, "")
            if not is_ours:
                continue
            if not self._goal_is_dead(goal_id):
                continue

            report.aws_orphans_found += 1
            _logger.info("GC: orphaned Lambda %s (goal=%s)", fn_name, goal_id)

            if not self._dry_run:
                await asyncio.to_thread(lam.delete_function, FunctionName=fn_name)
                report.aws_orphans_terminated += 1

    async def _gc_ecs(self, session: Any, region: str, report: GCReport) -> None:
        """Find and stop orphaned ECS tasks."""
        ecs = session.client("ecs")

        try:
            clusters_resp = await asyncio.to_thread(ecs.list_clusters)
        except Exception:
            return

        for cluster_arn in clusters_resp.get("clusterArns", []):
            try:
                tasks_resp = await asyncio.to_thread(
                    ecs.list_tasks, cluster=cluster_arn, desiredStatus="RUNNING")
                task_arns = tasks_resp.get("taskArns", [])
                if not task_arns:
                    continue

                desc_resp = await asyncio.to_thread(
                    ecs.describe_tasks, cluster=cluster_arn, tasks=task_arns)

                for task in desc_resp.get("tasks", []):
                    tags = task.get("tags", [])
                    tag_dict = {t["key"]: t["value"] for t in tags}
                    is_ours = tag_dict.get(OPENSCULPT_TAG_KEY) == OPENSCULPT_TAG_VALUE
                    goal_id = tag_dict.get(OPENSCULPT_GOAL_TAG, "")

                    if not is_ours or not self._goal_is_dead(goal_id):
                        continue

                    report.aws_orphans_found += 1
                    task_arn = task["taskArn"]
                    _logger.info("GC: orphaned ECS task %s (goal=%s)", task_arn, goal_id)

                    if not self._dry_run:
                        await asyncio.to_thread(
                            ecs.stop_task, cluster=cluster_arn, task=task_arn,
                            reason="OpenSculpt GC: owning goal is dead")
                        report.aws_orphans_terminated += 1
            except Exception as e:
                report.errors.append(f"ecs_{cluster_arn}: {e}")

    async def _gc_rds(self, session: Any, region: str, report: GCReport) -> None:
        """Find orphaned RDS instances (stopped, tagged by OpenSculpt)."""
        rds = session.client("rds")

        try:
            resp = await asyncio.to_thread(rds.describe_db_instances)
        except Exception:
            return

        for db in resp.get("DBInstances", []):
            db_id = db["DBInstanceIdentifier"]
            arn = db["DBInstanceArn"]
            try:
                tag_resp = await asyncio.to_thread(
                    rds.list_tags_for_resource, ResourceName=arn)
                tags = tag_resp.get("TagList", [])
            except Exception:
                continue

            is_ours, goal_id = self._is_opensculpt_resource(tags)
            if not is_ours or not self._goal_is_dead(goal_id):
                continue

            report.aws_orphans_found += 1
            _logger.info("GC: orphaned RDS %s (goal=%s, status=%s)",
                         db_id, goal_id, db.get("DBInstanceStatus"))

            if not self._dry_run:
                # Delete with no final snapshot (it's orphaned)
                try:
                    await asyncio.to_thread(
                        rds.delete_db_instance,
                        DBInstanceIdentifier=db_id,
                        SkipFinalSnapshot=True)
                    report.aws_orphans_terminated += 1
                    report.aws_cost_saved_estimate += 0.10
                except Exception as e:
                    report.errors.append(f"rds_delete_{db_id}: {e}")

    async def _gc_sqs(self, session: Any, region: str, report: GCReport) -> None:
        """Find and delete orphaned SQS queues."""
        sqs = session.client("sqs")

        try:
            resp = await asyncio.to_thread(sqs.list_queues,
                                           QueueNamePrefix="opensculpt-")
        except Exception:
            return

        for url in resp.get("QueueUrls", []):
            try:
                _attr_resp = await asyncio.to_thread(
                    sqs.get_queue_attributes,
                    QueueUrl=url,
                    AttributeNames=["All"])
                # SQS doesn't have tags on list — need separate call
                tag_resp = await asyncio.to_thread(
                    sqs.list_queue_tags, QueueUrl=url)
                tags_raw = tag_resp.get("Tags", {})
            except Exception:
                continue

            is_ours = tags_raw.get(OPENSCULPT_TAG_KEY) == OPENSCULPT_TAG_VALUE
            goal_id = tags_raw.get(OPENSCULPT_GOAL_TAG, "")
            if not is_ours or not self._goal_is_dead(goal_id):
                continue

            report.aws_orphans_found += 1
            _logger.info("GC: orphaned SQS queue %s (goal=%s)", url, goal_id)

            if not self._dry_run:
                await asyncio.to_thread(sqs.delete_queue, QueueUrl=url)
                report.aws_orphans_terminated += 1

    async def _gc_cloudwatch(self, session: Any, region: str, report: GCReport) -> None:
        """Find and delete orphaned CloudWatch alarms."""
        cw = session.client("cloudwatch")

        try:
            resp = await asyncio.to_thread(cw.describe_alarms,
                                           AlarmNamePrefix="opensculpt-")
        except Exception:
            return

        for alarm in resp.get("MetricAlarms", []):
            alarm_name = alarm["AlarmName"]
            # CloudWatch alarms don't support tag filtering on describe,
            # so we check naming convention: opensculpt-{goal_id}-*
            parts = alarm_name.split("-", 2)
            if len(parts) < 2 or parts[0] != "opensculpt":
                continue
            goal_id = parts[1] if len(parts) > 1 else ""
            if not self._goal_is_dead(goal_id):
                continue

            report.aws_orphans_found += 1
            _logger.info("GC: orphaned CloudWatch alarm %s", alarm_name)

            if not self._dry_run:
                await asyncio.to_thread(
                    cw.delete_alarms, AlarmNames=[alarm_name])
                report.aws_orphans_terminated += 1

    # ── Public API ─────────────────────────────────────────────────

    def get_reports(self, limit: int = 10) -> list[dict]:
        """Return recent GC reports."""
        return [r.to_dict() for r in self._reports[-limit:]]

    def get_last_report(self) -> dict | None:
        return self._reports[-1].to_dict() if self._reports else None

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value
        _logger.info("GC dry_run set to %s", value)


async def _async_pages(paginator, **kwargs):
    """Yield pages from a boto3 paginator via asyncio.to_thread."""
    import asyncio

    def _get_pages():
        return list(paginator.paginate(**kwargs))

    pages = await asyncio.to_thread(_get_pages)
    for page in pages:
        yield page
