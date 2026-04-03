"""Setup commands — agos provider, agos channel, agos tool, agos setup."""

from __future__ import annotations

import json
import os

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ── Interactive Setup Wizard ─────────────────────────────────────

setup_app = typer.Typer(help="Interactive setup wizard")


@setup_app.callback(invoke_without_command=True)
def setup_wizard(ctx: typer.Context):
    """Interactive getting-started wizard for OpenSculpt."""
    if ctx.invoked_subcommand is not None:
        return
    _run_wizard()


def _run_wizard():
    from agos.config import settings
    from agos.setup_store import mark_wizard_complete

    console.print(Panel(
        "[bold]Welcome to OpenSculpt[/bold] — The Self-Evolving Agentic OS\n\n"
        "This wizard will help you configure the basics:\n"
        "  1. Connect an LLM provider\n"
        "  2. Set up notification channels\n"
        "  3. Detect vibe coding tools\n"
        "  4. Verify everything works",
        title="[bold cyan]sculpt setup[/bold cyan]",
        border_style="cyan",
    ))

    # Step 1: Detect and configure LLM
    console.print("\n[bold blue]Step 1/3: LLM Provider[/bold blue]")
    console.print("[dim]Scanning for local LLM servers and environment API keys...[/dim]\n")

    detected = _detect_providers()
    provider_configured = False

    if detected:
        console.print(f"[green]Found {len(detected)} provider(s):[/green]\n")
        for i, p in enumerate(detected, 1):
            if p["type"] == "local":
                models = ", ".join(p.get("models", [])[:3]) or "running"
                console.print(f"  {i}. [bold]{p['label']}[/bold] (local) — {models}")
            else:
                console.print(f"  {i}. [bold]{p['label']}[/bold] (cloud) — {p.get('key_preview', '')}")

        use_detected = typer.confirm("\nEnable detected provider(s)?", default=True)
        if use_detected:
            from agos.setup_store import set_provider_config
            for p in detected:
                cfg = {"enabled": True}
                if p["type"] == "local":
                    if p.get("url"):
                        cfg["base_url"] = p["url"]
                    if p.get("models"):
                        cfg["model"] = p["models"][0]
                elif p.get("env_var"):
                    key = os.environ.get(p["env_var"], "")
                    if key:
                        cfg["api_key"] = key
                set_provider_config(settings.workspace_dir, p["name"], cfg)
                console.print(f"  [green]Enabled[/green] {p['label']}")
            provider_configured = True

    if not provider_configured:
        console.print("[dim]No providers auto-detected.[/dim]" if not detected else "")
        console.print("\n[bold]Enter an API key to get started:[/bold]")
        provider_choices = {
            "1": ("anthropic", "Anthropic (Claude)"),
            "2": ("openai", "OpenAI (GPT)"),
            "3": ("groq", "Groq"),
            "4": ("deepseek", "DeepSeek"),
            "5": ("together", "Together AI"),
            "6": ("openrouter", "OpenRouter"),
        }
        for k, (_, label) in provider_choices.items():
            console.print(f"  {k}. {label}")
        console.print("  s. Skip (configure later)")

        choice = typer.prompt("\nSelect provider", default="1")
        if choice.lower() != "s" and choice in provider_choices:
            name, label = provider_choices[choice]
            api_key = typer.prompt(f"Enter {label} API key", hide_input=True)
            if api_key.strip():
                from agos.setup_store import set_provider_config
                set_provider_config(settings.workspace_dir, name, {
                    "enabled": True, "api_key": api_key.strip(),
                })
                console.print(f"  [green]Saved[/green] {label}")
                provider_configured = True
        elif choice.lower() == "s":
            console.print("  [dim]Skipped — configure later with: agos provider configure[/dim]")

    # Step 2: Channels
    console.print("\n[bold blue]Step 2/4: Notification Channels[/bold blue]")
    console.print("[dim]How should OpenSculpt notify you?[/dim]\n")

    channel_options = {
        "1": ("slack", "Slack", [("webhook_url", "Webhook URL")]),
        "2": ("discord", "Discord", [("webhook_url", "Webhook URL")]),
        "3": ("telegram", "Telegram", [("bot_token", "Bot Token"), ("chat_id", "Chat ID")]),
        "4": ("webhook", "Webhook", [("url", "URL")]),
        "5": ("ntfy", "ntfy (free push)", [("topic", "Topic name")]),
    }
    for k, (_, label, _) in channel_options.items():
        console.print(f"  {k}. {label}")
    console.print("  s. Skip")

    ch_choice = typer.prompt("\nSelect channel (or 's' to skip)", default="s")
    if ch_choice.lower() != "s" and ch_choice in channel_options:
        name, label, fields = channel_options[ch_choice]
        config = {}
        for key, field_label in fields:
            val = typer.prompt(f"  {field_label}")
            if val.strip():
                config[key] = val.strip()
        from agos.setup_store import set_channel_config
        set_channel_config(settings.workspace_dir, name, {
            "enabled": True, "config": config,
        })
        console.print(f"  [green]Saved[/green] {label}")

    # Step 3: Vibe coding tools
    console.print("\n[bold blue]Step 3/4: Vibe Coding Tools[/bold blue]")
    console.print("[dim]Scanning for AI coding tools (Claude Code, Cursor, Aider, Copilot, etc.)...[/dim]\n")

    from agos.vibe_tools import detect_vibe_tools
    vibe_tools = detect_vibe_tools(use_cache=False)
    installed_vibe = [t for t in vibe_tools if t.installed]

    if installed_vibe:
        console.print(f"[green]Found {len(installed_vibe)} tool(s):[/green]\n")
        for i, t in enumerate(installed_vibe, 1):
            version_str = f" ({t.version})" if t.version else ""
            cat_str = {"cli": "CLI", "ide": "IDE", "extension": "VS Code ext"}.get(t.category, t.category)
            console.print(f"  {i}. [bold]{t.label}[/bold] [{cat_str}]{version_str}")
            if t.path:
                console.print(f"     [dim]{t.path}[/dim]")

        # Auto-save detected tools
        from agos.setup_store import set_vibe_tool_config
        for t in installed_vibe:
            set_vibe_tool_config(settings.workspace_dir, t.name, {
                "enabled": True, "label": t.label, "path": t.path,
                "category": t.category, "auto_detected": True,
            })

        # Ask for preferred tool
        if len(installed_vibe) > 1:
            console.print("\n[bold]Which tool should OpenSculpt use by default for evolution nudges?[/bold]")
            for i, t in enumerate(installed_vibe, 1):
                console.print(f"  {i}. {t.label}")
            pref = typer.prompt("Select default", default="1")
            try:
                idx = int(pref) - 1
                if 0 <= idx < len(installed_vibe):
                    from agos.setup_store import set_preferred_vibe_tool
                    set_preferred_vibe_tool(settings.workspace_dir, installed_vibe[idx].name)
                    console.print(f"  [green]Default:[/green] {installed_vibe[idx].label}")
            except (ValueError, IndexError):
                pass
        elif len(installed_vibe) == 1:
            from agos.setup_store import set_preferred_vibe_tool
            set_preferred_vibe_tool(settings.workspace_dir, installed_vibe[0].name)
            console.print(f"  [green]Default:[/green] {installed_vibe[0].label}")
    else:
        console.print("[yellow]No AI coding tools detected.[/yellow]")
        console.print("[dim]Install one of: Claude Code, Cursor, Aider, Windsurf, GitHub Copilot[/dim]")
        console.print("[dim]The OS will re-scan when you run 'sculpt setup' again.[/dim]")

    # Step 4: Summary
    console.print("\n[bold blue]Step 4/4: Summary[/bold blue]\n")

    from agos.setup_store import load_setup
    setup = load_setup(settings.workspace_dir)
    providers_on = [n for n, c in setup.get("providers", {}).items() if c.get("enabled")]
    channels_on = [n for n, c in setup.get("channels", {}).items() if c.get("enabled")]

    summary = ""
    if providers_on:
        summary += f"  [green]LLM Providers:[/green] {', '.join(providers_on)}\n"
    else:
        summary += "  [yellow]LLM Providers:[/yellow] none configured\n"
    if channels_on:
        summary += f"  [green]Channels:[/green] {', '.join(channels_on)}\n"
    else:
        summary += "  [dim]Channels:[/dim] none (add later)\n"
    if installed_vibe:
        summary += f"  [green]Vibe Tools:[/green] {', '.join(t.label for t in installed_vibe)}\n"
    else:
        summary += "  [dim]Vibe Tools:[/dim] none detected\n"
    summary += "\n  [dim]Change settings anytime:[/dim]\n"
    summary += "    agos provider list / configure / test\n"
    summary += "    agos channel list / configure / test\n"
    summary += "    agos dashboard  (web UI at :8420)\n"

    console.print(Panel(summary, title="[bold green]Setup Complete[/bold green]", border_style="green"))

    mark_wizard_complete(settings.workspace_dir)

    if typer.confirm("\nLaunch dashboard?", default=True):
        console.print("[dim]Starting dashboard at http://127.0.0.1:8420 ...[/dim]")
        from agos.cli.main import _app
        _app(["dashboard"])


def _detect_providers() -> list[dict]:
    """Detect local LLM servers and env-var API keys."""
    import httpx

    detected = []

    env_providers = [
        ("openai", "OPENAI_API_KEY", "OpenAI"),
        ("anthropic", "ANTHROPIC_API_KEY", "Anthropic"),
        ("groq", "GROQ_API_KEY", "Groq"),
        ("together", "TOGETHER_API_KEY", "Together AI"),
        ("mistral", "MISTRAL_API_KEY", "Mistral"),
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek"),
        ("xai", "XAI_API_KEY", "xAI / Grok"),
        ("openrouter", "OPENROUTER_API_KEY", "OpenRouter"),
        ("cohere", "COHERE_API_KEY", "Cohere"),
        ("gemini", "GOOGLE_API_KEY", "Google Gemini"),
    ]
    from agos.config import settings
    # Check AGOS_ prefixed key
    if settings.anthropic_api_key:
        key = settings.anthropic_api_key
        detected.append({
            "name": "anthropic", "label": "Anthropic (OpenSculpt config)", "type": "cloud",
            "key_preview": key[:6] + "...", "env_var": "AGOS_ANTHROPIC_API_KEY",
        })

    for name, env_key, label in env_providers:
        val = os.environ.get(env_key, "")
        if val and not any(d["name"] == name for d in detected):
            detected.append({
                "name": name, "label": label, "type": "cloud",
                "key_preview": val[:6] + "...", "env_var": env_key,
            })

    # Probe local LLM servers (synchronous for CLI)
    local_servers = [
        ("ollama", "http://localhost:11434/api/tags", "Ollama"),
        ("lmstudio", "http://localhost:1234/v1/models", "LM Studio"),
        ("vllm", "http://localhost:8000/v1/models", "vLLM"),
    ]
    try:
        with httpx.Client(timeout=2.0) as client:
            for name, url, label in local_servers:
                try:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = []
                        if name == "ollama" and "models" in data:
                            models = [m.get("name", "") for m in data["models"][:5]]
                        elif "data" in data:
                            models = [m.get("id", "") for m in data["data"][:5]]
                        detected.append({
                            "name": name, "label": label, "type": "local",
                            "models": models, "url": url.rsplit("/", 1)[0],
                        })
                except Exception:
                    pass
    except Exception:
        pass

    return detected


# ── Provider commands ────────────────────────────────────────────

provider_app = typer.Typer(help="Manage LLM providers")


@provider_app.command("list")
def provider_list():
    """List all LLM providers and their config status."""
    from agos.llm.providers import ALL_PROVIDERS
    from agos.setup_store import load_setup
    from agos.config import settings

    setup = load_setup(settings.workspace_dir)
    saved = setup.get("providers", {})

    table = Table(title="LLM Providers")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white", max_width=40)
    table.add_column("Enabled", style="green", max_width=8)
    table.add_column("Key Set", style="yellow", max_width=8)

    for name, cls in ALL_PROVIDERS.items():
        cfg = saved.get(name, {})
        enabled = "yes" if cfg.get("enabled") else "no"
        key_set = "yes" if cfg.get("api_key") or cfg.get("base_url") else "no"
        desc = getattr(cls, "description", "")
        table.add_row(name, desc[:40], enabled, key_set)

    console.print(table)
    console.print(f"\n[dim]{len(ALL_PROVIDERS)} providers available[/dim]")


@provider_app.command("configure")
def provider_configure(
    name: str = typer.Argument(help="Provider name (e.g. openai, groq, ollama)"),
    key: str = typer.Option("", "--key", "-k", help="API key"),
    model: str = typer.Option("", "--model", "-m", help="Model name"),
    url: str = typer.Option("", "--url", "-u", help="Base URL (for local providers)"),
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable or disable"),
):
    """Configure an LLM provider.

    Examples:
        agos provider configure openai --key sk-abc123
        agos provider configure ollama --url http://localhost:11434 --model llama3 --enable
        agos provider configure groq --key gsk-abc123 --enable
    """
    from agos.llm.providers import ALL_PROVIDERS
    from agos.setup_store import get_provider_config, set_provider_config
    from agos.config import settings

    if name not in ALL_PROVIDERS:
        console.print(f"[red]Unknown provider: {name}[/red]")
        console.print(f"[dim]Available: {', '.join(ALL_PROVIDERS.keys())}[/dim]")
        raise SystemExit(1)

    cfg = get_provider_config(settings.workspace_dir, name)
    if key:
        cfg["api_key"] = key
    if model:
        cfg["model"] = model
    if url:
        cfg["base_url"] = url
    if enable is not None:
        cfg["enabled"] = enable

    set_provider_config(settings.workspace_dir, name, cfg)
    status = "[green]enabled[/green]" if cfg.get("enabled") else "[dim]disabled[/dim]"
    console.print(f"[green]Saved[/green] provider [bold]{name}[/bold] ({status})")


@provider_app.command("test")
def provider_test(
    name: str = typer.Argument(help="Provider name to test"),
):
    """Test an LLM provider by sending a short prompt."""
    from agos.llm.providers import ALL_PROVIDERS
    from agos.llm.base import LLMMessage
    from agos.setup_store import get_provider_config
    from agos.config import settings
    from agos.cli.context import run_async
    import inspect

    if name not in ALL_PROVIDERS:
        console.print(f"[red]Unknown provider: {name}[/red]")
        raise SystemExit(1)

    cfg = get_provider_config(settings.workspace_dir, name)
    if not cfg.get("enabled"):
        console.print(f"[red]Provider {name} is not enabled.[/red] Use: agos provider configure {name} --enable")
        raise SystemExit(1)

    cls = ALL_PROVIDERS[name]

    async def _test():
        sig = inspect.signature(cls.__init__)
        params = set(sig.parameters.keys()) - {"self"}
        kwargs = {}
        for k, v in cfg.items():
            if k in params and v:
                kwargs[k] = v
        if "token" in params and "api_key" in cfg:
            kwargs["token"] = cfg["api_key"]
        provider = cls(**kwargs)
        return await provider.complete(
            [LLMMessage(role="user", content="Say hello in 5 words.")],
            max_tokens=50,
        )

    with console.status(f"[bold cyan]Testing {name}...", spinner="dots"):
        try:
            resp = run_async(_test())
            console.print(f"[green]OK[/green] — {resp.content}")
            console.print(f"[dim]Tokens: {resp.output_tokens}[/dim]")
        except Exception as e:
            console.print(f"[red]Failed:[/red] {e}")
            raise SystemExit(1)


# ── Channel commands ─────────────────────────────────────────────

channel_app = typer.Typer(help="Manage notification channels")


@channel_app.command("list")
def channel_list():
    """List all notification channels and their config status."""
    from agos.channels.adapters import ALL_CHANNELS
    from agos.setup_store import load_setup
    from agos.config import settings

    setup = load_setup(settings.workspace_dir)
    saved = setup.get("channels", {})

    table = Table(title="Notification Channels")
    table.add_column("Icon", max_width=4)
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white", max_width=40)
    table.add_column("Enabled", style="green", max_width=8)

    for cls in ALL_CHANNELS:
        inst = cls()
        ch_cfg = saved.get(inst.name, {})
        enabled = "yes" if ch_cfg.get("enabled") else "no"
        table.add_row(inst.icon, inst.name, inst.description[:40], enabled)

    console.print(table)
    console.print(f"\n[dim]{len(ALL_CHANNELS)} channels available[/dim]")


@channel_app.command("configure")
def channel_configure(
    name: str = typer.Argument(help="Channel name (e.g. slack, discord, webhook)"),
    config: str = typer.Option("", "--config", "-c", help='Config JSON (e.g. \'{"webhook_url":"https://..."}\''),
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable or disable"),
):
    """Configure a notification channel.

    Examples:
        agos channel configure webhook --config '{"url":"https://example.com/hook"}' --enable
        agos channel configure slack --config '{"webhook_url":"https://hooks.slack.com/..."}' --enable
        agos channel configure discord --disable
    """
    from agos.channels.adapters import ALL_CHANNELS
    from agos.setup_store import get_channel_config, set_channel_config
    from agos.config import settings

    valid_names = {cls().name for cls in ALL_CHANNELS}
    if name not in valid_names:
        console.print(f"[red]Unknown channel: {name}[/red]")
        console.print(f"[dim]Available: {', '.join(sorted(valid_names))}[/dim]")
        raise SystemExit(1)

    ch_cfg = get_channel_config(settings.workspace_dir, name)
    if config:
        try:
            ch_cfg["config"] = json.loads(config)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON:[/red] {e}")
            raise SystemExit(1)
    if enable is not None:
        ch_cfg["enabled"] = enable

    set_channel_config(settings.workspace_dir, name, ch_cfg)
    status = "[green]enabled[/green]" if ch_cfg.get("enabled") else "[dim]disabled[/dim]"
    console.print(f"[green]Saved[/green] channel [bold]{name}[/bold] ({status})")


@channel_app.command("test")
def channel_test(
    name: str = typer.Argument(help="Channel name to test"),
):
    """Test a notification channel by sending a test message."""
    from agos.channels.adapters import ALL_CHANNELS
    from agos.channels.base import ChannelMessage
    from agos.setup_store import get_channel_config
    from agos.config import settings
    from agos.cli.context import run_async

    valid_names = {}
    for cls in ALL_CHANNELS:
        inst = cls()
        valid_names[inst.name] = inst

    if name not in valid_names:
        console.print(f"[red]Unknown channel: {name}[/red]")
        raise SystemExit(1)

    ch_cfg = get_channel_config(settings.workspace_dir, name)
    if not ch_cfg.get("enabled"):
        console.print(f"[red]Channel {name} is not enabled.[/red] Use: agos channel configure {name} --enable")
        raise SystemExit(1)

    inst = valid_names[name]
    msg = ChannelMessage(text="Test message from OpenSculpt", title="OpenSculpt Test", level="info")

    async def _test():
        return await inst.send(msg, ch_cfg.get("config", {}))

    with console.status(f"[bold cyan]Testing {name}...", spinner="dots"):
        try:
            result = run_async(_test())
            if result.success:
                console.print(f"[green]OK[/green] — {result.detail or 'Message sent'}")
            else:
                console.print(f"[red]Failed:[/red] {result.detail}")
                raise SystemExit(1)
        except SystemExit:
            raise
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)


# ── Tool commands ────────────────────────────────────────────────

tool_app = typer.Typer(help="Manage available tools")


@tool_app.command("list")
def tool_list():
    """List all available tools and their enabled status."""
    from agos.setup_store import load_setup
    from agos.config import settings
    from agos.cli.context import AgosContext

    ctx = AgosContext.get()
    setup = load_setup(settings.workspace_dir)
    saved_tools = setup.get("tools", {})

    schemas = ctx.runtime._tool_registry.list_tools()
    if not schemas:
        console.print("[dim]No tools registered.[/dim]")
        return

    table = Table(title="Available Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white", max_width=50)
    table.add_column("Enabled", style="green", max_width=8)

    for schema in schemas:
        t_cfg = saved_tools.get(schema.name, {})
        enabled = "yes" if t_cfg.get("enabled", True) else "no"
        table.add_row(schema.name, (schema.description or "")[:50], enabled)

    console.print(table)
    console.print(f"\n[dim]{len(schemas)} tools available[/dim]")


@tool_app.command("enable")
def tool_enable(
    name: str = typer.Argument(help="Tool name to enable"),
):
    """Enable a tool."""
    from agos.setup_store import set_tool_config
    from agos.config import settings

    set_tool_config(settings.workspace_dir, name, {"enabled": True})
    console.print(f"[green]Enabled[/green] tool [bold]{name}[/bold]")


@tool_app.command("disable")
def tool_disable(
    name: str = typer.Argument(help="Tool name to disable"),
):
    """Disable a tool."""
    from agos.setup_store import set_tool_config
    from agos.config import settings

    set_tool_config(settings.workspace_dir, name, {"enabled": False})
    console.print(f"[dim]Disabled[/dim] tool [bold]{name}[/bold]")
