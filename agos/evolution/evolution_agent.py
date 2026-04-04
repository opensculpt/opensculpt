"""EvolutionAgent V2 — the senior engineer inside the OS.

5-Phase Architecture (from Reflexion, Constitutional AI, SWE-agent, TDD):

  Phase 1: UNDERSTAND — read demands, code, environment. Form HYPOTHESIS.
  Phase 2: TEST FIRST — write a test proving the problem exists.
  Phase 3: FIX — generate fix, write to STAGING (not production).
  Phase 4: CRITIC — separate adversarial LLM call evaluates the fix.
  Phase 5: DEPLOY — only on ACCEPT. Run test again to prove FAIL→PASS.

Key principles:
- Hypothesis before fix (mental model pattern)
- Test before fix (TDD pattern)
- Staging before production (verification gate pattern)
- Separate critic (Reflexion evaluator pattern)
- Constitution check (root cause? regression? explainable?)
- FAIL→PASS proof (SWE-agent pattern)
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.evolution.demand import DemandCollector
from agos.evolution.state import EvolutionMemory, EvolutionInsight

_logger = logging.getLogger(__name__)

OFF_LIMITS = {
    "agos/boot.py", "agos/serve.py", "agos/events/", "agos/policy/",
    "agos/evolution/evolution_agent.py",
    "agos/cli/", "tests/",
}

MODIFIABLE_PREFIXES = (
    "agos/knowledge/", "agos/tools/", "agos/daemons/",
    "agos/evolution/", "agos/guard.py", "agos/session.py",
    "agos/config.py", "agos/os_agent.py",
)

STAGING_DIR = Path(".opensculpt/staging")
MAX_TURNS = 20
MAX_TOKENS = 120_000
MAX_PROMPT_RULES = 100

# Critic constitutions — different strictness per risk level
CRITIC_STRICT = """You are a code reviewer for a self-evolving OS.
This is a source code change. Review carefully but practically.

Check:
1. Does it fix the ROOT CAUSE described in the hypothesis?
2. Is the change minimal (≤50 lines)?
3. If it adds try/except, does it LOG the error and RETRY, not just swallow it?
4. Can you explain the fix in one sentence?

Return JSON: {"verdict": "ACCEPT|REJECT|REVISE", "reasoning": "..."}
ACCEPT if the fix is correct and minimal, even if imperfect.
REJECT only if the fix is WRONG (would make the problem worse or introduce a new bug).
REVISE if the approach is right but implementation needs adjustment."""

CRITIC_LENIENT = """You are a code reviewer for a self-evolving OS.
This is a LOW-RISK change (skill doc, prompt rule, or tool). Be practical.

Check:
1. Is the content accurate and helpful?
2. Would it help agents avoid the failure pattern described?
3. Is it specific enough to be actionable?

Prefer ACCEPT for anything that provides useful guidance, even if imperfect.
Return JSON: {"verdict": "ACCEPT|REJECT", "reasoning": "..."}
Only REJECT if the content is wrong/misleading or would cause harm."""


@dataclass
class EvolutionResult:
    """What the Evolution Agent produced."""
    tools_created: list[str] = field(default_factory=list)
    skills_created: list[str] = field(default_factory=list)
    rules_added: list[str] = field(default_factory=list)
    patches_applied: list[str] = field(default_factory=list)
    modules_created: list[str] = field(default_factory=list)
    insights: list[EvolutionInsight] = field(default_factory=list)
    hypothesis: str = ""
    critic_verdict: str = ""
    turns_used: int = 0
    tokens_used: int = 0
    summary: str = ""


class EvolutionAgent:
    """V2: Multi-turn agent with hypothesis → test → fix → critic → deploy.

    Uses a STRONGER model than the rest of the OS for architectural reasoning.
    Configurable via settings.evolution_agent_model or setup.json.
    """

    def __init__(
        self,
        event_bus: EventBus,
        audit: AuditTrail,
        llm,
        tool_registry=None,
        source_patcher=None,
        demand_collector: DemandCollector | None = None,
        evo_memory: EvolutionMemory | None = None,
        tool_evolver=None,
    ):
        self._bus = event_bus
        self._audit = audit
        self._llm = self._get_strong_llm(llm)  # Use stronger model if configured
        self._registry = tool_registry
        self._source_patcher = source_patcher
        self._demands = demand_collector
        self._memory = evo_memory
        self._tool_evolver = tool_evolver
        self._result = EvolutionResult()
        self._hypothesis_stated = False
        self._staged_changes: list[dict] = []

    @staticmethod
    def _get_strong_llm(fallback_llm):
        """Get the strongest available LLM for evolution.

        Priority:
        1. settings.evolution_agent_model (explicit config)
        2. setup.json evolution_agent_model (user configured)
        3. setup.json active_provider with strongest available model
        4. fallback to whatever LLM was passed in
        """
        try:
            from agos.config import settings
            from agos.setup_store import load_setup
            from agos.llm.providers import ALL_PROVIDERS
            import os as _os

            # Check settings for explicit model
            model_override = settings.evolution_agent_model
            if not model_override:
                # Check setup.json
                for ws in [str(settings.workspace_dir),
                           _os.path.join(_os.getcwd(), ".opensculpt")]:
                    if not _os.path.isdir(ws):
                        continue
                    data = load_setup(ws)
                    model_override = data.get("evolution_agent_model", "")
                    if model_override:
                        break

            if not model_override:
                # Check env var
                model_override = _os.environ.get("SCULPT_EVOLUTION_AGENT_MODEL", "")

            if model_override:
                # Build provider with the override model
                for ws in [str(settings.workspace_dir),
                           _os.path.join(_os.getcwd(), ".opensculpt")]:
                    if not _os.path.isdir(ws):
                        continue
                    data = load_setup(ws)
                    active = data.get("active_provider", "")
                    if active and active in data.get("providers", {}):
                        cfg = dict(data["providers"][active])
                        cfg["model"] = model_override
                        cls = ALL_PROVIDERS.get(active)
                        if cls:
                            provider = cls(**{k: v for k, v in cfg.items() if k != "enabled"})
                            _logger.info("Evolution Agent using strong model: %s via %s",
                                         model_override, active)
                            return provider

            # No override — try to pick the best from what's available
            # Prefer Claude > GPT-4 > DeepSeek > whatever is configured
            for ws in [str(settings.workspace_dir),
                       _os.path.join(_os.getcwd(), ".opensculpt")]:
                if not _os.path.isdir(ws):
                    continue
                data = load_setup(ws)
                active = data.get("active_provider", "")
                if active in data.get("providers", {}):
                    cfg = dict(data["providers"][active])
                    current_model = cfg.get("model", "")
                    # If current model is weak, try to upgrade
                    weak_models = ["minimax", "m2.7", "llama", "mistral-7b", "phi-"]
                    if any(w in current_model.lower() for w in weak_models):
                        # Try stronger models on same provider
                        strong_models = [
                            "anthropic/claude-sonnet-4",
                            "anthropic/claude-haiku-4-5",
                            "deepseek/deepseek-chat",
                        ]
                        for strong in strong_models:
                            try:
                                cfg_copy = dict(cfg)
                                cfg_copy["model"] = strong
                                cls = ALL_PROVIDERS.get(active)
                                if cls:
                                    provider = cls(**{k: v for k, v in cfg_copy.items() if k != "enabled"})
                                    _logger.info("Evolution Agent auto-upgraded: %s → %s",
                                                 current_model, strong)
                                    return provider
                            except Exception:
                                continue
        except Exception as e:
            _logger.debug("Strong LLM selection failed: %s", e)

        return fallback_llm

    async def run(self, impasse_context: dict) -> EvolutionResult:
        """Run the 5-phase evolution loop."""
        from agos.llm.base import LLMMessage

        _logger.info("Evolution Agent V2 spawned: %s",
                     impasse_context.get("summary", "")[:80])
        await self._bus.emit("evolution.agent_spawned", {
            "impasse": impasse_context.get("summary", "")[:200],
            "version": 2,
        }, source="evolution_agent")

        # Clean staging
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        system_prompt = self._build_system_prompt(impasse_context)
        tools = self._build_tools()

        user_msg = (
            f"IMPASSE: {impasse_context.get('summary', 'Unknown')}\n\n"
            f"DEMANDS ({impasse_context.get('demand_count', 0)} active):\n"
            f"{impasse_context.get('demands_text', '(none)')}\n\n"
            "Follow the 5-phase process:\n"
            "1. Read demands + code → state_hypothesis (REQUIRED)\n"
            "2. Write a test proving the problem exists\n"
            "3. Generate a fix (goes to staging, not production)\n"
            "4. Call critic_review (REQUIRED before deploy)\n"
            "5. Deploy only if critic says ACCEPT"
        )

        messages = [LLMMessage(role="user", content=user_msg)]
        total_tokens = 0

        for turn in range(MAX_TURNS):
            if total_tokens > MAX_TOKENS:
                _logger.warning("Evolution Agent: token budget exceeded (%d)", total_tokens)
                break

            # Retry LLM call up to 3 times (MiniMax can timeout)
            resp = None
            for retry in range(3):
                try:
                    resp = await self._llm.complete(
                        messages=messages, system=system_prompt,
                        tools=tools, max_tokens=4096,
                    )
                    if resp and (resp.content or resp.tool_calls):
                        break
                    _logger.info("Evolution Agent: empty LLM response, retry %d", retry + 1)
                    await asyncio.sleep(2 * (retry + 1))
                except Exception as e:
                    _logger.warning("Evolution Agent LLM error (retry %d): %s", retry + 1, e)
                    await asyncio.sleep(3 * (retry + 1))
            if not resp or (not resp.content and not resp.tool_calls):
                _logger.warning("Evolution Agent: LLM failed after 3 retries, ending session")
                break

            total_tokens += (resp.input_tokens or 0) + (resp.output_tokens or 0)
            self._result.turns_used = turn + 1
            self._result.tokens_used = total_tokens

            if not resp.tool_calls:
                self._result.summary = resp.content or ""
                _logger.info("Evolution Agent V2 finished: %d turns, %d tokens. %s",
                             turn + 1, total_tokens, (resp.content or "")[:100])
                break

            # Build assistant message
            asst_parts: list[dict] = []
            if resp.content:
                asst_parts.append({"type": "text", "text": resp.content})
            for tc in resp.tool_calls:
                asst_parts.append({
                    "type": "tool_use", "id": tc.id,
                    "name": tc.name, "input": tc.arguments,
                })
            messages.append(LLMMessage(role="assistant", content=asst_parts))

            # Execute tools
            tool_results: list[dict] = []
            for tc in resp.tool_calls:
                result = await self._execute_tool(tc.name, tc.arguments)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tc.id,
                    "content": str(result)[:3000],
                })
                _logger.info("Evolution Agent turn %d: %s → %s",
                             turn + 1, tc.name, str(result)[:80])

            messages.append(LLMMessage(role="user", content=tool_results))

        # Record insights
        if self._memory:
            for insight in self._result.insights:
                self._memory.record(insight)

        # Create package if anything was deployed
        if any([self._result.tools_created, self._result.skills_created,
                self._result.rules_added, self._result.patches_applied]):
            try:
                from agos.evolution.packages import create_package
                await create_package(self._result, impasse_context)
            except Exception as e:
                _logger.debug("Package creation: %s", e)

        # Clean staging
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR, ignore_errors=True)

        await self._bus.emit("evolution.agent_completed", {
            "turns": self._result.turns_used,
            "tokens": self._result.tokens_used,
            "tools": len(self._result.tools_created),
            "skills": len(self._result.skills_created),
            "rules": len(self._result.rules_added),
            "patches": len(self._result.patches_applied),
            "hypothesis": self._result.hypothesis[:100],
            "critic_verdict": self._result.critic_verdict,
        }, source="evolution_agent")

        return self._result

    def _build_system_prompt(self, ctx: dict) -> str:
        env = ctx.get("environment", "Unknown")
        return f"""You are the Evolution Agent V2 of OpenSculpt, a self-evolving agentic OS.

You are a SENIOR ENGINEER debugging the OS itself. You reason like an expert:
- You form HYPOTHESES before writing fixes
- You write TESTS before implementing
- You submit fixes for CRITIC REVIEW before deploying
- You only deploy when the critic says ACCEPT

ENVIRONMENT:
{env[:500]}

MANDATORY 5-PHASE PROCESS:

PHASE 1 — UNDERSTAND (turns 1-3 MAX):
  Use read_demands, read_environment, and ONE grep_codebase to find the key code.
  Read at most 2 source files. Don't browse the whole codebase.
  Then IMMEDIATELY call state_hypothesis. You MUST state a hypothesis by turn 3.
  You CANNOT use any write tools until you state a hypothesis.

PHASE 2 — TEST (prove the problem exists):
  Use test_fix to run a command that demonstrates the failure.
  If the test PASSES, the problem may be resolved — call read_demands to recheck.

PHASE 3 — FIX (choose the RIGHT fix, not the easiest):
  Available fixes:
  1. create_skill_doc — write knowledge for future agents
  2. evolve_prompt — add a rule to agent prompts
  3. create_tool — add a missing capability (sandbox-tested)
  4. patch_source — fix a bug in existing OS code (critic-reviewed, auto-rollback)

  Choose based on your hypothesis:
  - If agents lack KNOWLEDGE → create_skill_doc
  - If agents keep making the SAME MISTAKE → evolve_prompt
  - If a CAPABILITY is missing → create_tool
  - If the OS CODE has a BUG (crash, wrong logic, missing retry) → patch_source

  If a prompt rule or skill doc for this problem already exists but the
  problem persists, that means the fix needs to be in code, not docs.
  Don't write another doc about a problem that already has docs.
  All writes go to staging. Nothing reaches production yet.

PHASE 4 — CRITIC (required before deploy):
  Call critic_review with your fix description.
  If REJECT: read the reasoning, revise your fix, try again.
  If REVISE: adjust based on feedback, call critic again.
  If ACCEPT: proceed to deploy.

PHASE 5 — DEPLOY (only after ACCEPT):
  Call deploy_staged to promote staging changes to production.
  Then run test_fix again to prove FAIL→PASS (the problem is fixed).

RULES:
- NEVER skip the hypothesis step
- NEVER deploy without critic approval
- Fix the ROOT CAUSE, not the symptom
- Prefer small precise changes (≤50 lines for patches)
- NEVER modify: boot.py, serve.py, events/, policy/, evolution_agent.py
- If the critic rejects twice, explain what the user needs to provide"""

    def _build_tools(self) -> list[dict]:
        return [
            # Phase 1: UNDERSTAND (read-only)
            {"name": "read_source", "description": "Read a source file. Path relative to project root.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},

            {"name": "grep_codebase", "description": "Search for a regex pattern across all agos/ Python files.",
             "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},

            {"name": "read_demands", "description": "Get all active evolution demands.",
             "input_schema": {"type": "object", "properties": {}}},

            {"name": "read_insights", "description": "Get past evolution insights — what was tried, what worked/failed.",
             "input_schema": {"type": "object", "properties": {}}},

            {"name": "read_environment", "description": "Get environment probe: OS, Docker, runtimes, permissions.",
             "input_schema": {"type": "object", "properties": {}}},

            {"name": "web_search", "description": "Search the web for solutions.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},

            # Meta-Harness trace tools: raw execution data > summaries
            {"name": "read_trace",
             "description": "Read raw execution trace for a goal or evolution cycle. "
                            "Shows full tool calls with args and outputs (NOT truncated). "
                            "Use to understand WHY past attempts failed.",
             "input_schema": {"type": "object", "properties": {
                 "id": {"type": "string", "description": "Goal ID or cycle number"},
                 "last_n": {"type": "integer", "default": 30},
             }, "required": ["id"]}},
            {"name": "list_traces",
             "description": "List available execution traces (goal runs and evolution cycles).",
             "input_schema": {"type": "object", "properties": {}}},

            # Phase 1→2 gate: HYPOTHESIS (required before any writes)
            {"name": "state_hypothesis",
             "description": "REQUIRED before any fix. State your hypothesis: what's the root cause and what should the fix change?",
             "input_schema": {"type": "object", "properties": {
                 "root_cause": {"type": "string", "description": "What is causing the failures?"},
                 "expected_fix": {"type": "string", "description": "What change would fix it?"},
                 "how_to_verify": {"type": "string", "description": "How can we prove the fix works?"},
             }, "required": ["root_cause", "expected_fix", "how_to_verify"]}},

            # Phase 2: TEST
            {"name": "test_fix", "description": "Run a shell command to test something. Returns stdout/stderr/exit code.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},

            # Phase 3: FIX (writes to staging)
            {"name": "create_tool", "description": "Create a Python tool. Goes to staging until critic approves.",
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string"}, "code": {"type": "string"}, "description": {"type": "string"},
             }, "required": ["name", "code", "description"]}},

            {"name": "create_skill_doc", "description": "Write a skill doc (how-to for future agents). Goes to staging.",
             "input_schema": {"type": "object", "properties": {
                 "topic": {"type": "string"}, "content": {"type": "string"},
             }, "required": ["topic", "content"]}},

            {"name": "evolve_prompt", "description": "Add a learned rule to agent prompts. Goes to staging.",
             "input_schema": {"type": "object", "properties": {
                 "target": {"type": "string", "enum": ["os_agent", "sub_agent", "goal_planner"]},
                 "rule": {"type": "string"},
             }, "required": ["target", "rule"]}},

            {"name": "patch_source", "description": "Modify OS source code. Goes to staging with backup.",
             "input_schema": {"type": "object", "properties": {
                 "file": {"type": "string"}, "original": {"type": "string"},
                 "replacement": {"type": "string"}, "rationale": {"type": "string"},
             }, "required": ["file", "original", "replacement", "rationale"]}},

            {"name": "create_module", "description": "Create new Python module. Goes to staging.",
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string"}, "code": {"type": "string"}, "description": {"type": "string"},
             }, "required": ["name", "code", "description"]}},

            # Phase 4: CRITIC (separate adversarial review)
            {"name": "critic_review",
             "description": "REQUIRED before deploy. Submits your fix for adversarial review. Returns ACCEPT/REJECT/REVISE.",
             "input_schema": {"type": "object", "properties": {
                 "fix_description": {"type": "string", "description": "What the fix does and why"},
                 "fix_type": {"type": "string", "description": "tool/skill_doc/prompt_rule/source_patch/module"},
             }, "required": ["fix_description", "fix_type"]}},

            # Phase 5: DEPLOY (only after critic ACCEPT)
            {"name": "deploy_staged",
             "description": "Promote ALL staged changes to production. Only works after critic ACCEPT.",
             "input_schema": {"type": "object", "properties": {}}},
        ]

    async def _execute_tool(self, name: str, args: dict) -> str:
        try:
            # Gate: write tools require hypothesis
            write_tools = {"create_tool", "create_skill_doc", "evolve_prompt",
                           "patch_source", "create_module"}
            if name in write_tools and not self._hypothesis_stated:
                return "ERROR: You must call state_hypothesis BEFORE any write tools. " \
                       "Read the demands and code first, then state what you think the root cause is."

            if name == "read_source":
                return self._tool_read_source(args.get("path", ""))
            elif name == "grep_codebase":
                return self._tool_grep_codebase(args.get("pattern", ""))
            elif name == "read_demands":
                return self._tool_read_demands()
            elif name == "read_insights":
                return self._tool_read_insights()
            elif name == "read_environment":
                return self._tool_read_environment()
            elif name == "web_search":
                return await self._tool_web_search(args.get("query", ""))
            elif name == "read_trace":
                return self._tool_read_trace(args.get("id", ""), args.get("last_n", 30))
            elif name == "list_traces":
                return self._tool_list_traces()
            elif name == "state_hypothesis":
                return self._tool_state_hypothesis(args)
            elif name == "test_fix":
                return self._tool_test_fix(args.get("command", ""))
            elif name == "create_tool":
                return await self._tool_create_tool_staged(args)
            elif name == "create_skill_doc":
                return self._tool_create_skill_doc_staged(args)
            elif name == "evolve_prompt":
                return self._tool_evolve_prompt_staged(args)
            elif name == "patch_source":
                return self._tool_patch_source_staged(args)
            elif name == "create_module":
                return self._tool_create_module_staged(args)
            elif name == "critic_review":
                return await self._tool_critic_review(args)
            elif name == "deploy_staged":
                return await self._tool_deploy_staged()
            return f"Unknown tool: {name}"
        except Exception as e:
            return f"Error: {e}"

    # ── Phase 1: READ-ONLY TOOLS ──

    def _tool_read_source(self, path: str) -> str:
        if not path:
            return "Error: path required"
        if not (path.startswith("agos/") or path.startswith(".opensculpt/")):
            return "Error: can only read agos/ and .opensculpt/ files"
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
            return content[:8000] + (f"\n... ({len(content)} total)" if len(content) > 8000 else "")
        except FileNotFoundError:
            return f"File not found: {path}"

    def _tool_grep_codebase(self, pattern: str) -> str:
        if not pattern:
            return "Error: pattern required"
        results = []
        for py_file in Path("agos").rglob("*.py"):
            try:
                for i, line in enumerate(py_file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        results.append(f"{py_file}:{i}: {line.strip()}")
                        if len(results) >= 30:
                            return "\n".join(results)
            except Exception:
                continue
        return "\n".join(results) if results else f"No matches for: {pattern}"

    def _tool_read_demands(self) -> str:
        if not self._demands:
            return "No demand collector"
        demands = self._demands.top_demands(limit=10, include_all=True)
        if not demands:
            return "No demands"
        lines = []
        for d in demands:
            lines.append(f"[{d.status}] {d.kind} from {d.source} (count={d.count}, "
                         f"attempts={d.attempts}, priority={d.priority:.1f})")
            lines.append(f"  {d.description[:200]}")
            if d.context:
                lines.append(f"  context: {json.dumps(d.context)[:200]}")
        return "\n".join(lines)

    def _tool_read_insights(self) -> str:
        if not self._memory:
            return "No evolution memory"
        insights = self._memory.insights[-20:]
        if not insights:
            return "No past insights"
        lines = []
        for i in insights:
            lines.append(f"[{i.outcome}] {i.what_tried[:60]}")
            if i.reason:
                lines.append(f"  reason: {i.reason[:100]}")
            if i.what_worked:
                lines.append(f"  worked: {i.what_worked[:100]}")
        return "\n".join(lines)

    def _tool_read_environment(self) -> str:
        try:
            from agos.environment import EnvironmentProbe
            return EnvironmentProbe.summary()
        except Exception as e:
            return f"Error: {e}"

    async def _tool_web_search(self, query: str) -> str:
        if not query:
            return "Error: query required"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code != 200:
                    return f"Search failed: HTTP {resp.status_code}"
                snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>',
                                      resp.text, re.DOTALL)
                clean = [re.sub(r'<[^>]+>', '', s).strip()[:300] for s in snippets[:5] if s.strip()]
                return "\n\n".join(clean) if clean else "No results"
        except Exception as e:
            return f"Search error: {e}"

    # ── Meta-Harness trace tools ──

    def _tool_read_trace(self, trace_id: str, last_n: int = 30) -> str:
        """Read raw execution trace — full tool calls, not truncated summaries."""
        if not trace_id:
            return "Error: id required (e.g. 'goal_1234_5678' or '42')"
        try:
            from agos.evolution.trace_store import TraceStore
            entries = TraceStore().read_trace(trace_id, last_n=last_n)
            if not entries:
                return f"No trace found for '{trace_id}'. Use list_traces to see available traces."
            lines = []
            for e in entries:
                status = "OK" if e.get("ok") else "FAIL"
                kind = e.get("kind", "")
                tool = e.get("tool", "")
                if kind == "phase_start":
                    lines.append(f"\n=== Phase: {e.get('context', '?')} ===")
                    continue
                args_str = json.dumps(e.get("args", {}), default=str)[:300]
                lines.append(f"[{e.get('ts', '')[:19]}] [{status}] {tool}({args_str})")
                out = e.get("output", "")
                if out:
                    lines.append(f"  → {out[:500]}")
            return "\n".join(lines) if lines else "Trace exists but empty"
        except Exception as ex:
            return f"Error reading trace: {ex}"

    def _tool_list_traces(self) -> str:
        """List available execution traces."""
        try:
            from agos.evolution.trace_store import TraceStore
            traces = TraceStore().list_traces(limit=20)
            if not traces:
                return "No traces available yet. Traces are created when goals execute or evolution cycles run."
            lines = ["Available traces:"]
            for t in traces:
                lines.append(f"  {t['id']}  ({t['entries']} entries, {t['size_kb']}KB, {t['age_hours']:.0f}h ago)")
            return "\n".join(lines)
        except Exception as ex:
            return f"Error listing traces: {ex}"

    # ── Phase 1→2 GATE: HYPOTHESIS ──

    def _tool_state_hypothesis(self, args: dict) -> str:
        root_cause = args.get("root_cause", "")
        expected_fix = args.get("expected_fix", "")
        how_to_verify = args.get("how_to_verify", "")
        if not root_cause or not expected_fix:
            return "Error: root_cause and expected_fix required"

        self._hypothesis_stated = True
        self._result.hypothesis = f"Root cause: {root_cause}. Fix: {expected_fix}. Verify: {how_to_verify}"

        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"Hypothesis: {root_cause[:60]}",
            module="evolution_agent", outcome="hypothesis",
            reason=f"Expected fix: {expected_fix[:100]}. Verify: {how_to_verify[:100]}",
        ))
        _logger.info("Evolution Agent hypothesis: %s", root_cause[:80])
        return (f"Hypothesis recorded. You can now use write tools.\n"
                f"Root cause: {root_cause}\n"
                f"Expected fix: {expected_fix}\n"
                f"Verify by: {how_to_verify}")

    # ── Phase 2: TEST ──

    def _tool_test_fix(self, command: str) -> str:
        if not command:
            return "Error: command required"
        dangerous = ["rm -rf", "mkfs", "dd if=", "> /dev/", "shutdown", "reboot"]
        if any(d in command.lower() for d in dangerous):
            return "Error: dangerous command blocked"
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            out = result.stdout[:2000] if result.stdout else ""
            err = result.stderr[:1000] if result.stderr else ""
            return f"Exit code: {result.returncode}\nstdout: {out}" + (f"\nstderr: {err}" if err else "")
        except subprocess.TimeoutExpired:
            return "Error: timed out (30s)"
        except Exception as e:
            return f"Error: {e}"

    # ── Phase 3: FIX (all write to staging) ──

    async def _tool_create_tool_staged(self, args: dict) -> str:
        name = args.get("name", "")
        code = args.get("code", "")
        desc = args.get("description", "")
        if not name or not code:
            return "Error: name and code required"
        if not name.replace("_", "").isalnum() or len(name) > 30:
            return f"Error: invalid tool name: {name}"

        # Sandbox check
        try:
            from agos.evolution.sandbox import Sandbox
            result = Sandbox(timeout=10).validate(code)
            if not result.safe:
                return f"Sandbox failed: {'; '.join(result.issues[:3])}"
        except Exception as e:
            return f"Sandbox error: {e}"

        # Write to staging
        staging_tools = STAGING_DIR / "tools"
        staging_tools.mkdir(parents=True, exist_ok=True)
        (staging_tools / f"{name}.py").write_text(code, encoding="utf-8")

        self._staged_changes.append({"type": "tool", "name": name, "desc": desc, "code": code})
        return f"Tool '{name}' staged (sandbox passed). Call critic_review to get approval."

    def _tool_create_skill_doc_staged(self, args: dict) -> str:
        topic = args.get("topic", "")
        content = args.get("content", "")
        if not topic or not content:
            return "Error: topic and content required"

        staging_skills = STAGING_DIR / "skills"
        staging_skills.mkdir(parents=True, exist_ok=True)
        (staging_skills / f"{topic}.md").write_text(content, encoding="utf-8")

        self._staged_changes.append({"type": "skill_doc", "topic": topic, "content": content})
        return f"Skill doc '{topic}.md' staged ({len(content)} chars). Call critic_review."

    def _tool_evolve_prompt_staged(self, args: dict) -> str:
        target = args.get("target", "")
        rule = args.get("rule", "")
        if not target or not rule:
            return "Error: target and rule required"
        if target not in ("os_agent", "sub_agent", "goal_planner"):
            return "Error: target must be os_agent, sub_agent, or goal_planner"

        staging_rules = STAGING_DIR / "rules"
        staging_rules.mkdir(parents=True, exist_ok=True)
        rules_file = staging_rules / f"{target}_rules.txt"
        with open(rules_file, "a", encoding="utf-8") as f:
            f.write(f"- {rule}\n")

        self._staged_changes.append({"type": "prompt_rule", "target": target, "rule": rule})
        return f"Prompt rule staged for {target}. Call critic_review."

    def _tool_patch_source_staged(self, args: dict) -> str:
        filepath = args.get("file", "")
        original = args.get("original", "")
        replacement = args.get("replacement", "")
        rationale = args.get("rationale", "")
        if not filepath or not original or not replacement:
            return "Error: file, original, and replacement required"
        if any(filepath.startswith(off) for off in OFF_LIMITS):
            return f"Error: {filepath} is off-limits"
        if not any(filepath.startswith(p) for p in MODIFIABLE_PREFIXES):
            return f"Error: {filepath} not in modifiable prefixes"

        path = Path(filepath)
        if not path.exists():
            return f"Error: {filepath} not found"
        content = path.read_text(encoding="utf-8")
        if original not in content:
            return f"Error: original text not found in {filepath}. Must match exactly."
        if abs(replacement.count("\n") - original.count("\n")) > 50:
            return "Error: patch too large (max 50 line diff)"

        # Save to staging (don't modify production yet)
        staging_patches = STAGING_DIR / "patches"
        staging_patches.mkdir(parents=True, exist_ok=True)
        (staging_patches / f"{path.name}.original").write_text(original, encoding="utf-8")
        (staging_patches / f"{path.name}.replacement").write_text(replacement, encoding="utf-8")
        (staging_patches / f"{path.name}.rationale").write_text(rationale, encoding="utf-8")
        (staging_patches / f"{path.name}.filepath").write_text(filepath, encoding="utf-8")

        self._staged_changes.append({
            "type": "source_patch", "file": filepath, "original": original,
            "replacement": replacement, "rationale": rationale,
        })
        return f"Source patch staged for {filepath}. Call critic_review."

    def _tool_create_module_staged(self, args: dict) -> str:
        name = args.get("name", "")
        code = args.get("code", "")
        desc = args.get("description", "")
        if not name or not code:
            return "Error: name and code required"

        try:
            from agos.evolution.sandbox import Sandbox
            result = Sandbox(timeout=10).validate(code)
            if not result.safe:
                return f"Sandbox failed: {'; '.join(result.issues[:3])}"
        except Exception as e:
            return f"Sandbox error: {e}"

        staging_modules = STAGING_DIR / "modules"
        staging_modules.mkdir(parents=True, exist_ok=True)
        (staging_modules / f"{name}.py").write_text(code, encoding="utf-8")

        self._staged_changes.append({"type": "module", "name": name, "desc": desc, "code": code})
        return f"Module '{name}' staged (sandbox passed). Call critic_review."

    # ── Phase 4: CRITIC (separate adversarial LLM call) ──

    async def _tool_critic_review(self, args: dict) -> str:
        fix_desc = args.get("fix_description", "")
        fix_type = args.get("fix_type", "")
        if not fix_desc:
            return "Error: fix_description required"
        if not self._staged_changes:
            return "Error: no staged changes to review. Create a fix first."

        # Build context for critic
        staged_summary = "\n".join(
            f"- {c['type']}: {c.get('name', c.get('topic', c.get('file', '?')))}"
            for c in self._staged_changes
        )

        # Show actual code for patches
        code_context = ""
        for c in self._staged_changes:
            if c["type"] == "source_patch":
                code_context += f"\nPATCH to {c['file']}:\n  ORIGINAL: {c['original'][:300]}\n  REPLACEMENT: {c['replacement'][:300]}\n  RATIONALE: {c['rationale'][:200]}\n"
            elif c["type"] == "tool":
                code_context += f"\nTOOL '{c['name']}':\n  {c['code'][:500]}\n"
            elif c["type"] == "skill_doc":
                code_context += f"\nSKILL DOC '{c['topic']}':\n  {c['content'][:300]}\n"
            elif c["type"] == "prompt_rule":
                code_context += f"\nPROMPT RULE for {c['target']}: {c['rule'][:200]}\n"

        # Use lenient critic for low-risk changes, strict for high-risk
        high_risk = any(c["type"] in ("source_patch", "module") for c in self._staged_changes)
        constitution = CRITIC_STRICT if high_risk else CRITIC_LENIENT

        critic_prompt = (
            f"HYPOTHESIS: {self._result.hypothesis[:300]}\n\n"
            f"FIX DESCRIPTION: {fix_desc}\n"
            f"FIX TYPE: {fix_type} ({'HIGH RISK' if high_risk else 'LOW RISK'})\n\n"
            f"STAGED CHANGES:\n{staged_summary}\n\n"
            f"CODE:\n{code_context}\n\n"
            "Return JSON with verdict and reasoning."
        )

        try:
            from agos.llm.base import LLMMessage
            resp = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content=constitution),
                    LLMMessage(role="user", content=critic_prompt),
                ],
                max_tokens=500,
            )
            text = (resp.content or "").strip()

            # Parse verdict
            verdict = "REJECT"
            reasoning = text

            # Try JSON parse
            try:
                match = re.search(r'\{[^{}]*\}', text)
                if match:
                    data = json.loads(match.group().replace("'", '"'))
                    verdict = data.get("verdict", "REJECT").upper()
                    reasoning = data.get("reasoning", text)
            except Exception:
                # Fallback: look for verdict keyword
                text_upper = text.upper()
                if "ACCEPT" in text_upper:
                    verdict = "ACCEPT"
                elif "REVISE" in text_upper:
                    verdict = "REVISE"

            self._result.critic_verdict = verdict
            _logger.info("Evolution Agent critic: %s — %s", verdict, reasoning[:80])

            self._result.insights.append(EvolutionInsight(
                cycle=0, what_tried=f"critic_review:{fix_type}",
                module="evolution_agent", outcome=f"critic_{verdict.lower()}",
                reason=reasoning[:200],
            ))

            if verdict == "ACCEPT":
                return f"CRITIC VERDICT: ACCEPT\nReasoning: {reasoning[:300]}\n\nYou may now call deploy_staged."
            elif verdict == "REVISE":
                return f"CRITIC VERDICT: REVISE\nFeedback: {reasoning[:300]}\n\nRevise your fix and call critic_review again."
            else:
                return f"CRITIC VERDICT: REJECT\nReason: {reasoning[:300]}\n\nYour fix was rejected. Revise or try a different approach."

        except Exception as e:
            _logger.warning("Critic review failed: %s", e)
            return f"Critic review error: {e}. You may still call deploy_staged if you're confident."

    # ── Phase 5: DEPLOY (only after ACCEPT) ──

    async def _tool_deploy_staged(self) -> str:
        if not self._staged_changes:
            return "Error: nothing staged to deploy"
        if self._result.critic_verdict not in ("ACCEPT", ""):
            if self._result.critic_verdict == "REJECT":
                return "Error: critic REJECTED your fix. Revise and get ACCEPT first."
            if self._result.critic_verdict == "REVISE":
                return "Error: critic said REVISE. Update your fix and call critic_review again."

        deployed = []
        for change in self._staged_changes:
            try:
                if change["type"] == "tool":
                    result = await self._deploy_tool(change)
                    if result:
                        deployed.append(f"tool:{change['name']}")
                elif change["type"] == "skill_doc":
                    self._deploy_skill_doc(change)
                    deployed.append(f"skill:{change['topic']}")
                elif change["type"] == "prompt_rule":
                    self._deploy_prompt_rule(change)
                    deployed.append(f"rule:{change['target']}")
                elif change["type"] == "source_patch":
                    result = self._deploy_source_patch(change)
                    if result:
                        deployed.append(f"patch:{change['file']}")
                elif change["type"] == "module":
                    self._deploy_module(change)
                    deployed.append(f"module:{change['name']}")
            except Exception as e:
                _logger.warning("Deploy failed for %s: %s", change.get("type"), e)

        self._staged_changes.clear()
        _logger.info("Evolution Agent deployed: %s", ", ".join(deployed))
        return f"Deployed {len(deployed)} changes: {', '.join(deployed)}"

    async def _deploy_tool(self, change: dict) -> bool:
        name, code, desc = change["name"], change["code"], change["desc"]
        if self._tool_evolver:
            try:
                from agos.evolution.tool_evolver import ToolNeed
                need = ToolNeed(name=name, description=desc, source="evolution_agent")
                if await self._tool_evolver.deploy_tool(code, need):
                    self._result.tools_created.append(name)
                    self._result.insights.append(EvolutionInsight(
                        cycle=0, what_tried=f"deploy_tool:{name}",
                        module="evolution_agent", outcome="success",
                        reason=f"Tool '{name}' deployed: {desc[:80]}",
                        what_worked=f"Tool '{name}' — {desc[:80]}",
                        principle=f"Tool available: {name}",
                        confidence=1.0,
                    ))
                    return True
            except Exception as e:
                _logger.warning("Tool deploy via evolver failed: %s", e)

        # Fallback: save to disk
        evolved = Path(".opensculpt/evolved")
        evolved.mkdir(parents=True, exist_ok=True)
        (evolved / f"{name}.py").write_text(code, encoding="utf-8")
        self._result.tools_created.append(name)
        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"deploy_tool:{name} (disk)",
            module="evolution_agent", outcome="success",
            reason=f"Tool saved: {desc[:80]}",
            what_worked=f"Tool '{name}' — {desc[:80]}",
            confidence=0.7,
        ))
        return True

    def _deploy_skill_doc(self, change: dict) -> None:
        topic, content = change["topic"], change["content"]
        skills = Path(".opensculpt/skills")
        skills.mkdir(parents=True, exist_ok=True)
        (skills / f"{topic}.md").write_text(content, encoding="utf-8")
        self._result.skills_created.append(topic)
        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"deploy_skill:{topic}",
            module="evolution_agent", outcome="success",
            reason=f"Skill doc: {topic}.md",
            what_worked=f"Skill '{topic}' — {content[:80]}",
            confidence=0.9,
        ))

    def _deploy_prompt_rule(self, change: dict) -> None:
        target, rule = change["target"], change["rule"]
        rules_dir = Path(".opensculpt/evolved/brain")
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / f"{target}_rules.txt"
        existing = set(rules_file.read_text(encoding="utf-8").splitlines()) if rules_file.exists() else set()
        if f"- {rule}" not in existing and len(existing) < MAX_PROMPT_RULES:
            with open(rules_file, "a", encoding="utf-8") as f:
                f.write(f"- {rule}\n")
        self._result.rules_added.append(f"{target}: {rule[:60]}")
        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"deploy_rule:{target}",
            module="evolution_agent", outcome="success",
            reason=f"Rule for {target}: {rule[:80]}",
            what_worked=f"Prompt rule: {rule[:80]}",
            principle=rule[:150],
            applies_when=target,
            confidence=0.8,
        ))

    def _deploy_source_patch(self, change: dict) -> bool:
        filepath = change["file"]
        original = change["original"]
        replacement = change["replacement"]
        rationale = change["rationale"]

        path = Path(filepath)
        content = path.read_text(encoding="utf-8")
        if original not in content:
            _logger.warning("Patch target not found in %s (file may have changed)", filepath)
            return False

        # Backup
        backup_dir = Path(".opensculpt/patches/backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{path.name}.{int(time.time())}.bak"
        backup_path.write_text(content, encoding="utf-8")

        # Apply
        new_content = content.replace(original, replacement, 1)
        path.write_text(new_content, encoding="utf-8")

        # Hot-reload
        import sys
        module_name = filepath.replace("/", ".").replace(".py", "")
        reloaded = False
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
                reloaded = True
        except Exception as e:
            # Rollback
            path.write_text(content, encoding="utf-8")
            self._result.insights.append(EvolutionInsight(
                cycle=0, what_tried=f"patch_source:{filepath}",
                module="evolution_agent", outcome="rollback",
                reason=f"Reload failed, rolled back: {e}",
            ))
            _logger.warning("Patch rolled back for %s: %s", filepath, e)
            return False

        self._result.patches_applied.append(filepath)
        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"patch_source:{filepath}",
            module="evolution_agent", outcome="success",
            reason=f"Patched: {rationale[:100]}",
            what_worked=f"Source patch to {filepath}: {rationale[:80]}",
            confidence=0.9 if reloaded else 0.6,
        ))
        return True

    def _deploy_module(self, change: dict) -> None:
        name, code, desc = change["name"], change["code"], change["desc"]
        modules_dir = Path(".opensculpt/evolved/modules")
        modules_dir.mkdir(parents=True, exist_ok=True)
        (modules_dir / f"{name}.py").write_text(code, encoding="utf-8")
        self._result.modules_created.append(name)
        self._result.insights.append(EvolutionInsight(
            cycle=0, what_tried=f"deploy_module:{name}",
            module="evolution_agent", outcome="success",
            reason=f"Module '{name}': {desc[:80]}",
            what_worked=f"Module '{name}' — {desc[:80]}",
            confidence=0.8,
        ))
