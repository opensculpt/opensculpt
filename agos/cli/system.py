"""System commands — sculpt init, sculpt status."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from agos.config import settings

app = typer.Typer(help="System management")
console = Console()


@app.command()
def init():
    """Initialize an OpenSculpt workspace in the current directory."""
    workspace = settings.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "agents").mkdir(exist_ok=True)
    (workspace / "policies").mkdir(exist_ok=True)
    (workspace / "knowledge").mkdir(exist_ok=True)

    console.print(
        Panel(
            f"[green]OpenSculpt workspace initialized at {workspace}[/green]\n\n"
            "Set your API key:\n"
            "  [bold]export SCULPT_LLM_API_KEY=your-key[/bold]\n\n"
            "Then start using OpenSculpt:\n"
            '  [bold]sculpt "analyze my codebase"[/bold]',
            title="OpenSculpt",
            border_style="cyan",
        )
    )


@app.command()
def status():
    """Show system-wide status."""
    from agos.cli.context import AgosContext

    ctx = AgosContext.get()
    agents = ctx.runtime.list_agents()
    tools = ctx.tool_registry.list_tools()

    has_key = bool(settings.anthropic_api_key)
    workspace_exists = settings.workspace_dir.exists()

    from agos import __version__
    console.print(Panel(
        f"[bold]OpenSculpt v{__version__}[/bold]\n\n"
        f"API Key:    {'[green]set[/green]' if has_key else '[red]not set[/red]'}\n"
        f"Workspace:  {'[green]' + str(settings.workspace_dir) + '[/green]' if workspace_exists else '[red]not initialized[/red]'}\n"
        f"Model:      {settings.default_model}\n"
        f"Agents:     {len(agents)} running\n"
        f"Tools:      {len(tools)} available",
        title="System Status",
        border_style="cyan",
    ))
