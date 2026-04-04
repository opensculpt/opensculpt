"""GoalRunner — persistent autonomous goal execution.

When a user gives a high-level goal like "handle sales for my startup",
the GoalRunner:
1. Persists the goal to disk (survives restarts)
2. Plans phases using the OS agent's LLM
3. Executes each phase autonomously
4. Creates domain-specific daemons for ongoing operations
5. Saves skill docs (what it learned about APIs, data models, auth)
6. Reports progress to the dashboard

Inspired by OpenFang's autonomous Daemons and OpenClaw's Lobster workflows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from agos.daemons.base import Daemon, DaemonResult

_logger = logging.getLogger(__name__)


class GoalRunner(Daemon):
    """Autonomous goal execution hand.

    Unlike other daemons that do one thing (research, monitor), the GoalRunner
    takes a high-level goal and works toward it across multiple sessions.
    It creates sub-daemons, saves skill docs, and evolves its approach.
    """

    name = "goal_runner"
    description = "Persistent goal execution — takes a high-level goal and works on it autonomously"
    icon = "🎯"
    one_shot = False
    default_interval = 10  # Base interval — adapts based on active goals

    def __init__(self) -> None:
        super().__init__()
        self._goals_dir = Path(".opensculpt/goals")
        self._skills_dir = Path(".opensculpt/skills")
        self._os_agent: Any = None
        self._daemon_manager: Any = None
        self._resource_registry: Any = None

    def set_resource_registry(self, rr: Any) -> None:
        self._resource_registry = rr

    def set_os_agent(self, agent: Any) -> None:
        self._os_agent = agent

    def set_daemon_manager(self, hm: Any) -> None:
        self._daemon_manager = hm

    async def setup(self) -> None:
        self._goals_dir.mkdir(parents=True, exist_ok=True)
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    async def tick(self) -> None:
        """Advance active goals. Newest goals first, stale goals expired.

        1. Skip if already executing a phase (prevents stacking LLM calls)
        2. Expire goals older than 2 hours with no progress (zombie cleanup)
        3. Sort by creation time (newest first - user's latest request has priority)
        4. Run up to 3 goals in parallel (prevent resource exhaustion)
        """
        # Guard: don't stack ticks while a phase is already running
        if getattr(self, "_executing", False):
            _logger.debug("Skipping tick — phase already executing")
            return

        goals = self._load_goals()

        # Adaptive interval: fast when goals are active, slow when idle
        active_count = sum(1 for g in goals if g.get("status") == "active")
        if active_count > 0:
            self.interval = 10   # phases pending — check often
        else:
            self.interval = 120  # nothing to do — save resources

        # Expire zombie goals: older than 2 hours, stuck on same phase
        now = time.time()
        for g in goals:
            if g.get("status") == "active":
                age_hours = (now - g.get("created_at", now)) / 3600
                _pending = [p for p in g.get("phases", []) if p.get("status") == "pending"]
                done = [p for p in g.get("phases", []) if p.get("status") == "done"]
                # If goal is >2 hours old and has no completed phases, mark stale
                if age_hours > 2 and not done:
                    g["status"] = "stale"
                    self._save_goal(g)
                    _logger.info("Goal expired (no progress in 2h): %s", g["description"][:40])
                # If goal is >6 hours old regardless, mark stale
                elif age_hours > 6:
                    g["status"] = "stale"
                    self._save_goal(g)
                    _logger.info("Goal expired (>6h old): %s", g["description"][:40])

        # Get active goals, newest first (user's latest request has priority)
        active = [g for g in goals if g.get("status") == "active"]
        active.sort(key=lambda g: g.get("created_at", 0), reverse=True)

        # ── Service health: verify completed goals' services are alive ──
        await self._verify_services(goals)

        if not active:
            return

        # Run up to 3 goals concurrently (prevent resource exhaustion)
        to_run = active[:3]
        tasks = [asyncio.create_task(self._advance_goal(g)) for g in to_run]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def create_goal(self, description: str, category: str = "") -> dict:
        """Create a new persistent goal.

        Args:
            description: What the user wants (e.g., "Handle sales for my startup")
            category: Optional category (sales, support, devops, etc.)
        """
        # Dedup: don't create if a similar active goal exists
        existing = self._load_goals()
        desc_key = description[:50].lower().strip()
        for g in existing:
            if g.get("status") in ("active", "operating", "planning"):
                if g.get("description", "")[:50].lower().strip() == desc_key:
                    _logger.info("Goal dedup: reusing existing goal %s", g["id"])
                    return g

        goal_id = f"goal_{int(time.time())}_{hash(description) % 10000}"
        goal = {
            "id": goal_id,
            "description": description,
            "category": category or self._detect_category(description),
            "status": "planning",  # planning → setup → operating → evolving
            "created_at": time.time(),
            "phases": [],
            "current_phase": 0,
            "daemons_created": [],
            "skills_learned": [],
            "history": [],
        }

        # Plan the phases — check plan cache first (LLM-native: .md files)
        if not self._os_agent or not self._os_agent._llm:
            raise RuntimeError("No LLM available — cannot plan goal. Set an API key first.")

        cached_plan = self._check_plan_cache(description, goal["category"])
        if cached_plan:
            phases = cached_plan["phases"]
            strategy = cached_plan.get("strategy", "sequential")
            _logger.info("Plan cache HIT for '%s' — skipping LLM planning", description[:40])
        else:
            phases, strategy = await self._plan_phases(description, goal["category"])
            self._save_plan_cache(description, goal["category"], phases, strategy)
        goal["phases"] = phases
        goal["strategy"] = strategy

        goal["status"] = "active"
        self._save_goal(goal)

        await self.emit("goal_created", {
            "id": goal_id, "description": description[:100],
            "phases": len(goal["phases"]),
        })
        _logger.info("Goal created: %s (%d phases)", description[:50], len(goal["phases"]))

        # Start executing immediately — don't make the user wait 5 minutes
        asyncio.create_task(self._advance_goal(goal))

        return goal

    async def _plan_phases(self, description: str, category: str) -> list[dict]:
        """Use the LLM to plan phases for a goal."""
        from agos.llm.base import LLMMessage

        # Inject environment info so LLM plans realistic phases + verify commands
        try:
            from agos.environment import EnvironmentProbe
            env_summary = EnvironmentProbe.summary()
        except Exception:
            env_summary = "Environment unknown."

        # Inject learned constraints — tagged .md files, environment-filtered
        constraint_lines = ""
        try:
            from agos.knowledge.tagged_store import TaggedConstraintStore
            _cs = TaggedConstraintStore()
            constraint_lines = _cs.load(max_chars=3000)
        except Exception:
            pass

        prompt = f"""You are an AI operating system planning how to achieve a user's goal.

Goal: {description}
Category: {category}

ENVIRONMENT (plan for THIS, not hypothetical):
{env_summary}
{f'''
KNOWN CONSTRAINTS (learned from past failures — respect these):
{constraint_lines}
''' if constraint_lines else ''}

Break this into 4-8 concrete phases.
- Each phase should build ON what the previous phase created — don't build isolated modules.
- Do NOT create "end_to_end_test" or "system_verification" mega-phases. Each phase verifies itself via its own verify command. The last phase should be simple: just confirm the main output exists (e.g. curl to localhost or python -c to test a connection).
- Your LAST phase should verify the ENTIRE system works from the end user's perspective.

CRITICAL SHELL RULES — read the ENVIRONMENT above and follow these:
- Use ONLY syntax compatible with this environment's shell.
- For file creation: ALWAYS use python -c "with open('file.py','w') as f: f.write(...)" — NEVER use heredoc (<< EOF), cat >, or echo >>.
- For multi-line file content: use python -c with triple quotes or write a small Python script that creates the file.
- NEVER mix shell syntaxes (no Windows batch `for /f` in bash, no bash `$(...)` in cmd.exe).
- Prefer `python -c "..."` for anything complex — Python works on ALL platforms.

For each phase output:
- name: short name
- description: what to accomplish
- exec: LIST of concrete shell commands to run IN ORDER. These run directly — no LLM interprets them.
  GOOD: ["pip install flask", "python -c 'with open(\"app.py\",\"w\") as f: f.write(\"from flask import Flask\")'"]
  GOOD: ["python -c 'import subprocess; subprocess.run([\"pip\",\"install\",\"flask\"])'"]
  BAD: heredoc (cat << EOF) — Unix only, will fail on Windows
  BAD: for /f batch loops — Windows cmd only, will fail in bash
  RULE: For file creation or complex logic, ALWAYS use python -c. It works everywhere.
  Write the actual commands. Be specific. Include flags (-y, -q, etc).
- command: NATURAL LANGUAGE fallback (used ONLY if exec commands fail and the system needs to reason about why). Example: "Install EspoCRM using Docker on port 8081 with MySQL backend."
- depends_on: list of phase NAMES this depends on (empty [] if independent)
- pattern: which execution pattern to use:
  * "tool_use" - direct tool execution (install, deploy, run commands)
  * "planning" - multi-step with verification (configure, setup)
  * "reflection" - draft then self-critique (writing, analysis)
  * "prompt_chaining" - sequential stages with validation (data processing)
  * "goal_monitoring" - ongoing monitoring (health checks, alerts)
  * "rag_retrieval" - search knowledge first then act
- creates_daemon: if this creates a persistent background task, format "name: description"
- daemon_check_type: if creates_daemon, how to fast-check ("http", "api_query", "docker", "always")
- interval: if creates_daemon, how often to run (seconds, 0=one-shot)
- verify_type: how to verify this phase succeeded:
  * "auto" — write a shell command that proves success (exit 0 = pass, non-zero = fail).
    Discover what's available in this environment. Don't hardcode — adapt.
    Examples: "curl -sf http://localhost:8081 -o /dev/null" or "test -d ~/organized"
  * "ask_user" — ONLY for truly subjective outcomes where the OS genuinely cannot judge quality
    (e.g., "does this report look right?" or "is this file organization what you wanted?")
    Do NOT use ask_user for: installations, configurations, data creation, service setup.
    The user delegated the task — they want the OS to DO it, not ask permission for every step.
  * "none" — no verification possible (internal config, abstract analysis)
- verify: the shell command (for "auto"), description to show user (for "ask_user"), or empty (for "none")

IMPORTANT: Write the verify check BEFORE the work. You're writing a test, not grading your own homework.
IMPORTANT: Default to "auto". Use sensible defaults and ACT. Only ask the user when you genuinely cannot proceed without their input.
IMPORTANT: Verify commands must use UNIVERSAL tools only: curl, test, ls, cat, grep, python -c, pgrep.
  Do NOT use: pg_isready, mysql, redis-cli, docker-compose ps, npm test — these may not be installed.
  Good: "curl -sf http://localhost:8080/health" or "test -f /app/db.sqlite3" or "python -c 'import sqlite3; sqlite3.connect(\"/app/data.db\").execute(\"SELECT 1\")'"
  Bad: "pg_isready -h localhost" or "docker-compose ps | grep Up"

Also output a top-level "strategy" field:
- "sequential" - phases run one at a time
- "dag" - phases have dependencies, independent ones run in parallel

Return JSON only:
{{"strategy": "dag", "phases": [{{"name": "...", "description": "...", "exec": ["apt-get install -y nginx", "systemctl start nginx"], "command": "Install nginx web server", "depends_on": [], "pattern": "tool_use", "verify_type": "auto", "verify": "curl -sf http://localhost:80 -o /dev/null", "creates_daemon": "", "interval": 0}}]}}"""

        try:
            resp = await self._os_agent._llm.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                max_tokens=4000,
            )
            text = (resp.content or "").strip()
            if not text:
                raise RuntimeError("LLM returned empty response")
            # Extract JSON from response
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            parsed = json.loads(text)
            # Support both old format (array) and new format (object with strategy)
            if isinstance(parsed, list):
                phases = parsed
                strategy = "sequential"
            else:
                phases = parsed.get("phases", parsed.get("steps", []))
                strategy = parsed.get("strategy", "sequential")

            result = []
            for i, p in enumerate(phases):
                result.append({
                    "name": p.get("name", f"Phase {i+1}"),
                    "description": p.get("description", ""),
                    "exec": p.get("exec", []),  # direct shell commands (no LLM needed)
                    "command": p.get("command", ""),  # natural language fallback
                    "depends_on": p.get("depends_on", []),
                    "pattern": p.get("pattern", "tool_use"),
                    "verify_type": p.get("verify_type", "none"),
                    "verify": p.get("verify", ""),
                    "creates_daemon": p.get("creates_daemon", ""),
                    "interval": p.get("interval", 0),
                    "status": "pending",
                    "result": "",
                    "completed_at": 0,
                })
            return result, strategy
        except Exception as e:
            _logger.error("Phase planning failed: %s", e)
            raise RuntimeError(f"LLM failed to plan goal: {e}")

    async def _advance_goal(self, goal: dict) -> None:
        """Execute ready phases based on the goal's execution strategy.

        - sequential: one phase at a time
        - dag: phases with met dependencies run in parallel
        - pipeline: one at a time, output feeds to next
        - fan_out: independent phases run in parallel
        """
        phases = goal.get("phases", [])
        if not phases:
            return

        strategy = goal.get("strategy", "sequential")

        # Auto-approve awaiting_confirmation after 60s — user delegated the task
        for p in phases:
            if p.get("status") == "awaiting_confirmation":
                completed_at = p.get("completed_at", 0)
                # If completed_at is 0 (never set), set it now and wait 60s
                if not completed_at:
                    p["completed_at"] = time.time()
                    self._save_goal(goal)
                elif (time.time() - completed_at) > 60:
                    p["status"] = "done_unverified"
                    p["result"] = (p.get("result", "") + "\n[Auto-approved: user delegated task]")[:500]
                    _logger.info("Auto-approved phase '%s' (user delegated)", p["name"])

        # Un-block blocked phases — but only ONCE, then emit demand for evolution
        blocked = [p for p in phases if p.get("status") == "blocked"]
        running = [p for p in phases if p.get("status") in ("running", "retrying")]
        if blocked and not running:
            for p in blocked:
                unblock_count = p.get("unblock_count", 0)
                if unblock_count < 1:
                    # First unblock — retry once more
                    p["status"] = "pending"
                    p["retries"] = 0
                    p["unblock_count"] = unblock_count + 1
                    p["result"] = (p.get("result", "") + "\n[Unblocked for retry]")[:500]
                    _logger.info("Unblocked phase '%s' for retry", p["name"])
                else:
                    # Already unblocked once and failed again — emit demand for evolution
                    p["status"] = "failed"
                    p["result"] = (p.get("result", "") + "\n[STUCK: failed after unblock+retry. Needs evolution.]")[:500]
                    _logger.warning("Phase '%s' stuck after unblock — emitting demand", p["name"])
                    await self.emit("os.capability_gap", {
                        "command": goal.get("description", "")[:100],
                        "tool": "goal_execution",
                        "detail": f"Phase '{p['name']}' keeps failing: {p.get('result', '')[:200]}",
                        "phase_name": p["name"],
                        "goal_id": goal.get("id", ""),
                    })
            self._save_goal(goal)

        # Recover failed phases: retry (< 2 attempts) or replan (>= 2 attempts)
        failed_phases = [p for p in phases if p.get("status") == "failed"]
        if failed_phases and not running:
            for p in failed_phases:
                retries = p.get("retries", 0)
                if retries < 2:
                    p["status"] = "retrying"
                    p["retries"] = retries + 1
                    _logger.info("Recovering failed phase '%s' for retry %d", p["name"], retries + 1)
                elif retries >= 2 and not p.get("_replanned"):
                    p["_replanned"] = True
                    _logger.info("Phase '%s' failed %d times — triggering replan", p["name"], retries)
                    await self._replan_goal(goal, p)
                    self._save_goal(goal)
                    return
            self._save_goal(goal)

        # Find phases that are ready to run (dependencies met)
        ready = []
        for i, p in enumerate(phases):
            if p.get("status") not in ("pending", "retrying"):
                continue
            deps = p.get("depends_on", [])
            deps_met = all(
                any(pp["name"] == dep and pp["status"] in ("done", "done_unverified", "skipped") for pp in phases)
                for dep in deps
            ) if deps else True
            if deps_met:
                ready.append((i, p))

        if not ready:
            # Check if all done
            _terminal = ("done", "done_unverified", "failed", "skipped")
            all_done = all(p.get("status") in _terminal for p in phases)
            if all_done:
                succeeded = all(p.get("status") in ("done", "done_unverified", "skipped") for p in phases)
                goal["status"] = "complete" if succeeded else "operating"
                # Generate completion summary from phase results
                if succeeded and not goal.get("completion_summary"):
                    summaries = []
                    for p in phases:
                        r = p.get("result", "")
                        if r:
                            # Extract key outcome lines (URLs, ports, "Done!", etc.)
                            for line in r.split("\n"):
                                line = line.strip()
                                if any(kw in line.lower() for kw in ["http://", "https://", "port ", "localhost", "running", "ready", "deployed", "created", "installed"]):
                                    clean = line.replace("**", "").replace("|", "").strip()
                                    if len(clean) > 10:
                                        summaries.append(clean[:100])
                    goal["completion_summary"] = "\n".join(summaries[:5]) if summaries else "All phases completed successfully."
                    await self.emit("goal_completed", {
                        "goal_id": goal["id"],
                        "description": goal.get("description", "")[:100],
                        "summary": goal["completion_summary"][:200],
                    })
                self._save_goal(goal)
            return

        # Execute ready phases based on strategy
        if strategy == "sequential" or strategy == "pipeline":
            # Run only the first ready phase
            phase_idx, current = ready[0]
            await self._execute_phase(goal, current, phase_idx)
        else:
            # DAG / fan_out: run all ready phases in parallel
            tasks = [
                asyncio.create_task(self._execute_phase(goal, p, idx))
                for idx, p in ready
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_phase(self, goal: dict, current: dict, phase_idx: int = 0) -> None:
        """Execute a single phase by spawning a sub-agent and waiting."""
        self._executing = True
        try:
            await self._execute_phase_inner(goal, current, phase_idx)
        finally:
            self._executing = False

    async def _execute_phase_inner(self, goal: dict, current: dict, phase_idx: int = 0) -> None:
        # ── DIRECT EXECUTION (no LLM cost) ──────────────────────────
        # If the planner provided `exec` commands, run them directly.
        # Only fall back to sub-agent (LLM) if direct exec fails.
        exec_cmds = current.get("exec", [])
        if exec_cmds and isinstance(exec_cmds, list):
            _logger.info("Phase '%s' — direct execution (%d commands, zero LLM cost)",
                         current.get("name", "?"), len(exec_cmds))
            await self.emit("phase_started", {"goal_id": goal["id"], "phase": current["name"], "phase_idx": phase_idx})
            direct_ok = await self._direct_execute(goal, current, exec_cmds)
            if direct_ok:
                return  # Done — no LLM needed
            _logger.info("Phase '%s' direct exec failed, falling back to LLM sub-agent",
                         current.get("name", "?"))

        command = current.get("command", "")
        if not command:
            current["status"] = "failed"
            current["result"] = "Phase has no command."
            self._save_goal(goal)
            return
        if not self._os_agent:
            _logger.error("GoalRunner has no OS agent — cannot execute phase '%s'", current["name"])
            return

        # Safety limits (keep these — they prevent runaway cost)
        _GOAL_TURN_LIMIT = 200
        _GOAL_TOKEN_BUDGET = 200_000
        goal_turns = goal.get("_total_turns", 0)
        if goal_turns > _GOAL_TURN_LIMIT:
            goal["status"] = "stale"
            goal["_stale_reason"] = f"Turn limit exceeded ({goal_turns})"
            self._save_goal(goal)
            return
        if goal.get("_total_tokens", 0) > _GOAL_TOKEN_BUDGET:
            goal["status"] = "stale"
            goal["_stale_reason"] = f"Token budget exceeded ({goal['_total_tokens']:,})"
            self._save_goal(goal)
            return

        _logger.info("Phase '%s' — LLM sub-agent (turns: %d/%d)",
                      current["name"], goal_turns, _GOAL_TURN_LIMIT)
        await self.emit("phase_started", {"goal_id": goal["id"], "phase": current["name"], "phase_idx": phase_idx})

        try:
            agent_name = f"{goal['category']}_{current['name'].lower().replace(' ', '_')}"

            # Context from completed phases
            prev_context = ""
            for prev in goal.get("phases", []):
                if prev.get("status") in ("done", "done_unverified") and prev.get("result"):
                    prev_context += f"\nCompleted '{prev['name']}': {prev['result'][:200]}"

            # Self-feedback on retry (LLM analyzes WHY previous attempt failed)
            feedback = ""
            if current.get("retries", 0) > 0 and current.get("result"):
                if self._os_agent and self._os_agent._llm:
                    feedback = await self._generate_self_feedback(current, goal)
                else:
                    feedback = f"\n\nPREVIOUS ATTEMPT FAILED:\n{current['result'][:300]}\nUse a DIFFERENT approach."

            full_command = command
            if prev_context:
                full_command += f"\n\nCONTEXT FROM PREVIOUS PHASES:{prev_context}"
            if feedback:
                full_command += feedback

            result = await self._os_agent._spawn_agent_and_wait(
                agent_name, full_command, timeout=300,
                goal_id=goal.get("id", ""),
            )
            phase_ok = result.get("ok", False)

            # Track turns + tokens
            _data = result.get("data", {}) if isinstance(result, dict) else {}
            goal["_total_turns"] = goal.get("_total_turns", 0) + _data.get("turns", 0)
            goal["_total_tokens"] = goal.get("_total_tokens", 0) + _data.get("tokens_used", 0)

            current["result"] = (result.get("message", "") or "")[:500]

            # Write execution trace (Meta-Harness pattern: raw traces > summaries)
            _trace_steps = _data.get("steps", [])
            if _trace_steps:
                try:
                    from agos.evolution.trace_store import TraceStore
                    TraceStore().write_goal_trace(goal["id"], current["name"], _trace_steps)
                except Exception:
                    pass  # trace storage is best-effort

            # Verify
            phase_ok = await self._verify_phase(current, phase_ok)

            # Set status
            if phase_ok:
                verify_type = current.get("verify_type", "none")
                if verify_type == "ask_user":
                    current["status"] = "awaiting_confirmation"
                    self._save_goal(goal)
                    await self.emit("phase_needs_confirmation", {
                        "goal_id": goal["id"], "phase": current["name"],
                        "summary": current.get("result", "")[:500],
                    })
                    return
                current["status"] = "done" if verify_type == "auto" else "done_unverified"
                if current.get("retries", 0) > 0:
                    self._record_resolution(f"Phase '{current['name']}' succeeded after retry",
                                            current.get("result", "")[:100])
            else:
                current["status"] = "failed"
            current["completed_at"] = time.time()

            # Save skill doc if we learned something
            if phase_ok:
                await self._save_skill_from_result(goal, current, result)

            # Create a hand if this phase requires one
            if current.get("creates_daemon") and self._daemon_manager:
                await self._create_domain_daemon(goal, current)

            # ── AUTO SERVICE MONITOR (like systemd Restart=always) ──
            # If this phase started a service (detected from verify command or result),
            # auto-spawn a health daemon even if creates_daemon wasn't set.
            # The LLM forgets to set creates_daemon — the OS shouldn't depend on that.
            if not current.get("creates_daemon") and self._daemon_manager:
                # Detect services even on failed phases — the service may be
                # running despite a bad verify command (demand #6)
                await self._auto_detect_service(goal, current)

            # ── SERVICE CARD EXTRACTION ──
            # If this phase deployed a persistent service, extract a service card
            # so ServiceKeeper can monitor, debug, and restore it.
            if phase_ok and (current.get("creates_daemon") or current.get("creates_hand")):
                try:
                    from agos.services import extract_service_card, run_health_check, find_pid_on_port
                    card = await extract_service_card(self._os_agent._llm, goal, current)
                    if card:
                        # Immediately adopt — don't wait for next ServiceKeeper tick
                        healthy, _ = run_health_check(card.health_check)
                        if healthy:
                            card.status = "healthy"
                            card.pid = find_pid_on_port(card.port) if card.port else 0
                            import time as _t
                            card.last_healthy = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
                            card.save()
                        _logger.info("Service card extracted + adopted: %s (port %d, status=%s)",
                                    card.name, card.port, card.status)
                except Exception as e:
                    _logger.debug("Service card extraction failed: %s", e)

            # ── SMART RETRY: Diagnose WHY it failed before retrying ──
            retries = current.get("retries", 0)
            if current["status"] == "failed" and retries >= 2:
                # If sub-agent reported success but verify keeps failing,
                # accept as done_unverified rather than replanning
                if phase_ok is False and "VERIFICATION FAILED" in (current.get("result") or ""):
                    current["status"] = "done_unverified"
                    current["result"] += "\n[Accepted: verify unreliable after 2 rewrites]"
                    _logger.info("Phase '%s': accepting as done_unverified (verify unreliable)", current["name"])
                else:
                    # Emit impasse for Evolution Agent — phase failed 2+ times
                    try:
                        from agos.environment import EnvironmentProbe
                        env = EnvironmentProbe.summary()[:300]
                    except Exception:
                        env = "unknown"
                    await self.emit("evolution.impasse", {
                        "goal": goal["description"][:100],
                        "phase": current["name"],
                        "error": (current.get("result") or "")[:300],
                        "environment": env,
                        "attempts": retries,
                        "category": goal.get("category", "general"),
                    })
                    _logger.info("Evolution impasse: phase '%s' failed %d times",
                                 current["name"], retries)
                    await self._replan_goal(goal, current)
                    return

            if current["status"] == "failed" and retries < 2:
                # Don't blindly retry. Diagnose first.
                diagnosis = await self._diagnose_failure(goal, current)

                # Tell the user what went wrong so they can help
                await self.emit("goal_needs_help", {
                    "goal_id": goal["id"],
                    "phase": current["name"],
                    "error": diagnosis.get("reason", current.get("result", "")[:200])[:300],
                    "suggestion": diagnosis.get("fix", "retry"),
                    "attempt": retries + 1,
                })

                if diagnosis.get("fix") == "rewrite_verify":
                    # Verification command was wrong, not the deployment
                    new_verify = diagnosis.get("new_verify", "")
                    if new_verify:
                        _logger.info("Phase '%s': fixing verify command → %s",
                                     current["name"], new_verify[:60])
                        current["verify"] = new_verify
                        current["status"] = "pending"
                        current["retries"] = retries + 1
                        current["result"] += "\n[Diagnosis: verify command was wrong. Fixed.]"
                        self._save_goal(goal)
                        # Re-run verification immediately with fixed command
                        await self.emit("phase_retrying", {
                            "goal_id": goal["id"],
                            "phase": current["name"],
                            "action": "rewrite_verify",
                        })
                        return

                elif diagnosis.get("fix") == "retry_phase":
                    # Clean up resources from failed attempt (Unix kill -PGID)
                    if self._resource_registry:
                        phase_resources = [
                            r for r in self._resource_registry.active()
                            if r.goal_id == goal["id"] and r.phase_name == current["name"]
                        ]
                        for r in phase_resources:
                            await self._resource_registry.destroy(r.id)
                            _logger.info("Phase retry cleanup: destroyed %s %s", r.type.value, r.name)
                    # Deployment actually failed — retry with backoff
                    import asyncio as _aio
                    backoff = min(60, 10 * (retries + 1))  # 10s, 20s, 30s...
                    _logger.info("Phase '%s' failed — retrying (attempt %d, backoff %ds): %s",
                                 current["name"], retries + 1, backoff, diagnosis.get("reason", "")[:60])
                    current["status"] = "retrying"
                    current["retries"] = retries + 1
                    current["result"] += f"\n[Diagnosis: {diagnosis.get('reason', 'retry needed')}]"
                    self._save_goal(goal)
                    await _aio.sleep(backoff)  # Don't hammer immediately
                    await self.emit("phase_retrying", {
                        "goal_id": goal["id"],
                        "phase": current["name"],
                        "attempt": retries + 1,
                    })
                    # Tell evolution what failed so it can learn
                    await self.emit("phase_failed", {
                        "goal_id": goal["id"],
                        "phase": current["name"],
                        "error": diagnosis.get("reason", "verification failed")[:500],
                        "attempt": retries + 1,
                        "task": current.get("command", "")[:300],
                        "verify": current.get("verify", "")[:200],
                        "category": goal.get("category", ""),
                        "goal_description": goal.get("description", "")[:200],
                    })
                    return

                elif diagnosis.get("fix") == "mark_done":
                    # The work actually succeeded but verify was wrong
                    _logger.info("Phase '%s': diagnosis says work succeeded despite verify failure",
                                 current["name"])
                    current["status"] = "done_unverified"
                    current["result"] += f"\n[Diagnosis: {diagnosis.get('reason', 'work appears successful')}]"
                    phase_ok = True
                    # Fall through to save

                else:
                    # Unknown diagnosis — retry once
                    current["status"] = "retrying"
                    current["retries"] = retries + 1
                    self._save_goal(goal)
                    return

            goal["history"].append({
                "phase": current["name"],
                "status": current["status"],
                "timestamp": time.time(),
            })

            # Reinforcement: track which principles were active when phase succeeded/failed
            injected = getattr(self._os_agent, '_last_injected_principles', []) if self._os_agent else []
            if injected and current["status"] in ("done", "done_unverified"):
                await self.emit("principles_reinforced", {
                    "phase": current["name"],
                    "principles": injected,
                    "outcome": "success",
                })
            elif injected and current["status"] == "failed":
                await self.emit("principles_reinforced", {
                    "phase": current["name"],
                    "principles": injected,
                    "outcome": "failure",
                })

            self._save_goal(goal)

            await self.emit("phase_completed", {
                "goal_id": goal["id"],
                "phase": current["name"],
                "status": current["status"],
            })

            self.results.append(DaemonResult(
                daemon_name=self.name,
                success=current["status"] == "done",
                summary=f"Phase '{current['name']}': {current['status']}",
                data={"goal_id": goal["id"], "phase": current["name"]},
            ))

        except Exception as e:
            current["status"] = "failed"
            current["result"] = str(e)[:500]
            self._save_goal(goal)
            _logger.error("Goal phase failed: %s — %s", current["name"], e)
            # Tell evolution engine WHAT failed so it can learn
            await self.emit("phase_failed", {
                "goal_id": goal.get("id", ""),
                "phase": current["name"],
                "error": str(e)[:500],
                "attempt": current.get("retries", 0) + 1,
                "task": current.get("command", "")[:300],
                "verify": current.get("verify", "")[:200],
                "category": goal.get("category", ""),
                "goal_description": goal.get("description", "")[:200],
            })

    async def _direct_execute(self, goal: dict, current: dict, commands: list[str]) -> bool:
        """Run shell commands directly. No LLM. Returns True if all succeed."""
        outputs = []
        for cmd in commands:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                out = stdout.decode("utf-8", errors="replace").strip()
                err = stderr.decode("utf-8", errors="replace").strip()
                if proc.returncode != 0:
                    current["result"] = f"Command failed: {cmd}\n{err or out}"[:500]
                    current["status"] = "failed"
                    self._save_goal(goal)
                    return False
                if out:
                    outputs.append(out[:200])
            except asyncio.TimeoutError:
                current["result"] = f"Command timed out: {cmd}"
                current["status"] = "failed"
                self._save_goal(goal)
                return False
            except Exception as e:
                current["result"] = f"Command error: {cmd}\n{e}"[:500]
                current["status"] = "failed"
                self._save_goal(goal)
                return False

        current["result"] = "\n".join(outputs)[:500] if outputs else "All commands completed."

        # Run verify if available
        phase_ok = await self._verify_phase(current, True)
        if phase_ok:
            verify_type = current.get("verify_type", "none")
            current["status"] = "done" if verify_type == "auto" else "done_unverified"
        else:
            current["status"] = "failed"
        current["completed_at"] = time.time()
        self._save_goal(goal)

        await self.emit("phase_completed", {
            "goal_id": goal["id"], "phase": current["name"], "status": current["status"],
        })
        return current["status"] in ("done", "done_unverified")

    async def _verify_phase(self, current: dict, phase_ok: bool) -> bool:
        """Run the verify command if verify_type is auto. Returns pass/fail."""
        verify_type = current.get("verify_type", "none")
        verify_cmd = current.get("verify", "").strip()

        if verify_type != "auto" or not verify_cmd or not phase_ok:
            return phase_ok

        try:
            _logger.info("Verifying phase '%s': %s", current["name"], verify_cmd[:80])
            proc = await asyncio.create_subprocess_shell(
                verify_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                current["result"] += f"\n[Verified: {stdout.decode()[:100].strip()}]"
                return True
            else:
                err = stderr.decode()[:100] or stdout.decode()[:100] or "check failed"
                current["result"] += f"\n[VERIFICATION FAILED: {err}]"
                return False
        except asyncio.TimeoutError:
            current["result"] += "\n[VERIFICATION TIMEOUT]"
            return False
        except Exception as e:
            _logger.debug("Verification error: %s", e)
            return phase_ok  # Don't fail on verify infrastructure issues

    async def _save_skill_from_result(self, goal: dict, phase: dict, result: dict) -> None:
        """Extract and save skill knowledge from a completed phase.

        Like OpenClaw's SKILL.md — saves what the OS learned about APIs,
        endpoints, auth, data models so it can reuse them.
        """
        steps = result.get("data", {}).get("steps", [])
        if not steps:
            return

        # Extract what happened from tool steps — no keyword filtering,
        # just record successful actions so the OS can replay them
        learned = []
        for step in steps:
            if not step.get("ok"):
                continue
            tool = step.get("tool", "")
            args = step.get("args", {})
            preview = step.get("preview", "")
            if tool == "http":
                learned.append(f"API: {args.get('method', 'GET')} {args.get('url', '')}")
            elif tool == "shell":
                learned.append(f"Command: {args.get('command', '')[:100]}")
            elif tool == "write_file":
                learned.append(f"Created: {args.get('path', '')}")
            elif preview:
                learned.append(f"{tool}: {preview[:100]}")

        if learned:
            skill_name = f"{goal['category']}_{phase['name'].lower().replace(' ', '_')}"
            skill_doc = f"""# Skill: {phase['name']}
## Category: {goal['category']}
## Goal: {goal['description'][:100]}

## What was learned:
{chr(10).join(f'- {item}' for item in learned[:20])}

## Phase result:
{phase.get('result', '')[:300]}

## Useful for:
- Repeating this setup on another system
- Debugging if this breaks
- Teaching other agents about this domain
"""
            skill_path = self._skills_dir / f"{skill_name}.md"
            skill_path.write_text(skill_doc, encoding="utf-8")
            goal["skills_learned"].append(skill_name)
            _logger.info("Saved skill doc: %s", skill_name)

    async def _create_domain_daemon(self, goal: dict, phase: dict) -> None:
        """Create a DomainDaemon from a phase definition.

        Unlike the old scheduler approach (dumb cron), DomainDaemons are
        LLM-powered workers that read skill docs, recall TheLoom, and
        reason about what to do each tick. This is what makes the OS
        accumulate domain knowledge over weeks.
        """
        daemon_desc = phase.get("creates_daemon", "")
        if not daemon_desc or not self._daemon_manager:
            return

        # Parse "name: description" format
        if ":" in daemon_desc:
            daemon_name, daemon_description = daemon_desc.split(":", 1)
            daemon_name = daemon_name.strip().replace(" ", "_")
            daemon_description = daemon_description.strip()
        else:
            daemon_name = daemon_desc.replace(" ", "_")[:30]
            daemon_description = daemon_desc

        interval = phase.get("interval", 3600)

        check_type = phase.get("daemon_check_type", "always")
        check_target = ""

        # Collect all skill docs learned by this goal
        skill_paths = [
            str(self._skills_dir / f"{s}.md")
            for s in goal.get("skills_learned", [])
        ]

        try:
            _daemon = await self._daemon_manager.create_domain_daemon(
                name=daemon_name,
                config={
                    "task": daemon_description,
                    "category": goal.get("category", "general"),
                    "skill_paths": skill_paths,
                    "check_type": check_type,
                    "check_target": check_target,
                    "interval": interval,
                    "role": f"daemon:{goal.get('category', 'general')}",
                },
            )
            goal["daemons_created"].append(daemon_name)
            _logger.info("Created domain daemon: %s (every %ds, check=%s)",
                         daemon_name, interval, check_type)
            await self.emit("daemon_created", {
                "goal_id": goal["id"],
                "daemon_name": daemon_name,
                "interval": interval,
                "check_type": check_type,
            })
        except Exception as e:
            _logger.warning("Failed to create domain daemon %s: %s", daemon_name, e)

    async def _auto_detect_service(self, goal: dict, phase: dict) -> None:
        """Auto-detect if a phase left a service running and spawn a health monitor.

        Like systemd's Restart=always — the OS automatically monitors services
        without the LLM needing to set creates_daemon. Detects from:
        1. Verify command contains curl/http (service is web-accessible)
        2. Phase result mentions "running on port" or "listening"
        3. Phase command mentions "start", "serve", "run"
        """
        verify_cmd = phase.get("verify", "")
        result_text = phase.get("result", "")
        _command = phase.get("command", "")
        exec_cmds = " ".join(phase.get("exec", []))

        # Detect port from verify, result, exec commands, or natural language command
        # Scan all available text — failed phases may only have port in exec/command
        import re
        _all_text = f"{verify_cmd} {result_text} {exec_cmds} {_command}"
        port_match = re.search(r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)', _all_text)
        if not port_match:
            # Also check for "port NNNN" pattern in natural language
            port_match = re.search(r'\bport\s+(\d{2,5})\b', _all_text, re.IGNORECASE)
        if not port_match:
            return  # No service detected

        port = port_match.group(1)
        service_name = f"{goal.get('category', 'svc')}_{phase['name']}_health"
        service_name = service_name.replace(" ", "_")[:40]

        # Don't create duplicate monitors
        if service_name in goal.get("daemons_created", []):
            return

        try:
            _daemon = await self._daemon_manager.create_domain_daemon(
                name=service_name,
                config={
                    "task": f"Monitor service on port {port}. If health check fails, restart it.",
                    "category": goal.get("category", "general"),
                    "check_type": "http",
                    "check_target": f"http://localhost:{port}",
                    "interval": 120,  # Check every 2 min
                    "role": f"health_monitor:{port}",
                },
            )
            goal.setdefault("daemons_created", []).append(service_name)
            _logger.info("Auto-detected service on port %s — spawned health monitor '%s'",
                        port, service_name)
        except Exception as e:
            _logger.debug("Auto-detect service monitor failed: %s", e)

    async def _generate_self_feedback(self, phase: dict, goal: dict) -> str:
        """M2.7-style self-feedback: critically analyze WHY the previous attempt failed
        and generate specific guidance for the next attempt.

        Unlike simple "try something different," this:
        1. Identifies the ROOT CAUSE (not just the symptom)
        2. Lists what was tried and why it failed
        3. Suggests a SPECIFIC alternative approach
        4. Notes environmental constraints to respect

        Cost: 1 small LLM call (~500 tokens). Saves many retry tokens.
        """
        from agos.llm.base import LLMMessage
        try:
            from agos.environment import EnvironmentProbe
            env_brief = EnvironmentProbe.summary()[:300]
        except Exception:
            env_brief = ""

        result_text = (phase.get("result", "") or "")[:400]
        prompt = f"""Analyze this failed phase and give specific guidance for the retry.

PHASE: {phase.get('name', '')}
TASK: {phase.get('command', '')[:200]}
RESULT: {result_text}
ENVIRONMENT: {env_brief}
RETRIES SO FAR: {phase.get('retries', 0)}

Answer these 4 questions in 2-3 lines TOTAL (be brief):
1. ROOT CAUSE: Why did it fail? (not "it failed" — WHY specifically)
2. WHAT WAS TRIED: What approach was used?
3. WHAT TO AVOID: What should NOT be tried again?
4. WHAT TO TRY: One specific alternative approach that could work.

Format as a single paragraph of guidance for the next agent."""

        try:
            # Use cheap model for feedback generation (cost-aware routing)
            _llm = self._os_agent.get_cheap_llm() or self._os_agent._llm
            resp = await _llm.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                max_tokens=200,
            )
            feedback_text = (resp.content or "").strip()
            if feedback_text:
                # Store feedback in phase for future reference
                phase.setdefault("feedback_history", [])
                phase["feedback_history"].append(feedback_text)
                self._save_goal(goal)
                return f"\n\nSELF-FEEDBACK FROM PREVIOUS FAILURE:\n{feedback_text}"
        except Exception as e:
            _logger.debug("Self-feedback generation failed: %s", e)

        return f"\n\nPREVIOUS ATTEMPT FAILED:\n{result_text[:200]}\nUse a DIFFERENT approach."

    async def _replan_goal(self, goal: dict, failed_phase: dict) -> None:
        """Re-plan remaining phases after exhausting retries (EvoAgentX pattern)."""
        if not self._os_agent or not self._os_agent._llm:
            return

        from agos.llm.base import LLMMessage
        try:
            from agos.environment import EnvironmentProbe
            env_summary = EnvironmentProbe.summary()
        except Exception:
            env_summary = "Unknown"

        completed = [p["name"] for p in goal.get("phases", [])
                     if p.get("status") in ("done", "done_unverified")]
        failed_result = (failed_phase.get("result", "") or "")[:300]

        prompt = f"""A phase in your goal failed after multiple retries. Re-plan.

GOAL: {goal['description'][:100]}
COMPLETED PHASES: {completed}
FAILED PHASE: {failed_phase['name']}
FAILURE: {failed_result}
ENVIRONMENT: {env_summary}

Create 2-4 NEW phases to replace the failed one. Use a COMPLETELY DIFFERENT approach.
Avoid whatever caused the failure.

SHELL RULES: Use ONLY python -c for file creation (no heredoc, no cat >, no batch for /f).
Prefer python -c for complex operations — it works on all platforms.

Each phase MUST have these fields:
- name: short name
- description: what to accomplish
- exec: list of concrete shell commands to run directly (no LLM needed)
- command: natural language fallback if exec fails
- depends_on: [] (no dependencies for replanned phases)
- pattern: "tool_use"
- verify_type: "auto"
- verify: shell command to prove success (exit 0 = pass)

Return JSON: {{"phases": [...]}}"""

        try:
            resp = await self._os_agent._llm.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                max_tokens=1500,
            )
            text = (resp.content or "").strip()
            if not text:
                raise RuntimeError("LLM returned empty response")
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()

            import json as _json
            import re as _re
            # Try to extract JSON from response — handle malformed LLM output
            try:
                parsed = _json.loads(text)
            except _json.JSONDecodeError:
                # Try to find JSON array or object in the text
                match = _re.search(r'(\[.*\]|\{.*\})', text, _re.DOTALL)
                if match:
                    try:
                        parsed = _json.loads(match.group(1))
                    except _json.JSONDecodeError:
                        raise
                else:
                    raise
            if isinstance(parsed, list):
                new_phases = parsed
            else:
                new_phases = parsed.get("phases", parsed.get("steps", []))

            # Keep completed + done phases, replace failed + pending
            keep = [p for p in goal["phases"]
                    if p.get("status") in ("done", "done_unverified")]
            reformatted = []
            for i, p in enumerate(new_phases):
                reformatted.append({
                    "name": p.get("name", f"Replan {i+1}"),
                    "description": p.get("description", ""),
                    "exec": p.get("exec", []),
                    "command": p.get("command", ""),
                    "depends_on": p.get("depends_on", []),
                    "pattern": p.get("pattern", "tool_use"),
                    "verify_type": p.get("verify_type", "none"),
                    "verify": p.get("verify", ""),
                    "creates_daemon": p.get("creates_daemon", ""),
                    "interval": p.get("interval", 0),
                    "status": "pending",
                    "result": "",
                    "completed_at": 0,
                })

            goal["phases"] = keep + reformatted
            goal["status"] = "active"
            self._save_goal(goal)
            _logger.info("Re-planned goal '%s': %d completed + %d new phases",
                         goal["description"][:30], len(keep), len(reformatted))
            await self.emit("goal_replanned", {
                "goal_id": goal["id"],
                "completed": len(keep),
                "new_phases": len(reformatted),
                "failed_phase": failed_phase["name"],
            })
        except Exception as e:
            _logger.warning("Re-plan failed: %s", e)
            # Mark goal as blocked, not silently stuck
            failed_phase["status"] = "blocked"
            failed_phase["result"] += "\n[BLOCKED: retries exhausted, re-plan failed]"
            self._save_goal(goal)

    async def _diagnose_failure(self, goal: dict, phase: dict) -> dict:
        """Diagnose WHY a phase failed. Use LLM + environment to decide the fix.

        Returns: {"fix": "rewrite_verify"|"retry_phase"|"mark_done"|"give_up",
                  "reason": "...", "new_verify": "..." (if rewrite)}
        """
        if not self._os_agent or not self._os_agent._llm:
            return {"fix": "retry_phase", "reason": "no LLM for diagnosis"}

        from agos.llm.base import LLMMessage
        try:
            from agos.environment import EnvironmentProbe
            env_summary = EnvironmentProbe.summary()
        except Exception:
            env_summary = "Unknown"

        result_text = phase.get("result", "")[:500]
        verify_cmd = phase.get("verify", "")
        verify_type = phase.get("verify_type", "none")

        prompt = f"""A phase in a goal execution failed verification. Diagnose WHY and decide what to do.

PHASE: {phase.get('name', '')}
DESCRIPTION: {phase.get('description', '')}
COMMAND: {phase.get('command', '')[:200]}
SUB-AGENT RESULT: {result_text}
VERIFY TYPE: {verify_type}
VERIFY COMMAND: {verify_cmd}
VERIFICATION ERROR: {result_text.split('[VERIFICATION FAILED:')[-1][:200] if 'VERIFICATION FAILED' in result_text else 'unknown'}

ENVIRONMENT:
{env_summary}

DIAGNOSE: Is the problem that:
A) The verify command is wrong (references tools not available, wrong version, wrong port, wrong path) but the work likely succeeded
B) The deployment actually failed and needs to be retried with a different approach
C) The work succeeded but can't be verified — mark as done_unverified

If A: write a new verify command that ONLY CHECKS (reads, tests, curls). It must NOT start services, install anything, or modify state. It must exit 0 if the work succeeded, non-zero if it didn't.
Good: "curl -sf http://localhost:8080 -o /dev/null" or "test -S /run/mysqld/mysqld.sock" or "pgrep -x nginx"
Bad: "service mysql start" or "apt-get install" or "mysqld_safe"

Return JSON only:
{{"fix": "rewrite_verify" or "retry_phase" or "mark_done", "reason": "one line explanation", "new_verify": "READ-ONLY check command (only if fix=rewrite_verify, exit 0=pass)"}}"""

        try:
            import asyncio as _aio
            # Use cheap model for diagnosis (cost-aware routing)
            _llm = self._os_agent.get_cheap_llm() or self._os_agent._llm
            text = ""
            for _retry in range(3):
                resp = await _llm.complete(
                    messages=[LLMMessage(role="user", content=prompt)],
                    max_tokens=300,
                )
                text = (resp.content or "").strip()
                if text:
                    break
                _logger.debug("Diagnosis LLM empty (attempt %d/3)", _retry + 1)
                if _retry < 2:
                    await _aio.sleep(2 ** _retry)
            if not text:
                return {"fix": "retry_phase", "reason": "LLM empty after 3 retries — will retry phase"}
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            import json as _json
            import re as _re
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                # LLM often adds explanation after the JSON — extract first object
                match = _re.search(r'\{[^{}]*\}', text)
                if match:
                    return _json.loads(match.group(0))
                raise
        except Exception as e:
            _logger.debug("Diagnosis failed: %s", e)
            return {"fix": "retry_phase", "reason": f"diagnosis error: {e}"}

    # ── Knowledge Writing: .md files (LLM-native) ────────────────

    def _record_constraint(self, text: str) -> None:
        """Add a learned constraint to the tagged constraint store."""
        try:
            from agos.knowledge.tagged_store import TaggedConstraintStore, environment_tags
            _cs = TaggedConstraintStore()
            _cs.add(text, env_tags=environment_tags(), source="goal_runner")
        except Exception as e:
            _logger.debug("Failed to record constraint: %s", e)

    def _record_resolution(self, symptom: str, fix: str) -> None:
        """Add a resolution pattern to the tagged resolution store."""
        try:
            from agos.knowledge.tagged_store import TaggedResolutionStore
            _rs = TaggedResolutionStore()
            _rs.add(symptom, fix, source="goal_runner")
        except Exception as e:
            _logger.debug("Failed to record resolution: %s", e)

    # ── Plan Cache: LLM-native .md files ──────────────────────────

    def _check_plan_cache(self, description: str, category: str) -> dict | None:
        """Check if a similar goal was planned before. Returns cached plan or None.

        LLM-native: plans stored as .md files that Claude Code can read/edit.
        Matching is keyword-based — same category + >50% word overlap.
        """
        cache_dir = Path(".opensculpt/plan_cache")
        if not cache_dir.exists():
            return None

        desc_words = set(description.lower().split())
        best_match = None
        best_overlap = 0.0

        for f in cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("category") != category:
                    continue
                cached_words = set(data.get("description", "").lower().split())
                if not cached_words:
                    continue
                overlap = len(desc_words & cached_words) / max(len(desc_words | cached_words), 1)
                if overlap > 0.5 and overlap > best_overlap:
                    best_overlap = overlap
                    best_match = data
            except Exception:
                continue

        return best_match

    def _save_plan_cache(self, description: str, category: str,
                         phases: list[dict], strategy: str) -> None:
        """Cache a plan for future reuse. LLM-native: stored as .md + .json."""
        cache_dir = Path(".opensculpt/plan_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Clean phases for caching (remove runtime state)
        clean_phases = []
        for p in phases:
            clean_phases.append({
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "command": p.get("command", ""),
                "depends_on": p.get("depends_on", []),
                "pattern": p.get("pattern", "tool_use"),
                "verify_type": p.get("verify_type", "none"),
                "verify": p.get("verify", ""),
                "creates_daemon": p.get("creates_daemon", ""),
                "interval": p.get("interval", 0),
            })

        slug = category + "_" + "_".join(description.lower().split()[:5])
        slug = "".join(c if c.isalnum() or c == "_" else "" for c in slug)[:60]

        data = {
            "description": description,
            "category": category,
            "strategy": strategy,
            "phases": clean_phases,
            "cached_at": time.time(),
        }
        (cache_dir / f"{slug}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
        _logger.info("Plan cached: %s (%d phases)", slug, len(clean_phases))

    def _detect_category(self, description: str) -> str:
        """Fallback category — the planner LLM sets this properly via the category field."""
        return "general"

    # ── Service Lifecycle: read health from resource registry ──

    async def _verify_services(self, goals: list[dict] | None = None) -> dict:
        """Check service health by reading the resource registry.

        The resource registry (agos/processes/resources.py) already tracks
        containers and runs reconcile() every 60s via serve.py. We just
        read its status — no stale verify commands, no shell calls.

        Returns: {"checked": N, "up": N, "down": N}
        """
        if goals is None:
            goals = self._load_goals()

        result = {"checked": 0, "up": 0, "down": 0}

        # Get resource registry if available
        resource_registry = None
        try:
            from agos.dashboard.app import _resource_registry
            resource_registry = _resource_registry
        except Exception:
            pass

        for goal in goals:
            phases = goal.get("phases", [])
            all_done = all(p.get("status") in ("done", "done_unverified") for p in phases) if phases else False
            if not all_done:
                continue

            # Check resource registry for this goal's resources
            if resource_registry:
                goal_resources = [r for r in resource_registry.all_resources() if r.goal_id == goal.get("id")]
                containers = [r for r in goal_resources if r.type == "container"]
                if containers:
                    result["checked"] += len(containers)
                    up = sum(1 for c in containers if c.status == "active")
                    down = len(containers) - up
                    result["up"] += up
                    result["down"] += down
                    if down == 0:
                        goal["service_health"] = "up"
                        goal["service_health_detail"] = f"{up} containers running"
                    elif up > 0:
                        goal["service_health"] = "degraded"
                        goal["service_health_detail"] = f"{up} up, {down} down"
                    else:
                        goal["service_health"] = "down"
                        goal["service_health_detail"] = f"{down} containers down"
                else:
                    goal["service_health"] = "no_services"
            else:
                goal["service_health"] = "no_services"

            self._save_goal(goal)

        return result

    # ── Persistence ──

    def _load_goals(self) -> list[dict]:
        goals = []
        if not self._goals_dir.exists():
            return goals
        for path in self._goals_dir.glob("*.json"):
            try:
                goals.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return goals

    def _save_goal(self, goal: dict) -> None:
        """Atomic write: tmp file + rename to prevent corruption from concurrent writes."""
        self._goals_dir.mkdir(parents=True, exist_ok=True)
        path = self._goals_dir / f"{goal['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(goal, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)  # atomic on same filesystem
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def get_goals(self) -> list[dict]:
        """Return all goals with status."""
        return self._load_goals()
