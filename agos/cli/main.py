"""OpenSculpt CLI — natural language first.

`sculpt "do something"` routes to the Intent Engine.
`sculpt ps`, `sculpt init`, etc. are management subcommands.

The trick: we intercept sys.argv BEFORE Typer sees it. If the first
argument is not a known subcommand, we treat the entire input as
natural language intent.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from agos.cli import agents, setup, system

console = Console()

# Known subcommands — anything else is natural language
_SUBCOMMANDS = {
    "ps", "init", "status", "agent", "system", "ask", "recall", "timeline",
    "watch", "schedule", "triggers", "team",
    "audit", "policy", "dashboard", "evolve", "ambient", "proactive",
    "update", "version", "mcp", "talk",
    "provider", "channel", "tool", "setup",
    "seed", "contribute", "curator", "demands", "verify",
    "--help", "-h", "--install-completion", "--show-completion",
}

_app = typer.Typer(
    name="sculpt",
    help="sculpt — OpenSculpt: The Self-Evolving Agentic OS.",
    no_args_is_help=True,
)

# Register subcommand groups
_app.add_typer(agents.app, name="agent", help="Manage agents (ps, kill, pause, resume, logs)")
_app.add_typer(system.app, name="system", help="System management (init, status)")
_app.add_typer(setup.provider_app, name="provider", help="Manage LLM providers (list, configure, test)")
_app.add_typer(setup.channel_app, name="channel", help="Manage notification channels (list, configure, test)")
_app.add_typer(setup.tool_app, name="tool", help="Manage tools (list, enable, disable)")
_app.add_typer(setup.setup_app, name="setup", help="Interactive setup wizard")


@_app.command("ps")
def ps():
    """List all agents."""
    agents.ps()


@_app.command("init")
def init():
    """Initialize workspace."""
    system.init()


@_app.command("status")
def status():
    """Show system status."""
    system.status()


@_app.command("ask")
def ask(
    intent: str = typer.Argument(help="What do you want agos to do?"),
):
    """Ask agos to do something using natural language."""
    from agos.cli.intent import handle_intent
    handle_intent(intent)


@_app.command("talk")
def talk():
    """Talk to OpenSculpt using your voice. Requires: pip install SpeechRecognition pyaudio pyttsx3"""
    try:
        import speech_recognition as sr
    except ImportError:
        console.print("[red]Missing dependency.[/red] Install with:")
        console.print("  pip install SpeechRecognition pyaudio pyttsx3")
        raise typer.Exit(1)

    from agos.cli.context import AgosContext, run_async
    from agos.os_agent import OSAgent
    from agos.processes.manager import ProcessManager
    from agos.processes.workload import WorkloadDiscovery
    from agos.processes.registry import AgentRegistry

    ctx = AgosContext.get()
    run_async(ctx.ensure_loom())

    pm = ProcessManager(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail)
    wd = WorkloadDiscovery(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail)
    ar = AgentRegistry(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail,
                       process_manager=pm, workload_discovery=wd)
    os_agent = OSAgent(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail,
                       agent_registry=ar, process_manager=pm,
                       policy_engine=ctx.policy_engine, llm=ctx.llm)

    from agos.daemons.manager import DaemonManager
    hm = DaemonManager(event_bus=ctx.event_bus, audit=ctx.audit_trail)
    hm.register_builtin_daemons()
    os_agent.set_daemon_manager(hm)

    recognizer = sr.Recognizer()
    mic = sr.Microphone()

    # Optional TTS
    tts_engine = None
    try:
        import pyttsx3
        tts_engine = pyttsx3.init()
        tts_engine.setProperty('rate', 180)
    except Exception:
        pass

    def speak(text: str):
        if tts_engine and len(text) < 500:
            try:
                tts_engine.say(text)
                tts_engine.runAndWait()
            except Exception:
                pass

    console.print("[bold cyan]OpenSculpt Voice Mode[/bold cyan]")
    console.print("[dim]Speak naturally. Say 'exit' or 'quit' to stop. Press Ctrl+C to cancel.[/dim]")
    speak("OpenSculpt voice mode active. I'm listening.")

    while True:
        try:
            console.print("\n[green]Listening...[/green]", end=" ")
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=30)
            console.print("[dim]Processing speech...[/dim]")
            text = recognizer.recognize_google(audio)
            console.print(f"[cyan]You:[/cyan] {text}")

            if text.lower().strip() in ("exit", "quit", "stop", "goodbye", "bye"):
                speak("Goodbye!")
                console.print("[dim]Voice mode ended.[/dim]")
                break

            console.print("[dim]Thinking...[/dim]")
            result = run_async(os_agent.execute(text))
            msg = result.get("message", str(result))
            console.print(f"[bold green]OpenSculpt:[/bold green] {msg}")
            speak(msg)

        except sr.WaitTimeoutError:
            console.print("[dim]No speech detected, still listening...[/dim]")
        except sr.UnknownValueError:
            console.print("[dim]Couldn't understand, try again...[/dim]")
        except sr.RequestError as e:
            console.print(f"[red]Speech API error: {e}[/red]")
        except KeyboardInterrupt:
            speak("Goodbye!")
            console.print("\n[dim]Voice mode ended.[/dim]")
            break


@_app.command("recall")
def recall(
    query: str = typer.Argument(help="What do you want to recall?"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Search past knowledge and interactions."""
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table

    ctx = AgosContext.get()

    async def _recall():
        loom = await ctx.ensure_loom()
        return await loom.recall(query, limit=limit)

    threads = run_async(_recall())

    if not threads:
        console.print("[dim]No knowledge found yet.[/dim]")
        return

    table = Table(title=f"Knowledge — '{query}'")
    table.add_column("When", style="dim", no_wrap=True, max_width=19)
    table.add_column("Kind", style="cyan", max_width=12)
    table.add_column("Content", style="white")
    table.add_column("Tags", style="blue", max_width=20)

    for t in threads:
        table.add_row(
            t.created_at.strftime("%Y-%m-%d %H:%M"),
            t.kind,
            t.content[:120] + ("..." if len(t.content) > 120 else ""),
            ", ".join(t.tags[:3]),
        )

    console.print(table)


@_app.command("timeline")
def timeline(
    limit: int = typer.Option(20, "--limit", "-n", help="Max events"),
):
    """Show recent activity and events."""
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table

    ctx = AgosContext.get()

    async def _timeline():
        loom = await ctx.ensure_loom()
        return await loom.timeline(limit=limit)

    threads = run_async(_timeline())

    if not threads:
        console.print("[dim]No events recorded yet.[/dim]")
        return

    table = Table(title="Timeline")
    table.add_column("When", style="dim", no_wrap=True, max_width=19)
    table.add_column("Kind", style="cyan", max_width=12)
    table.add_column("Event", style="white")

    for t in threads:
        table.add_row(
            t.created_at.strftime("%Y-%m-%d %H:%M"),
            t.kind,
            t.content[:150] + ("..." if len(t.content) > 150 else ""),
        )

    console.print(table)


@_app.command("watch")
def watch(
    path: str = typer.Argument(help="Directory or file to watch"),
    intent: str = typer.Argument(help="What to do when changes are detected"),
    patterns: str = typer.Option("*", "--patterns", "-p", help="Glob patterns (comma-separated)"),
    interval: int = typer.Option(2, "--interval", "-i", help="Seconds between checks"),
):
    """Watch a path for changes and trigger an action.

    Example: agos watch ./src "review changes for bugs"
    """
    from agos.cli.context import AgosContext, run_async
    from agos.triggers.base import TriggerConfig

    ctx = AgosContext.get()
    pattern_list = [p.strip() for p in patterns.split(",")]

    config = TriggerConfig(
        kind="file_watch",
        description=f"Watch {path}",
        intent=intent,
        params={
            "path": path,
            "patterns": pattern_list,
            "interval": interval,
        },
    )

    async def _handle_trigger(trigger_intent: str) -> None:
        from agos.cli.intent import handle_intent
        handle_intent(trigger_intent)

    async def _start():
        ctx.trigger_manager.set_handler(_handle_trigger)
        trigger = await ctx.trigger_manager.register(config)
        console.print(f"[green]Watching[/green] [bold]{path}[/bold] (patterns={pattern_list})")
        console.print(f"[dim]On change: {intent}[/dim]")
        console.print(f"[dim]Trigger ID: {config.id}[/dim]")
        console.print("[dim]Press Ctrl+C to stop.[/dim]")

        # Keep running until interrupted
        try:
            while trigger.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    import asyncio
    try:
        run_async(_start())
    except KeyboardInterrupt:
        console.print("\n[dim]Watcher stopped.[/dim]")


@_app.command("schedule")
def schedule(
    interval: int = typer.Argument(help="Seconds between runs"),
    intent: str = typer.Argument(help="What to do each time"),
    max_fires: int = typer.Option(0, "--max", "-m", help="Stop after N runs (0=unlimited)"),
):
    """Schedule a recurring action.

    Example: agos schedule 1800 "check server health"
    """
    from agos.cli.context import AgosContext, run_async
    from agos.triggers.base import TriggerConfig

    ctx = AgosContext.get()

    config = TriggerConfig(
        kind="schedule",
        description=f"Every {interval}s: {intent[:50]}",
        intent=intent,
        params={
            "interval_seconds": interval,
            "max_fires": max_fires,
        },
    )

    async def _handle_trigger(trigger_intent: str) -> None:
        from agos.cli.intent import handle_intent
        handle_intent(trigger_intent)

    async def _start():
        ctx.trigger_manager.set_handler(_handle_trigger)
        trigger = await ctx.trigger_manager.register(config)
        console.print(f"[green]Scheduled[/green] every [bold]{interval}s[/bold]")
        console.print(f"[dim]Action: {intent}[/dim]")
        if max_fires:
            console.print(f"[dim]Max runs: {max_fires}[/dim]")
        console.print(f"[dim]Trigger ID: {config.id}[/dim]")
        console.print("[dim]Press Ctrl+C to stop.[/dim]")

        try:
            while trigger.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    import asyncio
    try:
        run_async(_start())
    except KeyboardInterrupt:
        console.print("\n[dim]Schedule stopped.[/dim]")


@_app.command("triggers")
def triggers():
    """List all active triggers."""
    from agos.cli.context import AgosContext
    from rich.table import Table

    ctx = AgosContext.get()
    active = ctx.trigger_manager.list_triggers()

    if not active:
        console.print("[dim]No active triggers.[/dim]")
        return

    table = Table(title="Active Triggers")
    table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
    table.add_column("Kind", style="green", max_width=12)
    table.add_column("Description", style="white")
    table.add_column("Intent", style="dim")
    table.add_column("Active", style="yellow", max_width=6)

    for t in active:
        table.add_row(
            t["id"],
            t["kind"],
            t["description"],
            (t["intent"][:60] + "...") if len(t["intent"]) > 60 else t["intent"],
            "yes" if t["active"] else "no",
        )

    console.print(table)


@_app.command("team")
def team(
    task: str = typer.Argument(help="What the team should accomplish"),
    agents_list: str = typer.Option(
        "researcher,coder,reviewer",
        "--agents", "-a",
        help="Comma-separated agent personas",
    ),
    strategy: str = typer.Option(
        "parallel",
        "--strategy", "-s",
        help="Coordination: solo, pipeline, parallel, debate",
    ),
    name: str = typer.Option("default", "--name", "-n", help="Team name"),
):
    """Run a team of agents on a task.

    Example: agos team "build a REST API" --agents coder,reviewer --strategy pipeline
    """
    from agos.cli.context import AgosContext, run_async
    from agos.types import CoordinationStrategy
    from agos.intent.personas import PERSONAS
    from agos.coordination.team import Team as AgentTeam
    from rich.table import Table

    ctx = AgosContext.get()

    from agos.config import settings
    if not settings.anthropic_api_key:
        console.print(
            "[red]No API key set.[/red] Run: "
            "[bold]export AGOS_ANTHROPIC_API_KEY=your-key[/bold]"
        )
        raise SystemExit(1)

    # Parse strategy
    strat_map = {s.value: s for s in CoordinationStrategy}
    coord = strat_map.get(strategy, CoordinationStrategy.PARALLEL)

    # Parse agent names
    agent_names = [n.strip() for n in agents_list.split(",")]
    member_defs = []
    for aname in agent_names:
        persona = PERSONAS.get(aname)
        if not persona:
            console.print(f"[red]Unknown agent: {aname}[/red] (available: {', '.join(PERSONAS.keys())})")
            raise SystemExit(1)
        member_defs.append(persona)

    console.print(
        f"[dim]team={name}  strategy={coord.value}  "
        f"agents=[{', '.join(agent_names)}][/dim]"
    )

    async def _run():
        loom = await ctx.ensure_loom()
        t = AgentTeam(name=name, runtime=ctx.runtime, strategy=coord)
        for d in member_defs:
            t.add_member(d)

        result = await t.run(task)

        # Learn from the team run
        try:
            await loom.learner.record_interaction(
                agent_id=t.id,
                agent_name=f"team:{name}",
                user_input=task,
                agent_output=result,
                tokens_used=sum(
                    a.context.tokens_used for a in t.agents
                ),
            )
        except Exception:
            pass

        return result, t

    with console.status("[bold cyan]team working...", spinner="dots"):
        result, finished_team = run_async(_run())

    # Display result
    from rich.markdown import Markdown
    clean_result = result.encode("ascii", errors="ignore").decode("ascii")
    console.print()
    console.print(Markdown(clean_result))
    console.print()

    # Show team summary
    status = finished_team.status()
    table = Table(title=f"Team '{name}' Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Strategy", status["strategy"])
    table.add_row("Members", ", ".join(status["members"]))
    table.add_row("Agents spawned", str(status["agents_spawned"]))
    table.add_row("Messages", str(status["channel_messages"]))
    table.add_row("Workspace items", str(status["workspace_items"]))

    total_tokens = sum(a.context.tokens_used for a in finished_team.agents)
    table.add_row("Total tokens", f"{total_tokens:,}")
    console.print(table)


@_app.command("audit")
def audit(
    agent_id: str = typer.Option("", "--agent", "-a", help="Filter by agent ID"),
    action: str = typer.Option("", "--action", help="Filter by action type"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries"),
):
    """Show the audit trail — who did what, when."""
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table

    ctx = AgosContext.get()

    async def _audit():
        audit_trail = ctx.audit_trail
        return await audit_trail.query(agent_id=agent_id, action=action, limit=limit)

    entries = run_async(_audit())

    if not entries:
        console.print("[dim]No audit entries yet.[/dim]")
        return

    table = Table(title="Audit Trail")
    table.add_column("When", style="dim", no_wrap=True, max_width=19)
    table.add_column("Agent", style="cyan", max_width=15)
    table.add_column("Action", style="green", max_width=18)
    table.add_column("Detail", style="white")
    table.add_column("OK", style="yellow", max_width=4)

    for e in entries:
        table.add_row(
            e.timestamp.strftime("%Y-%m-%d %H:%M"),
            e.agent_name or e.agent_id[:12],
            e.action,
            e.detail[:80] + ("..." if len(e.detail) > 80 else ""),
            "yes" if e.success else "NO",
        )

    console.print(table)


@_app.command("policy")
def policy(
    set_deny: str = typer.Option("", "--deny-tools", help="Comma-separated tools to deny globally"),
    set_max_tokens: int = typer.Option(0, "--max-tokens", help="Set global max token budget"),
    read_only: bool = typer.Option(False, "--read-only", help="Enable read-only mode globally"),
):
    """View or configure agent policies.

    Examples:
        agos policy                               # show current policies
        agos policy --deny-tools shell_exec       # block shell access
        agos policy --max-tokens 50000            # limit token budget
        agos policy --read-only                   # block all write/exec tools
    """
    from agos.cli.context import AgosContext
    from rich.table import Table

    ctx = AgosContext.get()
    engine = ctx.policy_engine

    # Apply settings if provided
    if set_deny or set_max_tokens or read_only:
        current = engine.get_policy("*")
        if set_deny:
            denied = [t.strip() for t in set_deny.split(",")]
            current.denied_tools = list(set(current.denied_tools + denied))
        if set_max_tokens:
            current.max_tokens = set_max_tokens
        if read_only:
            current.read_only = True
        engine.set_default(current)
        console.print("[green]Policy updated.[/green]")

    # Show current state
    default_p = engine.get_policy("*")
    table = Table(title="Active Policies")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Allowed tools", ", ".join(default_p.allowed_tools))
    table.add_row("Denied tools", ", ".join(default_p.denied_tools) or "(none)")
    table.add_row("Max tokens", f"{default_p.max_tokens:,}")
    table.add_row("Max turns", str(default_p.max_turns))
    table.add_row("Allow shell", str(default_p.allow_shell))
    table.add_row("Allow network", str(default_p.allow_network))
    table.add_row("Allow file write", str(default_p.allow_file_write))
    table.add_row("Read-only mode", str(default_p.read_only))
    table.add_row("Rate limit", f"{default_p.max_tool_calls_per_minute}/min")
    console.print(table)

    # Show per-agent overrides
    overrides = engine.list_policies()
    if overrides:
        ot = Table(title="Per-Agent Overrides")
        ot.add_column("Agent", style="cyan")
        ot.add_column("Denied tools", style="red")
        ot.add_column("Max tokens", style="white")
        ot.add_column("Read-only", style="yellow")
        for o in overrides:
            ot.add_row(
                o["agent_name"],
                ", ".join(o["denied_tools"]) or "(none)",
                f"{o['max_tokens']:,}",
                str(o["read_only"]),
            )
        console.print(ot)


@_app.command("dashboard")
def dashboard(
    port: int = typer.Option(8420, "--port", "-p", help="Port to run on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open browser"),
):
    """Launch the real-time monitoring dashboard.

    Opens a web UI at http://localhost:8420 with live agent status,
    event stream, audit trail, and system metrics.
    """
    from agos.cli.context import AgosContext, run_async
    from agos.dashboard.app import dashboard_app, configure

    ctx = AgosContext.get()

    # Initialize loom so knowledge endpoints work
    run_async(ctx.ensure_loom())

    # Initialize OS agent so the shell works
    from agos.os_agent import OSAgent
    from agos.processes.manager import ProcessManager
    from agos.processes.workload import WorkloadDiscovery
    from agos.processes.registry import AgentRegistry

    process_manager = ProcessManager(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail)
    workload_discovery = WorkloadDiscovery(event_bus=ctx.event_bus, audit_trail=ctx.audit_trail)
    agent_registry = AgentRegistry(
        event_bus=ctx.event_bus,
        audit_trail=ctx.audit_trail,
        process_manager=process_manager,
        workload_discovery=workload_discovery,
    )

    os_agent = OSAgent(
        event_bus=ctx.event_bus,
        audit_trail=ctx.audit_trail,
        agent_registry=agent_registry,
        process_manager=process_manager,
        policy_engine=ctx.policy_engine,
        llm=ctx.llm,
    )

    from agos.daemons.manager import DaemonManager
    daemon_manager = DaemonManager(event_bus=ctx.event_bus, audit=ctx.audit_trail)
    daemon_manager.register_builtin_daemons()
    os_agent.set_daemon_manager(daemon_manager)

    configure(
        runtime=ctx.runtime,
        event_bus=ctx.event_bus,
        audit_trail=ctx.audit_trail,
        policy_engine=ctx.policy_engine,
        tracer=ctx.tracer,
        loom=ctx.loom,
        process_manager=process_manager,
        workload_discovery=workload_discovery,
        agent_registry=agent_registry,
        os_agent=os_agent,
        mcp_manager=ctx.mcp_manager,
        daemon_manager=daemon_manager,
    )

    url = f"http://{host}:{port}"
    console.print(f"[bold cyan]agos dashboard[/bold cyan] starting at {url}")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    # Auto-open browser
    if not no_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, webbrowser.open, args=[url]).start()

    import uvicorn
    uvicorn.run(dashboard_app, host=host, port=port, log_level="warning")


@_app.command("evolve")
def evolve(
    days: int = typer.Option(7, "--days", "-d", help="Look back N days for papers"),
    max_papers: int = typer.Option(20, "--max", "-m", help="Max papers to analyze"),
    show_history: bool = typer.Option(False, "--history", help="Show evolution history"),
    show_proposals: bool = typer.Option(False, "--proposals", help="Show pending proposals"),
    accept: str = typer.Option("", "--accept", help="Accept a proposal by ID"),
    reject: str = typer.Option("", "--reject", help="Reject a proposal by ID"),
    apply_id: str = typer.Option("", "--apply", help="Apply an accepted proposal by ID"),
    rollback_id: str = typer.Option("", "--rollback", help="Rollback an integration by version ID"),
    auto: int = typer.Option(0, "--auto", help="Run every N hours automatically"),
):
    """Evolve agos by scanning arxiv for the latest research.

    Searches for new papers on agentic AI, memory systems, coordination,
    and other topics. Analyzes them via Claude and proposes improvements.

    Examples:
        agos evolve                    # scan last 7 days
        agos evolve --days 30          # scan last 30 days
        agos evolve --proposals        # view pending proposals
        agos evolve --accept <id>      # accept a proposal
        agos evolve --apply <id>       # apply an accepted proposal
        agos evolve --rollback <ver>   # rollback an integration
        agos evolve --history          # view past evolution cycles
        agos evolve --auto 24          # run every 24 hours
    """
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table
    from rich.panel import Panel

    ctx = AgosContext.get()

    async def _ensure():
        await ctx.ensure_loom()

    run_async(_ensure())
    engine = ctx.evolution_engine

    # Show history
    if show_history:
        async def _history():
            return await engine.history()

        reports = run_async(_history())
        if not reports:
            console.print("[dim]No evolution cycles recorded yet.[/dim]")
            return

        table = Table(title="Evolution History")
        table.add_column("When", style="dim", no_wrap=True, max_width=19)
        table.add_column("Papers", style="cyan")
        table.add_column("Analyzed", style="green")
        table.add_column("Proposals", style="yellow")
        table.add_column("Duration", style="dim")

        for r in reports:
            table.add_row(
                r.created_at.strftime("%Y-%m-%d %H:%M"),
                str(r.papers_found),
                str(r.papers_analyzed),
                str(r.proposals_created),
                f"{r.duration_ms:.0f}ms",
            )
        console.print(table)
        return

    # Show proposals
    if show_proposals:
        async def _proposals():
            return await engine.get_proposals()

        proposals = run_async(_proposals())
        if not proposals:
            console.print("[dim]No proposals yet. Run 'agos evolve' to scan for improvements.[/dim]")
            return

        table = Table(title="Evolution Proposals")
        table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
        table.add_column("Priority", style="yellow", max_width=8)
        table.add_column("Technique", style="white")
        table.add_column("Module", style="green", max_width=14)
        table.add_column("Code", style="magenta", max_width=6)
        table.add_column("Paper", style="dim")
        table.add_column("Status", style="cyan", max_width=10)

        for p in proposals:
            code_count = str(len(p.code_patterns)) if p.code_patterns else "-"
            table.add_row(
                p.id,
                p.insight.priority,
                p.insight.technique[:40],
                p.insight.agos_module,
                code_count,
                p.insight.paper_title[:35] + "...",
                p.status,
            )
        console.print(table)
        return

    # Accept/reject proposal
    if accept:
        async def _accept():
            return await engine.accept_proposal(accept)

        result = run_async(_accept())
        if result:
            console.print(f"[green]Accepted proposal {accept}:[/green] {result.insight.technique}")
        else:
            console.print(f"[red]Proposal {accept} not found.[/red]")
        return

    if reject:
        async def _reject():
            return await engine.reject_proposal(reject)

        result = run_async(_reject())
        if result:
            console.print(f"[dim]Rejected proposal {reject}.[/dim]")
        else:
            console.print(f"[red]Proposal {reject} not found.[/red]")
        return

    # Apply an accepted proposal
    if apply_id:
        async def _apply():
            return await engine.integrate_proposal(apply_id)

        with console.status("[bold cyan]applying proposal...", spinner="dots"):
            result = run_async(_apply())

        if result is None:
            console.print(f"[red]Proposal {apply_id} not found or no integrator available.[/red]")
        elif result.success:
            console.print(f"[green]Integration applied![/green] Version: {result.version_id}")
            for change in result.changes:
                console.print(f"  [dim]-[/dim] {change}")
        else:
            console.print(f"[red]Integration failed:[/red] {result.error}")
        return

    # Rollback an integration
    if rollback_id:
        async def _rollback():
            return await engine.rollback_integration(rollback_id)

        result = run_async(_rollback())
        if result:
            console.print(f"[green]Rolled back version {rollback_id}.[/green]")
        else:
            console.print(f"[red]Rollback failed for version {rollback_id}.[/red]")
        return

    # Auto-schedule mode
    if auto:
        from agos.triggers.base import TriggerConfig

        async def _handle_trigger(trigger_intent: str) -> None:
            await engine.run_cycle(days=days, max_papers=max_papers)

        async def _start_auto():
            ctx.trigger_manager.set_handler(_handle_trigger)
            config = TriggerConfig(
                kind="schedule",
                description=f"Evolution scan every {auto}h",
                intent="evolve",
                params={"interval_seconds": auto * 3600, "max_fires": 0},
            )
            trigger = await ctx.trigger_manager.register(config)
            console.print(f"[green]Auto-evolution enabled:[/green] scanning every {auto} hours")
            console.print(f"[dim]Trigger ID: {config.id}[/dim]")
            console.print("[dim]Press Ctrl+C to stop.[/dim]")

            import asyncio
            try:
                while trigger.is_running:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass

        try:
            run_async(_start_auto())
        except KeyboardInterrupt:
            console.print("\n[dim]Auto-evolution stopped.[/dim]")
        return

    # Default: run evolution cycle
    from agos.config import settings
    if not settings.anthropic_api_key:
        console.print(
            "[red]No API key set.[/red] Run: "
            "[bold]export AGOS_ANTHROPIC_API_KEY=your-key[/bold]\n"
            "[dim]Note: arxiv search works without a key, but analysis needs Claude.[/dim]"
        )
        raise SystemExit(1)

    console.print(f"[bold cyan]Scanning arxiv[/bold cyan] for papers from the last {days} days...")

    async def _run():
        return await engine.run_cycle(days=days, max_papers=max_papers)

    with console.status("[bold cyan]evolving...", spinner="dots"):
        report = run_async(_run())

    # Display results
    console.print()
    summary = (
        f"[bold]Papers found:[/bold] {report.papers_found}\n"
        f"[bold]Papers analyzed:[/bold] {report.papers_analyzed}\n"
        f"[bold]Proposals created:[/bold] {report.proposals_created}\n"
        f"[bold]Repos found:[/bold] {report.repos_found}\n"
        f"[bold]Code patterns:[/bold] {report.code_patterns_found}\n"
        f"[bold]Sandbox tests:[/bold] {report.sandbox_tests_passed}/{report.sandbox_tests_run} passed\n"
        f"[bold]Duration:[/bold] {report.duration_ms:.0f}ms"
    )
    console.print(Panel(summary, title="Evolution Cycle Complete", border_style="green"))

    if report.papers:
        console.print("\n[bold]Papers discovered:[/bold]")
        for title in report.papers[:10]:
            clean = title.encode("ascii", errors="ignore").decode("ascii")
            console.print(f"  [dim]-[/dim] {clean[:100]}")

    if report.proposal_ids:
        console.print(f"\n[yellow]{report.proposals_created} improvement proposals created.[/yellow]")
        if report.code_patterns_found:
            console.print(f"[yellow]{report.code_patterns_found} code patterns extracted from repos.[/yellow]")
        console.print("[dim]Run 'agos evolve --proposals' to review them.[/dim]")
    else:
        console.print("\n[dim]No new improvements found this cycle.[/dim]")


@_app.command("ambient")
def ambient(
    start: bool = typer.Option(False, "--start", help="Start all ambient watchers"),
    stop: bool = typer.Option(False, "--stop", help="Stop all ambient watchers"),
    show_status: bool = typer.Option(False, "--status", help="Show watcher status"),
    start_one: str = typer.Option("", "--start-one", help="Start a specific watcher by name"),
    stop_one: str = typer.Option("", "--stop-one", help="Stop a specific watcher by name"),
    show_observations: bool = typer.Option(False, "--observations", help="Show recent observations"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """Manage ambient watchers — always-on background intelligence.

    Examples:
        agos ambient --status              # show watcher status
        agos ambient --start               # start all watchers
        agos ambient --stop                # stop all watchers
        agos ambient --start-one git_watcher  # start just git watcher
        agos ambient --observations        # show what watchers noticed
    """
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table

    ctx = AgosContext.get()

    if show_status or (not start and not stop and not start_one and not stop_one and not show_observations):
        watchers = ctx.ambient_manager.list_watchers()
        if not watchers:
            console.print("[dim]No ambient watchers registered.[/dim]")
            return

        table = Table(title="Ambient Watchers")
        table.add_column("Name", style="cyan")
        table.add_column("Running", style="green")
        table.add_column("Observations", style="yellow")

        for w in watchers:
            table.add_row(
                w["name"],
                "yes" if w["running"] else "no",
                str(w["observations"]),
            )
        console.print(table)
        return

    if start:
        async def _start():
            await ctx.ensure_loom()
            return await ctx.ambient_manager.start_all(
                ctx.trigger_manager, ctx.event_bus, ctx.loom
            )

        count = run_async(_start())
        console.print(f"[green]Started {count} ambient watcher(s).[/green]")
        return

    if stop:
        async def _stop():
            return await ctx.ambient_manager.stop_all()

        count = run_async(_stop())
        console.print(f"[dim]Stopped {count} ambient watcher(s).[/dim]")
        return

    if start_one:
        async def _start_one():
            await ctx.ensure_loom()
            return await ctx.ambient_manager.start_one(start_one)

        ok = run_async(_start_one())
        if ok:
            console.print(f"[green]Started watcher '{start_one}'.[/green]")
        else:
            console.print(f"[red]Could not start '{start_one}' (not found or already running).[/red]")
        return

    if stop_one:
        async def _stop_one():
            return await ctx.ambient_manager.stop_one(stop_one)

        ok = run_async(_stop_one())
        if ok:
            console.print(f"[dim]Stopped watcher '{stop_one}'.[/dim]")
        else:
            console.print(f"[red]Could not stop '{stop_one}' (not found or not running).[/red]")
        return

    if show_observations:
        obs = ctx.ambient_manager.observations(limit=limit)
        if not obs:
            console.print("[dim]No observations yet. Start watchers with 'agos ambient --start'.[/dim]")
            return

        table = Table(title="Ambient Observations")
        table.add_column("When", style="dim", no_wrap=True, max_width=19)
        table.add_column("Watcher", style="cyan", max_width=14)
        table.add_column("Kind", style="green", max_width=12)
        table.add_column("Summary", style="white")
        table.add_column("Action", style="yellow")

        for o in obs:
            table.add_row(
                o.created_at.strftime("%Y-%m-%d %H:%M"),
                o.watcher_name,
                o.kind,
                o.summary[:60],
                o.suggested_action[:40] if o.suggested_action else "-",
            )
        console.print(table)


@_app.command("proactive")
def proactive(
    scan: bool = typer.Option(False, "--scan", help="Run pattern detection now"),
    show_suggestions: bool = typer.Option(False, "--suggestions", help="Show current suggestions"),
    dismiss: str = typer.Option("", "--dismiss", help="Dismiss a suggestion by ID"),
    act: str = typer.Option("", "--act", help="Act on a suggestion by ID"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Proactive intelligence — the OS suggests before you ask.

    Examples:
        agos proactive --scan              # detect patterns
        agos proactive --suggestions       # show suggestions
        agos proactive --dismiss <id>      # dismiss a suggestion
        agos proactive --act <id>          # act on a suggestion
    """
    from agos.cli.context import AgosContext, run_async
    from rich.table import Table

    ctx = AgosContext.get()

    if scan or (not show_suggestions and not dismiss and not act):
        async def _scan():
            await ctx.ensure_loom()
            return await ctx.proactive_engine.scan()

        with console.status("[bold cyan]scanning for patterns...", spinner="dots"):
            suggestions = run_async(_scan())

        if not suggestions:
            console.print("[dim]No new suggestions found.[/dim]")
        else:
            console.print(f"[green]Found {len(suggestions)} suggestion(s):[/green]")
            for s in suggestions:
                console.print(
                    f"  [cyan]{s.id[:12]}[/cyan] [{s.detector_name}] "
                    f"{s.description} → [yellow]{s.suggested_action}[/yellow]"
                )
        return

    if show_suggestions:
        async def _get():
            return await ctx.proactive_engine.get_suggestions(limit=limit)

        suggestions = run_async(_get())
        if not suggestions:
            console.print("[dim]No suggestions. Run 'agos proactive --scan' first.[/dim]")
            return

        table = Table(title="Proactive Suggestions")
        table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
        table.add_column("Detector", style="green", max_width=16)
        table.add_column("Description", style="white")
        table.add_column("Confidence", style="yellow", max_width=6)
        table.add_column("Action", style="dim")

        for s in suggestions:
            table.add_row(
                s.id[:12],
                s.detector_name,
                s.description[:50],
                f"{s.confidence:.0%}",
                s.suggested_action[:40],
            )
        console.print(table)
        return

    if dismiss:
        async def _dismiss():
            return await ctx.proactive_engine.dismiss(dismiss)

        ok = run_async(_dismiss())
        if ok:
            console.print(f"[dim]Dismissed suggestion {dismiss}.[/dim]")
        else:
            console.print(f"[red]Suggestion {dismiss} not found.[/red]")
        return

    if act:
        from agos.config import settings as _s
        if not _s.anthropic_api_key:
            console.print("[red]No API key set.[/red]")
            raise SystemExit(1)

        async def _act():
            await ctx.ensure_loom()
            return await ctx.proactive_engine.act_on(act, ctx.runtime)

        with console.status("[bold cyan]acting on suggestion...", spinner="dots"):
            result = run_async(_act())

        if result:
            from rich.markdown import Markdown
            clean = result.encode("ascii", errors="ignore").decode("ascii")
            console.print(Markdown(clean))
        else:
            console.print(f"[red]Could not act on suggestion {act}.[/red]")


@_app.command("update")
def update(
    check_only: bool = typer.Option(False, "--check", help="Only check, don't update"),
):
    """Check for updates and optionally self-update."""
    from agos.cli.context import run_async
    from agos.updater import check_for_update, self_update

    async def _check():
        return await check_for_update()

    result = run_async(_check())
    if not result["update_available"]:
        console.print(f"[green]agos v{result['current_version']} is up to date.[/green]")
        return

    console.print(
        f"[yellow]Update available:[/yellow] v{result['current_version']} -> "
        f"v{result['latest_version']} (from {result['source']})"
    )

    if check_only:
        return

    console.print("[dim]Updating...[/dim]")
    if self_update():
        console.print("[green]Updated! Restart agos to use the new version.[/green]")
    else:
        console.print("[red]Update failed. Try: pip install --upgrade agos[/red]")


@_app.command("version")
def version_cmd():
    """Show OpenSculpt version."""
    from agos import __version__
    console.print(f"OpenSculpt v{__version__}")


@_app.command("demands")
def demands(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
    prompt: bool = typer.Option(False, "--prompt", help="Output as a Claude Code prompt"),
):
    """Show what the OS needs — demand signals from failures and gaps.

    This is the bridge between OpenSculpt and Claude Code. Run this to see
    what the OS is struggling with, then let Claude Code fix it.

    Examples:
        sculpt demands                  # human-readable table
        sculpt demands --json           # machine-readable
        sculpt demands --prompt         # paste into Claude Code to fix
    """
    import json as _json
    from pathlib import Path
    from agos.config import settings

    signals_path = Path(settings.workspace_dir) / "demand_signals.json"
    if not signals_path.exists():
        console.print("[dim]No demand signals yet. Use OpenSculpt first — demands are generated from failures.[/dim]")
        raise typer.Exit(0)

    data = _json.loads(signals_path.read_text(encoding="utf-8"))
    signals = data.get("signals", [])
    if isinstance(signals, dict):
        signals = list(signals.values())

    if not signals:
        console.print("[green]No active demands. The OS is healthy.[/green]")
        raise typer.Exit(0)

    # Sort by priority
    signals.sort(key=lambda s: s.get("priority", 0) if isinstance(s, dict) else 0, reverse=True)

    if json_out:
        console.print(_json.dumps(signals, indent=2, default=str))
        return

    if prompt:
        # Generate a Claude Code prompt from demands
        active = [s for s in signals if isinstance(s, dict) and s.get("status") in ("active", "attempting", "escalated")]
        if not active:
            console.print("[green]No actionable demands.[/green]")
            raise typer.Exit(0)

        lines = [
            "# OpenSculpt Evolution Demands",
            "",
            "The OS has the following unresolved demands. Read the relevant source files,",
            "understand the root cause, write a fix, and run tests to verify.",
            "",
        ]
        for i, s in enumerate(active[:10], 1):
            kind = s.get("kind", "unknown")
            desc = s.get("description", "")
            source = s.get("source", "")
            priority = s.get("priority", 0)
            attempts = s.get("attempts", 0)
            status = s.get("status", "active")
            lines.append(f"## {i}. [{kind}] {desc[:80]}")
            lines.append(f"- Source: {source}")
            lines.append(f"- Priority: {priority:.2f} | Attempts: {attempts} | Status: {status}")
            ctx = s.get("context", {})
            if ctx:
                if ctx.get("command"):
                    lines.append(f"- Command: {ctx['command'][:100]}")
                if ctx.get("error"):
                    lines.append(f"- Error: {ctx['error'][:200]}")
            lines.append("")

        lines.extend([
            "## How to fix",
            "1. Read CLAUDE.md for architecture and wiring rules",
            "2. Read the relevant source files in agos/",
            "3. Write the minimal fix",
            "4. Run: python -m pytest tests/ --ignore=tests/test_frontend_playwright.py -q",
            "5. If tests pass, the fix is ready",
        ])
        console.print("\n".join(lines))
        return

    # Default: human-readable table
    from rich.table import Table
    table = Table(title="OpenSculpt Demand Signals")
    table.add_column("Status", width=10)
    table.add_column("Kind", width=15)
    table.add_column("Description", width=50)
    table.add_column("Priority", width=8)
    table.add_column("Attempts", width=8)

    status_colors = {"active": "yellow", "attempting": "cyan", "escalated": "red", "resolved": "green"}
    for s in signals[:20]:
        if not isinstance(s, dict):
            continue
        status = s.get("status", "unknown")
        color = status_colors.get(status, "white")
        table.add_row(
            f"[{color}]{status}[/{color}]",
            s.get("kind", "?"),
            (s.get("description", ""))[:50],
            f"{s.get('priority', 0):.2f}",
            str(s.get("attempts", 0)),
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(signals)} signals. Run [bold]sculpt demands --prompt[/bold] to generate a Claude Code fix prompt.[/dim]")


@_app.command("verify")
def verify():
    """Verify that recent changes resolved evolution demands.

    Runs tests, checks demand signal status, and reports what improved.
    Run this after your AI coding tool makes changes.

    Example:
        sculpt verify
    """
    import subprocess
    import json as _json
    from pathlib import Path
    from agos.config import settings
    from agos.evolution.nudge import get_demand_count, write_demands_md

    # Step 1: Run tests
    console.print("[cyan]Running tests...[/cyan]")
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "--ignore=tests/test_frontend_playwright.py",
         "--ignore=tests/test_user_stories.py", "--ignore=tests/test_services_e2e.py",
         "-q", "--tb=line"],
        capture_output=True, text=True, timeout=120,
    )
    passed = "passed" in result.stdout
    console.print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)

    if not passed:
        console.print("[red]Tests failed.[/red] Fix the failures before verifying demands.")
        raise typer.Exit(1)

    # Step 2: Check demand status
    active, escalated = get_demand_count()
    total = active + escalated

    if total == 0:
        console.print("[bold green]All demands resolved! Your OS is healthy.[/bold green]")
    else:
        console.print(f"[yellow]{total} demands still active ({escalated} escalated).[/yellow]")
        console.print("[dim]Run `sculpt demands` to see what's left.[/dim]")

    # Step 3: Refresh DEMANDS.md
    write_demands_md()
    console.print("[dim]Updated .opensculpt/DEMANDS.md[/dim]")

    # Step 4: Summary
    if total == 0:
        console.print("\n[bold green]✓ Verification complete. The OS evolved successfully.[/bold green]")
        console.print("[dim]Run `sculpt contribute` to share your improvements with the fleet.[/dim]")
    else:
        console.print(f"\n[yellow]⚡ {total} demands remaining. Keep going or run `sculpt contribute` to share what you have.[/yellow]")


@_app.command("seed")
def seed(
    release_dir: str = typer.Argument(
        "", help="Path to release dir (default: latest from .opensculpt/releases/)",
    ),
):
    """Bootstrap local knowledge from a curated fleet release.

    Merges evolved tools, skills, constraints, and resolutions from a
    release package into the local workspace with 0.7x trust discount.

    Examples:
        sculpt seed                              # latest local release
        sculpt seed .opensculpt/releases/v3       # specific release
    """
    from pathlib import Path
    from agos.evolution.curator import apply_release
    from agos.config import settings

    if release_dir:
        rdir = Path(release_dir)
    else:
        releases = Path(settings.workspace_dir) / "releases"
        if not releases.exists():
            console.print("[red]No releases found.[/red] Run: python -m agos.evolution.curator_loop --release")
            raise typer.Exit(1)
        versions = sorted(releases.glob("v*"))
        if not versions:
            console.print("[red]No release versions found.[/red]")
            raise typer.Exit(1)
        rdir = versions[-1]

    if not rdir.exists():
        console.print(f"[red]Release not found: {rdir}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Seeding from {rdir}...[/cyan]")
    result = apply_release(rdir, Path(settings.workspace_dir))
    console.print(
        f"[green]Applied:[/green] {result['tools']} tools, {result['skills']} skills, "
        f"{result['constraints']} constraints, {result['resolutions']} resolutions"
    )


@_app.command("contribute")
def contribute():
    """Export anonymized local knowledge for federation.

    Creates a contribution package at .opensculpt/contributions/{timestamp}/
    containing anonymized constraints, resolutions, skills, and demand summaries.
    Submit this to the OpenSculpt knowledge registry.
    """
    from pathlib import Path
    from agos.evolution.curator import export_contribution
    from agos.config import settings

    console.print("[cyan]Exporting anonymized knowledge...[/cyan]")
    contrib_dir = export_contribution(Path(settings.workspace_dir))
    console.print(f"[green]Contribution exported to:[/green] {contrib_dir}")
    console.print("[dim]Submit this directory to the OpenSculpt knowledge registry.[/dim]")


@_app.command("curator")
def curator(
    release: bool = typer.Option(False, "--release", help="Also create a release package"),
    fleet_dir: str = typer.Option(".opensculpt-fleet", "--fleet-dir", help="Fleet directory"),
):
    """Run the fleet curator — aggregate, score, and optionally release.

    Reads .opensculpt-fleet/ (or custom dir), generates fleet report,
    and optionally packages top artifacts into a release.
    """
    from pathlib import Path
    from agos.evolution.curator import generate_fleet_report, create_release

    fdir = Path(fleet_dir)
    report = generate_fleet_report(fdir)
    console.print(report)

    # Save report
    curator_dir = Path(".opensculpt") / "curator"
    curator_dir.mkdir(parents=True, exist_ok=True)
    (curator_dir / "fleet_report.md").write_text(report, encoding="utf-8")
    console.print(f"\n[dim]Report saved to {curator_dir / 'fleet_report.md'}[/dim]")

    if release:
        console.print("\n[cyan]Creating release...[/cyan]")
        rdir = create_release(fdir)
        console.print(f"[green]Release created at: {rdir}[/green]")


@_app.command("mcp")
def mcp(
    list_servers: bool = typer.Option(False, "--list", "-l", help="List configured MCP servers"),
    add: str = typer.Option("", "--add", "-a", help="Add MCP server (name:command:arg1,arg2)"),
    remove: str = typer.Option("", "--remove", "-r", help="Remove MCP server by name"),
    status: bool = typer.Option(False, "--status", help="Show live connection status"),
):
    """Manage MCP (Model Context Protocol) server connections.

    MCP servers expose tools that OpenSculpt agents can use. Add servers like
    databases, file systems, or APIs and agents automatically get access.

    Examples:
        agos mcp --list
        agos mcp --add sqlite:npx:-y,@modelcontextprotocol/server-sqlite
        agos mcp --remove sqlite
        agos mcp --status
    """
    from agos.cli.context import AgosContext, run_async
    from agos.config import settings
    from agos.mcp.client import MCPServerConfig
    from agos.mcp.config import (
        load_mcp_configs, add_mcp_config, remove_mcp_config,
    )
    from rich.table import Table

    if add:
        # Parse format: name:command:arg1,arg2
        parts = add.split(":", 2)
        if len(parts) < 2:
            console.print("[red]Format: name:command:arg1,arg2[/red]")
            raise SystemExit(1)
        name = parts[0]
        command = parts[1]
        args = parts[2].split(",") if len(parts) > 2 else []

        config = MCPServerConfig(name=name, command=command, args=args)
        run_async(add_mcp_config(settings.workspace_dir, config))
        console.print(f"[green]Added MCP server '{name}'[/green] ({command} {' '.join(args)})")
        return

    if remove:
        run_async(remove_mcp_config(settings.workspace_dir, remove))
        console.print(f"[dim]Removed MCP server '{remove}'[/dim]")
        return

    # List or status — show configured servers
    configs = run_async(load_mcp_configs(settings.workspace_dir))

    if not configs:
        console.print("[dim]No MCP servers configured. Use --add to add one.[/dim]")
        return

    if status:
        ctx = AgosContext.get()

        async def _connect():
            for config in configs:
                if config.enabled:
                    try:
                        await ctx.mcp_manager.add_server(config)
                    except Exception as e:
                        console.print(f"[red]{config.name}:[/red] {e}")

        with console.status("[bold cyan]connecting to MCP servers...", spinner="dots"):
            run_async(_connect())

        servers = ctx.mcp_manager.list_servers()
        table = Table(title="MCP Servers (Live)")
        table.add_column("Name", style="cyan")
        table.add_column("Connected", style="green")
        table.add_column("Tools", style="white")
        for s in servers:
            table.add_row(
                s["name"],
                "yes" if s["connected"] else "[red]no[/red]",
                str(s["tool_count"]),
            )
        console.print(table)
        return

    # Default: list configured servers
    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="white")
    table.add_column("Args", style="dim")
    table.add_column("Enabled", style="green")
    for c in configs:
        table.add_row(c.name, c.command, " ".join(c.args), "yes" if c.enabled else "[red]no[/red]")
    console.print(table)


def app(args: list[str] | None = None) -> None:
    """Entry point that intercepts natural language before Typer sees it.

    If the first arg is NOT a known subcommand, we rewrite the args
    to route through the `ask` command.
    """
    argv = args if args is not None else sys.argv[1:]

    if argv and argv[0] not in _SUBCOMMANDS:
        # Treat the entire input as natural language
        intent = " ".join(argv)
        argv = ["ask", intent]

    # Patch sys.argv for Typer
    original_argv = sys.argv
    sys.argv = ["sculpt"] + argv

    try:
        _app()
    finally:
        sys.argv = original_argv
        # ── Nudge: show evolution demands after every command ──
        try:
            from agos.evolution.nudge import nudge_line
            nudge = nudge_line()
            if nudge:
                print(nudge)
        except Exception:
            pass
