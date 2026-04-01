"""CLI runtime context — bridges sync CLI to async kernel."""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

import os as _os

from agos.config import settings
from agos.kernel.runtime import AgentRuntime
from agos.tools.registry import ToolRegistry
from agos.tools.builtins import register_builtin_tools
from agos.tools.extended import register_extended_tools
from agos.intent.engine import IntentEngine
from agos.intent.planner import Planner
from agos.knowledge.manager import TheLoom
from agos.triggers.manager import TriggerManager
from agos.policy.engine import PolicyEngine
from agos.policy.audit import AuditTrail
from agos.events.bus import EventBus
from agos.events.tracing import Tracer
from agos.evolution.scout import ArxivScout
from agos.evolution.analyzer import PaperAnalyzer
from agos.evolution.engine import EvolutionEngine
from agos.evolution.repo_scout import RepoScout
from agos.evolution.code_analyzer import CodeAnalyzer
from agos.evolution.sandbox import Sandbox
from agos.evolution.integrator import EvolutionIntegrator
from agos.evolution.strategies.memory_softmax import SoftmaxScoringStrategy
from agos.evolution.strategies.memory_layered import LayeredRetrievalStrategy
from agos.evolution.strategies.memory_semaphore import SemaphoreBatchStrategy
from agos.evolution.strategies.memory_confidence import AdaptiveConfidenceStrategy
from agos.knowledge.consolidator import Consolidator
from agos.ambient.watcher import (
    AmbientManager, GitWatcher, FileActivityWatcher, DailyBriefingWatcher,
)
from agos.intent.proactive import (
    ProactiveEngine,
    RepetitiveEditDetector, FailurePatternDetector,
    FrequentToolDetector, IdleProjectDetector,
)
from agos.mcp.client import MCPManager


class AgosContext:
    """Singleton runtime context that holds all subsystem instances."""

    _instance: AgosContext | None = None

    def __init__(self) -> None:
        self.llm = self._load_llm_from_setup()
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        register_extended_tools(self.tool_registry)
        self.runtime = AgentRuntime(
            llm_provider=self.llm,
            tool_registry=self.tool_registry,
        )
        self.intent_engine = IntentEngine(self.llm)
        self.planner = Planner(self.runtime)

        # Knowledge system
        db_path = str(settings.workspace_dir / "agos.db")
        self.loom = TheLoom(db_path)
        self._loom_initialized = False

        # Triggers — ambient awareness
        self.trigger_manager = TriggerManager()

        # Safety & observability
        self.policy_engine = PolicyEngine()
        self.audit_trail = AuditTrail(db_path)
        self.event_bus = EventBus()
        self.tracer = Tracer()
        self._audit_initialized = False

        # Evolution engine — self-improving via research
        self._scout = ArxivScout()
        self._analyzer = PaperAnalyzer(self.llm)
        self._repo_scout = RepoScout()
        self._code_analyzer = CodeAnalyzer(self.llm)
        self._sandbox = Sandbox()
        self.evolution_engine: EvolutionEngine | None = None
        self._integrator: EvolutionIntegrator | None = None

        # Ambient intelligence
        self.ambient_manager = AmbientManager()
        self.ambient_manager.register(GitWatcher())
        self.ambient_manager.register(FileActivityWatcher())
        self.ambient_manager.register(DailyBriefingWatcher())

        # MCP — Model Context Protocol
        self.mcp_manager = MCPManager(
            registry=self.tool_registry,
            event_bus=self.event_bus,
        )
        self._mcp_initialized = False

        # Proactive engine
        self.proactive_engine = ProactiveEngine()
        self.proactive_engine.register_detector(RepetitiveEditDetector())
        self.proactive_engine.register_detector(FailurePatternDetector())
        self.proactive_engine.register_detector(FrequentToolDetector())
        self.proactive_engine.register_detector(IdleProjectDetector())

    async def ensure_loom(self) -> TheLoom:
        """Initialize knowledge system on first use (async)."""
        if not self._loom_initialized:
            settings.workspace_dir.mkdir(parents=True, exist_ok=True)
            await self.loom.initialize()
            self._loom_initialized = True
        if not self._audit_initialized:
            await self.audit_trail.initialize()
            self._audit_initialized = True
        if self._integrator is None:
            consolidator = Consolidator(
                self.loom.episodic, self.loom.semantic, self.loom.graph
            )
            self._integrator = EvolutionIntegrator(
                loom=self.loom,
                event_bus=self.event_bus,
                audit_trail=self.audit_trail,
                sandbox=self._sandbox,
            )
            self._integrator.register_strategy(
                SoftmaxScoringStrategy(self.loom.semantic)
            )
            self._integrator.register_strategy(
                LayeredRetrievalStrategy(self.loom)
            )
            self._integrator.register_strategy(
                SemaphoreBatchStrategy(consolidator)
            )
            self._integrator.register_strategy(
                AdaptiveConfidenceStrategy(self.loom.semantic)
            )
        # Wire loom + event bus into proactive engine
        if self.proactive_engine._loom is None:
            self.proactive_engine._loom = self.loom
            self.proactive_engine._event_bus = self.event_bus

        # Auto-connect to configured MCP servers
        if settings.mcp_auto_connect and not self._mcp_initialized:
            from agos.mcp.config import load_mcp_configs
            mcp_configs = await load_mcp_configs(settings.workspace_dir)
            for mc in mcp_configs:
                if mc.enabled:
                    try:
                        await self.mcp_manager.add_server(mc)
                    except Exception:
                        pass  # Log but don't fail startup
            self._mcp_initialized = True

        if self.evolution_engine is None:
            self.evolution_engine = EvolutionEngine(
                scout=self._scout,
                analyzer=self._analyzer,
                loom=self.loom,
                event_bus=self.event_bus,
                audit_trail=self.audit_trail,
                repo_scout=self._repo_scout,
                code_analyzer=self._code_analyzer,
                sandbox=self._sandbox,
                integrator=self._integrator,
            )
        return self.loom

    @staticmethod
    def _load_llm_from_setup():
        """Load LLM provider from setup.json, falling back to Anthropic."""
        from agos.llm.providers import ALL_PROVIDERS
        try:
            from agos.setup_store import load_setup
            for ws in [_os.path.join(_os.getcwd(), ".agos"),
                        _os.path.join(_os.path.expanduser("~"), ".agos")]:
                if not _os.path.isdir(ws):
                    continue
                data = load_setup(ws)
                providers = data.get("providers", {})
                for name, cfg in providers.items():
                    if not cfg.get("enabled", False):
                        continue
                    cls = ALL_PROVIDERS.get(name)
                    if not cls:
                        continue
                    kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
                    try:
                        return cls(**kwargs)
                    except Exception:
                        continue
        except Exception:
            pass
        # Fallback to Anthropic if configured
        from agos.llm.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.default_model,
        )

    @classmethod
    def get(cls) -> AgosContext:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def run_async(coro: Coroutine) -> Any:
    """Run an async coroutine from sync CLI code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're already in an async context — shouldn't happen in CLI
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)
