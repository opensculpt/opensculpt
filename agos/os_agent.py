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
import re as _re
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
from agos.os_agent_tools import (  # noqa: E402
    ToolManager,
    shell as _shell, read_file as _read_file, write_file as _write_file,
    http as _http, python as _python,
    make_list_agents as _make_list_agents, make_manage_agent as _make_manage_agent,
    sanitize_response as _sanitize_response, reply as _reply, trunc_args as _trunc_args,
)
from agos.os_agent_subagent import SubAgentRunner  # noqa: E402
from agos.os_agent_context import (  # noqa: E402
    MAX_TURNS, MAX_TOKENS, CACHE_BOUNDARY,
    SYSTEM_PROMPT_STATIC, SYSTEM_PROMPT_DYNAMIC, SYSTEM_PROMPT,
    SYSTEM_PROMPT_BASIC, SYSTEM_PROMPT_CHAT_ONLY, BASIC_TOOLS_WHITELIST,
    ContextBuilder, CostTracker, LLMLoader,
)


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
        # Tool management (brain/body separation — ToolManager is the "body")
        self._tool_manager = ToolManager(event_bus, agent_registry)
        self._tool_manager.register_base_tools()
        self._tool_manager.subscribe_evolution_events()
        self._inner_registry = self._tool_manager.registry  # backward compat
        # Sub-agent management (extracted spawn/wait/check lifecycle)
        self._subagent_runner = SubAgentRunner(event_bus, audit_trail, agent_registry)
        self._subagent_runner._run_impl = self._run_sub_agent  # wire the LLM loop
        self._sub_agents = self._subagent_runner._sub_agents  # shared dict
        self._start_time = time.time()
        self._daemon_manager: Any = None
        self._cheap_llm: BaseLLMProvider | None = None
        self._loom: Any = None  # Knowledge system (TheLoom)
        self._intent_engine: Any = None  # Intent classification engine
        self._pattern_registry: Any = None  # Evolvable design pattern registry
        self._resource_registry: Any = None  # Linux-style resource tracking
        self._llm_capability: Any = None  # LLMCapability from probe (None = not probed yet)
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
        # Cost tracking (extracted to CostTracker)
        self._cost_tracker = CostTracker()
        # Backward compat — delegate to cost tracker
        self._session_tokens = 0
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        self._session_requests = 0
        self._session_cost_usd = 0.0
        self._lifetime_cost = self._cost_tracker.lifetime_cost
        # Context builder (extracted — assembles OS state for LLM)
        self._context_builder = ContextBuilder()
        self._context_builder._start_time = self._start_time

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

    def set_llm_capability(self, cap: Any) -> None:
        """Set the LLM capability from probe. Adapts prompts/tools per tier."""
        self._llm_capability = cap

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
        """Auto-load LLM from setup.json (delegated to LLMLoader)."""
        llm = LLMLoader.auto_load()
        if llm:
            self._llm = llm

    def _build_os_context(self) -> str:
        """Gather live OS state so the LLM can reason about OpenSculpt."""
        # Sync mutable refs into the context builder
        cb = self._context_builder
        cb._registry = self._registry
        cb._daemon_manager = self._daemon_manager
        cb._inner_registry = self._inner_registry
        cb._loom = self._loom
        cb._llm = self._llm
        cb._compactor = self._compactor
        cb._sub_agents = self._sub_agents
        cb._session_requests = self._session_requests
        cb._session_tokens = self._session_tokens
        cb._conversation_history = self._conversation_history
        return cb.build()

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
        # ── TIER-ADAPTIVE PROMPT + TOOLS ──
        _tier = self._llm_capability.tier if self._llm_capability else "full"
        if _tier == "chat_only":
            system = SYSTEM_PROMPT_CHAT_ONLY
        elif _tier == "basic_tools":
            system = SYSTEM_PROMPT_BASIC
        else:
            dynamic_context = self._build_os_context() + knowledge_context + os_rules
            system = SYSTEM_PROMPT_STATIC + CACHE_BOUNDARY + SYSTEM_PROMPT_DYNAMIC.format(context=dynamic_context)

        # Inject recent conversation history for continuity
        messages: list[LLMMessage] = []
        _history_limit = 2 if _tier in ("basic_tools", "chat_only") else 5
        for h in self._conversation_history[-_history_limit:]:
            messages.append(LLMMessage(role="user", content=h["command"]))
            messages.append(LLMMessage(role="assistant", content=h["response"]))
        messages.append(LLMMessage(role="user", content=command))

        if _tier == "chat_only":
            # No tools in chat_only mode
            all_tools = []
            tools = None
        else:
            # ── DEFERRED TOOL LOADING (Claude Code pattern) ──
            all_tools = self._tools.get_anthropic_tools(command=command)
            if len(all_tools) < 5:
                all_tools = self._tools.get_anthropic_tools()
            # Filter to core tools only for basic_tools tier
            if _tier == "basic_tools":
                all_tools = [t for t in all_tools if t["name"] in BASIC_TOOLS_WHITELIST]

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
                # ── TIER-ADAPTIVE max_tokens ──
                if _tier == "chat_only":
                    _max_tok = 512
                elif _tier == "basic_tools":
                    _max_tok = 512 if tools is None else 1024
                else:
                    _max_tok = 512 if tools is None else 4096

                # ── PRE-CALL CONTEXT CHECK ──
                if self._llm_capability and self._llm_capability.context_window > 0:
                    _ctx_limit = self._llm_capability.context_window
                    _est_input = len(system) // 4 + sum(len(str(m.content)) // 4 for m in messages)
                    if tools:
                        _est_input += len(tools) * 200  # ~200 tokens per tool schema
                    if _est_input + _max_tok > int(_ctx_limit * 0.9):
                        messages = _os_condenser.compact(messages)

                resp = await self._llm.complete(
                    messages=messages, system=system,
                    tools=tools, max_tokens=_max_tok,
                )

                # ── RESPONSE TRIAGE (catch weak-model failures before acting) ──
                _triage_retry_counts: dict[str, int] = getattr(self, "_triage_retry_counts", {})
                _triage_hint = None

                # A. Token limit hit
                if resp.stop_reason in ("length", "max_tokens"):
                    _k = "length"
                    _triage_retry_counts[_k] = _triage_retry_counts.get(_k, 0) + 1
                    if _triage_retry_counts[_k] <= 2:
                        _triage_hint = "Your response was cut off. Be more concise."

                # B. Hallucinated tool
                if not _triage_hint and resp.tool_calls and tools:
                    _known = {t["name"] for t in tools}
                    _bad = [tc for tc in resp.tool_calls if tc.name not in _known]
                    if _bad:
                        _k = "hallucinated"
                        _triage_retry_counts[_k] = _triage_retry_counts.get(_k, 0) + 1
                        if _triage_retry_counts[_k] <= 2:
                            _names = ", ".join(sorted(_known)[:5])
                            _triage_hint = f"Tool '{_bad[0].name}' doesn't exist. Available: {_names}"

                # C. Malformed tool args
                if not _triage_hint and resp.tool_calls:
                    for tc in resp.tool_calls:
                        if "raw" in tc.arguments:
                            _k = "malformed"
                            _triage_retry_counts[_k] = _triage_retry_counts.get(_k, 0) + 1
                            if _triage_retry_counts[_k] <= 2:
                                _triage_hint = f"Tool call to '{tc.name}' had invalid JSON. Send valid JSON arguments."
                            break

                # D. Empty response
                if not _triage_hint and not resp.content and not resp.tool_calls:
                    _k = "empty"
                    _triage_retry_counts[_k] = _triage_retry_counts.get(_k, 0) + 1
                    if _triage_retry_counts[_k] <= 2:
                        _triage_hint = "You returned nothing. Please respond or use a tool."

                self._triage_retry_counts = _triage_retry_counts

                if _triage_hint:
                    messages.append(LLMMessage(role="user", content=_triage_hint))
                    tokens += resp.input_tokens + resp.output_tokens
                    self._track_usage(resp.input_tokens, resp.output_tokens)
                    # Check if max retries exceeded for any type
                    if any(v > 2 for v in _triage_retry_counts.values()):
                        final_text = f"Stopped: model couldn't complete the request after retries."
                        await self._bus.emit("os.model_failure", {
                            "retry_counts": dict(_triage_retry_counts),
                            "tier": _tier,
                        }, source="os_agent")
                        break
                    continue

                # If first turn had no tools but model seems to want action, retry with tools
                if turns == 1 and tools is None and _tier != "chat_only" and resp.content:
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
                                # Exit code 1 for query tools (grep, findstr, find, test, diff)
                                # means "no match" — not a failure. Only flag exit >= 2 or
                                # exit 1 for non-query commands.
                                _cmd = str(tc.arguments.get("command", "")).strip().lower()
                                _query_tools = ("grep ", "findstr ", "find ", "test ", "diff ",
                                                "where ", "which ")
                                if exit_code == 1 and any(_cmd.startswith(q) or f"| {q.strip()}" in _cmd
                                                          for q in _query_tools):
                                    pass  # "no match" is informational, not a failure
                                else:
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
                "message": _sanitize_response(final_text),
                "data": {"turns": turns, "tokens_used": tokens, "steps": steps},
            }

        except Exception as e:
            # Fail-fast with clear messages for auth/connection errors
            from agos.llm.anthropic import AuthenticationError, ConnectionFailedError
            if isinstance(e, AuthenticationError):
                msg = "Your LLM API key is invalid. Update it in the Setup tab."
                await self._bus.emit("os.llm_fatal_error", {
                    "error": str(e), "type": "AuthenticationError",
                }, source="os_agent")
                return _reply(False, "error", msg)
            if isinstance(e, ConnectionFailedError):
                msg = "Cannot reach LLM provider. Check your internet connection or provider URL in Settings."
                await self._bus.emit("os.llm_fatal_error", {
                    "error": str(e), "type": "ConnectionFailedError",
                }, source="os_agent")
                return _reply(False, "error", msg)
            await self._bus.emit("os.error", {
                "command": command[:200], "error": str(e)[:300],
            }, source="os_agent")
            return _reply(False, "error", f"Failed: {e}")

    # ── Cost tracking (delegated to CostTracker in os_agent_context.py) ──

    def _calc_cost(self, input_tokens: int, output_tokens: int) -> float:
        model = getattr(self._llm, 'model', '') if self._llm else ''
        return self._cost_tracker.calc_cost(input_tokens, output_tokens, model)

    def _load_lifetime_cost(self) -> dict:
        return self._cost_tracker.load()

    def _save_lifetime_cost(self) -> None:
        # Sync session state into cost tracker before saving
        self._cost_tracker.session_cost_usd = self._session_cost_usd
        self._cost_tracker.session_input_tokens = self._session_input_tokens
        self._cost_tracker.session_output_tokens = self._session_output_tokens
        self._cost_tracker.session_requests = self._session_requests
        self._cost_tracker.save()

    def _track_usage(self, input_tokens: int, output_tokens: int) -> None:
        cost = self._calc_cost(input_tokens, output_tokens)
        self._session_input_tokens += input_tokens
        self._session_output_tokens += output_tokens
        self._session_tokens += input_tokens + output_tokens
        self._session_cost_usd += cost

    # ── Tool registration ────────────────────────────────────────

    # Tool registration, evolution hooks, and docker/browser packs are in
    # agos/os_agent_tools.py (ToolManager class). Wired up in __init__.

    def activate_tool_pack(self, pack: str) -> bool:
        """Manually activate a tool pack (for testing or first-run setup)."""
        return self._tool_manager.activate_pack(pack)

    # Docker and browser tool packs are in agos/os_agent_tools.py (ToolManager).

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

    # ── Sub-agent spawning (delegated to SubAgentRunner) ──────────

    def _select_design_pattern(self, task: str) -> tuple[str, str]:
        """Select the best agentic design pattern(s) for a task."""
        return self._subagent_runner.select_design_pattern(task)

    async def _spawn_agent(self, name: str, task: str, persona: str = "",
                            goal_id: str = "") -> str:
        """Spawn a sub-agent that works on a task independently."""
        self._subagent_runner.set_refs(llm=self._llm, tools=self._tools,
                                        approval=self._approval, loom=self._loom,
                                        pattern_registry=self._pattern_registry,
                                        resource_registry=self._resource_registry,
                                        capability_gate=self._capability_gate,
                                        cheap_llm=self._cheap_llm)
        return await self._subagent_runner.spawn(name, task, persona, goal_id=goal_id)

    async def _spawn_agent_and_wait(self, name: str, task: str,
                                     persona: str = "", timeout: int = 300,
                                     goal_id: str = "") -> dict:
        """Spawn ONE sub-agent and WAIT for it to complete."""
        self._subagent_runner.set_refs(llm=self._llm, tools=self._tools,
                                        approval=self._approval, loom=self._loom,
                                        pattern_registry=self._pattern_registry,
                                        resource_registry=self._resource_registry,
                                        capability_gate=self._capability_gate,
                                        cheap_llm=self._cheap_llm)
        return await self._subagent_runner.spawn_and_wait(name, task, persona, timeout, goal_id)

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
        # Meta-Harness lesson: capture full tool call steps for trace storage.
        # Unlike the truncated audit entries, these preserve full args + 2000-char output.
        _sub_steps: list[dict] = []

        def _store_sub_data(turns: int = 0) -> None:
            """Store execution data (steps, tokens, turns) in sub-agent record."""
            self._sub_agents[name]["data"] = {
                "turns": turns,
                "tokens_used": _total_tokens,
                "steps": _sub_steps,
            }

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
                    _store_sub_data(turn)
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
                    _store_sub_data(turn)
                    break

                # Retry LLM calls with exponential backoff (empty response / disconnect)
                # But fail IMMEDIATELY on auth errors (401) or connection failures
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
                        # Fail-fast on auth/connection errors — no point retrying
                        from agos.llm.anthropic import AuthenticationError, ConnectionFailedError
                        if isinstance(llm_err, (AuthenticationError, ConnectionFailedError)):
                            _logger.error("Sub-agent '%s' fatal LLM error: %s", name, llm_err)
                            self._sub_agents[name]["status"] = "done"
                            self._sub_agents[name]["result"] = f"(fatal: {llm_err})"
                            _store_sub_data(turn)
                            await self._bus.emit("os.llm_fatal_error", {
                                "error": str(llm_err),
                                "type": type(llm_err).__name__,
                                "agent": name,
                            }, source="os_agent")
                            return
                        _logger.warning("Sub-agent '%s' LLM error (attempt %d/3): %s", name, _retry + 1, llm_err)
                        if _retry < 2:
                            await asyncio.sleep(2 ** _retry)
                            continue
                        raise
                if not resp or (not resp.content and not resp.tool_calls):
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = "(LLM returned empty after 3 retries)"
                    _store_sub_data(turn)
                    break
                _total_tokens += resp.input_tokens + resp.output_tokens
                self._track_usage(resp.input_tokens, resp.output_tokens)

                if not resp.tool_calls:
                    self._sub_agents[name]["status"] = "done"
                    self._sub_agents[name]["result"] = resp.content or "(no output)"
                    _store_sub_data(turn)
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
                    # Capture step for execution traces (Meta-Harness pattern)
                    _sub_steps.append({
                        "tool": tc.name,
                        "full_args": tc.arguments,    # full args, not truncated
                        "ok": _real_ok,
                        "preview": out[:200],
                        "full_output": out[:2000],    # 10x audit, capped for disk
                        "ms": int(res.execution_time_ms),
                    })
                messages.append(LLMMessage(role="user", content=results))

                # Record tool calls for LoopGuard (OpenFang SHA256 pattern detection)
                for tc in resp.tool_calls:
                    _sub_loop_guard.record(tc.name, tc.arguments)
            else:
                self._sub_agents[name]["status"] = "done"
                self._sub_agents[name]["result"] = resp.content or "(max turns reached)"
                _store_sub_data(25)

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
            _store_sub_data(0)  # Preserve steps collected before crash
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
        return await self._subagent_runner.check(name)


# ── Standalone tool implementations ──────────────────────────────


# Tool handler functions, factory closures, and response utilities are in
# agos/os_agent_tools.py — imported at the top of this file.
