"""OS Agent Context — prompts, state assembly, cost tracking, LLM loading.

Extracted from os_agent.py following the industry pattern of separating
context assembly from the reasoning loop (OpenHands Condenser, OpenClaw
bootstrap files, Anthropic SDK context gathering).

Contains:
- System prompt constants (static/dynamic/tiered)
- ContextBuilder: assembles live OS state for the LLM
- CostTracker: per-model token cost accounting
- LLMLoader: provider auto-detection from setup.json
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


# ── Prompt Constants ──────────────────────────────────────────────

MAX_TURNS = 40
MAX_TOKENS = 200_000

# Prompt Cache Boundary (Claude Code pattern)
# Split system prompt into STATIC prefix (cacheable, identical across turns)
# and DYNAMIC suffix (changes per turn: live state, memory, rules).
# Anthropic prompt caching gives 90% savings on the static prefix.
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

SYSTEM_PROMPT_DYNAMIC = """

LIVE OS STATE:
{context}"""

SYSTEM_PROMPT = SYSTEM_PROMPT_STATIC + SYSTEM_PROMPT_DYNAMIC

SYSTEM_PROMPT_BASIC = """\
You are OpenSculpt. You help users by creating goals.
- Use set_goal to start any task. Provide a goal and a list of steps.
- Use check_goals to see progress.
- Use shell to run commands.
Be concise. One goal at a time."""

SYSTEM_PROMPT_CHAT_ONLY = """\
You are OpenSculpt, an AI assistant. Answer questions helpfully.
You cannot execute tasks or run commands in this mode.
If the user asks you to do something, explain what steps they could take manually."""

BASIC_TOOLS_WHITELIST = {"set_goal", "check_goals", "shell"}


# ── Context Builder ───────────────────────────────────────────────

class ContextBuilder:
    """Assembles live OS state for the LLM system prompt.

    Pure read — gathers state from subsystems without modifying anything.
    """

    def __init__(self) -> None:
        # Set by OSAgent after init
        self._start_time: float = time.time()
        self._registry: Any = None
        self._daemon_manager: Any = None
        self._inner_registry: Any = None
        self._loom: Any = None
        self._llm: Any = None
        self._compactor: Any = None
        self._sub_agents: dict = {}
        self._session_requests: int = 0
        self._session_tokens: int = 0
        self._conversation_history: list = []

    def build(self) -> str:
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
        if self._daemon_manager:
            daemons = self._daemon_manager.list_daemons()
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

        # Tools
        if self._inner_registry:
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

        if self._loom:
            parts.append("KNOWLEDGE: TheLoom active (episodic + semantic + graph memory)")

        compact_info = ""
        if self._compactor and self._compactor.stats.get('compactions'):
            compact_info = f", {self._compactor.stats['compactions']} compactions"
        parts.append(f"SESSION: {self._session_requests} requests, {self._session_tokens:,} tokens used, {len(self._conversation_history)} in memory{compact_info}")

        return "\n\n".join(parts)


# ── Cost Tracker ──────────────────────────────────────────────────

class CostTracker:
    """Per-model token cost accounting."""

    # Per-million-token pricing (USD). Input / Output.
    MODEL_PRICING = {
        "claude-haiku-4-5-20251001": (1.00, 5.00),
        "claude-sonnet-4-20250514": (3.00, 15.00),
        "claude-opus-4-20250514": (15.00, 75.00),
        "anthropic/claude-haiku-4-5": (0.80, 4.00),
        "anthropic/claude-sonnet-4": (3.00, 15.00),
        "anthropic/claude-opus-4": (15.00, 75.00),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "deepseek-chat": (0.27, 1.10),
        "gemini-2.5-flash": (0.15, 0.60),
        "gemini-2.5-pro": (1.25, 10.00),
        "_default": (1.00, 5.00),
    }

    def __init__(self) -> None:
        self.session_tokens = 0
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_requests = 0
        self.session_cost_usd = 0.0
        self.lifetime_cost = self.load()

    def calc_cost(self, input_tokens: int, output_tokens: int, model: str = "") -> float:
        """Calculate USD cost from actual token counts."""
        pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING["_default"])
        input_cost = (input_tokens / 1_000_000) * pricing[0]
        output_cost = (output_tokens / 1_000_000) * pricing[1]
        return input_cost + output_cost

    def track(self, input_tokens: int, output_tokens: int, model: str = "") -> None:
        """Track token usage and cost for a single LLM call."""
        cost = self.calc_cost(input_tokens, output_tokens, model)
        self.session_input_tokens += input_tokens
        self.session_output_tokens += output_tokens
        self.session_tokens += input_tokens + output_tokens
        self.session_cost_usd += cost

    def load(self) -> dict:
        """Load accumulated cost from .opensculpt/cost.json."""
        cost_path = Path(".opensculpt/cost.json")
        if cost_path.exists():
            try:
                return json.loads(cost_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"total_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}

    def save(self) -> None:
        """Persist accumulated cost to disk."""
        cost_path = Path(".opensculpt/cost.json")
        cost_path.parent.mkdir(parents=True, exist_ok=True)
        self.lifetime_cost["total_usd"] += self.session_cost_usd
        self.lifetime_cost["input_tokens"] += self.session_input_tokens
        self.lifetime_cost["output_tokens"] += self.session_output_tokens
        self.lifetime_cost["requests"] += self.session_requests
        cost_path.write_text(json.dumps(self.lifetime_cost, indent=2), encoding="utf-8")


# ── LLM Loader ────────────────────────────────────────────────────

class LLMLoader:
    """Auto-detect and load LLM providers from setup.json."""

    @staticmethod
    def auto_load() -> Any:
        """Try loading LLM from setup.json. Returns (llm, cheap_llm) or (None, None)."""
        import os as _os
        try:
            from agos.setup_store import load_setup
            from agos.llm.providers import ALL_PROVIDERS

            for ws in [
                _os.path.join(_os.getcwd(), ".opensculpt"),
                _os.path.join(_os.path.expanduser("~"), ".opensculpt"),
                _os.path.join(_os.getcwd(), ".agos"),
                _os.path.join(_os.path.expanduser("~"), ".agos"),
            ]:
                if not _os.path.isdir(ws):
                    continue
                data = load_setup(ws)
                providers = data.get("providers", {})

                active = data.get("active_provider", "")
                if active and active in providers:
                    cfg = providers[active]
                    llm = LLMLoader._try_load(active, cfg, ALL_PROVIDERS)
                    if llm:
                        return llm

                for name, cfg in providers.items():
                    if not cfg.get("enabled", False):
                        continue
                    llm = LLMLoader._try_load(name, cfg, ALL_PROVIDERS)
                    if llm:
                        return llm
        except Exception:
            pass
        return None

    @staticmethod
    def _try_load(name: str, cfg: dict, all_providers: dict) -> Any:
        """Try to load a single LLM provider. Returns provider or None."""
        if name == "anthropic" and cfg.get("api_key"):
            from agos.llm.anthropic import AnthropicProvider
            try:
                return AnthropicProvider(
                    api_key=cfg["api_key"],
                    model=cfg.get("model", "claude-haiku-4-5-20251001"),
                )
            except Exception:
                return None
        cls = all_providers.get(name)
        if not cls:
            return None
        kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
        try:
            return cls(**kwargs)
        except Exception:
            return None

    @staticmethod
    def create_cheap_llm(provider_name: str, api_key: str) -> Any:
        """Create a cheap/fast LLM for simple tasks."""
        try:
            if provider_name == "anthropic":
                from agos.llm.anthropic import AnthropicProvider
                return AnthropicProvider(api_key=api_key, model="anthropic/claude-haiku-4-5")
            elif provider_name == "openrouter":
                from agos.llm.providers import OpenRouterProvider
                return OpenRouterProvider(api_key=api_key, model="anthropic/claude-haiku-4-5")
        except Exception:
            pass
        return None
