"""OpenSculpt live server — dashboard + real agent engine running together."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from agos.config import settings
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.policy.engine import PolicyEngine
from agos.events.tracing import Tracer
from agos.knowledge.manager import TheLoom
from agos.evolution.state import EvolutionState
from agos.evolution.meta import MetaEvolver
from agos.processes.manager import ProcessManager
from agos.processes.workload import WorkloadDiscovery
from agos.processes.registry import AgentRegistry
from agos.os_agent import OSAgent
from agos.dashboard.app import dashboard_app, configure
from agos.boot import boot_system
from agos.mcp.client import MCPManager
from agos.mcp.config import load_mcp_configs
from agos.approval.gate import ApprovalGate, ApprovalMode
from agos.daemons.manager import DaemonManager
from agos.evolution.demand import DemandCollector

_logger = logging.getLogger(__name__)


async def _boot_os(
    agent_registry: AgentRegistry,
    event_bus: EventBus,
) -> None:
    """OS boot sequence: discover available agents (but don't start them).

    Like Windows discovering installed programs on boot —
    they show up in the Start Menu, but the user decides when to run them.
    """
    await event_bus.emit("os.boot", {"phase": "agent_discovery"}, source="kernel")

    # Discover bundled agents (shipped with the OS image)
    available = await agent_registry.discover_available()
    _logger.info("Discovered %d available agents", len(available))

    for agent in available:
        _logger.info(
            "  [%s] %s (%s) — %s",
            agent.runtime, agent.display_name, agent.name, agent.status.value,
        )

    await event_bus.emit("os.boot", {
        "phase": "complete",
        "agents_available": len(available),
        "agents": [{"name": a.name, "runtime": a.runtime} for a in available],
    }, source="kernel")


def _detect_provider_from_key(api_key: str) -> str | None:
    """Auto-detect LLM provider from API key prefix."""
    if not api_key:
        return None
    prefixes = {
        "sk-ant-": "anthropic",
        "sk-or-": "openrouter",
        "sk-proj-": "openai",
        "sk-": "openai",      # generic OpenAI
        "gsk_": "groq",
        "xai-": "xai",
        "pplx-": "perplexity",
        "r8_": "replicate",
    }
    for prefix, provider in prefixes.items():
        if api_key.startswith(prefix):
            return provider
    return None


async def main() -> None:
    event_bus = EventBus()
    policy_engine = PolicyEngine()
    tracer = Tracer()

    settings.workspace_dir.mkdir(parents=True, exist_ok=True)

    # ── P0: Auto-generate dashboard API key on first boot ──
    if not settings.dashboard_api_key:
        import secrets
        _key_file = settings.workspace_dir / ".dashboard_key"
        if _key_file.exists():
            settings.dashboard_api_key = _key_file.read_text(encoding="utf-8").strip()
        else:
            _generated_key = f"sculpt-{secrets.token_urlsafe(24)}"
            _key_file.write_text(_generated_key, encoding="utf-8")
            settings.dashboard_api_key = _generated_key
            _logger.warning(
                "Generated dashboard API key: %s\n"
                "  Set SCULPT_DASHBOARD_API_KEY env var to override.\n"
                "  Key saved to %s",
                _generated_key, _key_file,
            )

    db_path = str(settings.workspace_dir / "opensculpt.db")
    audit_trail = AuditTrail(db_path)
    await audit_trail.initialize()

    # Initialize TheLoom knowledge substrate for evolution
    loom_path = str(settings.workspace_dir / "knowledge.db")
    loom = TheLoom(loom_path)
    await loom.initialize()

    # Initialize evolution state persistence
    evolution_state = EvolutionState(settings.workspace_dir / "evolution_state.json")

    # Initialize meta-evolver (ALMA-style all-component evolution)
    meta_evolver = MetaEvolver()

    # Initialize OS process management
    process_manager = ProcessManager(event_bus, audit_trail)
    # Workload dirs: local ./workloads/ + workspace agents dir
    _workload_dir = str(settings.workspace_dir / "agents")
    Path(_workload_dir).mkdir(parents=True, exist_ok=True)
    workload_discovery = WorkloadDiscovery(event_bus, audit_trail, workload_dir=_workload_dir)

    # Initialize agent registry (user-installed agents)
    agent_registry = AgentRegistry(
        event_bus=event_bus,
        audit_trail=audit_trail,
        process_manager=process_manager,
        workload_discovery=workload_discovery,
        state_path=settings.workspace_dir / "agent_registry.json",
    )

    # Initialize LLM — use setup.json provider (any provider), fall back to Anthropic env var
    # P0: Wrapped in timeout so bad LLM config doesn't hang boot forever
    llm = None

    def _init_llm():
        """Synchronous LLM init — called inside timeout wrapper."""
        nonlocal llm
        try:
            from agos.setup_store import load_setup
            from agos.llm.providers import ALL_PROVIDERS
            import os as _os
            for ws in [str(settings.workspace_dir), _os.path.join(_os.getcwd(), ".opensculpt")]:
                if not _os.path.isdir(ws):
                    continue
                data = load_setup(ws)
                providers_cfg = data.get("providers", {})

                # P1: Auto-detect provider from key prefix
                for prov_name, cfg in list(providers_cfg.items()):
                    key = cfg.get("api_key", "")
                    detected = _detect_provider_from_key(key)
                    if detected and detected != prov_name:
                        _logger.warning("Key prefix suggests '%s' not '%s' — auto-correcting", detected, prov_name)
                        providers_cfg[detected] = {**cfg, "enabled": True}
                        del providers_cfg[prov_name]
                        data["providers"] = providers_cfg
                        data["active_provider"] = detected
                        # Persist the fix
                        from agos.setup_store import save_setup
                        save_setup(ws, data)

                # Try active provider first
                active = data.get("active_provider", "")
                if active and active in providers_cfg:
                    cfg = providers_cfg[active]
                    kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
                    cls = ALL_PROVIDERS.get(active)
                    if cls:
                        try:
                            llm = cls(**kwargs)
                            return
                        except Exception:
                            pass
                # Try all enabled providers
                for name, cfg in providers_cfg.items():
                    if not cfg.get("enabled"):
                        continue
                    kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
                    cls = ALL_PROVIDERS.get(name)
                    if cls:
                        try:
                            llm = cls(**kwargs)
                            return
                        except Exception:
                            continue
                if llm is not None:
                    return
        except Exception:
            pass
        # Fallback: Anthropic env var
        if llm is None and settings.anthropic_api_key:
            from agos.llm.anthropic import AnthropicProvider
            llm = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.default_model,
            )

    try:
        await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, _init_llm), timeout=30)
    except asyncio.TimeoutError:
        _logger.error("LLM initialization timed out after 30s — starting without LLM")
    except Exception as e:
        _logger.error("LLM initialization failed: %s — starting without LLM", e)

    if llm:
        _logger.info("LLM provider initialized: %s", type(llm).__name__)
        # Validate API key at boot — catch 401 immediately, not after 6 retries
        if hasattr(llm, 'validate_key'):
            try:
                ok, err_msg = await llm.validate_key()
                if not ok:
                    _logger.error("LLM API key validation FAILED: %s", err_msg)
                    await event_bus.emit("os.llm_fatal_error", {
                        "error": err_msg,
                        "type": "AuthenticationError",
                        "phase": "boot",
                    }, source="kernel")
                    # Don't nullify llm — let dashboard show the error clearly
                else:
                    _logger.info("LLM API key validated OK")
            except Exception as e:
                _logger.warning("LLM key validation check failed: %s (continuing anyway)", e)
    else:
        _logger.warning("No LLM configured. Run `sculpt setup` or set SCULPT_LLM_API_KEY.")

    # Initialize approval gate (human-in-the-loop for dashboard)
    approval_gate = ApprovalGate(
        mode=ApprovalMode(settings.approval_mode),
        event_bus=event_bus,
        timeout_seconds=settings.approval_timeout_seconds,
    )

    # Build sandbox config from default policy
    sandbox_config = None
    default_policy = policy_engine.get_policy("*")
    if default_policy.sandbox_level != "none":
        from agos.sandbox.executor import SandboxConfig, SandboxLevel
        sandbox_config = SandboxConfig(
            level=SandboxLevel(default_policy.sandbox_level),
            memory_limit_mb=default_policy.sandbox_memory_limit_mb,
            cpu_time_limit_s=default_policy.sandbox_cpu_time_limit_s,
            allowed_paths=default_policy.sandbox_allowed_paths,
        )

    # Initialize the OS agent (the brain) — with real Claude reasoning
    os_agent = OSAgent(
        event_bus=event_bus,
        audit_trail=audit_trail,
        agent_registry=agent_registry,
        process_manager=process_manager,
        policy_engine=policy_engine,
        llm=llm,
        approval_gate=approval_gate,
        sandbox_config=sandbox_config,
    )

    # Initialize resource registry (Linux-style process table for deployed resources)
    from agos.processes.resources import ResourceRegistry
    resource_registry = ResourceRegistry(
        state_path=settings.workspace_dir / "resource_registry.json",
    )
    os_agent.set_resource_registry(resource_registry)
    _logger.info("ResourceRegistry wired — %d active resources", len(resource_registry.active()))

    # Initialize pattern registry (evolvable design patterns with fitness tracking)
    from agos.evolution.pattern_registry import PatternRegistry
    pattern_registry = PatternRegistry()
    pattern_registry.seed_builtins()
    # Restore persisted patterns from evolution state if available
    if evolution_state and evolution_state.data:
        saved = getattr(evolution_state.data, 'pattern_registry', None)
        if saved:
            try:
                pattern_registry = PatternRegistry.from_dict(saved)
                _logger.info("Restored %d patterns from evolution state", len(pattern_registry.all_patterns()))
            except Exception:
                pass
    os_agent.set_pattern_registry(pattern_registry)
    _logger.info("PatternRegistry wired — %d patterns available", len(pattern_registry.all_patterns()))

    # Initialize intent engine (classifies user commands for pattern selection)
    intent_engine = None
    if llm:
        try:
            from agos.intent.engine import IntentEngine
            intent_engine = IntentEngine(llm)
            _logger.info("IntentEngine wired — command classification active")
        except Exception as e:
            _logger.debug("IntentEngine init failed: %s", e)
    os_agent.set_intent_engine(intent_engine)

    # Initialize demand collector — restore from disk so demands survive restarts
    demand_collector = DemandCollector()
    try:
        import json as _json
        _signals_path = Path(settings.workspace_dir) / "demand_signals.json"
        if _signals_path.exists():
            _saved = _json.loads(_signals_path.read_text(encoding="utf-8"))
            demand_collector = DemandCollector.from_dict(_saved)
            _logger.info("Restored %d demand signals from disk", len(demand_collector._signals))
    except Exception as e:
        _logger.warning("Could not restore demands: %s", e)
    demand_collector.subscribe(event_bus)
    _logger.info("DemandCollector wired to EventBus — evolution demand signals active")

    # Activate docker and browser tools at boot so scenarios work immediately
    os_agent.activate_tool_pack("docker")
    os_agent.activate_tool_pack("browser")
    _logger.info("Docker and browser tools activated at boot")

    # Initialize MCP manager (external tool servers)
    # Register MCP tools on the inner registry so they also go through
    # the sandbox wrapper when executed.
    mcp_manager = MCPManager(
        registry=os_agent._inner_registry,
        event_bus=event_bus,
    )
    if settings.mcp_auto_connect:
        mcp_configs = await load_mcp_configs(settings.workspace_dir)
        for mc in mcp_configs:
            if mc.enabled:
                try:
                    await mcp_manager.add_server(mc)
                except Exception as e:
                    _logger.warning("Failed to connect MCP server '%s': %s", mc.name, e)

    # Initialize A2A server (Agent-to-Agent protocol)
    a2a_server = None
    if settings.a2a_enabled:
        from agos.a2a.server import A2AServer
        from agos.a2a.client import A2ADirectory
        a2a_server = A2AServer(
            os_agent=os_agent,
            agent_registry=agent_registry,
            event_bus=event_bus,
        )
        a2a_server.set_base_url(
            f"http://{settings.dashboard_host}:{settings.dashboard_port}"
        )

        # A2A discovery runs as a background task after uvicorn starts,
        # because in a fleet all nodes boot simultaneously and need time
        # for their HTTP servers to come up before they can discover each other.
        async def _discover_a2a_peers() -> None:
            await asyncio.sleep(30)  # wait for all nodes to start serving
            if not settings.a2a_remote_agents:
                return
            a2a_dir = A2ADirectory(
                state_path=settings.workspace_dir / "a2a_directory.json",
            )
            for url in settings.a2a_remote_agents.split(","):
                url = url.strip()
                if not url:
                    continue
                for attempt in range(3):
                    try:
                        await a2a_dir.register(url)
                        _logger.info("Registered remote A2A agent: %s", url)
                        break
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(10)
                        else:
                            _logger.warning("Failed to discover A2A agent at '%s': %s", url, e)

    # Initialize Daemons (autonomous capability packages)
    daemon_manager = DaemonManager(event_bus=event_bus, audit=audit_trail)
    daemon_manager.register_builtin_daemons()
    os_agent.set_daemon_manager(daemon_manager)
    os_agent.set_loom(loom)

    # Initialize tagged knowledge stores (scalable: environment-tagged .md directories)
    from agos.knowledge.tagged_store import TaggedConstraintStore, TaggedResolutionStore
    _constraint_store = TaggedConstraintStore()
    _resolution_store = TaggedResolutionStore()

    # Migrate legacy flat files → tagged directories (one-time, on first boot after upgrade)
    _flat_constraints = Path(settings.workspace_dir) / "constraints.md"
    _flat_resolutions = Path(settings.workspace_dir) / "resolutions.md"
    _migrated_c = _constraint_store.migrate_flat_file(_flat_constraints)
    _migrated_r = _resolution_store.migrate_flat_file(_flat_resolutions)
    if _migrated_c or _migrated_r:
        _logger.info("Migrated knowledge: %d constraints, %d resolutions → tagged directories", _migrated_c, _migrated_r)

    _logger.info("Knowledge stores ready: %d constraints, %d resolutions",
                 _constraint_store.count(), _resolution_store.count())

    # Wire GC daemon dependencies (resource registry, goal runner, agent registry, loom)
    _gc = daemon_manager.get_gc()
    if _gc:
        _gc.set_resource_registry(resource_registry)
        _gc.set_goal_runner(daemon_manager.get_goal_runner())
        _gc.set_agent_registry(agent_registry)
        _gc.set_loom(loom)
        _gc.set_daemon_manager(daemon_manager)
        _gc.set_audit_trail(audit_trail)
        _gc.set_demand_collector(demand_collector)
        _gc.set_os_agent(os_agent)
        _logger.info("GarbageCollector wired — internal + AWS resource reclamation ready")

    # Wire resource registry to goal runner (for phase-scoped cleanup before retry)
    _gr = daemon_manager.get_goal_runner()
    if _gr:
        _gr.set_resource_registry(resource_registry)

    # Auto-start GC daemon — resource cleanup is NOT optional (prevents OOM)
    async def _auto_start_gc():
        await asyncio.sleep(30)  # let boot complete
        try:
            await daemon_manager.start_daemon("gc", config={"dry_run": True})
            _logger.info("GC daemon auto-started (dry_run=True — set SCULPT_GC_LIVE=1 to enable deletion)")
        except Exception as e:
            _logger.warning("GC auto-start failed: %s", e)
    asyncio.create_task(_auto_start_gc())

    # Wire Chaos Monkey daemon (if enabled) with OS agent + demand collector
    _chaos = getattr(daemon_manager, "_chaos", None)
    if _chaos:
        _chaos.set_os_agent(os_agent)
        _chaos.set_demand_collector(demand_collector)
        _chaos.set_evo_memory(evolution_state)
        _logger.info("Chaos Monkey wired — two-layer resilience testing ready")

    # Auto-start ServiceKeeper — restore deployed services on boot
    from agos.services import ServiceKeeper
    service_keeper = ServiceKeeper(os_agent=os_agent)
    async def _auto_start_service_keeper():
        await asyncio.sleep(15)  # let boot + LLM init complete
        try:
            restored = await service_keeper.boot_restore()
            _logger.info("ServiceKeeper restored %d services on boot", len(restored))
            await service_keeper.start()
            _logger.info("ServiceKeeper daemon started (30s health checks)")
        except Exception as e:
            _logger.warning("ServiceKeeper start failed: %s", e)
    asyncio.create_task(_auto_start_service_keeper())

    # Initialize Task Planner (persistent multi-step tasks)
    from agos.task_planner import TaskPlanner
    task_planner = TaskPlanner(workspace_dir=settings.workspace_dir)

    # Wire into dashboard
    configure(
        event_bus=event_bus,
        audit_trail=audit_trail,
        policy_engine=policy_engine,
        tracer=tracer,
        loom=loom,
        evolution_state=evolution_state,
        meta_evolver=meta_evolver,
        process_manager=process_manager,
        workload_discovery=workload_discovery,
        agent_registry=agent_registry,
        os_agent=os_agent,
        mcp_manager=mcp_manager,
        approval_gate=approval_gate,
        a2a_server=a2a_server,
        daemon_manager=daemon_manager,
        task_planner=task_planner,
        demand_collector=demand_collector,
        resource_registry=resource_registry,
        service_keeper=service_keeper,
    )

    # Reality check loop: verify tracked resources are actually alive
    async def _reality_check_loop():
        await asyncio.sleep(30)
        while True:
            try:
                changes = await resource_registry.reconcile()
                if changes.get("updated"):
                    await event_bus.emit("os.reality_check", {
                        "alive": changes["alive"],
                        "dead": changes["dead"],
                        "changes": changes["updated"],
                    }, source="resource_registry")
            except Exception:
                pass
            await asyncio.sleep(60)

    asyncio.create_task(_reality_check_loop())

    # Boot: discover available agents (don't start them — user decides)
    boot_task = asyncio.create_task(
        _boot_os(agent_registry, event_bus)
    )

    # Start system-level agents + evolution engine
    boot_task_sys = asyncio.create_task(
        boot_system(None, event_bus, audit_trail, policy_engine, tracer,
                    loom=loom, evolution_state=evolution_state,
                    meta_evolver=meta_evolver,
                    demand_collector=demand_collector)
    )

    # Discover A2A peers (delayed to let all nodes boot first)
    if settings.a2a_enabled:
        asyncio.create_task(_discover_a2a_peers())

    # Fleet sync — peer-to-peer evolution sharing (replaces GitHub PRs at scale)
    if settings.fleet_sync_enabled:
        from agos.evolution.sync import sync_loop
        from agos.evolution.state import EvolutionMemory, DesignArchive
        _sync_memory = evolution_state.restore_evolution_memory() if evolution_state else EvolutionMemory()
        _sync_archive = evolution_state.restore_design_archive() if evolution_state else DesignArchive()
        asyncio.create_task(sync_loop(
            event_bus, evolution_state, _sync_memory, _sync_archive,
        ))

    # Auto-open browser after a short delay
    import webbrowser
    async def _open_browser():
        await asyncio.sleep(2)
        url = f"http://localhost:{settings.dashboard_port}"
        _logger.info("Opening dashboard at %s", url)
        webbrowser.open(url)
    asyncio.create_task(_open_browser())

    # Run uvicorn — auto-find free port if default is taken
    import socket
    port = settings.dashboard_port
    for attempt in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((settings.dashboard_host, port))
            sock.close()
            break  # port is free
        except OSError:
            _logger.warning("Port %d in use, trying %d", port, port + 1)
            port += 1
    settings.dashboard_port = port

    config = uvicorn.Config(
        dashboard_app,
        host=settings.dashboard_host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()

    # Cleanup on shutdown
    await process_manager.shutdown()
    boot_task.cancel()
    boot_task_sys.cancel()


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())
