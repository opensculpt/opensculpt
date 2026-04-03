"""OS Agent - the agentic brain of OpenSculpt.

The single entry point for ALL user interaction. Uses Claude to reason
about ANY request, then executes it using tools and sub-agents.

The OS agent can:
- Run any shell command, install any package, write any file
- Spawn sub-agents for specialized work (security, code analysis, etc.)
- Build entire applications from scratch
- Debug, fix, deploy - anything a senior engineer can do
- Manage the system: processes, resources, networking

Everything else in OpenSculpt is a sub-agent or subsystem that the OS agent
can call on when needed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

_logger = logging.getLogger(__name__)

from agos.llm.base import BaseLLMProvider, LLMMessage  # noqa: E402
from agos.tools.schema import ToolSchema, ToolParameter  # noqa: E402
from agos.tools.registry import ToolRegistry  # noqa: E402
from agos.events.bus import EventBus  # noqa: E402
from agos.policy.audit import AuditTrail, AuditEntry  # noqa: E402
from agos.guard import LoopGuard, CapabilityGate  # noqa: E402
from agos.session import SessionCompactor  # noqa: E402
from agos.knowledge.working import WorkingMemory  # noqa: E402

MAX_TURNS = 40
MAX_TOKENS = 200_000

# ── Prompt Cache Boundary (Claude Code pattern) ──────────────────
# Split system prompt into STATIC prefix (cacheable, identical across turns)
# and DYNAMIC suffix (changes per turn: live state, memory, rules).
# Anthropic prompt caching gives 90% savings on the static prefix.
# The boundary marker tells the LLM provider where to set cache_control.

CACHE_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

SYSTEM_PROMPT_STATIC = """\
You are OpenSculpt, an agentic operating system.

HOW YOU WORK:
- EVERY task uses set_goal. Even simple ones like "deploy Redis" become a goal with phases.
  This ensures every resource is tracked, every action is verified, and everything can be undone.
- check_goals FIRST. If a goal exists for this task, report its status. Don't duplicate.
- The GoalRunner executes ONE phase at a time via a sub-agent. Sequential. No parallelism.
- You ONLY answer questions directly. Everything else → set_goal.

TOOLS:
- set_goal: YOUR ONLY TOOL for doing work. Creates a tracked, sequential plan.
- check_goals: Check status of all goals and their phases.

For questions/lookups ONLY (no side effects):
- shell / http / docker_ps / docker_logs / browse: Read-only checks.

NEVER use spawn_agent directly. ALL work goes through set_goal.

RULES:
1. EVERY action that creates, modifies, or deploys anything → set_goal. No exceptions.
2. check_goals first. Don't duplicate goals.
3. Use sensible TECHNICAL defaults (which database, port, framework). Don't ask about these.
4. BUT ask the user for BUSINESS context they would know:
   - Company/project name (for branding, templates)
   - Categories/stages they need (ticket types, sales pipeline stages)
   - Existing data to import (CSV, database, other system)
   Ask these in your FIRST response, then start the goal immediately with defaults.
   The user can answer while infrastructure deploys.
5. When a phase FAILS: tell the user what went wrong and what they can do to help.
6. When all phases COMPLETE: tell the user WHERE the result is (URL, port),
   HOW to use it (sample commands, login credentials), and WHAT to do next.
7. Be concise. Status table for goals. 1-2 sentences for questions."""

# Dynamic suffix — rebuilt per turn (live state, knowledge, evolved rules)
SYSTEM_PROMPT_DYNAMIC = """

LIVE OS STATE:
{context}"""

# Combined for backward compat
SYSTEM_PROMPT = SYSTEM_PROMPT_STATIC + SYSTEM_PROMPT_DYNAMIC


class OSAgent:
    """The brain. Handles ANY request via Claude + tools + sub-agents."""

    def __init__(
        self,
        event_bus: EventBus,
        audit_trail: AuditTrail,
        agent_registry=None,
        process_manager=None,
        policy_engine=None,
        llm: BaseLLMProvider | None = None,
        approval_gate=None,
        sandbox_config=None,
    ) -> None:
        self._bus = event_bus
        self._audit = audit_trail
        self._registry = agent_registry
        self._pm = process_manager
        self._policy = policy_engine
        self._llm = llm
        self._approval = approval_gate
        self._inner_registry = ToolRegistry()
        self._start_time = time.time()
        self._sub_agents: dict[str, dict] = {}
        self._daemon_manager: Any = None
        self._cheap_llm: BaseLLMProvider | None = None
        self._loom: Any = None  # Knowledge system (TheLoom)
        self._intent_engine: Any = None  # Intent classification engine
        self._pattern_registry: Any = None  # Evolvable design pattern registry
        self._resource_registry: Any = None  # Linux-style resource tracking
        # Loop guard - detect and break infinite tool call loops (from OpenFang)
        self._loop_guard = LoopGuard()
        # Capability gate - tool-level permissions (from OpenFang)
        self._capability_gate = CapabilityGate()
        self._capability_gate.grant_all("os_agent")  # Main agent gets everything
        # Session compaction - summarize old context instead of truncating (from OpenClaw)
        self._compactor = SessionCompactor(max_messages=20, compact_to=5)
        # Working memory - active context for current task (the "desk" metaphor)
        self._working_memory = WorkingMemory(capacity=20)
        # Conversation memory - keeps recent exchanges for context
        self._conversation_history: list[dict] = []  # [{command, response, ts}]
        self._max_history = 30  # Higher limit - compactor handles overflow
        # Response cache - skip LLM for identical repeated queries
        self._response_cache: dict[str, str] = {}
        self._max_cache = 100
        # Session token tracking
        self._session_tokens = 0
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        self._session_requests = 0
        self._session_cost_usd = 0.0
        self._lifetime_cost = self._load_lifetime_cost()
        self._register_tools()

        # Wrap with sandbox if configured
        if sandbox_config is not None:
            from agos.sandbox.executor import SandboxedToolExecutor
            self._tools = SandboxedToolExecutor(
                inner_registry=self._inner_registry,
                config=sandbox_config,
            )
        else:
            self._tools = self._inner_registry

    def get_cheap_llm(self) -> BaseLLMProvider | None:
        """Get a cheap/fast LLM for simple tasks (diagnosis, classification, feedback).

        Cost-aware model routing: use the cheapest available model for tasks
        that don't need strong reasoning. Falls back to the main LLM.
        """
        if self._cheap_llm:
            return self._cheap_llm
        return self._llm

    def set_llm(self, llm: BaseLLMProvider) -> None:
        self._llm = llm
        self._cheap_llm: BaseLLMProvider | None = None
        # Try to create a cheap model for simple tasks
        try:
            from agos.setup_store import load_setup
            from pathlib import Path
            from agos.config import settings
            data = load_setup(Path(settings.workspace_dir))
            provider_name = data.get("active_provider", "")
            provider_cfg = data.get("providers", {}).get(provider_name, {})
            api_key = provider_cfg.get("api_key", "")
            if api_key and provider_name == "anthropic":
                from agos.llm.anthropic import AnthropicProvider
                self._cheap_llm = AnthropicProvider(api_key=api_key, model="anthropic/claude-haiku-4-5")
            elif api_key and provider_name == "openrouter":
                from agos.llm.providers import OpenRouterProvider
                self._cheap_llm = OpenRouterProvider(api_key=api_key, model="anthropic/claude-haiku-4-5")
        except Exception:
            pass  # No cheap LLM available — use main model
        # Propagate to DaemonManager so DomainDaemons can reason
        if self._daemon_manager:
            self._daemon_manager.set_llm(llm)

    def set_loom(self, loom: Any) -> None:
        """Set the knowledge system and wire up the Learner for auto-learning."""
        self._loom = loom
        # Wire the Learner so every interaction gets recorded to all 3 weaves
        try:
            from agos.knowledge.learner import Learner
            self._learner = Learner(
                episodic=loom._episodic,
                semantic=loom._semantic,
                graph=loom._graph,
            )
        except Exception:
            self._learner = None
        # Propagate to DaemonManager so DomainDaemons get TheLoom access
        if self._daemon_manager:
            self._daemon_manager.set_loom(loom)

    def set_intent_engine(self, engine: Any) -> None:
        """Set the intent engine for command classification and pattern selection."""
        self._intent_engine = engine

    def set_pattern_registry(self, registry: Any) -> None:
        """Set the evolvable pattern registry for design pattern selection."""
        self._pattern_registry = registry

    def set_resource_registry(self, registry: Any) -> None:
        """Set the resource registry for tracking deployed containers, files, etc."""
        self._resource_registry = registry

    def set_daemon_manager(self, hm: Any) -> None:
        self._daemon_manager = hm
        # Wire goal runner to this OS agent
        goal_runner = hm.get_goal_runner()
        if goal_runner:
            goal_runner.set_os_agent(self)
            goal_runner.set_daemon_manager(hm)
        # Wire knowledge + LLM so DomainDaemons can use them
        if self._loom:
            hm.set_loom(self._loom)
        if self._llm:
            hm.set_llm(self._llm)
        # Register hand tools now that the manager is available
        T, P = ToolSchema, ToolParameter
        self._inner_registry.register(T(
            name="set_goal",
            description=(
                "Set a HIGH-LEVEL GOAL for the OS. The OS will autonomously work toward it "
                "across multiple sessions - installing software, creating data, scheduling "
                "monitoring, and evolving its approach. Use this for big asks like "
                "'handle sales for my startup' or 'set up customer support'. "
                "The OS breaks it into phases and executes them over time."
            ),
            parameters=[
                P(name="goal", description="The high-level goal (e.g. 'Handle sales for my startup')"),
                P(name="category", description="Optional category: sales, support, devops, knowledge", required=False),
            ],
        ), self._set_goal)
        self._inner_registry.register(T(
            name="check_goals",
            description="Check status of all active goals and their phases.",
            parameters=[],
        ), self._check_goals)
        self._inner_registry.register(T(
            name="start_daemon",
            description=(
                "Start an autonomous background hand. Available daemons: "
                "'researcher' (searches arxiv, compiles reports on any topic), "
                "'monitor' (watches URLs/services, alerts on downtime), "
                "'digest' (periodic activity summaries), "
                "'scheduler' (run commands on a schedule). "
                "Use config to pass parameters like topic, urls, command, interval."
            ),
            parameters=[
                P(name="name", description="Daemon name: researcher, monitor, digest, scheduler"),
                P(name="config", description="JSON config string, e.g. {\"topic\": \"AI ethics\"} for researcher, {\"urls\": [\"http://...\"]} for monitor", required=False),
            ],
        ), self._start_daemon)
        self._inner_registry.register(T(
            name="stop_daemon",
            description="Stop a running background hand.",
            parameters=[P(name="name", description="Daemon name to stop")],
        ), self._stop_daemon)
        self._inner_registry.register(T(
            name="daemon_results",
            description="Get recent results/output from a background hand.",
            parameters=[P(name="name", description="Daemon name")],
        ), self._daemon_results)

    async def _try_auto_load_llm(self) -> None:
        """Auto-load LLM from setup.json.

        Uses whichever provider the user explicitly enabled in the Settings tab.
        The 'active_provider' key (if set) takes priority. Otherwise tries
        all enabled providers in the order they appear.
        """
        import os as _os
        try:
            from agos.setup_store import load_setup
            from agos.llm.providers import ALL_PROVIDERS

            for ws in [_os.path.join(_os.getcwd(), ".opensculpt"), _os.path.join(_os.path.expanduser("~"), ".opensculpt"),
                       _os.path.join(_os.getcwd(), ".agos"), _os.path.join(_os.path.expanduser("~"), ".agos")]:
                if not _os.path.isdir(ws):
                    continue
                data = load_setup(ws)
                providers = data.get("providers", {})

                # If user explicitly set a provider via Settings tab, use that first
                active = data.get("active_provider", "")
                if active and active in providers:
                    cfg = providers[active]
                    if self._load_provider(active, cfg, ALL_PROVIDERS):
                        return

                # Otherwise try all enabled providers
                for name, cfg in providers.items():
                    if not cfg.get("enabled", False):
                        continue
                    if self._load_provider(name, cfg, ALL_PROVIDERS):
                        return
        except Exception:
            pass

    def _load_provider(self, name: str, cfg: dict, all_providers: dict) -> bool:
        """Try to load a single LLM provider. Returns True on success."""
        # Anthropic shortcut - needs special provider class
        if name == "anthropic" and cfg.get("api_key"):
            from agos.llm.anthropic import AnthropicProvider
            try:
                self._llm = AnthropicProvider(
                    api_key=cfg["api_key"],
                    model=cfg.get("model", "claude-haiku-4-5-20251001"),
                )
                return True
            except Exception:
                return False
        cls = all_providers.get(name)
        if not cls:
            return False
        kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
        try:
            self._llm = cls(**kwargs)
            return True
        except Exception:
            return False

    def _build_os_context(self) -> str:
        """Gather live OS state so the LLM can reason about OpenSculpt."""
        import os as _os
        parts = []
        uptime = int(time.time() - self._start_time)
        parts.append(f"UPTIME: {uptime}s")

        # Core services
        services = ["EventBus: online", "AuditTrail: online", "PolicyEngine: online"]
        services.append(f"LLM: {'connected' if self._llm else 'not configured'}")
        parts.append("SERVICES:\n" + "\n".join(f"  [+] {s}" for s in services))

        # Agents
        if self._registry:
            agents = self._registry.list_agents()
            if agents:
                lines = [f"  {a['name']} [{a.get('runtime','?')}] - {a.get('status','idle')}" for a in agents]
                parts.append(f"INSTALLED AGENTS ({len(agents)}):\n" + "\n".join(lines))
            else:
                parts.append("INSTALLED AGENTS: none yet (agents are like apps - install to extend OpenSculpt)")

        # Daemons
        hm = getattr(self, "_daemon_manager", None)
        if hm:
            daemons = hm.list_daemons()
            lines = []
            for h in daemons:
                status = "RUNNING" if h["status"] == "running" else h["status"]
                line = f"  {h['icon']} {h['name']}: {h['description']} [{status}]"
                if h["status"] == "running":
                    line += f" (ticks: {h['ticks']})"
                if h.get("last_result"):
                    line += f"\n    Last: {h['last_result'].get('summary','')[:100]}"
                lines.append(line)
            running = sum(1 for h in daemons if h["status"] == "running")
            parts.append(f"HANDS ({len(daemons)} registered, {running} active):\n" + "\n".join(lines))
            parts.append("  Daemons are autonomous background tasks. Start with: 'start researcher with topic ...'")

        # Tools (just count - full schemas are in the tools param already)
        tools = self._inner_registry.list_tools()
        if tools:
            parts.append(f"TOOLS: {len(tools)} available (shell, files, HTTP, python, git, etc.)")

        # Evolution
        from agos.config import settings as _cfg
        evolved_dir = _os.path.join(str(_cfg.workspace_dir), "evolved")
        if _os.path.isdir(evolved_dir):
            files = [f for f in _os.listdir(evolved_dir) if f.endswith(".py")]
            if files:
                evo_lines = []
                for f in files[:10]:
                    fpath = _os.path.join(evolved_dir, f)
                    desc = ""
                    try:
                        with open(fpath, encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if line.startswith("# Pattern:") or line.startswith("# Description:"):
                                    desc = line.split(":", 1)[1].strip()
                                    break
                                if line.startswith("# Module:"):
                                    desc = "targets " + line.split(":", 1)[1].strip()
                    except Exception:
                        pass
                    evo_lines.append(f"  - {f}" + (f" ({desc})" if desc else ""))
                parts.append(
                    f"EVOLUTION ENGINE: active, {len(files)} evolved strategies in {evolved_dir}/\n"
                    + "\n".join(evo_lines) + "\n"
                    "  Pipeline: arxiv scan -> paper analysis -> code generation -> sandbox test -> integrate\n"
                    "  Evolved code modifies live OS: knowledge retrieval, policy, intent understanding.\n"
                    "  Each strategy is sandbox-tested before being loaded into the running OS."
                )
            else:
                parts.append("EVOLUTION ENGINE: active, no evolved strategies yet")
        else:
            parts.append("EVOLUTION ENGINE: active, no evolved strategies yet")

        if self._sub_agents:
            lines = [f"  {k}: {v['status']}" for k, v in self._sub_agents.items()]
            parts.append("RUNNING SUB-AGENTS:\n" + "\n".join(lines))

        # Knowledge summary
        if self._loom:
            parts.append("KNOWLEDGE: TheLoom active (episodic + semantic + graph memory)")

        # Constraints are injected per-command in execute(), not here in the static prompt

        # Session stats
        compact_info = f", {self._compactor.stats['compactions']} compactions" if self._compactor.stats['compactions'] else ""
        parts.append(f"SESSION: {self._session_requests} requests, {self._session_tokens:,} tokens used, {len(self._conversation_history)} in memory{compact_info}")

        return "\n\n".join(parts)

    async def execute(self, command: str) -> dict[str, Any]:
        """Execute ANY natural language command."""
        command = command.strip()
        if not command:
            return _reply(False, "error", "No command.")

        if not self._llm:
            # Try to auto-load from setup.json
            await self._try_auto_load_llm()
            if not self._llm:
                return _reply(False, "error",
                              "No LLM configured. Set a provider in the Setup tab.")

        # Response cache - return instantly for identical recent queries
        _cache_key = command.lower().strip()
        if _cache_key in self._response_cache:
            self._session_requests += 1
            return {
                "ok": True, "action": "execute",
                "message": self._response_cache[_cache_key],
                "data": {"turns": 0, "tokens_used": 0, "steps": [], "cached": True},
            }

        await self._bus.emit("os.command", {"command": command}, source="os_agent")

        # Reset loop guard for new command
        self._loop_guard.reset()

        # Session compaction - compress old history instead of truncating
        if self._compactor.should_compact(self._conversation_history):
            self._conversation_history = self._compactor.compact(self._conversation_history)

        # ── WORKING MEMORY: Assemble active context for this task ──
        # Like pulling relevant files onto your desk before starting work.
        # WorkingMemory ranks by relevance and evicts the least useful.
        self._working_memory.clear()
        self._working_memory.set_task(command)

        # Load relevant long-term memories from TheLoom into working memory
        if self._loom and len(command.split()) > 2:
            try:
                recalled = await self._loom.recall(command, limit=5)
                if recalled:
                    self._working_memory.add_from_recall(recalled, max_items=5)
            except Exception:
                pass  # Knowledge recall is best-effort

        # Load learned constraints — tagged .md files, environment-filtered
        try:
            from agos.knowledge.tagged_store import TaggedConstraintStore
            _cs = TaggedConstraintStore()
            _constraints_text = _cs.load()  # Only loads files matching THIS environment
            if _constraints_text and len(_constraints_text) > 30:
                self._working_memory.add(
                    _constraints_text[:4000],
                    source="constraints", relevance=0.95,
                )
        except Exception:
            pass

        # Load skill docs relevant to this command
        try:
            from agos.config import settings as _cfg
            skills_dir = _cfg.workspace_dir / "skills"
            if skills_dir.exists():
                for skill_file in skills_dir.glob("*.md"):
                    skill_name = skill_file.stem.lower()
                    if any(word in command.lower() for word in skill_name.split("_") if len(word) > 3):
                        content = skill_file.read_text(errors="ignore")[:500]
                        self._working_memory.add(
                            f"Skill ({skill_file.name}): {content}",
                            source="recall", relevance=0.85,
                        )
        except Exception:
            pass

        # ── INTENT CLASSIFICATION: Enrich context with intent analysis ──
        # Don't replace the execute loop - just add context so the LLM is smarter.
        if self._intent_engine and len(command.split()) > 4:
            try:
                plan = await self._intent_engine.understand(command)
                intent_type = plan.intent_type.value if hasattr(plan.intent_type, 'value') else str(plan.intent_type)
                self._working_memory.add(
                    f"Intent: {intent_type} - {plan.description}",
                    source="system", relevance=0.9,
                )
                strategy = plan.strategy.value if hasattr(plan.strategy, 'value') else str(plan.strategy)
                if strategy in ("pipeline", "parallel", "debate"):
                    agent_names = [a.name for a in plan.agents] if plan.agents else []
                    self._working_memory.add(
                        f"Suggested strategy: {strategy} with agents: {agent_names}",
                        source="system", relevance=0.85,
                    )
            except Exception:
                pass  # Intent classification is best-effort

        # Build live OS state context + working memory for the LLM
        wm_context = self._working_memory.to_context_string(max_items=8)
        knowledge_context = f"\n\n{wm_context}" if wm_context else ""
        # Inject evolved OS agent rules from Evolution Agent
        os_rules = ""
        try:
            from agos.config import settings as _settings_rules
            rules_file = _settings_rules.workspace_dir / "evolved" / "brain" / "os_agent_rules.txt"
            if rules_file.exists():
                rules_text = rules_file.read_text(encoding="utf-8", errors="ignore").strip()
                if rules_text:
                    os_rules = f"\n\nLEARNED RULES (evolved from past failures):\n{rules_text[:500]}"
        except Exception:
            pass
        # ── PROMPT CACHE BOUNDARY (Claude Code pattern) ──
        # Static prefix is identical across turns → cacheable (90% savings).
        # Dynamic suffix changes per turn (live state, knowledge, rules).
        dynamic_context = self._build_os_context() + knowledge_context + os_rules
        system = SYSTEM_PROMPT_STATIC + CACHE_BOUNDARY + SYSTEM_PROMPT_DYNAMIC.format(context=dynamic_context)

        # Inject recent conversation history for continuity
        messages: list[LLMMessage] = []
        for h in self._conversation_history[-5:]:  # Last 5 exchanges
            messages.append(LLMMessage(role="user", content=h["command"]))
            messages.append(LLMMessage(role="assistant", content=h["response"]))
        messages.append(LLMMessage(role="user", content=command))
        # ── DEFERRED TOOL LOADING (Claude Code pattern) ──
        # Tools marked deferred=True only included when keywords match command.
        # Combined with ITR dynamic pruning. Saves ~70% tool schema tokens.
        all_tools = self._tools.get_anthropic_tools(command=command)
        # Fallback: if deferred loading removed too many, get all
        if len(all_tools) < 5:
            all_tools = self._tools.get_anthropic_tools()

        # For short conversational queries, try without tools first (much faster)
        _cmd_lower = command.lower().strip("?!. ")
        _chat_signals = {"hi", "hello", "hey", "how are you", "how are you doing",
                         "what are you", "who are you", "thanks", "thank you",
                         "help", "what can you do", "good morning", "good night"}
        use_tools = _cmd_lower not in _chat_signals and len(command.split()) > 3
        tools = all_tools if use_tools else None

        steps: list[dict] = []
        tokens = 0
        turns = 0
        final_text = ""

        # ── TIERED COMPACTION for OS agent (Claude Code 4-tier pattern) ──
        # Microcompact (free) → observation masking → summary, with circuit breaker.
        from agos.session import get_condenser
        _os_condenser = get_condenser("tiered", threshold=6, keep_recent=3, keep_first=True)

        try:
            while turns < MAX_TURNS and tokens < MAX_TOKENS:
                # Compact context if it's growing (masks old tool outputs)
                if _os_condenser.should_compact(messages):
                    messages = _os_condenser.compact(messages)

                turns += 1
                _max_tok = 512 if tools is None else 4096
                resp = await self._llm.complete(
                    messages=messages, system=system,
                    tools=tools, max_tokens=_max_tok,
                )
                # If first turn had no tools but model seems to want action, retry with tools
                if turns == 1 and tools is None and resp.content:
                    _resp_lower = resp.content.lower()
                    if any(w in _resp_lower for w in ["i'll run", "let me execute", "i need to use", "i'll use the"]):
                        tools = all_tools
                        continue
                tokens += resp.input_tokens + resp.output_tokens
                self._track_usage(resp.input_tokens, resp.output_tokens)

                if resp.content:
                    await self._bus.emit("os.thinking", {
                        "turn": turns, "text": resp.content[:500],
                    }, source="os_agent")

                # Done - no tool calls
                if not resp.tool_calls:
                    final_text = resp.content or ""
                    messages.append(LLMMessage(role="assistant", content=final_text))
                    break

                # Append assistant message with tool_use blocks
                asst: list[dict] = []
                if resp.content:
                    asst.append({"type": "text", "text": resp.content})
                for tc in resp.tool_calls:
                    asst.append({
                        "type": "tool_use", "id": tc.id,
                        "name": tc.name, "input": tc.arguments,
                    })
                messages.append(LLMMessage(role="assistant", content=asst))

                # Execute tools (with loop guard + approval gate)
                results: list[dict] = []
                for tc in resp.tool_calls:
                    # Loop guard - detect infinite tool call loops
                    self._loop_guard.record(tc.name, tc.arguments)
                    if self._loop_guard.is_looping():
                        await self._bus.emit("os.loop_detected", {
                            "turn": turns, "reason": self._loop_guard.trip_reason,
                        }, source="os_agent")
                        results.append({
                            "type": "tool_result", "tool_use_id": tc.id,
                            "content": f"LOOP DETECTED: {self._loop_guard.trip_reason}. "
                                       "Stop repeating the same actions. Try a completely different approach or report what you found so far.",
                            "is_error": True,
                        })
                        continue

                    await self._bus.emit("os.tool_call", {
                        "turn": turns, "tool": tc.name,
                        "args": _trunc_args(tc.arguments),
                    }, source="os_agent")

                    # Approval gate: check if human needs to approve
                    if self._approval:
                        approved = await self._approval.check(tc.name, tc.arguments)
                        if not approved:
                            out = f"Tool '{tc.name}' rejected by human operator."
                            results.append({
                                "type": "tool_result", "tool_use_id": tc.id,
                                "content": out, "is_error": True,
                            })
                            steps.append({
                                "tool": tc.name,
                                "args": _trunc_args(tc.arguments),
                                "ok": False, "preview": out, "ms": 0,
                            })
                            continue

                    res = await self._tools.execute(tc.name, tc.arguments)
                    out = str(res.result) if res.success else str(res.error)
                    if len(out) > 8000:
                        out = out[:4000] + "\n...[truncated]...\n" + out[-2000:]

                    # Detect real failures inside "successful" shell commands
                    # Shell returns success=True even when command exits non-zero
                    _real_ok = res.success
                    if tc.name == "shell" and res.success and out.startswith("exit="):
                        try:
                            exit_code = int(out.split("\n")[0].split("=")[1])
                            if exit_code != 0:
                                _real_ok = False
                        except (IndexError, ValueError):
                            pass

                    # Detect operational issues from tool output
                    _out_lower = out.lower()
                    if _real_ok and tc.name in ("docker_ps", "docker_logs", "browse", "http"):
                        if any(w in _out_lower for w in ("exited", "access denied", "connection refused",
                                                          "installation error", "err_connection_refused",
                                                          "authentication failed", "401", "403")):
                            await self._bus.emit("os.capability_gap", {
                                "command": command[:100],
                                "tool": tc.name,
                                "detail": out[:200],
                            }, source="os_agent")

                    # ── RESOURCE TRACKING: Register deployed resources ──
                    if _real_ok and self._resource_registry and tc.name in (
                        "docker_run", "docker_network", "write_file",
                    ):
                        try:
                            from agos.processes.resources import Resource, ResourceType
                            args = tc.arguments or {}
                            if tc.name == "docker_run":
                                cid = out.strip()[:64] if len(out.strip()) >= 12 else out[:20]
                                self._resource_registry.register(Resource(
                                    id=cid, type=ResourceType.CONTAINER,
                                    name=args.get("name", cid[:12]),
                                    metadata={"image": args.get("image", ""), "ports": args.get("ports", "")},
                                    cleanup_command=f"docker rm -f {args.get('name', cid[:12])}",
                                    cleanup_order=10,
                                ))
                            elif tc.name == "docker_network" and "create" in str(args.get("action", "")):
                                nid = out.strip()[:64]
                                net_name = args.get("name", nid[:12])
                                self._resource_registry.register(Resource(
                                    id=nid, type=ResourceType.NETWORK, name=net_name,
                                    cleanup_command=f"docker network rm {net_name}",
                                    cleanup_order=20,
                                ))
                            elif tc.name == "write_file":
                                fpath = args.get("path", "")
                                # Only track user-facing files, not temp/internal files
                                if fpath and not fpath.startswith("/tmp") and not fpath.startswith("/var/tmp"):
                                    self._resource_registry.register(Resource(
                                        id=fpath, type=ResourceType.FILE, name=fpath,
                                        cleanup_command=f"rm {fpath}",
                                        cleanup_order=5,
                                    ))
                        except Exception:
                            pass

                    results.append({
                        "type": "tool_result", "tool_use_id": tc.id,
                        "content": out, "is_error": not res.success,
                    })
                    # Feed output to loop guard for stuck detection (patterns 2-4)
                    self._loop_guard._outputs.append(out[:200] if out else "")
                    self._loop_guard._errors.append(not _real_ok)
                    steps.append({
                        "tool": tc.name,
                        "args": _trunc_args(tc.arguments),
                        "ok": _real_ok,
                        "preview": out[:200],
                        "ms": res.execution_time_ms,
                    })

                    # For ALL failed tools, include truncated args so
                    # DemandCollector can diagnose what actually went wrong
                    _preview = out[:200]
                    if not _real_ok:
                        _args_str = str(_trunc_args(tc.arguments))[:120]
                        _preview = f"args={_args_str} | {out[:150]}"
                    await self._bus.emit("os.tool_result", {
                        "turn": turns, "tool": tc.name,
                        "ok": _real_ok, "preview": _preview,
                    }, source="os_agent")

                    # Update working memory with important tool results
                    if _real_ok and tc.name in ("docker_run", "docker_ps", "http",
                                                 "browse", "python", "shell"):
                        self._working_memory.add(
                            f"{tc.name}: {out[:150]}",
                            source="agent", relevance=0.8,
                        )

                    # Audit every tool call (not just lifecycle events)
                    try:
                        await self._audit.record(AuditEntry(
                            agent_id="os_agent", agent_name="OSAgent",
                            action="tool_execution",
                            tool_name=tc.name,
                            arguments=_trunc_args(tc.arguments),
                            detail=out[:200],
                            success=_real_ok,
                        ))
                    except Exception:
                        pass

                messages.append(LLMMessage(role="user", content=results))

            # Audit
            try:
                await self._audit.record(AuditEntry(
                    agent_id="os_agent", agent_name="OSAgent",
                    action="execute",
                    detail=f"{command[:80]} | turns={turns} tokens={tokens}",
                    success=True,
                ))
            except Exception:
                pass

            await self._bus.emit("os.complete", {
                "command": command[:200], "turns": turns,
                "tokens": tokens, "steps": len(steps),
            }, source="os_agent")

            # Emit demand signals for capabilities used via shell workarounds
            # If the agent used shell to run docker/kubectl/psql etc., that means
            # the OS doesn't have a native tool for it - evolution should fix that
            _shell_cmds_used = set()
            for step in steps:
                if step["tool"] == "shell":
                    args = step.get("args", {})
                    cmd_str = str(args.get("command", ""))
                    first_word = cmd_str.strip().split()[0] if cmd_str.strip() else ""
                    if first_word in ("docker", "docker-compose", "kubectl", "helm",
                                      "psql", "mysql", "mongo", "redis-cli",
                                      "npm", "npx", "node", "pip",
                                      "curl", "wget", "playwright"):
                        _shell_cmds_used.add(first_word)
            if _shell_cmds_used:
                await self._bus.emit("os.capability_gap", {
                    "command": command[:100],
                    "shell_workarounds": list(_shell_cmds_used),
                    "turns": turns, "tokens": tokens,
                }, source="os_agent")

            # Strip <think>...</think> blocks (Qwen-style reasoning)
            import re
            final_text = re.sub(r"<think>.*?</think>\s*", "", final_text, flags=re.DOTALL).strip()

            # Save to conversation history
            self._conversation_history.append({
                "command": command, "response": final_text[:500],
                "ts": time.time(),
            })
            if len(self._conversation_history) > self._max_history:
                self._conversation_history = self._conversation_history[-self._max_history:]

            # ── MEMORY: Save interaction to TheLoom ──
            # This is what makes the OS remember what it did.
            # Without this, "show me my leads" after "install CRM" returns nothing.
            if self._loom:
                try:
                    # 1. Record the command + outcome as episodic memory
                    tools_used = [s["tool"] for s in steps]
                    tool_summary = ", ".join(set(tools_used)) if tools_used else "none"
                    await self._loom.remember(
                        f"User asked: {command[:200]}\n"
                        f"Tools used: {tool_summary}\n"
                        f"Result: {final_text[:300]}",
                        kind="interaction",
                        tags=["os_agent", "command"] + list(set(tools_used))[:5],
                        agent_id="os_agent",
                    )

                    # 2. Record important tool results as semantic facts + KnowledgeGraph links
                    for step in steps:
                        if step["ok"] and step["tool"] in ("docker_run", "docker_pull", "http", "write_file"):
                            preview = step.get("preview", "")
                            args = step.get("args", {})
                            if step["tool"] == "docker_run":
                                image = args.get("image", "")
                                name = args.get("name", "")
                                ports = args.get("ports", "")
                                await self._loom.remember(
                                    f"Installed {image} as container '{name}' on port {ports}. "
                                    f"Command context: {command[:100]}",
                                    kind="fact",
                                    tags=["docker", "install", name, image],
                                    agent_id="os_agent",
                                )
                                # KnowledgeGraph: link system → port, system → image
                                try:
                                    g = self._loom.graph
                                    system_name = name or image.split("/")[-1].split(":")[0]
                                    if ports:
                                        await g.link(f"system:{system_name}", f"port:{ports}", "runs_on")
                                    await g.link(f"system:{system_name}", f"image:{image}", "uses_image")
                                    await g.link(f"system:{system_name}", "os_agent", "installed_by")
                                except Exception:
                                    pass
                            elif step["tool"] == "http" and "200" in preview:
                                url = args.get("url", "")
                                method = args.get("method", "GET")
                                await self._loom.remember(
                                    f"API endpoint works: {method} {url} → success. "
                                    f"Context: {command[:80]}",
                                    kind="fact",
                                    tags=["api", "endpoint", url[:50]],
                                    agent_id="os_agent",
                                )
                                # KnowledgeGraph: link endpoint
                                try:
                                    await self._loom.graph.link(f"endpoint:{url[:80]}", "os_agent", "discovered_by")
                                except Exception:
                                    pass
                except Exception as e:
                    _logger.debug("Memory save failed: %s", e)

            # Cache response for repeated queries (only short, non-tool responses)
            if len(steps) == 0 and final_text:
                self._response_cache[_cache_key] = final_text
                if len(self._response_cache) > self._max_cache:
                    oldest = next(iter(self._response_cache))
                    del self._response_cache[oldest]

            # Track session stats
            self._session_requests += 1
            # Persist cost to disk every command
            try:
                self._save_lifetime_cost()
            except Exception:
                pass

            # ── LEARNER: Auto-record to all 3 knowledge weaves ──
            if hasattr(self, '_learner') and self._learner:
                try:
                    tools_used = list(set(s["tool"] for s in steps))
                    await self._learner.record_interaction(
                        agent_id="os_agent",
                        agent_name="OSAgent",
                        user_input=command,
                        agent_output=final_text[:1000],
                        tokens_used=tokens,
                        tools_used=tools_used,
                    )
                    # Record important tool calls individually
                    for step in steps:
                        if step["tool"] in ("docker_run", "docker_pull", "http", "write_file", "browse"):
                            await self._learner.record_tool_call(
                                agent_id="os_agent",
                                agent_name="OSAgent",
                                tool_name=step["tool"],
                                arguments=step.get("args", {}),
                                result=step.get("preview", "")[:300],
                                success=step["ok"],
                            )
                except Exception as e:
                    _logger.debug("Learner record failed: %s", e)

            return {
                "ok": True, "action": "execute",
                "message": final_text,
                "data": {"turns": turns, "tokens_used": tokens, "steps": steps},
            }

        except Exception as e:
            await self._bus.emit("os.error", {
                "command": command[:200], "error": str(e)[:300],
            }, source="os_agent")
            return _reply(False, "error", f"Failed: {e}")

    # ── Cost tracking ────────────────────────────────────────────

    # Per-million-token pricing (USD). Input / Output.
    _MODEL_PRICING = {
        # Anthropic
        "claude-haiku-4-5-20251001": (1.00, 5.00),
        "claude-sonnet-4-20250514": (3.00, 15.00),
        "claude-opus-4-20250514": (15.00, 75.00),
        # OpenRouter Anthropic
        "anthropic/claude-haiku-4-5": (0.80, 4.00),
        "anthropic/claude-sonnet-4": (3.00, 15.00),
        "anthropic/claude-opus-4": (15.00, 75.00),
        # OpenAI
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        # Groq
        "llama-3.3-70b-versatile": (0.59, 0.79),
        # DeepSeek
        "deepseek-chat": (0.27, 1.10),
        # Google
        "gemini-2.5-flash": (0.15, 0.60),
        "gemini-2.5-pro": (1.25, 10.00),
        # Fallback
        "_default": (1.00, 5.00),
    }

    def _calc_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate USD cost from actual token counts."""
        model = getattr(self._llm, 'model', '') if self._llm else ''
        pricing = self._MODEL_PRICING.get(model, self._MODEL_PRICING["_default"])
        input_cost = (input_tokens / 1_000_000) * pricing[0]
        output_cost = (output_tokens / 1_000_000) * pricing[1]
        return input_cost + output_cost

    def _load_lifetime_cost(self) -> dict:
        """Load accumulated cost from .opensculpt/cost.json."""
        import json
        from pathlib import Path
        cost_path = Path(".opensculpt/cost.json")
        if cost_path.exists():
            try:
                return json.loads(cost_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"total_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}

    def _save_lifetime_cost(self) -> None:
        """Persist accumulated cost to disk — survives restart."""
        import json
        from pathlib import Path
        cost_path = Path(".opensculpt/cost.json")
        cost_path.parent.mkdir(parents=True, exist_ok=True)
        self._lifetime_cost["total_usd"] += self._session_cost_usd
        self._lifetime_cost["input_tokens"] += self._session_input_tokens
        self._lifetime_cost["output_tokens"] += self._session_output_tokens
        self._lifetime_cost["requests"] += self._session_requests
        cost_path.write_text(json.dumps(self._lifetime_cost, indent=2), encoding="utf-8")

    def _track_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Track token usage and cost for a single LLM call."""
        cost = self._calc_cost(input_tokens, output_tokens)
        self._session_input_tokens += input_tokens
        self._session_output_tokens += output_tokens
        self._session_tokens += input_tokens + output_tokens
        self._session_cost_usd += cost

    # ── Tool registration ────────────────────────────────────────

    def _register_tools(self) -> None:
        T, P = ToolSchema, ToolParameter
        reg = self._inner_registry

        reg.register(T(
            name="shell",
            description="Run any shell command. You have root. Use for: apt-get, pip, npm, git, ls, ps, curl, make, gcc, etc.",
            parameters=[
                P(name="command", description="Shell command to execute"),
                P(name="timeout", type="integer", description="Timeout seconds (default 60)", required=False),
            ],
        ), _shell)

        reg.register(T(
            name="read_file",
            description="Read a file or list a directory.",
            parameters=[P(name="path", description="File or directory path")],
        ), _read_file)

        reg.register(T(
            name="write_file",
            description="Write content to a file. Creates parent dirs.",
            parameters=[
                P(name="path", description="File path"),
                P(name="content", description="Content to write"),
            ],
        ), _write_file)

        reg.register(T(
            name="http",
            description="HTTP request. Use for APIs, web scraping, downloads.",
            parameters=[
                P(name="url", description="URL"),
                P(name="method", description="GET/POST/PUT/DELETE", required=False),
                P(name="body", description="Request body", required=False),
                P(name="headers", description="JSON headers string", required=False),
            ],
        ), _http)

        reg.register(T(
            name="python",
            description="Run Python code. Use print() for output.",
            parameters=[P(name="code", description="Python code")],
        ), _python)

        # Think tool (OpenHands pattern) — agent reasons without executing.
        # Counts as progress for loop detection. Prevents "agent stuck because
        # it needs to plan but loop detector sees no action."
        async def _think(thought: str) -> str:
            return f"[Thought recorded: {thought[:200]}]"

        reg.register(T(
            name="think",
            description="Reason about your approach WITHOUT executing anything. Use this to plan multi-step work, debug why something failed, or decide between approaches. Counts as progress.",
            parameters=[P(name="thought", description="Your reasoning or plan")],
        ), _think)

        # NOTE: spawn_agent and check_agent are NOT registered as tools.
        # All work goes through set_goal → GoalRunner → sub-agents.
        # The OS agent must NOT bypass GoalRunner by spawning agents directly.
        # Internal methods _spawn_agent/_spawn_agent_and_wait still exist
        # for GoalRunner to use.

        # Agent management if registry available
        if self._registry:
            agent_reg = self._registry
            reg.register(T(
                name="list_agents",
                description="List installed agents on this system.",
                parameters=[],
            ), _make_list_agents(agent_reg))

            reg.register(T(
                name="manage_agent",
                description="Manage installed agents: setup/start/stop/restart/uninstall/status.",
                parameters=[
                    P(name="action", description="setup|start|stop|restart|uninstall|status"),
                    P(name="name", description="Agent name"),
                    P(name="github_url", description="GitHub URL (for setup)", required=False),
                ],
            ), _make_manage_agent(agent_reg))

        # ── Docker + Browser tools - NOT registered at boot ──
        # These are activated by evolution when demand signals detect capability gaps.
        # The OS agent starts with shell/http only. When it uses shell("docker ..."),
        # the demand collector fires os.capability_gap → evolution activates these.
        # This makes evolution REAL - the OS grows tools organically.
        self._dormant_tools: dict[str, bool] = {"docker": False, "browser": False}

        # Listen for evolution events to register tools on demand
        if hasattr(self, '_bus'):
            self._bus.subscribe("evolution.builtin_activated", self._on_tool_activated)
            self._bus.subscribe("evolution.tool_deployed", self._on_evolved_tool_deployed)

    async def _on_tool_activated(self, event) -> None:
        """Evolution activated a builtin tool - register it now."""
        module = event.data.get("module", "")
        _tool = event.data.get("tool", "")
        if "docker" in module and not self._dormant_tools.get("docker"):
            self._register_docker_tools()
            self._dormant_tools["docker"] = True
            _logger.info("Evolution activated docker tools")
        if "browser" in module and not self._dormant_tools.get("browser"):
            self._register_browser_tools()
            self._dormant_tools["browser"] = True
            _logger.info("Evolution activated browser tools")

    async def _on_evolved_tool_deployed(self, event) -> None:
        """Evolution deployed a new tool — register it for agent use."""
        tool_name = event.data.get("tool", "")
        if not tool_name or self._inner_registry.get_tool(tool_name):
            return
        from agos.config import settings as _settings_tool
        tool_file = _settings_tool.workspace_dir / "evolved" / "tools" / f"{tool_name}.py"
        if not tool_file.exists():
            return
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(f"evolved_{tool_name}", tool_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            handler = getattr(module, "handler", None)
            if not handler:
                return
            from agos.tools.schema import ToolSchema, ToolParameter
            params_raw = getattr(module, "TOOL_PARAMETERS", [])
            parameters = [
                ToolParameter(name=p["name"], type=p.get("type", "string"),
                             description=p.get("description", ""), required=p.get("required", True))
                for p in params_raw if isinstance(p, dict)
            ]
            schema = ToolSchema(
                name=getattr(module, "TOOL_NAME", tool_name),
                description=getattr(module, "TOOL_DESCRIPTION", event.data.get("description", "Evolved tool")),
                parameters=parameters,
            )
            self._inner_registry.register(schema, handler)
            _logger.info("Registered evolved tool: %s", tool_name)
        except Exception as e:
            _logger.warning("Failed to load evolved tool %s: %s", tool_name, e)

    def activate_tool_pack(self, pack: str) -> bool:
        """Manually activate a tool pack (for testing or first-run setup)."""
        if pack == "docker" and not self._dormant_tools.get("docker"):
            self._register_docker_tools()
            self._dormant_tools["docker"] = True
            return True
        if pack == "browser" and not self._dormant_tools.get("browser"):
            self._register_browser_tools()
            self._dormant_tools["browser"] = True
            return True
        return False

    def _register_docker_tools(self) -> None:
        """Register Docker tools - called when evolution activates them."""
        T, P = ToolSchema, ToolParameter
        reg = self._inner_registry
        from agos.tools.docker_tool import (
            docker_run, docker_ps, docker_stop, docker_rm,
            docker_logs, docker_pull, docker_network, docker_exec,
        )
        _docker_kw = ["docker", "container", "deploy", "postgres", "mysql", "redis", "nginx", "install", "crm", "database"]
        reg.register(T(
            name="docker_run",
            description="Run a Docker container. Use for installing software (CRM, databases, etc).",
            parameters=[
                P(name="image", description="Docker image (e.g. 'espocrm/espocrm:latest')"),
                P(name="name", description="Container name", required=False),
                P(name="ports", description="Port mapping (e.g. '8081:80')", required=False),
                P(name="env", description="Env vars as JSON: {\"KEY\": \"value\"}", required=False),
                P(name="network", description="Docker network name", required=False),
                P(name="extra", description="Additional docker flags", required=False),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_run)
        reg.register(T(
            name="docker_ps",
            description="List running Docker containers.",
            parameters=[
                P(name="all_containers", type="boolean", description="Show all (including stopped)", required=False),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_ps)
        reg.register(T(
            name="docker_stop",
            description="Stop a Docker container.",
            parameters=[P(name="name", description="Container name or ID")],
            deferred=True, keywords=_docker_kw,
        ), docker_stop)
        reg.register(T(
            name="docker_rm",
            description="Remove a Docker container.",
            parameters=[
                P(name="name", description="Container name or ID"),
                P(name="force", type="boolean", description="Force remove", required=False),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_rm)
        reg.register(T(
            name="docker_logs",
            description="Get logs from a Docker container.",
            parameters=[
                P(name="name", description="Container name"),
                P(name="tail", type="integer", description="Number of lines (default 50)", required=False),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_logs)
        reg.register(T(
            name="docker_pull",
            description="Pull a Docker image.",
            parameters=[P(name="image", description="Image to pull (e.g. 'mysql:8.0')")],
            deferred=True, keywords=_docker_kw,
        ), docker_pull)
        reg.register(T(
            name="docker_network",
            description="Manage Docker networks (create, rm, ls).",
            parameters=[
                P(name="action", description="create, rm, or ls"),
                P(name="name", description="Network name"),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_network)
        reg.register(T(
            name="docker_exec",
            description="Run a command inside a Docker container.",
            parameters=[
                P(name="container", description="Container name"),
                P(name="command", description="Command to run"),
            ],
            deferred=True, keywords=_docker_kw,
        ), docker_exec)

    def _register_browser_tools(self) -> None:
        """Register Browser tools - called when evolution activates them."""
        T, P = ToolSchema, ToolParameter
        reg = self._inner_registry
        from agos.tools.browser_tool import (
            browse, browser_fill, browser_click, browser_screenshot, browser_content,
        )
        _browser_kw = ["browse", "scrape", "website", "navigate", "click", "screenshot", "browser", "page", "form", "login", "dashboard", "ui"]
        reg.register(T(
            name="browse",
            description="Open a URL in a headless browser and return the page text. Use for web UIs, CRM dashboards, setup wizards.",
            parameters=[P(name="url", description="URL to navigate to")],
            deferred=True, keywords=_browser_kw,
        ), browse)
        reg.register(T(
            name="browser_fill",
            description="Fill a form field on the current page.",
            parameters=[
                P(name="selector", description="CSS selector (e.g. '#username', 'input[name=email]')"),
                P(name="value", description="Text to type"),
            ],
            deferred=True, keywords=_browser_kw,
        ), browser_fill)
        reg.register(T(
            name="browser_click",
            description="Click a button or link on the current page.",
            parameters=[P(name="selector", description="CSS selector or text selector (e.g. 'text=Sign In')")],
            deferred=True, keywords=_browser_kw,
        ), browser_click)
        reg.register(T(
            name="browser_screenshot",
            description="Take a screenshot of the current browser page.",
            parameters=[P(name="path", description="File path to save screenshot", required=False)],
            deferred=True, keywords=_browser_kw,
        ), browser_screenshot)
        reg.register(T(
            name="browser_content",
            description="Get text content of a page element.",
            parameters=[P(name="selector", description="CSS selector (default 'body')", required=False)],
            deferred=True, keywords=_browser_kw,
        ), browser_content)

    async def _set_goal(self, goal: str, category: str = "") -> str:
        """Set a persistent high-level goal for autonomous execution."""
        goal_runner = self._daemon_manager.get_goal_runner() if self._daemon_manager else None
        if not goal_runner:
            return "Error: Goal runner not available"

        # Start goal runner if not running
        from agos.daemons.base import DaemonStatus
        if goal_runner.status != DaemonStatus.RUNNING:
            await self._daemon_manager.start_daemon("goal_runner", {})

        result = await goal_runner.create_goal(goal, category)
        phases = result.get("phases", [])
        phase_names = [p.get("name", "?") for p in phases]
        return (
            f"Goal set: '{goal}'\n"
            f"Category: {result.get('category', '?')}\n"
            f"Phases ({len(phases)}): {', '.join(phase_names)}\n\n"
            f"The OS will now work on this autonomously. "
            f"It will install software, create data, set up monitoring, and evolve. "
            f"Use check_goals to see progress."
        )

    async def _check_goals(self) -> str:
        """Check status of all goals."""
        goal_runner = self._daemon_manager.get_goal_runner() if self._daemon_manager else None
        if not goal_runner:
            return "No active goals"

        goals = goal_runner.get_goals()
        if not goals:
            return "No goals set. Use set_goal to create one."

        lines = []
        for g in goals:
            lines.append(f"[{g['status'].upper()}] {g['description'][:60]}")
            for p in g.get("phases", []):
                status_icon = {"done": "✓", "failed": "✗", "pending": "○"}.get(p["status"], "?")
                lines.append(f"  {status_icon} {p['name']}: {p['status']}")
                if p.get("result"):
                    lines.append(f"    → {p['result'][:80]}")
            if g.get("daemons_created"):
                lines.append(f"  Daemons created: {', '.join(g['daemons_created'])}")
            if g.get("skills_learned"):
                lines.append(f"  Skills learned: {', '.join(g['skills_learned'])}")
            lines.append("")
        return "\n".join(lines)

    async def _start_daemon(self, name: str, config: str = "") -> str:
        """Start a background hand."""
        import json
        cfg = {}
        if config:
            try:
                cfg = json.loads(config)
            except Exception:
                cfg = {"topic": config}  # treat as topic string
        result = await self._daemon_manager.start_daemon(name, cfg)
        if result.get("success"):
            return f"Daemon '{name}' started. It runs autonomously in the background. Use daemon_results to check output."
        return f"Failed to start hand '{name}': {result.get('error', 'unknown error')}"

    async def _stop_daemon(self, name: str) -> str:
        """Stop a background hand."""
        result = await self._daemon_manager.stop_daemon(name)
        if result.get("success"):
            return f"Daemon '{name}' stopped."
        return f"Failed to stop hand '{name}': {result.get('error', 'unknown error')}"

    async def _daemon_results(self, name: str) -> str:
        """Get recent results from a hand."""
        results = self._daemon_manager.get_results(name, limit=5)
        if not results:
            hand = self._daemon_manager.get_daemon(name)
            if hand:
                return f"Daemon '{name}' is {hand.status.value}. No results yet - it may still be working."
            return f"Unknown hand: {name}"
        lines = []
        for r in results:
            summary = r.get("summary", r.get("result", ""))[:200]
            lines.append(f"- {summary}")
        return f"Recent results from '{name}':\n" + "\n".join(lines)

    # ── Sub-agent spawning ───────────────────────────────────────

    def _select_design_pattern(self, task: str) -> tuple[str, str]:
        """Select the best agentic design pattern(s) for a task.

        Uses the PatternRegistry (evolvable, fitness-weighted) if available,
        falls back to simple keyword matching otherwise.

        Returns (pattern_names_csv, combined_instructions).
        """
        # Use the evolvable PatternRegistry if available
        if self._pattern_registry:
            selected = self._pattern_registry.select_for_task(task, count=2)
            if selected:
                names = ", ".join(p.name for p in selected)
                instructions = "\n\n".join(p.instructions for p in selected)
                return names, instructions

        # Fallback: simple keyword matching (generation 0)
        task_lower = task.lower()
        if any(w in task_lower for w in ["review", "analyze", "audit", "evaluate"]):
            return "reflection", "PATTERN: Reflection - draft, self-critique, revise. Minimum 2 passes."
        if any(w in task_lower for w in ["install", "set up", "deploy", "configure"]):
            return "planning", "PATTERN: Planning - plan steps, execute in order, verify each step."
        if any(w in task_lower for w in ["monitor", "watch", "alert", "track"]):
            return "goal_monitoring", "PATTERN: Goal Monitoring - define success, track progress, alert on deviation."
        return "tool_use", "PATTERN: Tool Use - use tools aggressively, verify results."

    async def _spawn_agent(self, name: str, task: str, persona: str = "",
                            goal_id: str = "") -> str:
        """Spawn a sub-agent that works on a task independently."""
        if not self._llm:
            return "Error: No LLM available"

        # Select the best design pattern for this task
        pattern_name, pattern_instructions = self._select_design_pattern(task)

        agent_id = f"sub_{name}_{int(time.time()) % 10000}"
        self._sub_agents[name] = {
            "id": agent_id, "task": task, "status": "running",
            "result": None, "pattern": pattern_name, "goal_id": goal_id,
        }

        await self._bus.emit("os.sub_agent.spawned", {
            "name": name, "task": task[:200], "agent_id": agent_id,
        }, source="os_agent")

        # Register with AgentRegistry so it appears in the Agents tab
        if self._registry:
            try:
                self._registry.register_live_agent(agent_id, name, task, source="os_agent")
            except Exception:
                pass

        await self._bus.emit("agent.spawned", {
            "id": agent_id, "agent": name,
            "role": persona or task[:60],
        }, source="os_agent")
        await self._audit.record(AuditEntry(
            agent_id=agent_id, agent_name=name,
            action="state_change", detail="created -> running",
            success=True,
        ))

        # Run in background with selected design pattern
        asyncio.create_task(self._run_sub_agent(name, task, persona, pattern_instructions))
        return f"Sub-agent '{name}' spawned (pattern: {pattern_name}) working on: {task[:100]}"

    async def _spawn_agent_and_wait(self, name: str, task: str,
                                     persona: str = "", timeout: int = 300,
                                     goal_id: str = "") -> dict:
        """Spawn ONE sub-agent and WAIT for it to complete.

        Unlike _spawn_agent (fire-and-forget), this blocks until the agent
        finishes. Used by GoalRunner to enforce sequential phase execution -
        only one agent runs at a time.
        """
        await self._spawn_agent(name, task, persona, goal_id=goal_id)

        start = time.time()
        while time.time() - start < timeout:
            agent = self._sub_agents.get(name, {})
            status = agent.get("status", "")
            if status in ("done", "error"):
                return {
                    "ok": status == "done",
                    "message": agent.get("result", "") or "",
                }
            await asyncio.sleep(3)

        # Timeout - agent took too long
        return {"ok": False, "message": f"Agent '{name}' timed out after {timeout}s"}

    async def _run_sub_agent(self, name: str, task: str, persona: str,
                              pattern_instructions: str = "") -> None:
        """Run a sub-agent's task using its own Claude loop.

        Like Unix fork() - every sub-agent is a replica of the OS agent
        with FULL capabilities (shell, docker, browser, http, python, etc.)
        but specialized for its task. Sub-agents are powered by the same LLM
        and inherit all tools the OS agent has, just like every Unix process
        inherits init's capabilities.

        Each sub-agent executes using a design pattern selected for its task
        (reflection, planning, prompt_chaining, etc.) - patterns are rated
        and evolved over time by the evolution engine.
        """
        # Recall relevant knowledge for this sub-agent's task
        # Recall knowledge — cap at 3 items × 200 chars (was 5 × 300 = 1500 tokens wasted)
        memory_context = ""
        if self._loom:
            try:
                recalled = await self._loom.recall(task, limit=3)
                if recalled:
                    snippets = [
                        (t.content if hasattr(t, 'content') else str(t))[:200]
                        for t in recalled[:3]
                    ]
                    memory_context = "\n\nKNOWLEDGE:\n" + "\n---\n".join(snippets)
            except Exception:
                pass

        # Load skill docs — MAX 2 most relevant, 500 chars each (was: all matches, 1000 chars)
        # OpenHands lesson: context bloat kills performance. Less is more.
        skill_context = ""
        try:
            from agos.config import settings
            skills_dir = settings.workspace_dir / "skills"
            if skills_dir.exists():
                task_words = set(task.lower().split())
                scored_skills = []
                for skill_file in skills_dir.glob("*.md"):
                    skill_words = set(skill_file.stem.lower().split("_"))
                    overlap = len(task_words & skill_words)
                    if overlap > 0:
                        scored_skills.append((overlap, skill_file))
                # Top 2 most relevant only
                scored_skills.sort(key=lambda x: -x[0])
                for _, skill_file in scored_skills[:2]:
                    content = skill_file.read_text(errors="ignore")[:500]
                    skill_context += f"\n\nSKILL: {skill_file.name}\n{content}"
        except Exception:
            pass

        # ── Situation-matched operational knowledge (0 LLM calls — pure retrieval) ──
        # When one node learns "elasticsearch needs JDK", all nodes get it via fleet sync.
        # But we only inject RELEVANT principles — matched by keywords, env, and scenario.
        principles_context = ""
        try:
            import json as _json
            evo_path = settings.workspace_dir / "evolution_state.json"
            if evo_path.exists():
                evo_data = _json.loads(evo_path.read_text(errors="ignore"))
                evo_mem = evo_data.get("evolution_memory", {})
                insights = evo_mem.get("insights", [])

                # Only high-confidence insights with operational knowledge (cap at 20 candidates)
                cases = [i for i in insights[-50:] if i.get("what_worked") and i.get("confidence", 0) > 0.5][:20]

                if cases:
                    # Situation matching: score each case against current task + environment
                    task_lower = task.lower()
                    task_words = set(task_lower.split())

                    from agos.environment import EnvironmentProbe
                    env_type = "container" if EnvironmentProbe.probe().is_container else "baremetal"

                    scored = []
                    for c in cases:
                        score = 0.0
                        # Keyword overlap between applies_when and current task
                        kw = set(c.get("applies_when", "").lower().split())
                        overlap = len(kw & task_words)
                        if overlap > 0:
                            score += overlap * 0.3
                        # Scenario type match
                        if c.get("scenario_type", "") and c.get("scenario_type", "") in task_lower:
                            score += 0.2
                        # Environment match
                        env_match = c.get("environment_match", "any")
                        if env_match == "any" or env_match == env_type:
                            score += 0.1
                        elif env_match != env_type:
                            score -= 0.5  # Wrong environment, penalize
                        # Confidence weight
                        score *= c.get("confidence", 1.0)

                        if score > 0:
                            scored.append((score, c))

                    # Top 3 most relevant (was 5 — less context = better focus)
                    scored.sort(key=lambda x: -x[0])
                    relevant = scored[:3]

                    if relevant:
                        principles = [c["what_worked"] for _, c in relevant]
                        principles_context = "\n\nLEARNED PRINCIPLES (matched to your task from fleet experience):\n" + "\n".join(
                            f"- {p}" for p in principles
                        )
                        # Store which principles were injected for reinforcement tracking
                        self._last_injected_principles = [c.get("principle", c["what_worked"])[:50] for _, c in relevant]
        except Exception:
            self._last_injected_principles = []

        # Evolved tools are loaded via evolution.tool_deployed event handler

        # ── Load evolved prompt rules (from Evolution Agent) ──
        evolved_rules = ""
        try:
            from agos.config import settings as _settings
            rules_file = _settings.workspace_dir / "evolved" / "brain" / "sub_agent_rules.txt"
            if rules_file.exists():
                rules_text = rules_file.read_text(encoding="utf-8", errors="ignore").strip()
                if rules_text:
                    evolved_rules = f"\n\nLEARNED RULES (from past evolution — follow these):\n{rules_text[:500]}"
        except Exception:
            pass

        # ── Detect environment so sub-agent knows what it can use ──
        from agos.environment import EnvironmentProbe
        env_context = EnvironmentProbe.summary()

        sub_system = f"""You are a sub-agent of OpenSculpt named '{name}'.
{f'You are a {persona}.' if persona else ''}
Your task: {task}

ENVIRONMENT (what you have to work with):
{env_context}

You are a worker agent. Your tools: shell, http, python, read_file, write_file, browse.

RULES:
1. USE TOOLS. Don't explain — DO.
2. WORK WITH YOUR ENVIRONMENT. Read the environment info above. Use the recommended strategy.
3. FINISH THE JOB. Every task has these steps — do ALL of them, not just the first:
   - SET UP: install/create whatever is needed
   - MAKE IT WORK: start processes, apply configs, run migrations, move files
   - PROVE IT WORKS: check the result yourself (curl, ls, query, test)
   If you install something, start it. If you write a config, apply it. If you move files, verify they're there.
4. IF YOUR PRIMARY APPROACH FAILS — try a DIFFERENT approach, not the same thing again.
5. IF NOTHING WORKS — report HONESTLY what's blocking you and what the user needs to provide.
6. Your task is NOT done until you can PROVE the outcome yourself.
7. You CANNOT create goals or spawn other agents. You are a worker — do the work yourself.
8. SERVICE PERSISTENCE: If you start a persistent service (web server, API, database, worker),
   you MUST write a service card using write_file to .opensculpt/services/YOUR_SERVICE_NAME.md.
   Format — YAML frontmatter with: name, port, start_command, health_check, working_dir, status: starting.
   Then markdown body with: ## Access (URL, credentials), ## Dependencies, ## Files, ## How to Start.
   This is how the OS keeps your service alive after restart. Without a card, it dies and never comes back.

{pattern_instructions}
{memory_context}{skill_context}{principles_context}{evolved_rules}"""

        messages = [LLMMessage(role="user", content=task)]

        # ── TOKEN OPTIMIZATION: Dynamic tool selection ──
        # Like Unix only loading needed drivers — don't send 15 tool schemas every turn.
        # Prune by task keywords to cut 70% of per-turn schema tokens.
        _BLOCKED_FOR_SUB_AGENTS = {
            "set_goal", "check_goals", "start_daemon", "stop_daemon",
            "daemon_results", "spawn_agent", "check_agent",
            "manage_agent", "list_agents",
        }
        task_lower = task.lower()
        _all_tools = [t for t in self._tools.get_anthropic_tools()
                      if t["name"] not in _BLOCKED_FOR_SUB_AGENTS]
        # Core tools every sub-agent needs
        _CORE_TOOLS = {"shell", "read_file", "write_file", "python"}
        # Task-specific tool routing
        _DOCKER_KEYWORDS = {"docker", "container", "deploy", "postgres", "mysql", "redis", "nginx"}
        _HTTP_KEYWORDS = {"api", "http", "curl", "endpoint", "rest", "url", "fetch"}
        _needs_docker = any(k in task_lower for k in _DOCKER_KEYWORDS)
        _needs_http = any(k in task_lower for k in _HTTP_KEYWORDS)
        tools = []
        for t in _all_tools:
            tname = t["name"]
            if tname in _CORE_TOOLS:
                tools.append(t)
            elif "docker" in tname and _needs_docker:
                tools.append(t)
            elif tname == "http" and _needs_http:
                tools.append(t)
            elif tname not in ("docker_run", "docker_ps", "docker_stop",
                               "docker_exec", "docker_logs", "docker_network", "http"):
                tools.append(t)  # non-docker, non-http tools always included

        _TOKEN_BUDGET = 50_000  # Hard limit per sub-agent (like Unix rlimit)
        _start_time = time.time()
        _total_tokens = 0
        _errors = 0
        _tools_used = set()

        # ── Pluggable condenser for sub-agents (OpenClaw/OpenHands pattern) ──
        from agos.session import get_condenser
        _sub_condenser = get_condenser("observation_masking", threshold=8, keep_recent=4, keep_first=True)

        # ── Proper LoopGuard for sub-agents (OpenFang pattern) ──
        # Threshold=4 (not 3) because sub-agents legitimately retry commands.
        # OpenHands uses 4 for identical pairs. min_pattern_len=3 to avoid
        # false positives on normal shell→read→shell sequences.
        from agos.guard import LoopGuard
        _sub_loop_guard = LoopGuard(window_size=20, min_pattern_len=3, max_pattern_len=5, repeat_threshold=4)

        try:
            for turn in range(25):
                # ── TOKEN BUDGET: Hard stop like Unix rlimit ──
                if _total_tokens > _TOKEN_BUDGET:
                    _logger.info("Sub-agent '%s' hit token budget (%d/%d) at turn %d",
                                 name, _total_tokens, _TOKEN_BUDGET, turn)
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = f"(token budget reached at turn {turn}, {_total_tokens} tokens used)"
                    break

                # ── OBSERVATION MASKING via pluggable condenser ──
                if _sub_condenser.should_compact(messages):
                    messages = _sub_condenser.compact(messages)
                    _logger.debug("Sub-agent '%s' context compacted at turn %d", name, turn)

                # ── LOOP DETECTION via LoopGuard (OpenFang SHA256 pattern) ──
                if _sub_loop_guard.is_looping():
                    _logger.info("Sub-agent '%s' stuck in loop: %s", name, _sub_loop_guard.trip_reason)
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = f"(stopped: {_sub_loop_guard.trip_reason})"
                    break

                # Retry LLM calls with exponential backoff (empty response / disconnect)
                resp = None
                for _retry in range(3):
                    try:
                        resp = await self._llm.complete(
                            messages=messages, system=sub_system,
                            tools=tools, max_tokens=4096,
                        )
                        if resp and (resp.content or resp.tool_calls):
                            break
                        _logger.warning("Sub-agent '%s' got empty LLM response (attempt %d/3)", name, _retry + 1)
                    except Exception as llm_err:
                        _logger.warning("Sub-agent '%s' LLM error (attempt %d/3): %s", name, _retry + 1, llm_err)
                        if _retry < 2:
                            await asyncio.sleep(2 ** _retry)
                            continue
                        raise
                if not resp or (not resp.content and not resp.tool_calls):
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = "(LLM returned empty after 3 retries)"
                    break
                _total_tokens += resp.input_tokens + resp.output_tokens
                self._track_usage(resp.input_tokens, resp.output_tokens)

                if not resp.tool_calls:
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = resp.content or "(no output)"
                    break

                asst: list[dict] = []
                if resp.content:
                    asst.append({"type": "text", "text": resp.content})
                for tc in resp.tool_calls:
                    asst.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                messages.append(LLMMessage(role="assistant", content=asst))

                results: list[dict] = []
                for tc in resp.tool_calls:
                    # Approval gate for sub-agents too
                    if self._approval:
                        approved = await self._approval.check(tc.name, tc.arguments)
                        if not approved:
                            results.append({
                                "type": "tool_result", "tool_use_id": tc.id,
                                "content": f"Tool '{tc.name}' rejected by human operator.",
                                "is_error": True,
                            })
                            continue

                    res = await self._tools.execute(tc.name, tc.arguments)
                    out = str(res.result) if res.success else str(res.error)
                    if len(out) > 6000:
                        out = out[:3000] + "\n...[truncated]...\n" + out[-1500:]

                    # Track resources created by sub-agents
                    _real_ok = res.success
                    _tools_used.add(tc.name)
                    if not _real_ok:
                        _errors += 1
                    if _real_ok and self._resource_registry and tc.name in (
                        "docker_run", "docker_network", "write_file",
                    ):
                        try:
                            from agos.processes.resources import Resource, ResourceType
                            args = tc.arguments or {}
                            goal_id = self._sub_agents.get(name, {}).get("goal_id", "")
                            if tc.name == "docker_run":
                                cid = out.strip()[:64]
                                self._resource_registry.register(Resource(
                                    id=cid, type=ResourceType.CONTAINER,
                                    name=args.get("name", cid[:12]),
                                    goal_id=goal_id, phase_name=name, agent_id=name,
                                    metadata={"image": args.get("image", ""), "ports": args.get("ports", "")},
                                    cleanup_command=f"docker rm -f {args.get('name', cid[:12])}",
                                    cleanup_order=10,
                                ))
                            elif tc.name == "docker_network" and "create" in str(args.get("action", "")):
                                net_name = args.get("name", out.strip()[:12])
                                self._resource_registry.register(Resource(
                                    id=out.strip()[:64], type=ResourceType.NETWORK, name=net_name,
                                    goal_id=goal_id, phase_name=name, agent_id=name,
                                    cleanup_command=f"docker network rm {net_name}",
                                    cleanup_order=20,
                                ))
                            elif tc.name == "write_file":
                                fpath = args.get("path", "")
                                if fpath:
                                    self._resource_registry.register(Resource(
                                        id=fpath, type=ResourceType.FILE, name=fpath,
                                        goal_id=goal_id, phase_name=name, agent_id=name,
                                        cleanup_command=f"rm {fpath}",
                                        cleanup_order=5,
                                    ))
                        except Exception:
                            pass

                    # Detect docker containers spawned via shell (the biggest leak)
                    if _real_ok and self._resource_registry and tc.name == "shell":
                        try:
                            from agos.processes.resources import Resource, ResourceType
                            cmd_str = str(tc.arguments.get("command", "")).lower()
                            goal_id = self._sub_agents.get(name, {}).get("goal_id", "")
                            if "docker run" in cmd_str:
                                cid = out.strip().split("\n")[0].strip()[:64]
                                if cid and len(cid) >= 12 and all(c in "0123456789abcdef" for c in cid):
                                    self._resource_registry.register(Resource(
                                        id=cid, type=ResourceType.CONTAINER,
                                        name=cid[:12], goal_id=goal_id,
                                        phase_name=name, agent_id=name,
                                        cleanup_command=f"docker rm -f {cid[:12]}",
                                        cleanup_order=10,
                                    ))
                                    _logger.info("Auto-tracked shell docker container: %s (goal=%s)", cid[:12], goal_id[:20])
                        except Exception:
                            pass

                    results.append({
                        "type": "tool_result", "tool_use_id": tc.id,
                        "content": out, "is_error": not res.success,
                    })
                messages.append(LLMMessage(role="user", content=results))

                # Record tool calls for LoopGuard (OpenFang SHA256 pattern detection)
                for tc in resp.tool_calls:
                    _sub_loop_guard.record(tc.name, tc.arguments)
            else:
                self._sub_agents[name]["status"] = "done"
                self._sub_agents[name]["result"] = resp.content or "(max turns reached)"

            _elapsed_ms = int((time.time() - _start_time) * 1000)
            _success = self._sub_agents[name]["status"] == "done"

            await self._bus.emit("os.sub_agent.done", {
                "name": name, "result": (self._sub_agents[name]["result"] or "")[:300],
                "tokens": _total_tokens, "turns": turn + 1, "time_ms": _elapsed_ms,
                "success": _success,
            }, source="os_agent")

            # ── PATTERN RATING: Feed outcome back to PatternRegistry ──
            if self._pattern_registry:
                try:
                    from agos.evolution.pattern_registry import PatternOutcome
                    pattern_ids = [p.strip() for p in
                                   self._sub_agents[name].get("pattern", "tool_use").split(",")]
                    outcome = PatternOutcome(
                        pattern_ids=pattern_ids,
                        task_summary=task[:200],
                        task_type=self._pattern_registry._infer_task_type(task),
                        success=_success,
                        tokens_used=_total_tokens,
                        turns=turn + 1,
                        time_ms=_elapsed_ms,
                        tools_used=list(_tools_used),
                        errors_encountered=_errors,
                    )
                    self._pattern_registry.update_fitness(outcome)
                    await self._bus.emit("os.pattern_rated", {
                        "patterns": pattern_ids,
                        "task_type": outcome.task_type,
                        "fitness": round(outcome.tokens_used / max(_total_tokens, 1), 3),
                        "success": _success,
                        "tokens": _total_tokens,
                        "turns": turn + 1,
                    }, source="os_agent")
                except Exception as e:
                    _logger.debug("Pattern rating failed: %s", e)

            # Save sub-agent results to memory
            if self._loom:
                try:
                    result_text = self._sub_agents[name].get("result", "")
                    await self._loom.remember(
                        f"Sub-agent '{name}' completed task: {task[:200]}\n"
                        f"Result: {result_text[:300]}",
                        kind="interaction",
                        tags=["sub_agent", name, "completed"],
                        agent_id=self._sub_agents[name]["id"],
                    )
                except Exception:
                    pass
            if hasattr(self, '_learner') and self._learner:
                try:
                    await self._learner.record_interaction(
                        agent_id=self._sub_agents[name]["id"],
                        agent_name=name,
                        user_input=task,
                        agent_output=(self._sub_agents[name].get("result", "") or "")[:500],
                        tokens_used=0,
                        tools_used=[],
                    )
                except Exception:
                    pass

            # Auto-generate skill doc from sub-agent results
            try:
                from agos.config import settings as _cfg
                skills_dir = _cfg.workspace_dir / "skills"
                skills_dir.mkdir(parents=True, exist_ok=True)
                result_text = self._sub_agents[name].get("result", "") or ""
                if len(result_text) > 50:  # Only save meaningful results
                    skill_name = name.lower().replace(" ", "_").replace("-", "_")
                    skill_path = skills_dir / f"{skill_name}.md"
                    # Append if exists, create if not
                    with open(skill_path, "a", encoding="utf-8") as f:
                        f.write(f"\n\n## Task: {task[:200]}\n")
                        f.write(f"Result: {result_text[:500]}\n")
                        f.write(f"Agent: {name} ({self._sub_agents[name]['id']})\n")
            except Exception:
                pass

            # Mark completed in registry
            if self._registry:
                try:
                    self._registry.mark_agent_completed(self._sub_agents[name]["id"], success=True)
                except Exception:
                    pass
            await self._audit.record(AuditEntry(
                agent_id=self._sub_agents[name]["id"], agent_name=name,
                action="state_change", detail="running -> completed",
                success=True,
            ))

            # ── OPERATIONAL FAILURE ANALYSIS ──
            # When a sub-agent finishes, analyze its result for operational
            # problems the OS needs to evolve to handle. The LLM reads the
            # agent's output and emits specific demand signals.
            result_text = (self._sub_agents[name].get("result", "") or "")[:500]
            if self._llm and (not _success or _total_tokens > 30000):
                try:
                    analysis = await self._llm.complete(
                        messages=[LLMMessage(role="user", content=(
                            f"Agent '{name}' just finished this task:\n{task[:200]}\n\n"
                            f"Result (success={_success}, tokens={_total_tokens}):\n{result_text}\n\n"
                            f"List any operational problems as a JSON array of strings. Examples:\n"
                            f'["container crashed after config modification", "could not authenticate - no credentials", "service unreachable on expected port"]\n'
                            f"If no problems, return []. Output ONLY the JSON array."
                        ))],
                        system="Output only a JSON array of strings. No markdown.",
                        max_tokens=200,
                    )
                    import json as _json
                    problems_text = (analysis.content or "").strip()
                    import re as _re
                    problems_text = _re.sub(r'^```json\s*', '', problems_text)
                    problems_text = _re.sub(r'\s*```$', '', problems_text)
                    problems = _json.loads(problems_text) if problems_text.startswith("[") else []
                    for problem in problems[:3]:
                        await self._bus.emit("os.capability_gap", {
                            "command": task[:200],
                            "agent": name,
                            "error": str(problem),
                            "tokens_used": _total_tokens,
                        }, source="os_agent")
                except Exception:
                    pass

            # ── DEMAND SIGNAL: Let DemandCollector classify what went wrong ──
            # Just emit the raw failure data. DemandCollector._on_capability_gap()
            # will classify it, rank it, and feed it to the evolution engine.
            if not _success:
                await self._bus.emit("os.capability_gap", {
                    "command": task[:200],
                    "agent": name,
                    "error": (self._sub_agents[name].get("result", "") or "")[:300],
                    "tokens_used": _total_tokens,
                    "turns": turn + 1,
                    "tools_used": list(_tools_used),
                }, source="os_agent")

            if _total_tokens > 50000:
                await self._bus.emit("os.capability_gap", {
                    "command": task[:200],
                    "agent": name,
                    "tokens_used": _total_tokens,
                    "turns": turn + 1,
                }, source="os_agent")

        except Exception as e:
            error_detail = str(e) or f"{type(e).__name__} (no message)"
            self._sub_agents[name]["status"] = "error"
            self._sub_agents[name]["result"] = f"Error: {error_detail}"
            _logger.warning("Sub-agent '%s' crashed: %s: %s", name, type(e).__name__, error_detail[:200])
            await self._bus.emit("agent.error", {
                "agent": name, "error": f"{type(e).__name__}: {error_detail}"[:300],
                "task": task[:200],
            }, source="os_agent")
            if self._registry:
                try:
                    self._registry.mark_agent_completed(self._sub_agents[name]["id"], success=False)
                except Exception:
                    pass

    async def _check_agent(self, name: str) -> str:
        """Check a sub-agent's status."""
        if name not in self._sub_agents:
            return f"No sub-agent named '{name}'. Active: {list(self._sub_agents.keys())}"
        agent = self._sub_agents[name]
        if agent["status"] == "running":
            return f"Sub-agent '{name}' is still working on: {agent['task'][:100]}"
        result = agent.get("result", "(no result)")
        return f"Sub-agent '{name}' finished ({agent['status']}).\n\nResult:\n{result}"


# ── Standalone tool implementations ──────────────────────────────


async def _shell(command: str, timeout: int = 60) -> str:
    import subprocess as _sp
    import os as _os
    try:
        cwd = "/app" if _os.path.isdir("/app") else _os.getcwd()
        proc = await asyncio.create_subprocess_shell(
            command, stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        parts = [f"exit={proc.returncode}"]
        if stdout:
            parts.append(stdout.decode(errors="replace")[:6000])
        if stderr:
            parts.append(f"stderr: {stderr.decode(errors='replace')[:3000]}")
        return "\n".join(parts)
    except asyncio.TimeoutError:
        return f"Timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


async def _read_file(path: str) -> str:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return f"Not found: {path}"
    if p.is_dir():
        entries = sorted(p.iterdir())
        lines = []
        for e in entries[:100]:
            kind = "DIR " if e.is_dir() else "FILE"
            sz = e.stat().st_size if e.is_file() else 0
            lines.append(f"  {kind} {e.name:40s} {sz:>10,}b")
        return f"{path} ({len(entries)} entries)\n" + "\n".join(lines)
    try:
        c = p.read_text(encoding="utf-8", errors="replace")
        if len(c) > 10000:
            return c[:5000] + f"\n...[{len(c)} chars total]...\n" + c[-3000:]
        return c
    except Exception as e:
        return f"Error: {e}"


async def _write_file(path: str, content: str) -> str:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


async def _http(url: str, method: str = "GET", body: str = "", headers: str = "") -> str:
    import httpx
    import json
    try:
        hdrs = json.loads(headers) if headers else {}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.request(method, url, content=body or None, headers=hdrs)
            return f"HTTP {r.status_code}\n{r.text[:8000]}"
    except Exception as e:
        return f"Error: {e}"


async def _python(code: str) -> str:
    import subprocess as _sp
    import os as _os
    import sys as _sys
    try:
        cwd = "/app" if _os.path.isdir("/app") else _os.getcwd()
        python_cmd = _sys.executable or "python3"
        proc = await asyncio.create_subprocess_exec(
            python_cmd, "-c", code, stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = ""
        if stdout:
            out += stdout.decode(errors="replace")[:6000]
        if stderr:
            out += f"\nstderr: {stderr.decode(errors='replace')[:3000]}"
        return out or "(no output)"
    except asyncio.TimeoutError:
        return "Timed out after 60s"
    except Exception as e:
        return f"Error: {e}"


def _make_list_agents(registry):
    async def _fn() -> str:
        agents = registry.list_agents()
        if not agents:
            return "No agents installed."
        lines = [f"  {a['name']} [{a['runtime']}] {a['status']}" for a in agents]
        return "\n".join(lines)
    return _fn


def _make_manage_agent(registry):
    async def _fn(action: str, name: str, github_url: str = "") -> str:
        try:
            if action == "setup":
                a = await registry.setup(name, github_url=github_url)
                return f"Setup {a.display_name}: {a.status.value}"
            agent = registry.get_agent_by_name(name)
            if not agent:
                return f"Agent '{name}' not found."
            if action == "start":
                a = await registry.start(agent.id)
                return f"Started {a.display_name}: {a.status.value}"
            elif action == "stop":
                a = await registry.stop(agent.id)
                return f"Stopped {a.display_name}."
            elif action == "restart":
                if agent.status.value == "running":
                    await registry.stop(agent.id)
                a = await registry.start(agent.id)
                return f"Restarted {a.display_name}."
            elif action == "uninstall":
                await registry.uninstall(agent.id)
                return f"Uninstalled {name}."
            elif action == "status":
                return f"{agent.display_name} [{agent.runtime}] {agent.status.value} mem={agent.memory_limit_mb}MB"
            return f"Unknown action: {action}"
        except Exception as e:
            return f"Error: {e}"
    return _fn


def _reply(ok: bool, action: str, message: str, data: dict | None = None) -> dict:
    return {"ok": ok, "action": action, "message": message, "data": data or {}}


def _trunc_args(args: dict) -> dict:
    return {k: (str(v)[:100] + "..." if len(str(v)) > 100 else str(v)) for k, v in args.items()}
