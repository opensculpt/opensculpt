"""Natural language intent handler — the default CLI command.

`agos "why is my API slow"` routes here. Every interaction is
automatically stored so the OS learns over time.
"""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from agos.cli.context import AgosContext, run_async

console = Console()


def handle_intent(user_input: str) -> None:
    """Process a natural language request through the Intent Engine."""
    ctx = AgosContext.get()

    from agos.config import settings
    if not settings.anthropic_api_key:
        console.print(
            "[red]No API key set.[/red] Run: "
            "[bold]export SCULPT_ANTHROPIC_API_KEY=your-key[/bold]"
        )
        raise SystemExit(1)

    async def _run() -> str:
        # Initialize knowledge system
        loom = await ctx.ensure_loom()

        # Step 1: Understand intent
        plan = await ctx.intent_engine.understand(user_input)

        # Show what the OS decided
        agents_str = ", ".join(a.name for a in plan.agents)
        console.print(
            f"[dim]intent={plan.intent_type.value} "
            f"strategy={plan.strategy.value} "
            f"agents=[{agents_str}][/dim]"
        )

        # Step 2: Execute the plan
        result = await ctx.planner.execute(plan, user_input)

        # Step 3: Learn from this interaction
        try:
            agent_list = ctx.runtime.list_agents()
            total_tokens = sum(a["tokens_used"] for a in agent_list)
            agent_name = plan.agents[0].name if plan.agents else "unknown"

            await loom.learner.record_interaction(
                agent_id=agent_list[-1]["id"] if agent_list else "system",
                agent_name=agent_name,
                user_input=user_input,
                agent_output=result,
                tokens_used=total_tokens,
            )
        except Exception:
            pass  # Learning failure should never block the response

        return result

    with console.status("[bold cyan]thinking...", spinner="dots"):
        result = run_async(_run())

    # Display the result — strip emoji/non-ASCII to avoid Windows encoding errors
    clean_result = result.encode("ascii", errors="ignore").decode("ascii")
    console.print()
    console.print(Markdown(clean_result))
    console.print()

    # Show token usage
    agents = ctx.runtime.list_agents()
    total_tokens = sum(a["tokens_used"] for a in agents)
    console.print(f"[dim]tokens used: {total_tokens:,}[/dim]")
