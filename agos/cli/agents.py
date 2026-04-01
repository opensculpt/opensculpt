"""Agent management commands — agos ps, agos kill, agos logs."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agos.cli.context import AgosContext, run_async

app = typer.Typer(help="Manage running agents")
console = Console()


@app.command("ps")
def ps():
    """List all agents and their status."""
    ctx = AgosContext.get()
    agents = ctx.runtime.list_agents()

    if not agents:
        console.print("[dim]No agents running.[/dim]")
        return

    table = Table(title="Agents")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Role", style="blue")
    table.add_column("State", style="green")
    table.add_column("Tokens", justify="right", style="yellow")
    table.add_column("Turns", justify="right")

    for a in agents:
        state_style = {
            "running": "bold green",
            "completed": "dim",
            "error": "bold red",
            "paused": "yellow",
        }.get(a["state"], "white")

        table.add_row(
            a["id"],
            a["name"],
            a["role"],
            f"[{state_style}]{a['state']}[/{state_style}]",
            f"{a['tokens_used']:,}",
            str(a["turns"]),
        )

    console.print(table)


@app.command("kill")
def kill(agent_id: str = typer.Argument(help="Agent ID to kill")):
    """Kill a running agent."""
    ctx = AgosContext.get()
    try:
        run_async(ctx.runtime.kill(agent_id))
        console.print(f"[green]Killed agent {agent_id}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@app.command("pause")
def pause(agent_id: str = typer.Argument(help="Agent ID to pause")):
    """Pause a running agent."""
    ctx = AgosContext.get()
    try:
        run_async(ctx.runtime.pause(agent_id))
        console.print(f"[yellow]Paused agent {agent_id}[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@app.command("resume")
def resume(agent_id: str = typer.Argument(help="Agent ID to resume")):
    """Resume a paused agent."""
    ctx = AgosContext.get()
    try:
        run_async(ctx.runtime.resume(agent_id))
        console.print(f"[green]Resumed agent {agent_id}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@app.command("logs")
def logs(agent_id: str = typer.Argument(help="Agent ID to inspect")):
    """View an agent's conversation log."""
    ctx = AgosContext.get()
    try:
        agent = ctx.runtime.get(agent_id)
        for msg in agent.context.messages:
            role = msg.role.upper()
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            style = "blue" if role == "USER" else "green"
            console.print(f"[{style}][{role}][/{style}] {content[:500]}")
            console.print()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
