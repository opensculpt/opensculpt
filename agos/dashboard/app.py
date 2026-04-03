"""Dashboard — FastAPI + WebSocket real-time OS monitoring.

`agos dashboard` launches this server at localhost:8420.
Provides REST endpoints, real system intelligence, and a live WebSocket stream.
"""

from __future__ import annotations

import os
import time
import pathlib
import subprocess

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agos.events.bus import Event
from agos.config import settings
from agos.a2a.server import router as a2a_router, set_server as set_a2a_server


# ── API key authentication middleware ────────────────────────────
_AUTH_SKIP_PATHS = frozenset({"/", "/health", "/api/status", "/docs", "/openapi.json", "/logo.jpg", "/favicon.ico"})


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header when SCULPT_DASHBOARD_API_KEY is set."""

    async def dispatch(self, request: Request, call_next):
        api_key = settings.dashboard_api_key
        if not api_key:
            return await call_next(request)
        if request.url.path in _AUTH_SKIP_PATHS:
            return await call_next(request)
        import hmac as _hmac
        provided = request.headers.get("x-api-key", "")
        if not _hmac.compare_digest(provided, api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
        return await call_next(request)


dashboard_app = FastAPI(title="OpenSculpt Dashboard", version="0.1.0")
dashboard_app.add_middleware(ApiKeyAuthMiddleware)
dashboard_app.include_router(a2a_router)

_runtime = None
_event_bus = None
_audit_trail = None
_policy_engine = None
_tracer = None
_loom = None
_evolution_state = None
_meta_evolver = None
_process_manager = None
_workload_discovery = None
_agent_registry = None
_os_agent = None
_mcp_manager = None
_approval_gate = None
_daemon_manager = None
_start_time = time.time()


def configure(runtime=None, event_bus=None, audit_trail=None,
              policy_engine=None, tracer=None, loom=None,
              evolution_state=None, meta_evolver=None,
              process_manager=None, workload_discovery=None,
              agent_registry=None, os_agent=None,
              mcp_manager=None, approval_gate=None,
              a2a_server=None, daemon_manager=None,
              task_planner=None, demand_collector=None,
              resource_registry=None, service_keeper=None) -> None:
    global _runtime, _event_bus, _audit_trail, _policy_engine, _tracer, _loom
    global _evolution_state, _meta_evolver, _process_manager, _workload_discovery
    global _agent_registry, _os_agent, _mcp_manager, _approval_gate, _daemon_manager
    global _task_planner, _demand_collector, _resource_registry, _service_keeper
    _runtime = runtime
    _event_bus = event_bus
    _audit_trail = audit_trail
    _policy_engine = policy_engine
    _tracer = tracer
    _loom = loom
    _evolution_state = evolution_state
    _meta_evolver = meta_evolver
    _process_manager = process_manager
    _workload_discovery = workload_discovery
    _agent_registry = agent_registry
    _os_agent = os_agent
    _mcp_manager = mcp_manager
    _approval_gate = approval_gate
    _daemon_manager = daemon_manager
    _task_planner = task_planner
    _demand_collector = demand_collector
    _resource_registry = resource_registry
    _service_keeper = service_keeper
    if a2a_server is not None:
        set_a2a_server(a2a_server)

    # Subscribe to user-action-needed events for evolution blockers
    if event_bus is not None:
        async def _on_user_action_needed(event):
            import time as _time
            _user_action_needed.append({
                "message": event.data.get("message", ""),
                "demand": event.data.get("demand", ""),
                "kind": event.data.get("kind", ""),
                "priority": event.data.get("priority", 0.5),
                "count": event.data.get("count", 1),
                "escalated": event.data.get("escalated", False),
                "timestamp": _time.time(),
            })
            # Keep list bounded
            while len(_user_action_needed) > _MAX_USER_ACTIONS:
                _user_action_needed.pop(0)
        event_bus.subscribe("evolution.user_action_needed", _on_user_action_needed)


# ── OS Shell — the primary interface to OpenSculpt ────────────────


class CommandPayload(BaseModel):
    command: str

_MAX_COMMAND_LEN = 10_000  # 10KB max — prevents OOM from giant payloads


@dashboard_app.post("/api/os/command")
async def os_command(payload: CommandPayload) -> dict:
    """The OS shell. Talk to OpenSculpt in natural language.

    'set up openclaw'  |  'what's running'  |  'stop picoclaw'
    """
    if _os_agent is None:
        return {"ok": False, "action": "error", "message": "OS agent not initialized.", "data": {}}
    if len(payload.command) > _MAX_COMMAND_LEN:
        return {"ok": False, "action": "error", "message": f"Command too long ({len(payload.command)} chars, max {_MAX_COMMAND_LEN}).", "data": {}}
    if not payload.command.strip():
        return {"ok": False, "action": "error", "message": "Empty command.", "data": {}}
    try:
        return await _os_agent.execute(payload.command)
    except Exception as e:
        return {"ok": False, "action": "error", "message": f"OS agent error: {e}", "data": {}}


@dashboard_app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)) -> dict:
    """Transcribe audio from the browser mic using speech_recognition."""
    import tempfile
    try:
        import speech_recognition as sr
    except ImportError:
        return {"ok": False, "text": "", "error": "speech_recognition not installed"}

    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"ok": False, "text": "", "error": "No audio data received"}

    # Save to temp WAV file for speech_recognition
    import os as _os2
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(audio_bytes)

        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_path) as source:
            audio_data = recognizer.record(source)
        text = recognizer.recognize_google(audio_data)
        return {"ok": True, "text": text}
    except sr.UnknownValueError:
        return {"ok": False, "text": "", "error": "Could not understand audio — try speaking louder or closer to the mic"}
    except sr.RequestError as e:
        return {"ok": False, "text": "", "error": f"Speech API error: {e}"}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)}
    finally:
        if tmp_path:
            try:
                _os2.unlink(tmp_path)
            except Exception:
                pass  # Windows file locking — will be cleaned up later


# ── Original endpoints (kept) ────────────────────────────────────

@dashboard_app.get("/")
async def index() -> HTMLResponse:
    # Inject API key into page so JS fetch calls can authenticate
    key = settings.dashboard_api_key or ""
    html = _DASHBOARD_HTML.replace(
        "/*__SCULPT_API_KEY__*/",
        f"const _SCULPT_API_KEY = '{key}';",
    )
    return HTMLResponse(html)


@dashboard_app.get("/logo.jpg")
async def logo():
    """Serve the OpenSculpt logo."""
    from pathlib import Path
    # Try multiple locations
    for p in [Path(__file__).parent / "static" / "logo.jpg",
              Path("agos/dashboard/static/logo.jpg"),
              Path("OpenSculpt_icon.jpg")]:
        if p.exists():
            return FileResponse(p, media_type="image/jpeg")
    return JSONResponse({"error": "logo not found"}, status_code=404)


@dashboard_app.get("/favicon.ico")
async def favicon():
    """Serve logo as favicon."""
    from pathlib import Path
    for p in [Path(__file__).parent / "static" / "logo.jpg",
              Path("agos/dashboard/static/logo.jpg"),
              Path("OpenSculpt_icon.jpg")]:
        if p.exists():
            return FileResponse(p, media_type="image/jpeg")
    return JSONResponse({"error": "not found"}, status_code=404)


@dashboard_app.get("/api/agents")
async def list_agents() -> list[dict]:
    if _runtime is None:
        return []
    return _runtime.list_agents()


@dashboard_app.get("/api/events")
async def list_events(topic: str = "*", limit: int = 50) -> list[dict]:
    if _event_bus is None:
        return []
    events = _event_bus.history(topic_filter=topic, limit=limit)
    return [e.model_dump(mode="json") for e in events]


@dashboard_app.get("/api/audit")
async def list_audit(agent_id: str = "", action: str = "", limit: int = 50) -> list[dict]:
    if _audit_trail is None:
        return []
    entries = await _audit_trail.query(agent_id=agent_id, action=action, limit=limit)
    return [e.model_dump(mode="json") for e in entries]


@dashboard_app.get("/api/status")
async def system_status() -> dict:
    agents = _runtime.list_agents() if _runtime else []
    return {
        "status": "ok",
        "version": "0.1.0",
        "node_role": settings.node_role,
        "agents_total": len(agents),
        "agents_running": sum(1 for a in agents if a["state"] == "running"),
        "agents_completed": sum(1 for a in agents if a["state"] == "completed"),
        "event_subscribers": _event_bus.subscriber_count if _event_bus else 0,
        "ws_connections": _event_bus.ws_connection_count if _event_bus else 0,
        "audit_entries": await _audit_trail.count() if _audit_trail else 0,
        "policies": len(_policy_engine.list_policies()) if _policy_engine else 0,
        "active_spans": _tracer.active_span_count if _tracer else 0,
        "knowledge_available": _loom is not None,
        "evolution_cycles": _evolution_state.data.cycles_completed if _evolution_state else 0,
        "uptime_s": int(time.time() - _start_time),
        "session_requests": getattr(_os_agent, "_session_requests", 0) if _os_agent else 0,
        "session_tokens": getattr(_os_agent, "_session_tokens", 0) if _os_agent else 0,
        "session_cost_usd": round(getattr(_os_agent, "_session_cost_usd", 0.0), 4) if _os_agent else 0,
        "session_input_tokens": getattr(_os_agent, "_session_input_tokens", 0) if _os_agent else 0,
        "session_output_tokens": getattr(_os_agent, "_session_output_tokens", 0) if _os_agent else 0,
        "lifetime_cost": getattr(_os_agent, "_lifetime_cost", {}) if _os_agent else {},
        "conversation_memory": len(getattr(_os_agent, "_conversation_history", [])) if _os_agent else 0,
        "compactor_stats": getattr(_os_agent, "_compactor", None) and _os_agent._compactor.stats or {},
        "demand_signals": _demand_collector.pending_count() if _demand_collector else 0,
        "resources_active": len(_resource_registry.active()) if _resource_registry else 0,
        "resources_down": len([r for r in _resource_registry.all_resources() if r.status == "down"]) if _resource_registry else 0,
    }


# ── Settings: API Key ────────────────────────────────────────────

class ApiKeyPayload(BaseModel):
    api_key: str = ""
    provider: str = "anthropic"
    model: str = ""
    base_url: str = ""


class GitHubTokenPayload(BaseModel):
    github_token: str


class FederatedTogglePayload(BaseModel):
    enabled: bool
    interval: int = 3


@dashboard_app.get("/api/settings")
async def get_settings() -> dict:
    has_key = bool(settings.anthropic_api_key)
    has_gh = bool(settings.github_token)
    api_key_preview = settings.anthropic_api_key[:8] + "..." if has_key else ""
    # Detect active provider + key from setup.json (wizard saves here)
    active_provider = "anthropic"
    try:
        from agos.setup_store import load_setup
        import pathlib
        data = load_setup(pathlib.Path(settings.workspace_dir))
        active_provider = data.get("active_provider", "anthropic")
        # If in-memory key is empty, check setup.json providers
        if not has_key:
            providers = data.get("providers", {})
            prov_data = providers.get(active_provider, {})
            stored_key = prov_data.get("api_key", "")
            if stored_key:
                has_key = True
                api_key_preview = stored_key[:8] + "..."
                # Also hydrate in-memory settings so the OS agent can use it
                settings.anthropic_api_key = stored_key
    except Exception:
        pass
    return {
        "has_api_key": has_key,
        "api_key_preview": api_key_preview,
        "model": settings.default_model,
        "active_provider": active_provider,
        "has_github_token": has_gh,
        "github_token_preview": settings.github_token[:8] + "..." if has_gh else "",
        "sharing_model": "git-prs",
    }


@dashboard_app.post("/api/settings/apikey")
async def set_api_key(payload: ApiKeyPayload) -> dict:
    key = payload.api_key.strip()
    provider_name = payload.provider or "anthropic"
    model = payload.model or settings.default_model

    if not key and provider_name != "lmstudio":
        return {"ok": False, "error": "API key cannot be empty"}

    _base_url = payload.base_url.strip() if payload.base_url else ""

    # Wire LLM provider into OS agent
    if _os_agent is not None:
        try:
            actual_key = key or "local"
            # Use the correct provider class based on provider name
            from agos.llm.providers import (
                OpenRouterProvider, OpenAIProvider, GroqProvider,
                TogetherProvider, MistralProvider, FireworksProvider,
                DeepSeekProvider, PerplexityProvider, CohereProvider,
                GeminiProvider, OllamaProvider, LMStudioProvider,
                XAIProvider,
            )
            from agos.llm.anthropic import AnthropicProvider

            _provider_classes = {
                "anthropic": lambda: AnthropicProvider(api_key=actual_key, model=model),
                "openrouter": lambda: OpenRouterProvider(api_key=actual_key, model=model),
                "openai": lambda: OpenAIProvider(api_key=actual_key, model=model),
                "groq": lambda: GroqProvider(api_key=actual_key, model=model),
                "together": lambda: TogetherProvider(api_key=actual_key, model=model),
                "mistral": lambda: MistralProvider(api_key=actual_key, model=model),
                "fireworks": lambda: FireworksProvider(api_key=actual_key, model=model),
                "deepseek": lambda: DeepSeekProvider(api_key=actual_key, model=model),
                "perplexity": lambda: PerplexityProvider(api_key=actual_key, model=model),
                "cohere": lambda: CohereProvider(api_key=actual_key, model=model),
                "gemini": lambda: GeminiProvider(api_key=actual_key, model=model),
                "xai": lambda: XAIProvider(api_key=actual_key, model=model),
                "ollama": lambda: OllamaProvider(api_key=actual_key, model=model),
                "lmstudio": lambda: LMStudioProvider(api_key=actual_key, model=model),
            }
            factory = _provider_classes.get(provider_name)
            if factory:
                provider = factory()
            else:
                # Fallback: use Anthropic provider for unknown names
                provider = AnthropicProvider(api_key=actual_key, model=model)
            provider.name = provider_name
            _os_agent.set_llm(provider)
        except Exception as e:
            return {"ok": False, "error": f"Failed to configure provider: {e}"}

    settings.anthropic_api_key = key
    if model:
        settings.default_model = model

    # Save to setup.json
    try:
        from agos.setup_store import load_setup, save_setup
        import pathlib
        ws = pathlib.Path(settings.workspace_dir)
        data = load_setup(ws)
        data.setdefault("providers", {})[provider_name] = {"enabled": True, "api_key": key, "model": model}
        data["active_provider"] = provider_name
        save_setup(ws, data)
    except Exception:
        pass
    return {"ok": True, "preview": key[:8] + "..." if key else "local", "provider": provider_name, "model": model}


@dashboard_app.post("/api/settings/github-token")
async def set_github_token(payload: GitHubTokenPayload) -> dict:
    token = payload.github_token.strip()
    if not token:
        return {"ok": False, "error": "Token cannot be empty"}
    settings.github_token = token
    return {"ok": True, "preview": token[:8] + "..."}


@dashboard_app.post("/api/settings/federated")
async def set_federated(payload: FederatedTogglePayload) -> dict:
    # Auto-share removed — users share via git PRs
    return {"ok": True, "sharing_model": "git-prs"}


class ProviderPayload(BaseModel):
    provider: str
    model: str = ""


@dashboard_app.post("/api/settings/provider")
async def set_provider(payload: ProviderPayload) -> dict:
    """Switch LLM provider at runtime. Supports claude_code (no API key needed)."""
    name = payload.provider.strip()
    if not name:
        return {"ok": False, "error": "Provider name required"}

    from agos.llm.providers import ALL_PROVIDERS
    if name not in ALL_PROVIDERS:
        return {"ok": False, "error": f"Unknown provider: {name}", "available": list(ALL_PROVIDERS.keys())}

    cls = ALL_PROVIDERS[name]
    try:
        if name == "claude_code":
            # No API key needed — uses your Claude Code subscription
            kwargs = {}
            if payload.model:
                kwargs["model"] = payload.model
            # Default to CLI mode (free), allow oauth via model field "sonnet:oauth"
            if ":" in (payload.model or ""):
                model_part, mode_part = payload.model.rsplit(":", 1)
                kwargs["model"] = model_part
                kwargs["mode"] = mode_part
            provider = cls(**kwargs)
        elif name in ("ollama", "lmstudio", "vllm"):
            # Local providers — no API key
            kwargs = {}
            if payload.model:
                kwargs["model"] = payload.model
            provider = cls(**kwargs)
        else:
            return {"ok": False, "error": f"Provider '{name}' requires API key — use /api/settings/apikey"}

        if _os_agent is not None:
            _os_agent.set_llm(provider)
        return {"ok": True, "provider": name, "model": payload.model or "default"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Fleet Sync (peer-to-peer evolution sharing) ──────────────────


@dashboard_app.get("/api/sync/manifest")
async def sync_manifest() -> dict:
    """Advertise this node's evolution state for peer sync (includes efficacy data)."""
    from agos.evolution.sync import build_local_manifest
    if _evolution_state is None:
        return {"error": "evolution state not initialized"}
    # Pass demand_collector for efficacy data in manifest
    _dc = getattr(dashboard_app.state, "demand_collector", None) if hasattr(dashboard_app, "state") else None
    manifest = build_local_manifest(_evolution_state, demand_collector=_dc)
    return manifest.to_dict()


@dashboard_app.post("/api/sync/pull")
async def sync_pull(remote_manifest: dict) -> dict:
    """Return a delta payload for a requesting peer.

    The peer sends its manifest (what it already has), and we return
    only the data it doesn't have.
    """
    from agos.evolution.sync import SyncManifest, build_sync_payload
    if _evolution_state is None:
        return {"error": "evolution state not initialized"}
    manifest = SyncManifest.from_dict(remote_manifest)
    return build_sync_payload(_evolution_state, manifest)


# ── Federation knowledge endpoints ────────────────────────────────

@dashboard_app.get("/api/federation/status")
async def federation_status() -> dict:
    """Federation knowledge layer status — tagged .md directories."""
    result = {"constraints": 0, "resolutions": 0, "environment_tags": []}
    try:
        from agos.knowledge.tagged_store import TaggedConstraintStore, TaggedResolutionStore, environment_tags
        result["constraints"] = TaggedConstraintStore().count()
        result["resolutions"] = TaggedResolutionStore().count()
        result["environment_tags"] = environment_tags()
    except Exception:
        pass
    return result


@dashboard_app.get("/api/federation/resolutions")
async def federation_resolutions() -> dict:
    """Learned resolution patterns — from tagged resolution store."""
    try:
        from agos.knowledge.tagged_store import TaggedResolutionStore
        _rs = TaggedResolutionStore()
        # Return index (all resolutions, one-line each) as primary content
        index_path = _rs._resolutions_dir / "_index.md"
        content = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        if not content or _rs.count() == 0:
            content = "# No resolutions learned yet\n"
        return {"content": content, "count": _rs.count()}
    except Exception:
        return {"content": "# No resolutions learned yet\n", "count": 0}


@dashboard_app.get("/api/federation/scores")
async def federation_scores() -> dict:
    """Artifact scores from the local scoring engine."""
    _sp = pathlib.Path(settings.workspace_dir) / "artifact_scores.json"
    if not _sp.exists():
        return {"scores": {}}
    try:
        import json
        return {"scores": json.loads(_sp.read_text(encoding="utf-8"))}
    except Exception:
        return {"scores": {}}


@dashboard_app.get("/api/federation/fleet-report")
async def federation_fleet_report() -> dict:
    """Generate fleet report (if fleet data is available)."""
    fleet_dir = pathlib.Path(settings.fleet_dir)
    if not fleet_dir.exists():
        return {"report": "Fleet directory not found. Configure SCULPT_FLEET_DIR."}
    from agos.evolution.curator import generate_fleet_report
    return {"report": generate_fleet_report(fleet_dir)}


@dashboard_app.get("/api/evolution/nudge")
async def evolution_nudge() -> dict:
    """Evolution nudge data — demands + prompt for AI coding tools."""
    from agos.evolution.nudge import get_demand_count, write_demands_md
    active, escalated = get_demand_count()
    total = active + escalated

    # Generate DEMANDS.md and return its content
    demands_path = write_demands_md()
    prompt = ""
    try:
        prompt = demands_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # Build tools list from detected vibe tools (not hardcoded)
    from agos.vibe_tools import get_installed_tools
    detected = get_installed_tools()
    tools_list = [{"name": t.label, "command": t.how_to_use} for t in detected]
    # Always include generic fallback
    tools_list.append({"name": "Any tool", "command": "Copy the prompt below and paste into any AI coding tool."})

    return {
        "active": active,
        "escalated": escalated,
        "total": total,
        "prompt": prompt,
        "tools": tools_list,
    }


@dashboard_app.get("/api/federation/constraints")
async def federation_constraints() -> dict:
    """Learned environment constraints — from tagged constraint store."""
    try:
        from agos.knowledge.tagged_store import TaggedConstraintStore, environment_tags
        _cs = TaggedConstraintStore()
        content = _cs.load()  # Only environment-matched constraints
        return {
            "content": content or "# No constraints learned yet\n",
            "count": _cs.count(),
            "environment_tags": environment_tags(),
            "index": _cs.load_index(max_chars=2000),
        }
    except Exception:
        return {"content": "# No constraints learned yet\n", "count": 0}


# ── Vibe coding tools ─────────────────────────────────────────────


@dashboard_app.get("/api/vibe-tools")
async def vibe_tools_endpoint() -> dict:
    """Detected vibe coding tools on this machine."""
    from agos.vibe_tools import detect_vibe_tools
    all_tools = detect_vibe_tools()
    installed = [t for t in all_tools if t.installed]
    return {
        "installed": [t.to_dict() for t in installed],
        "all": [t.to_dict() for t in all_tools],
        "count": len(installed),
        "total": len(all_tools),
    }


@dashboard_app.post("/api/vibe-tools/rescan")
async def vibe_tools_rescan() -> dict:
    """Force re-scan for vibe coding tools."""
    from agos.vibe_tools import detect_vibe_tools, reset_cache
    reset_cache()
    all_tools = detect_vibe_tools(use_cache=False)
    installed = [t for t in all_tools if t.installed]
    return {
        "installed": [t.to_dict() for t in installed],
        "count": len(installed),
    }


@dashboard_app.post("/api/vibe-tools/preferred")
async def set_preferred_vibe_tool_endpoint(body: dict) -> dict:
    """Set the preferred vibe coding tool."""
    from agos.config import settings
    from agos.setup_store import set_preferred_vibe_tool
    name = body.get("name", "")
    if not name:
        return {"error": "name required"}
    set_preferred_vibe_tool(settings.workspace_dir, name)
    return {"ok": True, "preferred": name}


# ── Demand-driven evolution ──────────────────────────────────────

_demand_collector = None  # Set by serve.py


@dashboard_app.get("/api/evolution/changelog")
async def evolution_changelog() -> dict:
    """What evolved, when, why, and what it means for the user.

    Reads real evolved files from disk, real insights from EvolutionMemory,
    and real strategies from EvolutionState. Nothing is fabricated.
    """
    from pathlib import Path

    entries = []
    evolved_dir = Path(settings.workspace_dir) / "evolved"

    # 1. Read real evolved files from disk
    if evolved_dir.exists():
        for py_file in sorted(evolved_dir.glob("*.py"), key=lambda f: f.stat().st_mtime, reverse=True):
            if py_file.name.startswith("_"):
                continue
            header = {}
            try:
                raw = py_file.read_text(encoding="utf-8", errors="replace")
                for line in raw.splitlines():
                    line = line.strip()
                    if not line.startswith("#"):
                        break
                    if ":" in line:
                        key, _, val = line.lstrip("# ").partition(":")
                        header[key.strip().lower()] = val.strip()
            except Exception:
                continue

            stat = py_file.stat()
            try:
                line_count = len(py_file.read_text(encoding="utf-8", errors="replace").splitlines())
            except Exception:
                line_count = 0
            entry = {
                "file": py_file.name,
                "pattern": header.get("pattern", py_file.stem),
                "module": header.get("module", "unknown"),
                "paper": header.get("paper", None),
                "sandbox": header.get("sandbox", None),
                "evolved_at": stat.st_mtime,
                "size_bytes": stat.st_size,
                "lines": line_count,
                # User-facing explanation
                "what_it_does": _explain_module(header.get("module", "")),
            }
            entries.append(entry)

    # 2. Real evolution memory insights (what worked, what failed)
    insights_summary = []
    if _evolution_state:
        evo_mem = _evolution_state.restore_evolution_memory()
        recent = evo_mem.insights[-10:] if evo_mem.insights else []
        for ins in reversed(recent):
            insights_summary.append({
                "cycle": ins.cycle,
                "what": ins.what_tried,
                "module": ins.module,
                "outcome": ins.outcome,
                "reason": ins.reason,
                "paper": ins.source_paper,
                "what_worked": getattr(ins, "what_worked", ""),
                "principle": getattr(ins, "principle", ""),
                "recommendation": getattr(ins, "recommendation", ""),
                "applies_when": getattr(ins, "applies_when", ""),
                "scenario_type": getattr(ins, "scenario_type", ""),
                "environment_match": getattr(ins, "environment_match", "any"),
                "confidence": getattr(ins, "confidence", 1.0),
            })

    # 3. Demand signals (what the user's activity is requesting)
    demands = []
    if _demand_collector and _demand_collector.has_demands():
        for d in _demand_collector.top_demands(limit=5):
            demands.append({
                "kind": d.kind, "source": d.source,
                "description": d.description,
                "count": d.count, "priority": round(d.priority, 2),
            })

    return {
        "evolved_files": entries,
        "total_evolved": len(entries),
        "recent_insights": insights_summary,
        "active_demands": demands,
        "cycles_completed": _evolution_state.data.cycles_completed if _evolution_state else 0,
    }


def _explain_module(module: str) -> str:
    """Translate a module path into a user-facing explanation."""
    explanations = {
        "knowledge.semantic": "Improves how the OS finds and ranks relevant information from memory",
        "knowledge.graph": "Improves how the OS understands relationships between concepts",
        "knowledge": "Improves the OS memory and recall system",
        "knowledge.manager": "Improves how the OS organizes and prioritizes knowledge layers",
        "knowledge.consolidator": "Improves how the OS compresses and summarizes stored knowledge",
        "intent": "Improves how the OS understands what you're asking it to do",
        "intent.personas": "Improves how the OS adapts its behavior to different tasks",
        "intent.proactive": "Improves the OS ability to detect patterns and suggest actions",
        "tools": "Adds or improves tools the OS can use to complete tasks",
        "policy": "Improves the OS security and access control decisions",
        "policy.audit": "Improves how the OS tracks and logs actions for compliance",
        "coordination": "Improves how multiple agents work together on complex tasks",
        "orchestration.planner": "Improves how the OS plans and sequences multi-step tasks",
        "orchestration.runtime": "Improves how the OS schedules and dispatches work efficiently",
        "events": "Improves the OS internal communication and event routing",
        "events.tracing": "Improves observability — what happened, when, and why",
        "kernel": "Improves core OS internals — agent lifecycle and resource management",
        "evolution": "Improves the evolution engine itself — the OS gets better at getting better",
    }
    return explanations.get(module, f"Improves the {module} subsystem")


@dashboard_app.get("/api/evolution/demands")
async def evolution_demands() -> dict:
    """Return current demand signals driving evolution."""
    if _demand_collector is None:
        return {"total_signals": 0, "by_kind": {}, "top_demands": [],
                "tool_failure_counts": {}, "command_error_counts": {}}
    return _demand_collector.summary()


_source_patcher = None  # Set by serve.py

# User action needed — evolution blockers that the OS can't solve alone.
# Populated by the event bus (evolution.user_action_needed events).
_user_action_needed: list[dict] = []
_MAX_USER_ACTIONS = 20


@dashboard_app.get("/api/evolution/blockers")
async def evolution_blockers() -> dict:
    """Return things the OS needs from the user to proceed."""
    return {
        "blockers": _user_action_needed[-_MAX_USER_ACTIONS:],
        "count": len(_user_action_needed),
    }


@dashboard_app.post("/api/evolution/blockers/{index}/dismiss")
async def dismiss_blocker(index: int) -> dict:
    """User dismisses a blocker (they've handled it or it's not relevant)."""
    if 0 <= index < len(_user_action_needed):
        removed = _user_action_needed.pop(index)
        return {"dismissed": True, "blocker": removed}
    return {"dismissed": False, "reason": "invalid index"}


@dashboard_app.get("/api/patches")
async def list_patches() -> dict:
    """Return applied source patches from the self-modifying code engine."""
    if _source_patcher is None:
        return {"patches": [], "stats": {"total_patches": 0}}
    return {
        "patches": _source_patcher.get_patches(),
        "stats": _source_patcher.get_stats(),
    }



_resource_registry = None  # Set by serve.py
_service_keeper = None  # Set by serve.py — agentic service lifecycle


@dashboard_app.get("/api/resources")
async def list_resources() -> dict:
    """Return all tracked resources with REAL status (runs reality check)."""
    if _resource_registry is None:
        return {"resources": [], "stats": {"total": 0, "active": 0}}
    # Run reality check before returning - show truth, not fantasy
    await _resource_registry.reconcile()
    return {
        "resources": [r.to_dict() for r in _resource_registry.all_resources()],
        "stats": _resource_registry.stats(),
    }


# ── Task Plans (persistent multi-step tasks) ────────────────────

_task_planner = None  # Set by serve.py


@dashboard_app.get("/api/tasks")
async def list_tasks() -> dict:
    """List all task plans with progress."""
    if _task_planner is None:
        return {"plans": []}
    return {"plans": _task_planner.list_plans()}


@dashboard_app.get("/api/tasks/{plan_id}")
async def get_task(plan_id: str) -> dict:
    """Get a specific task plan with all steps."""
    if _task_planner is None:
        return {"error": "Task planner not available"}
    plan = _task_planner.get_plan(plan_id)
    if not plan:
        return {"error": "Plan not found"}
    from dataclasses import asdict
    return {
        "id": plan.id, "name": plan.name, "description": plan.description,
        "status": plan.status, "progress": plan.progress, "summary": plan.summary,
        "steps": [asdict(s) for s in plan.steps],
    }


# ── Goals (persistent autonomous goals) ─────────────────────────

@dashboard_app.get("/api/goals")
async def list_goals() -> dict:
    """List all autonomous goals and their progress."""
    if _daemon_manager is None:
        return {"goals": []}
    goal_runner = _daemon_manager.get_goal_runner()
    if not goal_runner:
        return {"goals": []}
    return {"goals": goal_runner.get_goals()}


@dashboard_app.post("/api/goals/{goal_id}/cancel")
async def cancel_goal(goal_id: str) -> dict:
    """Cancel a stale or failed goal to stop burning tokens."""
    if _daemon_manager is None:
        return {"ok": False, "error": "No daemon manager"}
    goal_runner = _daemon_manager.get_goal_runner()
    if not goal_runner:
        return {"ok": False, "error": "No goal runner"}
    goals = goal_runner.get_goals()
    for g in goals:
        if g.get("id") == goal_id:
            g["status"] = "cancelled"
            # Stop all pending/retrying phases
            for p in g.get("phases", []):
                if p.get("status") in ("pending", "retrying", "running"):
                    p["status"] = "cancelled"
            return {"ok": True, "goal_id": goal_id}
    return {"ok": False, "error": "Goal not found"}


# ── Wizard ───────────────────────────────────────────────────────


@dashboard_app.get("/api/wizard/status")
async def wizard_status() -> dict:
    from agos.setup_store import is_first_run
    return {"first_run": is_first_run(pathlib.Path(settings.workspace_dir))}


@dashboard_app.post("/api/wizard/complete")
async def wizard_complete() -> dict:
    from agos.setup_store import mark_wizard_complete
    mark_wizard_complete(pathlib.Path(settings.workspace_dir))
    return {"ok": True}


@dashboard_app.get("/api/wizard/detect")
async def wizard_detect() -> dict:
    """Auto-detect local LLM servers and env-var API keys."""
    import os
    import httpx

    detected: list[dict] = []

    # Check for env-var API keys
    env_providers = [
        ("openai", "OPENAI_API_KEY", "OpenAI"),
        ("anthropic", "ANTHROPIC_API_KEY", "Anthropic"),
        ("groq", "GROQ_API_KEY", "Groq"),
        ("together", "TOGETHER_API_KEY", "Together AI"),
        ("mistral", "MISTRAL_API_KEY", "Mistral"),
        ("fireworks", "FIREWORKS_API_KEY", "Fireworks AI"),
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek"),
        ("xai", "XAI_API_KEY", "xAI / Grok"),
        ("openrouter", "OPENROUTER_API_KEY", "OpenRouter"),
        ("cohere", "COHERE_API_KEY", "Cohere"),
        ("gemini", "GOOGLE_API_KEY", "Google Gemini"),
    ]
    for name, env_key, label in env_providers:
        val = os.environ.get(env_key, "") or os.environ.get(f"SCULPT_{env_key}", "") or os.environ.get(f"AGOS_{env_key}", "")
        if val:
            detected.append({
                "name": name, "label": label, "type": "cloud",
                "key_preview": val[:6] + "..." if len(val) > 6 else "***",
                "env_var": env_key,
            })

    if settings.anthropic_api_key and not any(d["name"] == "anthropic" for d in detected):
        key = settings.anthropic_api_key
        detected.append({
            "name": "anthropic", "label": "Anthropic (OpenSculpt config)", "type": "cloud",
            "key_preview": key[:6] + "..." if len(key) > 6 else "***",
            "env_var": "SCULPT_LLM_API_KEY",
        })

    # Probe local LLM servers
    local_servers = [
        ("ollama", "http://localhost:11434/api/tags", "Ollama"),
        ("lmstudio", "http://localhost:1234/v1/models", "LM Studio"),
        ("vllm", "http://localhost:8000/v1/models", "vLLM"),
    ]
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, url, label in local_servers:
            try:
                resp = await client.get(url)
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

    return {"detected": detected}


@dashboard_app.get("/api/wizard/detect-all")
async def wizard_detect_all() -> dict:
    """One-shot detection of everything: LLM providers, vibe tools, environment."""
    import httpx

    # 1. LLM providers (env vars + local servers)
    providers: list[dict] = []
    env_providers = [
        ("openai", "OPENAI_API_KEY", "OpenAI"),
        ("anthropic", "ANTHROPIC_API_KEY", "Anthropic"),
        ("groq", "GROQ_API_KEY", "Groq"),
        ("together", "TOGETHER_API_KEY", "Together AI"),
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek"),
        ("openrouter", "OPENROUTER_API_KEY", "OpenRouter"),
        ("gemini", "GOOGLE_API_KEY", "Google Gemini"),
        ("xai", "XAI_API_KEY", "xAI / Grok"),
        ("mistral", "MISTRAL_API_KEY", "Mistral"),
        ("cohere", "COHERE_API_KEY", "Cohere"),
    ]
    for name, env_key, label in env_providers:
        val = os.environ.get(env_key, "") or os.environ.get(f"SCULPT_{env_key}", "")
        if val:
            providers.append({
                "name": name, "label": label, "type": "cloud",
                "key_preview": val[:6] + "...", "env_var": env_key,
            })
    if settings.anthropic_api_key and not any(d["name"] == "anthropic" for d in providers):
        key = settings.anthropic_api_key
        providers.append({
            "name": "anthropic", "label": "Anthropic (config)", "type": "cloud",
            "key_preview": key[:6] + "...",
        })
    # Claude Code CLI — free, no key needed
    try:
        from agos.llm.claude_code import _find_claude_exe
        if _find_claude_exe():
            providers.append({
                "name": "claude_code", "label": "Claude Code (free, your subscription)",
                "type": "local", "models": [],
            })
    except Exception:
        pass
    # Local LLM servers
    local_servers = [
        ("ollama", "http://localhost:11434/api/tags", "Ollama"),
        ("lmstudio", "http://localhost:1234/v1/models", "LM Studio"),
    ]
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, url, label in local_servers:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    models = []
                    if name == "ollama" and "models" in data:
                        models = [m.get("name", "") for m in data["models"][:5]]
                    elif "data" in data:
                        models = [m.get("id", "") for m in data["data"][:5]]
                    providers.append({
                        "name": name, "label": label, "type": "local", "models": models,
                    })
            except Exception:
                pass

    # 2. Vibe coding tools
    from agos.vibe_tools import detect_vibe_tools
    all_vibe = detect_vibe_tools(use_cache=False)
    vibe_tools = [t.to_dict() for t in all_vibe]

    # 3. Environment summary
    from agos.environment import EnvironmentProbe
    env = EnvironmentProbe.to_dict()
    env_summary = EnvironmentProbe.summary()

    return {
        "providers": providers,
        "vibe_tools": vibe_tools,
        "environment": {
            "os": env.get("os_name", ""),
            "arch": env.get("os_arch", ""),
            "docker": env.get("docker_available", False),
            "internet": env.get("internet", False),
            "memory_mb": env.get("memory_total_mb", 0),
            "disk_gb": env.get("disk_free_gb", 0),
            "strategy": env.get("recommended_strategy", ""),
            "in_container": env.get("in_container", False),
        },
        "environment_summary": env_summary,
    }


@dashboard_app.post("/api/wizard/save")
async def wizard_save(body: dict) -> dict:
    """Save wizard selections: provider, vibe tools, preferences."""
    ws = pathlib.Path(settings.workspace_dir)
    from agos.setup_store import (
        set_provider_config, set_vibe_tool_config,
        set_preferred_vibe_tool, mark_wizard_complete,
    )

    # Save selected provider
    provider = body.get("provider")
    if provider:
        cfg = {"enabled": True}
        if provider.get("api_key"):
            cfg["api_key"] = provider["api_key"]
        if provider.get("base_url"):
            cfg["base_url"] = provider["base_url"]
        set_provider_config(ws, provider["name"], cfg)

        # Set as active provider
        from agos.setup_store import load_setup, save_setup
        data = load_setup(ws)
        data["active_provider"] = provider["name"]
        save_setup(ws, data)

    # Save vibe tools
    for vt in body.get("vibe_tools", []):
        set_vibe_tool_config(ws, vt["name"], {
            "enabled": True, "label": vt.get("label", ""),
            "auto_detected": True,
        })
    if body.get("preferred_vibe_tool"):
        set_preferred_vibe_tool(ws, body["preferred_vibe_tool"])

    mark_wizard_complete(ws)
    return {"ok": True}


@dashboard_app.post("/api/wizard/demo")
async def wizard_demo(payload: CommandPayload) -> dict:
    """Run a real system probe for the wizard demo (no OS agent needed)."""
    import asyncio
    import platform

    cmd = payload.command.lower().strip()
    lines: list[dict] = []  # {"text": ..., "cls": "ok"|"info"|"dim"|"cmd"|"warn"}

    try:
        if "system info" in cmd or "hardware" in cmd or "cpu" in cmd or "ram" in cmd:
            lines.append({"text": "$ systeminfo", "cls": "cmd"})
            import psutil
            uname = platform.uname()
            lines.append({"text": f"OS: {uname.system} {uname.release} ({uname.version})", "cls": "info"})
            lines.append({"text": f"Machine: {uname.node}", "cls": "info"})
            lines.append({"text": f"Processor: {uname.processor or platform.processor()}", "cls": "info"})
            lines.append({"text": f"CPU cores: {psutil.cpu_count(logical=False)} physical, {psutil.cpu_count()} logical", "cls": "info"})
            lines.append({"text": f"CPU usage: {psutil.cpu_percent(interval=0.5)}%", "cls": "ok" if psutil.cpu_percent() < 80 else "warn"})
            mem = psutil.virtual_memory()
            lines.append({"text": f"RAM: {mem.total // (1024**3)} GB total, {mem.available // (1024**3)} GB free ({mem.percent}% used)", "cls": "ok" if mem.percent < 80 else "warn"})
            disk = psutil.disk_usage("/")
            lines.append({"text": f"Disk: {disk.total // (1024**3)} GB total, {disk.free // (1024**3)} GB free ({disk.percent}% used)", "cls": "ok" if disk.percent < 90 else "warn"})
            # GPU detection
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                if out.strip():
                    for gpu_line in out.decode().strip().split("\n"):
                        lines.append({"text": f"GPU: {gpu_line.strip()}", "cls": "ok"})
            except Exception:
                lines.append({"text": "GPU: not detected (no nvidia-smi)", "cls": "dim"})

        elif "process" in cmd or "running" in cmd or "what" in cmd:
            lines.append({"text": "$ tasklist (top processes by memory)", "cls": "cmd"})
            import psutil
            procs = []
            for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
                try:
                    info = p.info
                    mem_mb = (info["memory_info"].rss // (1024 * 1024)) if info.get("memory_info") else 0
                    procs.append((info["name"], mem_mb, info.get("cpu_percent", 0)))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x[1], reverse=True)
            lines.append({"text": f"{'PROCESS':<30} {'MEM (MB)':>10} {'CPU %':>8}", "cls": "info"})
            lines.append({"text": "-" * 50, "cls": "dim"})
            for name, mem_mb, cpu in procs[:15]:
                cls = "warn" if mem_mb > 500 else "ok" if mem_mb > 50 else ""
                lines.append({"text": f"{name:<30} {mem_mb:>10} {cpu:>7.1f}", "cls": cls})
            lines.append({"text": f"\nTotal: {len(procs)} processes running", "cls": "info"})

        elif "codebase" in cmd or "analyze" in cmd or "project" in cmd:
            lines.append({"text": "$ scanning current directory...", "cls": "cmd"})
            import os
            cwd = os.getcwd()
            lines.append({"text": f"Directory: {cwd}", "cls": "info"})

            # Count files by extension
            ext_counts: dict[str, int] = {}
            total_files = 0
            total_size = 0
            for root, dirs, files in os.walk(cwd):
                # Skip hidden dirs, node_modules, __pycache__, .git
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")]
                for f in files:
                    total_files += 1
                    ext = os.path.splitext(f)[1] or "(no ext)"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
                if total_files > 5000:
                    break

            lines.append({"text": f"Files: {total_files:,} | Size: {total_size // 1024:,} KB", "cls": "info"})

            # Top extensions
            top_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            lines.append({"text": "", "cls": "dim"})
            lines.append({"text": "File types:", "cls": "info"})
            for ext, count in top_exts:
                bar = "#" * min(count // 2, 30)
                lines.append({"text": f"  {ext:<10} {count:>5}  {bar}", "cls": "ok" if ext in (".py", ".ts", ".js", ".go", ".rs") else ""})

            # Git info
            if os.path.isdir(os.path.join(cwd, ".git")):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "git", "log", "--oneline", "-5",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        cwd=cwd,
                    )
                    out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                    if out.strip():
                        lines.append({"text": "", "cls": "dim"})
                        lines.append({"text": "Recent commits:", "cls": "info"})
                        for commit_line in out.decode().strip().split("\n"):
                            lines.append({"text": f"  {commit_line}", "cls": "dim"})
                except Exception:
                    pass

                try:
                    proc = await asyncio.create_subprocess_exec(
                        "git", "remote", "-v",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        cwd=cwd,
                    )
                    out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
                    if out.strip():
                        remote = out.decode().strip().split("\n")[0]
                        lines.append({"text": f"Remote: {remote}", "cls": "info"})
                except Exception:
                    pass

        elif "git" in cmd and "repo" in cmd:
            lines.append({"text": "$ scanning for git repos...", "cls": "cmd"})
            import os
            home = os.path.expanduser("~")
            repos = []
            scanned = 0
            for root, dirs, _files in os.walk(home):
                scanned += 1
                if scanned > 3000:
                    lines.append({"text": "(stopped after scanning 3000 directories)", "cls": "dim"})
                    break
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "AppData", "Library")]
                if ".git" in dirs:
                    repos.append(root)
                    dirs.remove(".git")
                    if len(repos) >= 20:
                        break

            if repos:
                lines.append({"text": f"Found {len(repos)} repo(s):", "cls": "ok"})
                for repo in repos:
                    short = repo.replace(home, "~")
                    lines.append({"text": f"  {short}", "cls": "info"})
            else:
                lines.append({"text": "No git repos found in home directory", "cls": "warn"})

        elif "time" in cmd or "date" in cmd:
            import datetime
            now = datetime.datetime.now()
            lines.append({"text": "$ date", "cls": "cmd"})
            lines.append({"text": f"Local: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}", "cls": "info"})
            utc = datetime.datetime.now(datetime.UTC)
            lines.append({"text": f"UTC:   {utc.strftime('%Y-%m-%d %H:%M:%S')}", "cls": "info"})
            if "tokyo" in cmd.lower():
                tokyo = utc + datetime.timedelta(hours=9)
                lines.append({"text": f"Tokyo: {tokyo.strftime('%Y-%m-%d %H:%M:%S')} JST", "cls": "ok"})
            if "london" in cmd.lower() or "uk" in cmd.lower():
                lines.append({"text": f"London: {utc.strftime('%Y-%m-%d %H:%M:%S')} GMT", "cls": "ok"})

        elif "network" in cmd or "ip" in cmd or "internet" in cmd:
            lines.append({"text": "$ network scan", "cls": "cmd"})
            import psutil
            import socket
            hostname = socket.gethostname()
            lines.append({"text": f"Hostname: {hostname}", "cls": "info"})
            for name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                        lines.append({"text": f"Interface {name}: {addr.address}", "cls": "ok"})

            # Check internet
            import httpx
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get("https://httpbin.org/ip")
                    if resp.status_code == 200:
                        ip = resp.json().get("origin", "?")
                        lines.append({"text": f"Public IP: {ip}", "cls": "ok"})
                        lines.append({"text": "Internet: connected", "cls": "ok"})
            except Exception:
                lines.append({"text": "Internet: not reachable", "cls": "warn"})

        elif "disk" in cmd or "storage" in cmd or "space" in cmd:
            lines.append({"text": "$ disk usage", "cls": "cmd"})
            import psutil
            for part in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    pct = usage.percent
                    bar = "#" * int(pct / 5) + "-" * (20 - int(pct / 5))
                    cls = "warn" if pct > 90 else "ok" if pct < 70 else "info"
                    lines.append({"text": f"{part.device:<12} [{bar}] {pct}%  ({usage.free // (1024**3)} GB free / {usage.total // (1024**3)} GB)", "cls": cls})
                except (PermissionError, OSError):
                    pass

        else:
            # Generic — run as shell command (safe subset)
            lines.append({"text": f"$ {cmd}", "cls": "cmd"})
            # Only allow safe read-only commands
            safe_prefixes = ("echo ", "date", "whoami", "hostname", "pwd", "dir ", "ls ", "type ", "cat ", "systeminfo", "ver", "python --version", "node --version", "git --version", "git status", "git log", "pip --version")
            is_safe = any(cmd.startswith(p) for p in safe_prefixes)
            if is_safe:
                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
                    output = (out or b"").decode(errors="replace").strip()
                    if output:
                        for line in output.split("\n")[:30]:
                            lines.append({"text": line, "cls": ""})
                    if err and err.strip():
                        lines.append({"text": err.decode(errors="replace").strip()[:200], "cls": "warn"})
                except asyncio.TimeoutError:
                    lines.append({"text": "Command timed out (10s)", "cls": "warn"})
                except Exception as e:
                    lines.append({"text": f"Error: {e}", "cls": "warn"})
            else:
                lines.append({"text": "Hmm, I can't run that directly in the wizard.", "cls": "warn"})
                lines.append({"text": "Try: 'system info', 'processes', 'codebase', 'network', 'disk'", "cls": "info"})
                lines.append({"text": "Or finish the wizard and use the OS Shell for anything.", "cls": "dim"})
    except Exception as e:
        lines.append({"text": f"Error: {e}", "cls": "warn"})

    return {"lines": lines}


@dashboard_app.post("/api/wizard/daemons-demo")
async def wizard_daemons_demo() -> dict:
    """Launch a quick research hand and return live results for the wizard."""
    import httpx
    import xml.etree.ElementTree as ET

    lines: list[dict] = []
    lines.append({"text": "$ sculpt hand start researcher --topic 'agentic AI'", "cls": "cmd"})
    lines.append({"text": "Launching ResearchDaemon...", "cls": "dim"})

    # Actually search arxiv live
    topic = "agentic AI systems"
    query = topic.replace(" ", "+")
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=(ti:{query}+OR+abs:{query})"
        f"+AND+(cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG+OR+cat:cs.MA)"
        f"&max_results=5&sortBy=submittedDate&sortOrder=descending"
    )

    papers = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.text)
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
            abstract = entry.findtext("atom:summary", "", ns).strip()[:200]
            arxiv_id = entry.findtext("atom:id", "", ns).split("/abs/")[-1]
            published = entry.findtext("atom:published", "", ns)[:10]
            papers.append({"title": title, "id": arxiv_id, "date": published, "abstract": abstract})
    except Exception as e:
        lines.append({"text": f"Arxiv connection failed: {e}", "cls": "warn"})
        return {"lines": lines, "papers": []}

    lines.append({"text": f"Connected to arxiv.org — searching '{topic}'...", "cls": "info"})
    lines.append({"text": f"Found {len(papers)} recent papers:", "cls": "ok"})
    lines.append({"text": "", "cls": "dim"})

    for i, p in enumerate(papers, 1):
        lines.append({"text": f"  [{i}] {p['title']}", "cls": "info"})
        lines.append({"text": f"      {p['date']} | arxiv:{p['id']}", "cls": "dim"})

    lines.append({"text": "", "cls": "dim"})
    lines.append({"text": "ResearchDaemon complete. Report stored in knowledge base.", "cls": "ok"})

    # Also register with daemon_manager if available
    if _daemon_manager is not None:
        try:
            await _daemon_manager.start_daemon("researcher", {"topic": topic, "max_papers": 5})
        except Exception:
            pass

    return {"lines": lines, "papers": papers}


@dashboard_app.post("/api/wizard/evolve-demo")
async def wizard_evolve_demo() -> dict:
    """Run a REAL mini-evolution cycle — same code path as production.

    Hits arxiv, analyzes a real paper, picks a matching seed pattern,
    runs real sandbox, writes real evolved code. Nothing is faked.
    Falls back to seed-only if arxiv is unreachable.
    """
    import textwrap
    import time as _time
    from pathlib import Path
    from agos.evolution.scout import ArxivScout
    from agos.evolution.heuristics import heuristic_analyze, _select_topics
    from agos.evolution.sandbox import Sandbox
    from agos.evolution.seed_patterns import TESTABLE_SNIPPETS

    stages: list[dict] = []
    t0 = _time.time()
    papers_scanned = 0

    # ── Stage 1: Check real demand signals (if collector is running) ──
    demand_detail = ""
    demand_topic = ""
    if _demand_collector and _demand_collector.has_demands():
        top = _demand_collector.top_demands(limit=1)
        if top:
            demand_detail = top[0].description
            topics = _demand_collector.demand_topics(limit=1)
            demand_topic = topics[0] if topics else ""
    if not demand_detail:
        demand_detail = "No active demand signals — using scheduled evolution topic"
    stages.append({
        "id": "demand", "label": "Demand Check",
        "icon": "zap", "status": "done",
        "detail": demand_detail,
        "sub": f"Demand topic: {demand_topic}" if demand_topic else "Using scheduled rotation",
    })

    # ── Stage 2: Real arxiv search ──
    scout = ArxivScout(timeout=15)
    topic = demand_topic or _select_topics(int(_time.time()) % 100)[0]
    papers = []
    arxiv_error = ""
    try:
        papers = await scout.search(topic, max_results=3)
        papers_scanned = len(papers)
    except Exception as e:
        arxiv_error = str(e)[:120]

    if papers:
        stages.append({
            "id": "search", "label": "Arxiv Search",
            "icon": "search", "status": "done",
            "detail": f'Searched: "{topic}" -- found {len(papers)} papers',
            "sub": "Source: export.arxiv.org | Categories: cs.AI, cs.CL, cs.LG",
        })
    else:
        stages.append({
            "id": "search", "label": "Arxiv Search",
            "icon": "search", "status": "done",
            "detail": f'Searched: "{topic}" -- {arxiv_error or "no results"}',
            "sub": "Will use built-in seed pattern instead (honest fallback)",
        })

    # ── Stage 3: Analyze paper (real heuristic_analyze on real paper) ──
    insight = None
    used_paper = None
    for paper in papers:
        insight = heuristic_analyze(paper)
        if insight:
            used_paper = paper
            break

    if insight and used_paper:
        stages.append({
            "id": "paper", "label": "Paper Analyzed",
            "icon": "paper", "status": "done",
            "detail": used_paper.title[:90],
            "sub": f"arxiv:{used_paper.arxiv_id} | Module: {insight.agos_module} | Priority: {insight.priority}",
        })
    elif papers:
        # Papers found but none passed heuristic filter
        stages.append({
            "id": "paper", "label": "Papers Filtered",
            "icon": "paper", "status": "done",
            "detail": f"Scanned {len(papers)} papers -- none passed relevance filter",
            "sub": "Filters: CS-only, methodology required, 2+ keyword matches",
        })
    else:
        stages.append({
            "id": "paper", "label": "No Papers",
            "icon": "paper", "status": "done",
            "detail": "Arxiv unavailable or no results -- using seed pattern",
            "sub": "This is the honest fallback, not a fake",
        })

    # ── Stage 4: Pick matching pattern (real seed or insight-matched) ──
    pattern_key = insight.agos_module if insight else "knowledge.semantic"
    pattern = TESTABLE_SNIPPETS.get(pattern_key)
    if not pattern:
        # Try parent module
        parent = pattern_key.split(".")[0] if "." in pattern_key else pattern_key
        pattern = TESTABLE_SNIPPETS.get(parent)
    if not pattern:
        pattern = TESTABLE_SNIPPETS.get("knowledge.semantic")
    if not pattern:
        stages.append({"id": "codegen", "label": "Code Generation", "icon": "code",
                        "status": "fail", "detail": "No patterns available", "sub": ""})
        return {"stages": stages, "evolved": False, "metrics": {}}

    code_lines = len(pattern.code_snippet.strip().split("\n"))
    source_label = f"From paper: {used_paper.title[:50]}..." if used_paper else "Built-in seed pattern"
    stages.append({
        "id": "codegen", "label": "Code Selected",
        "icon": "code", "status": "done",
        "detail": f"{pattern.name} ({code_lines} lines)",
        "sub": f"Module: {pattern.agos_module} | Source: {source_label}",
        "code_preview": pattern.code_snippet.strip().split("\n")[:12],
    })

    # ── Stage 5: Real sandbox validation ──
    sandbox = Sandbox(timeout=10)
    result = await sandbox.test_pattern(pattern.code_snippet)
    sandbox_ms = round(result.execution_time_ms)

    if result.passed:
        out_lines = result.output.strip().split("\n")
        pass_line = next((line for line in out_lines if "PASS" in line), out_lines[-1] if out_lines else "OK")
        stages.append({
            "id": "sandbox", "label": "Sandbox Passed",
            "icon": "test", "status": "done",
            "detail": pass_line,
            "sub": f"Execution: {sandbox_ms}ms | Static analysis + subprocess isolation",
        })
    else:
        stages.append({
            "id": "sandbox", "label": "Sandbox Failed",
            "icon": "test", "status": "fail",
            "detail": (result.error or result.output)[:150],
            "sub": f"Execution: {sandbox_ms}ms",
        })
        return {"stages": stages, "evolved": False, "metrics": {"sandbox_ms": sandbox_ms}}

    # ── Stage 6: Write real evolved file ──
    evolved_dir = Path(settings.workspace_dir) / "evolved"
    evolved_dir.mkdir(parents=True, exist_ok=True)
    safe_module = pattern.agos_module.replace(".", "_")
    filename = f"wizard_demo_{safe_module}.py"
    filepath = evolved_dir / filename

    paper_line = f"# Paper: {used_paper.title} (arxiv:{used_paper.arxiv_id})" if used_paper else "# Source: built-in seed pattern"
    header = textwrap.dedent(f"""\
        # Evolved by OpenSculpt wizard — real pipeline execution
        {paper_line}
        # Pattern: {pattern.name}
        # Module: {pattern.agos_module}
        # Sandbox: PASS ({sandbox_ms}ms)
        # ---
    """)
    filepath.write_text(header + pattern.code_snippet)

    evolved_count = len(list(evolved_dir.glob("*.py")))
    total_ms = round((_time.time() - t0) * 1000)

    stages.append({
        "id": "deploy", "label": "Deployed to OS",
        "icon": "deploy", "status": "done",
        "detail": f"Written: {evolved_dir.name}/{filename}",
        "sub": f"Total evolved strategies: {evolved_count} | Pipeline time: {total_ms}ms",
    })

    return {
        "stages": stages,
        "evolved": True,
        "filename": filename,
        "pattern_name": pattern.name,
        "paper_title": used_paper.title if used_paper else None,
        "paper_id": used_paper.arxiv_id if used_paper else None,
        "demand": demand_detail,
        "metrics": {
            "papers_scanned": papers_scanned,
            "sandbox_ms": sandbox_ms,
            "code_lines": code_lines,
            "total_ms": total_ms,
            "evolved_count": evolved_count,
        },
    }


@dashboard_app.post("/api/wizard/monitor-demo")
async def wizard_monitor_demo() -> dict:
    """Start the monitor hand watching the OpenSculpt dashboard itself."""
    lines: list[dict] = []
    lines.append({"text": "$ sculpt hand start monitor", "cls": "cmd"})
    lines.append({"text": "Launching MonitorDaemon — watching OpenSculpt health...", "cls": "dim"})

    import httpx
    import time as _time

    # Actually check the dashboard health
    targets = [
        {"url": f"http://127.0.0.1:{settings.dashboard_port}/api/status", "name": "OpenSculpt Dashboard"},
    ]

    results = []
    async with httpx.AsyncClient(timeout=5) as client:
        for t in targets:
            start = _time.monotonic()
            try:
                resp = await client.get(t["url"])
                ms = round((_time.monotonic() - start) * 1000)
                data = resp.json() if resp.status_code == 200 else {}
                results.append({"name": t["name"], "status": "up", "ms": ms, "data": data})
                lines.append({"text": f"  {t['name']}: UP ({ms}ms)", "cls": "ok"})
                if data.get("knowledge_available"):
                    lines.append({"text": "    Knowledge system: online", "cls": "dim"})
                uptime_s = data.get("uptime_s", 0)
                if uptime_s:
                    lines.append({"text": f"    Uptime: {uptime_s}s", "cls": "dim"})
                version = data.get("version", "")
                if version:
                    lines.append({"text": f"    Version: {version}", "cls": "dim"})
            except Exception as e:
                ms = round((_time.monotonic() - start) * 1000)
                results.append({"name": t["name"], "status": "down", "ms": ms})
                lines.append({"text": f"  {t['name']}: DOWN ({e})", "cls": "warn"})

    lines.append({"text": "", "cls": "dim"})
    lines.append({"text": "Monitor running. Will check every 60s and alert on changes.", "cls": "ok"})

    # Actually start the monitor hand
    if _daemon_manager is not None:
        try:
            await _daemon_manager.start_daemon("monitor", {"targets": targets, "interval": 60})
        except Exception:
            pass

    return {"lines": lines, "targets": results}


# ── Setup: Providers ─────────────────────────────────────────────


class ProviderConfigPayload(BaseModel):
    enabled: bool = True
    config: dict = {}


@dashboard_app.get("/api/setup/providers")
async def list_providers_setup() -> list[dict]:
    from agos.llm.providers import ALL_PROVIDERS, provider_config_fields
    from agos.setup_store import load_setup
    setup = load_setup(pathlib.Path(settings.workspace_dir))
    saved = setup.get("providers", {})
    result = []
    for name, cls in ALL_PROVIDERS.items():
        cfg = saved.get(name, {})
        key = cfg.get("api_key", "")
        key_preview = (key[:6] + "...") if len(key) > 6 else ""
        # Build saved_config without exposing full api_key
        safe_cfg = {k: v for k, v in cfg.items() if k not in ("enabled", "api_key")}
        if key:
            safe_cfg["api_key"] = key_preview  # show masked
        result.append({
            "name": name,
            "description": getattr(cls, "description", ""),
            "enabled": cfg.get("enabled", False),
            "key_preview": key_preview,
            "config_fields": provider_config_fields(name),
            "saved_config": safe_cfg,
        })
    return result


@dashboard_app.post("/api/setup/providers/{name}")
async def set_provider_setup(name: str, payload: ProviderConfigPayload) -> dict:
    from agos.llm.providers import ALL_PROVIDERS
    from agos.setup_store import set_provider_config
    if name not in ALL_PROVIDERS:
        return {"ok": False, "error": f"Unknown provider: {name}"}
    cfg: dict = {"enabled": payload.enabled, **payload.config}
    # Preserve existing api_key if the masked preview was sent back
    if cfg.get("api_key", "").endswith("..."):
        from agos.setup_store import get_provider_config
        existing = get_provider_config(pathlib.Path(settings.workspace_dir), name)
        if existing.get("api_key"):
            cfg["api_key"] = existing["api_key"]
    set_provider_config(pathlib.Path(settings.workspace_dir), name, cfg)
    # If this provider is being enabled, make it the active provider
    # and wire it into the OS agent immediately
    if payload.enabled and _os_agent is not None:
        from agos.setup_store import load_setup, save_setup
        ws = pathlib.Path(settings.workspace_dir)
        data = load_setup(ws)
        data["active_provider"] = name
        save_setup(ws, data)
        # Try to load and set the provider on the OS agent
        from agos.llm.providers import ALL_PROVIDERS
        _os_agent._load_provider(name, cfg, ALL_PROVIDERS)
    return {"ok": True}


@dashboard_app.post("/api/setup/evolution-model")
async def set_evolution_model(payload: dict) -> dict:
    """Set the model used by the Evolution Agent (stronger model for OS-level reasoning)."""
    from agos.setup_store import load_setup, save_setup
    model = payload.get("model", "")
    if not model:
        return {"ok": False, "error": "model required"}
    data = load_setup(pathlib.Path(settings.workspace_dir))
    data["evolution_agent_model"] = model
    save_setup(pathlib.Path(settings.workspace_dir), data)
    return {"ok": True, "model": model}


@dashboard_app.get("/api/setup/evolution-model")
async def get_evolution_model() -> dict:
    from agos.setup_store import load_setup
    data = load_setup(pathlib.Path(settings.workspace_dir))
    return {"model": data.get("evolution_agent_model", "")}


@dashboard_app.post("/api/setup/providers/{name}/test")
async def test_provider_setup(name: str) -> dict:
    from agos.llm.providers import ALL_PROVIDERS
    from agos.llm.base import LLMMessage
    from agos.setup_store import get_provider_config
    import inspect
    cfg = get_provider_config(pathlib.Path(settings.workspace_dir), name)
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Provider not enabled"}
    cls = ALL_PROVIDERS.get(name)
    if cls is None:
        return {"ok": False, "error": "Unknown provider"}
    try:
        sig = inspect.signature(cls.__init__)
        params = set(sig.parameters.keys()) - {"self"}
        kwargs = {}
        for k, v in cfg.items():
            if k in params and v:
                kwargs[k] = v
        # Map api_key -> token for github_models
        if "token" in params and "api_key" in cfg:
            kwargs["token"] = cfg["api_key"]
        provider = cls(**kwargs)
        resp = await provider.complete(
            [LLMMessage(role="user", content="Say hello in 5 words.")], max_tokens=50,
        )
        return {"ok": True, "response": resp.content or "", "tokens": resp.output_tokens}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Setup: Channels ──────────────────────────────────────────────


class ChannelConfigPayload(BaseModel):
    enabled: bool = True
    config: dict = {}


@dashboard_app.get("/api/setup/channels")
async def list_channels_setup() -> list[dict]:
    from agos.channels.adapters import ALL_CHANNELS
    from agos.setup_store import load_setup
    setup = load_setup(pathlib.Path(settings.workspace_dir))
    saved = setup.get("channels", {})
    result = []
    for cls in ALL_CHANNELS:
        inst = cls()
        ch_cfg = saved.get(inst.name, {})
        result.append({
            "name": inst.name,
            "description": inst.description,
            "icon": inst.icon,
            "enabled": ch_cfg.get("enabled", False),
            "config_schema": inst.config_schema(),
            "saved_config": ch_cfg.get("config", {}),
        })
    return result


@dashboard_app.post("/api/setup/channels/{name}")
async def set_channel_setup(name: str, payload: ChannelConfigPayload) -> dict:
    from agos.channels.adapters import ALL_CHANNELS
    from agos.setup_store import set_channel_config
    valid_names = {cls().name for cls in ALL_CHANNELS}
    if name not in valid_names:
        return {"ok": False, "error": f"Unknown channel: {name}"}
    set_channel_config(pathlib.Path(settings.workspace_dir), name, {
        "enabled": payload.enabled, "config": payload.config,
    })
    return {"ok": True}


@dashboard_app.post("/api/setup/channels/{name}/test")
async def test_channel_setup(name: str) -> dict:
    from agos.channels.adapters import ALL_CHANNELS
    from agos.channels.base import ChannelMessage
    from agos.setup_store import get_channel_config
    ch_cfg = get_channel_config(pathlib.Path(settings.workspace_dir), name)
    if not ch_cfg.get("enabled"):
        return {"ok": False, "error": "Channel not enabled"}
    inst = None
    for cls in ALL_CHANNELS:
        i = cls()
        if i.name == name:
            inst = i
            break
    if inst is None:
        return {"ok": False, "error": "Unknown channel"}
    try:
        msg = ChannelMessage(text="Test message from OpenSculpt", title="OpenSculpt Test", level="info")
        result = await inst.send(msg, ch_cfg.get("config", {}))
        return {"ok": result.success, "detail": result.detail}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Setup: Tools ─────────────────────────────────────────────────


class ToolConfigPayload(BaseModel):
    enabled: bool = True


@dashboard_app.get("/api/setup/tools")
async def list_tools_setup() -> list[dict]:
    from agos.setup_store import load_setup
    setup = load_setup(pathlib.Path(settings.workspace_dir))
    saved_tools = setup.get("tools", {})
    tools = []
    # Try OS agent's tool registry first (always has tools), fall back to runtime
    registry = None
    if _os_agent and hasattr(_os_agent, '_inner_registry'):
        registry = _os_agent._inner_registry
    elif _runtime and hasattr(_runtime, '_tool_registry'):
        registry = _runtime._tool_registry
    if registry:
        for schema in registry.list_tools():
            t_cfg = saved_tools.get(schema.name, {})
            tools.append({
                "name": schema.name,
                "description": schema.description,
                "enabled": t_cfg.get("enabled", True),
            })
    return tools


@dashboard_app.post("/api/setup/tools/{name}")
async def set_tool_setup(name: str, payload: ToolConfigPayload) -> dict:
    from agos.setup_store import set_tool_config
    set_tool_config(pathlib.Path(settings.workspace_dir), name, {"enabled": payload.enabled})
    if _policy_engine and not payload.enabled:
        current = _policy_engine.get_policy("*")
        if name not in current.denied_tools:
            current.denied_tools.append(name)
            _policy_engine.set_default(current)
    elif _policy_engine and payload.enabled:
        current = _policy_engine.get_policy("*")
        current.denied_tools = [t for t in current.denied_tools if t != name]
        _policy_engine.set_default(current)
    return {"ok": True}


# ── MCP (Model Context Protocol) ────────────────────────────────


@dashboard_app.get("/api/mcp/servers")
async def list_mcp_servers() -> list[dict]:
    """List configured MCP servers and their connection status."""
    if _mcp_manager is None:
        return []
    return _mcp_manager.list_servers()


# ── Approval gate (human-in-the-loop) ──────────────────────────


class ApprovalPayload(BaseModel):
    request_id: str
    approved: bool
    reason: str = ""


class ApprovalModePayload(BaseModel):
    mode: str


@dashboard_app.get("/api/approval/pending")
async def approval_pending() -> list[dict]:
    if _approval_gate is None:
        return []
    return _approval_gate.pending_requests()


@dashboard_app.post("/api/approval/respond")
async def approval_respond(payload: ApprovalPayload) -> dict:
    if _approval_gate is None:
        return {"ok": False, "error": "Approval gate not configured"}
    ok = await _approval_gate.respond(payload.request_id, payload.approved, payload.reason)
    return {"ok": ok}


@dashboard_app.get("/api/approval/mode")
async def get_approval_mode() -> dict:
    if _approval_gate is None:
        return {"mode": "auto"}
    return {"mode": _approval_gate.mode.value}


@dashboard_app.post("/api/approval/mode")
async def set_approval_mode(payload: ApprovalModePayload) -> dict:
    if _approval_gate is None:
        return {"ok": False, "error": "Approval gate not configured"}
    from agos.approval.gate import ApprovalMode
    try:
        _approval_gate.set_mode(ApprovalMode(payload.mode))
        return {"ok": True, "mode": payload.mode}
    except ValueError:
        return {"ok": False, "error": f"Invalid mode: {payload.mode}"}


# ── Evolution state + community sharing ──────────────────────────

@dashboard_app.get("/api/evolution/state")
async def evolution_state_endpoint() -> dict:
    if _evolution_state is None:
        return {"available": False}
    d = _evolution_state.data
    return {
        "available": True,
        "instance_id": d.instance_id,
        "cycles_completed": d.cycles_completed,
        "last_saved": d.last_saved,
        "strategies_applied": [s.model_dump() for s in d.strategies_applied],
        "discovered_patterns": [p.model_dump() for p in d.discovered_patterns],
        "parameters": d.parameters,
    }


@dashboard_app.get("/api/evolution/meta")
async def meta_evolution_endpoint() -> dict:
    """Per-component evolution status from MetaEvolver."""
    if _meta_evolver is None:
        return {"available": False}
    genomes = []
    for g in _meta_evolver.all_genomes():
        params = []
        for p in g.params:
            params.append({
                "name": p.name,
                "current": p.current,
                "default": p.default,
                "min": p.min_val,
                "max": p.max_val,
                "type": p.param_type,
                "description": p.description,
            })
        genomes.append({
            "component": g.component,
            "layer": g.layer,
            "fitness": round(g.fitness_score, 3),
            "mutations_applied": g.mutations_applied,
            "last_evaluated": g.last_evaluated,
            "params": params,
        })
    recent_mutations = [
        {
            "component": m.component,
            "param": m.param_name,
            "old": m.old_value,
            "new": m.new_value,
            "reason": m.reason,
            "applied": m.applied,
            "timestamp": m.timestamp,
        }
        for m in _meta_evolver.mutations[-20:]
    ]
    recent_signals = [
        {
            "component": s.component,
            "metric": s.metric,
            "value": round(s.value, 3),
            "timestamp": s.timestamp,
        }
        for s in _meta_evolver.fitness.recent_signals(30)
    ]
    return {
        "available": True,
        "genomes": genomes,
        "recent_mutations": recent_mutations,
        "recent_signals": recent_signals,
    }


@dashboard_app.get("/api/evolution/code")
async def evolved_code_endpoint() -> dict:
    """List all evolved code modules written by the evolution engine."""
    from agos.evolution.codegen import EVOLVED_DIR
    files = []
    if EVOLVED_DIR.exists():
        for f in sorted(EVOLVED_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            try:
                content = f.read_text(encoding="utf-8")
                lines = content.splitlines()
                summary = ""
                for line in lines[1:10]:
                    stripped = line.strip().strip('"').strip("'")
                    if stripped and not stripped.startswith("from") and not stripped.startswith("import"):
                        summary = stripped
                        break
                files.append({
                    "name": f.stem,
                    "file": str(f),
                    "size_bytes": f.stat().st_size,
                    "lines": len(lines),
                    "summary": summary[:120],
                    "modified": f.stat().st_mtime,
                })
            except Exception:
                pass
    return {
        "count": len(files),
        "evolved_dir": str(EVOLVED_DIR),
        "files": files,
    }


class SharePayload(BaseModel):
    github_token: str = ""


@dashboard_app.post("/api/evolution/share")
async def share_evolution(payload: SharePayload) -> dict:
    if _evolution_state is None:
        return {"ok": False, "error": "Evolution state not available"}
    token = payload.github_token.strip() or settings.github_token
    if not token:
        return {"ok": False, "error": "GitHub token required — set in Settings or SCULPT_GITHUB_TOKEN env var"}
    try:
        from agos.evolution.contribute import share_knowledge
        result = await share_knowledge(token)
        return {"ok": True, "pr_url": result["pr_url"], "branch": result["branch"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Hot-reload: live-patch modules without restart ───────────────

@dashboard_app.post("/api/reload")
async def reload_module(body: dict) -> dict:
    """Hot-reload a Python module in the running process.

    Like Unix SIGHUP — reload config/code without restarting.
    Works with volume-mounted source: edit on host → POST /api/reload → live.

    Body: {"module": "agos.daemons.goal_runner"} or {"all": true}
    """
    import importlib
    import sys

    RELOADABLE = [
        "agos.daemons.goal_runner",
        "agos.daemons.gc",
        "agos.daemons.domain",
        "agos.daemons.base",
        "agos.os_agent",
        "agos.tools.docker_tool",
        "agos.llm.anthropic",
        "agos.llm.providers",
        "agos.evolution.demand",
        "agos.evolution.demand_solver",
        "agos.evolution.source_patcher",
        "agos.evolution.evolution_agent",
        "agos.evolution.cycle",
        "agos.evolution.tool_evolver",
        "agos.guard",
        "agos.session",
        "agos.processes.resources",
        "agos.dashboard.app",
    ]

    reload_all = body.get("all", False)
    target = body.get("module", "")
    reloaded = []
    errors = []

    modules_to_reload = RELOADABLE if reload_all else ([target] if target in RELOADABLE else [])

    if not modules_to_reload:
        return {"ok": False, "error": f"Module '{target}' not in reloadable list", "reloadable": RELOADABLE}

    for mod_name in modules_to_reload:
        if mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[mod_name])
                reloaded.append(mod_name)
            except Exception as e:
                errors.append(f"{mod_name}: {e}")

    return {"ok": len(errors) == 0, "reloaded": reloaded, "errors": errors}


# ── NEW: Real system vitals ──────────────────────────────────────

@dashboard_app.get("/api/services")
async def list_services() -> dict:
    """List all deployed services with status, credentials, URLs."""
    if _service_keeper:
        return {"services": _service_keeper.get_services()}
    # Fallback: scan cards directly
    from agos.services import scan_service_cards
    cards = scan_service_cards()
    return {"services": [{"name": c.name, "status": c.status, "port": c.port,
                           "url": f"http://localhost:{c.port}" if c.port else "",
                           "health_check": c.health_check} for c in cards]}


@dashboard_app.post("/api/services/{name}/restart")
async def restart_service(name: str) -> dict:
    """Restart a service — resets circuit breaker, spawns debug agent."""
    if not _service_keeper:
        return {"ok": False, "error": "ServiceKeeper not initialized"}
    return await _service_keeper.restart_service(name)


@dashboard_app.post("/api/services/{name}/stop")
async def stop_service(name: str) -> dict:
    """Stop a running service."""
    if not _service_keeper:
        return {"ok": False, "error": "ServiceKeeper not initialized"}
    return await _service_keeper.stop_service(name)


@dashboard_app.post("/api/services/{name}/decommission")
async def decommission_service(name: str) -> dict:
    """Decommission a service — stop, archive card, remove from monitoring."""
    if not _service_keeper:
        return {"ok": False, "error": "ServiceKeeper not initialized"}
    return await _service_keeper.decommission_service(name)


@dashboard_app.get("/api/vitals")
async def system_vitals() -> dict:
    """Real CPU, memory, disk stats — works on both Linux and Windows."""
    vitals = {"cpu_percent": 0.0, "mem_total_mb": 0, "mem_used_mb": 0,
              "mem_percent": 0.0, "disk_total_gb": 0.0, "disk_used_gb": 0.0,
              "disk_percent": 0.0, "load_avg": [0, 0, 0], "processes": 0,
              "uptime_s": int(time.time() - _start_time)}
    try:
        import psutil
        vitals["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        vitals["mem_total_mb"] = mem.total // (1024 * 1024)
        vitals["mem_used_mb"] = mem.used // (1024 * 1024)
        vitals["mem_percent"] = mem.percent
        disk = psutil.disk_usage("/")
        vitals["disk_total_gb"] = round(disk.total / 1e9, 1)
        vitals["disk_used_gb"] = round(disk.used / 1e9, 1)
        vitals["disk_percent"] = disk.percent
        try:
            vitals["load_avg"] = [round(x, 2) for x in os.getloadavg()]
        except AttributeError:
            # Windows doesn't have getloadavg — approximate from cpu_percent
            cpu = vitals["cpu_percent"]
            vitals["load_avg"] = [round(cpu / 100 * psutil.cpu_count(), 2)] * 3
        vitals["processes"] = len(psutil.pids())
    except Exception:
        pass
    return vitals


# ── NEW: Real codebase analysis ──────────────────────────────────

@dashboard_app.get("/api/codebase")
async def codebase_analysis() -> dict:
    """Scan the actual source tree and return real metrics."""
    src = pathlib.Path("/app/agos")
    if not src.exists():
        src = pathlib.Path("agos")
    result = {"total_files": 0, "total_lines": 0, "total_bytes": 0,
              "python_files": 0, "modules": [], "file_types": {},
              "largest_files": [], "todos": [], "imports": set(),
              "classes": 0, "functions": 0, "health_score": 0}
    todos = []
    largest = []
    modules = set()
    classes = 0
    functions = 0
    imports = set()

    for f in src.rglob("*"):
        if f.is_dir() or "__pycache__" in str(f):
            continue
        result["total_files"] += 1
        size = f.stat().st_size
        result["total_bytes"] += size
        ext = f.suffix or "(none)"
        result["file_types"][ext] = result["file_types"].get(ext, 0) + 1

        if ext == ".py":
            result["python_files"] += 1
            # Track module
            parts = f.relative_to(src).parts
            if len(parts) > 1:
                modules.add(parts[0])
            try:
                lines = f.read_text(errors="ignore").splitlines()
                result["total_lines"] += len(lines)
                largest.append({"file": str(f.relative_to(src.parent)), "lines": len(lines)})
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if "TODO" in stripped or "FIXME" in stripped or "HACK" in stripped:
                        todos.append({
                            "file": str(f.relative_to(src.parent)),
                            "line": i,
                            "text": stripped[:120],
                        })
                    if stripped.startswith("class ") and "(" in stripped:
                        classes += 1
                    if stripped.startswith("def ") or stripped.startswith("async def "):
                        functions += 1
                    if stripped.startswith("import ") or stripped.startswith("from "):
                        mod = stripped.split()[1].split(".")[0]
                        if mod not in ("__future__",):
                            imports.add(mod)
            except Exception:
                pass

    largest.sort(key=lambda x: x["lines"], reverse=True)
    result["largest_files"] = largest[:10]
    result["todos"] = todos[:30]
    result["modules"] = sorted(modules)
    result["classes"] = classes
    result["functions"] = functions
    result["imports"] = sorted(imports)

    # Health score: 100 minus penalties
    score = 100
    if len(todos) > 10:
        score -= 10
    if len(todos) > 20:
        score -= 10
    if result["total_lines"] > 0 and result["python_files"] > 0:
        avg = result["total_lines"] / result["python_files"]
        if avg > 200:
            score -= 5
    result["health_score"] = max(0, min(100, score))

    return result


# ── NEW: Dependency health ───────────────────────────────────────

@dashboard_app.get("/api/deps")
async def dependency_health() -> list[dict]:
    """List real installed packages."""
    deps = []
    try:
        out = subprocess.check_output(
            ["pip", "list", "--format=json"], text=True, timeout=10,
            stderr=subprocess.DEVNULL,
        )
        import json
        for pkg in json.loads(out):
            deps.append({"name": pkg["name"], "version": pkg["version"]})
    except Exception:
        pass
    return deps


# ── User Agent Management — install, start, stop, monitor ────────


class SetupPayload(BaseModel):
    name: str
    github_url: str = ""


@dashboard_app.post("/api/agents/setup")
async def setup_agent(payload: SetupPayload) -> dict:
    """One command to rule them all: 'set up openclaw on my system'.

    The OS discovers the agent, installs deps, and starts it.
    Just give a name (for bundled agents) or a GitHub URL.
    """
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.setup(payload.name, github_url=payload.github_url)
        ok = agent.status.value in ("running", "installed")
        return {
            "ok": ok,
            "agent_id": agent.id,
            "name": agent.name,
            "status": agent.status.value,
            "error": agent.install_error if not ok else "",
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@dashboard_app.get("/api/agents/registry")
async def list_user_agents() -> dict:
    """List all user agents (available, installed, running)."""
    if _agent_registry is None:
        return {"agents": [], "count": 0}
    agents = _agent_registry.list_agents()
    return {"agents": agents, "count": len(agents)}


@dashboard_app.post("/api/agents/install/{agent_id}")
async def install_agent(agent_id: str) -> dict:
    """Install a discovered agent (install its dependencies)."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.install(agent_id)
        return {"ok": True, "agent_id": agent.id, "name": agent.name, "status": agent.status.value}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


class InstallFromGitHubPayload(BaseModel):
    url: str
    name: str = ""


@dashboard_app.post("/api/agents/install-from-github")
async def install_agent_from_github(payload: InstallFromGitHubPayload) -> dict:
    """Install an agent from a GitHub repo URL."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.install_from_github(
            payload.url, name=payload.name or None
        )
        return {
            "ok": agent.status.value != "error",
            "agent_id": agent.id,
            "name": agent.name,
            "status": agent.status.value,
            "error": agent.install_error,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@dashboard_app.post("/api/agents/{agent_id}/start")
async def start_agent(agent_id: str) -> dict:
    """Start a user-installed agent."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.start(agent_id)
        return {
            "ok": True,
            "agent_id": agent.id,
            "name": agent.name,
            "status": agent.status.value,
            "process_pid": agent.process_pid,
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@dashboard_app.post("/api/agents/{agent_id}/stop")
async def stop_agent(agent_id: str) -> dict:
    """Stop a running agent."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.stop(agent_id)
        return {"ok": True, "agent_id": agent.id, "name": agent.name, "status": agent.status.value}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@dashboard_app.delete("/api/agents/{agent_id}")
async def uninstall_agent(agent_id: str) -> dict:
    """Uninstall an agent."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        await _agent_registry.uninstall(agent_id)
        return {"ok": True, "agent_id": agent_id}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@dashboard_app.get("/api/agents/{agent_id}/output")
async def agent_output(agent_id: str, lines: int = 50) -> dict:
    """Get stdout/stderr from a running agent."""
    if _agent_registry is None or _process_manager is None:
        return {"stdout": [], "stderr": []}
    agent = _agent_registry.get_agent(agent_id)
    if not agent or not agent.process_pid:
        return {"stdout": [], "stderr": []}
    return _process_manager.get_output(agent.process_pid, lines=lines)


class QuotaPayload(BaseModel):
    memory_limit_mb: float | None = None
    token_limit: int | None = None
    max_restarts: int | None = None


@dashboard_app.post("/api/agents/{agent_id}/quota")
async def set_agent_quota(agent_id: str, payload: QuotaPayload) -> dict:
    """Update resource quotas for an agent."""
    if _agent_registry is None:
        return {"ok": False, "error": "Agent registry not available"}
    try:
        agent = await _agent_registry.set_quota(
            agent_id,
            memory_limit_mb=payload.memory_limit_mb,
            token_limit=payload.token_limit,
            max_restarts=payload.max_restarts,
        )
        return {
            "ok": True,
            "agent_id": agent.id,
            "memory_limit_mb": agent.memory_limit_mb,
            "token_limit": agent.token_limit,
            "max_restarts": agent.max_restarts,
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ── Low-level process table (system internals) ──────────────────

@dashboard_app.get("/api/processes")
async def list_processes() -> dict:
    """Low-level OS process table (system + user processes)."""
    if _process_manager is None:
        return {"processes": [], "count": 0}
    procs = _process_manager.list_processes()
    return {"processes": procs, "count": len(procs)}


# ── Daemons — autonomous capability packages ──────────────────────


class HandConfigPayload(BaseModel):
    config: dict = {}


@dashboard_app.get("/api/daemons")
async def list_daemons() -> dict:
    """List all registered daemons with status."""
    if _daemon_manager is None:
        return {"daemons": [], "count": 0}
    daemons = _daemon_manager.list_daemons()
    return {"daemons": daemons, "count": len(daemons)}


@dashboard_app.post("/api/daemons/{name}/start")
async def start_daemon(name: str, payload: HandConfigPayload = None) -> dict:
    """Start a hand with optional configuration."""
    if _daemon_manager is None:
        return {"success": False, "error": "Daemon manager not initialized"}
    config = payload.config if payload else {}
    return await _daemon_manager.start_daemon(name, config)


@dashboard_app.post("/api/daemons/{name}/stop")
async def stop_daemon(name: str) -> dict:
    """Stop a running hand."""
    if _daemon_manager is None:
        return {"success": False, "error": "Daemon manager not initialized"}
    return await _daemon_manager.stop_daemon(name)


@dashboard_app.get("/api/daemons/{name}/results")
async def daemon_results(name: str) -> dict:
    """Get recent results from a hand."""
    if _daemon_manager is None:
        return {"results": []}
    results = _daemon_manager.get_results(name, limit=10)
    return {"results": results, "count": len(results)}


# ── Garbage Collector API ────────────────────────────────────────

@dashboard_app.get("/api/gc/status")
async def gc_status() -> dict:
    """GC daemon status + last report."""
    if _daemon_manager is None:
        return {"status": "unavailable"}
    gc = _daemon_manager.get_gc()
    if not gc:
        return {"status": "unavailable"}
    return {
        "status": gc.status.value,
        "dry_run": gc.dry_run,
        "ticks": gc._ticks,
        "errors": gc._errors,
        "last_report": gc.get_last_report(),
    }


@dashboard_app.get("/api/gc/reports")
async def gc_reports() -> dict:
    """Recent GC sweep reports."""
    if _daemon_manager is None:
        return {"reports": []}
    gc = _daemon_manager.get_gc()
    if not gc:
        return {"reports": []}
    return {"reports": gc.get_reports(limit=20)}


@dashboard_app.post("/api/gc/trigger")
async def gc_trigger() -> dict:
    """Manually trigger a GC sweep (uses current dry_run setting)."""
    if _daemon_manager is None:
        return {"success": False, "error": "Daemon manager not initialized"}
    gc = _daemon_manager.get_gc()
    if not gc:
        return {"success": False, "error": "GC daemon not registered"}
    if gc.status.value == "running":
        return {"success": False, "error": "GC already running"}
    result = await _daemon_manager.start_daemon("gc")
    return result


@dashboard_app.post("/api/gc/config")
async def gc_config(payload: dict) -> dict:
    """Update GC configuration (dry_run, aws_regions, etc.)."""
    if _daemon_manager is None:
        return {"success": False, "error": "Daemon manager not initialized"}
    gc = _daemon_manager.get_gc()
    if not gc:
        return {"success": False, "error": "GC daemon not registered"}
    if "dry_run" in payload:
        gc.dry_run = bool(payload["dry_run"])
    if "aws_regions" in payload:
        gc._aws_regions = payload["aws_regions"]
    if "interval" in payload:
        gc.default_interval = int(payload["interval"])
    return {
        "success": True,
        "config": {
            "dry_run": gc.dry_run,
            "aws_regions": gc._aws_regions,
            "interval": gc.default_interval,
        },
    }


# ── WebSocket — live event stream ────────────────────────────────

@dashboard_app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()

    async def send_event(event: Event) -> None:
        try:
            await websocket.send_json(event.model_dump(mode="json"))
        except Exception:
            pass

    if _event_bus:
        _event_bus.add_ws_connection(send_event)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if _event_bus:
            _event_bus.remove_ws_connection(send_event)


# ── Full Dashboard HTML — Tabbed Layout ──────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenSculpt</title>
<link rel="icon" href="/favicon.ico" type="image/jpeg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
    /* Surfaces — deep navy observatory */
    --bg: #07080c; --bg2: #0e1018; --bg3: #161a25;
    --surface: rgba(14,16,24,0.88); --surface-hover: rgba(22,26,37,0.95);
    /* Borders */
    --border: rgba(255,255,255,0.06); --border-focus: rgba(255,255,255,0.14);
    /* Text */
    --text: #e2e6ef; --text2: #6a7486; --text-dim: #3d4555;
    /* Accents — warm amber + soft violet */
    --blue: #60a5fa; --blue2: #93c5fd; --green: #4ade80; --green2: #86efac;
    --yellow: #fbbf24; --red: #f87171; --purple: #9b7aed; --cyan: #67e8f9;
    --accent: #e8a44a; --accent2: #f0c674;
    /* Semantic */
    --glow-blue: rgba(96,165,250,0.25); --glow-green: rgba(74,222,128,0.25);
    --card-bg: var(--surface); --card-border: var(--border);
    --dock-bg: rgba(7,8,12,0.95);
    /* Evolution nudge */
    --evo-bg: rgba(232,164,74,0.06); --evo-border: rgba(232,164,74,0.3);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); overflow: hidden; height: 100vh; }
h1, h2, h3, .topbar-brand, .welcome h2, .detail-header h2, .modal-header h2 { font-family: 'Space Grotesk', 'DM Sans', sans-serif; }

/* ── Background atmosphere ── */
body::before { content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: radial-gradient(ellipse at 20% 50%, rgba(155,122,237,0.04) 0%, transparent 60%), radial-gradient(ellipse at 80% 20%, rgba(232,164,74,0.03) 0%, transparent 50%); pointer-events: none; z-index: 0; animation: breathe 8s ease infinite; }

/* ── Animations ── */
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
@keyframes breathe { 0%,100% { opacity: 0.03; } 50% { opacity: 0.07; } }
@keyframes ringPulse { 0%,100% { filter: drop-shadow(0 0 2px var(--accent)); } 50% { filter: drop-shadow(0 0 8px var(--accent)); } }
@keyframes dotPulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.3); } }
@keyframes cardEnter { from { opacity: 0; transform: translateY(16px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
.skeleton { background: linear-gradient(90deg, var(--bg3) 25%, rgba(255,255,255,0.04) 50%, var(--bg3) 75%); background-size: 200% 100%; animation: shimmer 1.5s ease infinite; border-radius: 6px; height: 120px; }

/* ── Top Bar (like macOS menu bar) ── */
.topbar { height: 40px; background: rgba(14,16,24,0.95); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 16px; backdrop-filter: blur(20px); -webkit-app-region: drag; z-index: 100; position: relative; }
.topbar-left { display: flex; align-items: center; gap: 12px; }
.topbar-brand { font-size: 18px; font-weight: 800; background: linear-gradient(135deg, var(--accent2), var(--accent)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: 1px; text-transform: uppercase; }
.topbar-right { display: flex; align-items: center; gap: 14px; font-size: 11px; color: var(--text2); font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace; }
.topbar-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.topbar-btn { background: none; border: none; color: var(--text2); cursor: pointer; font-size: 12px; padding: 2px 6px; border-radius: 4px; transition: all 0.15s; }
.topbar-btn:hover { background: rgba(255,255,255,0.06); color: var(--text); }

/* ── Desktop (the main area) ── */
.desktop { position: absolute; top: 32px; left: 0; right: 0; bottom: 100px; overflow-y: auto; overflow-x: hidden; padding: 24px; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; align-content: start; z-index: 1; }
.desktop.has-nudge { top: 68px; }
.desktop::-webkit-scrollbar { width: 4px; }
.desktop::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }

/* ── Goal Cards (floating desktop windows) ── */
.goal-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; backdrop-filter: blur(12px); transition: transform 0.3s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.3s ease, border-color 0.3s ease; overflow: hidden; }
.goal-card:nth-child(1) { animation: cardEnter 0.4s ease 0.0s both; }
.goal-card:nth-child(2) { animation: cardEnter 0.4s ease 0.08s both; }
.goal-card:nth-child(3) { animation: cardEnter 0.4s ease 0.16s both; }
.goal-card:nth-child(4) { animation: cardEnter 0.4s ease 0.24s both; }
.goal-card:nth-child(5) { animation: cardEnter 0.4s ease 0.32s both; }
.goal-card:nth-child(n+6) { animation: cardEnter 0.4s ease 0.4s both; }
.goal-card:hover { border-color: rgba(155,122,237,0.4); transform: translateY(-3px); box-shadow: 0 12px 40px rgba(0,0,0,0.35); }
.goal-card.active-goal { grid-column: span 2; border-color: rgba(155,122,237,0.4); box-shadow: 0 0 20px rgba(155,122,237,0.1); border-left: 3px solid var(--purple); }
.goal-card.complete { border-color: rgba(74,222,128,0.2); opacity: 0.7; }
.goal-card.complete:hover { opacity: 1; }
.goal-card.failed { border-color: rgba(248,113,113,0.4); box-shadow: 0 0 12px rgba(248,113,113,0.08); }
@media (max-width: 800px) { .goal-card.active-goal { grid-column: span 1; } }
.goal-card-header { padding: 14px 16px 10px; display: flex; align-items: center; gap: 12px; cursor: pointer; }
.goal-card-ring { width: 52px; height: 52px; flex-shrink: 0; }
.goal-card.complete .goal-card-ring { width: 36px; height: 36px; }
.goal-card-ring svg { transform: rotate(-90deg); overflow: visible; }
.goal-card-ring .ring-bg { fill: none; stroke: rgba(35,45,63,0.8); stroke-width: 5; }
.goal-card-ring .ring-fill { fill: none; stroke-width: 5; stroke-linecap: round; transition: stroke-dashoffset 0.8s ease; filter: drop-shadow(0 0 3px currentColor); }
.goal-card-ring .ring-text { font-size: 11px; font-weight: 800; fill: var(--text); text-anchor: middle; dominant-baseline: central; transform: rotate(90deg); transform-origin: 24px 24px; }
.goal-card.complete .goal-card-ring .ring-text { font-size: 9px; }
.goal-card-title { flex: 1; min-width: 0; }
.goal-card-title h3 { font-size: 14px; font-weight: 700; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 2px; line-height: 1.3; }
.goal-card.complete .goal-card-title h3 { -webkit-line-clamp: 1; font-size: 13px; }
.goal-card-title .goal-status { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.goal-card-phases { padding: 0 16px 12px; }
.goal-phase { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
.goal-phase-icon { width: 16px; text-align: center; flex-shrink: 0; }
.goal-phase-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.goal-phase-status { font-size: 10px; color: var(--text2); }
.goal-card-footer { padding: 8px 16px; border-top: 1px solid rgba(35,45,63,0.4); display: flex; gap: 12px; font-size: 11px; color: var(--text2); }

/* ── Special Cards (What I Learned, Notifications) ── */
.special-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; backdrop-filter: blur(12px); animation: fadeIn 0.4s ease; overflow: hidden; }
.special-card .sc-header { padding: 12px 16px; border-bottom: 1px solid rgba(35,45,63,0.3); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--text2); display: flex; align-items: center; gap: 8px; }
.special-card .sc-body { padding: 12px 16px; max-height: 300px; overflow-y: auto; }
.special-card .sc-body::-webkit-scrollbar { width: 3px; }
.special-card .sc-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.learned-item { padding: 8px 0; border-bottom: 1px solid rgba(35,45,63,0.3); font-size: 12px; line-height: 1.5; }
.learned-item:last-child { border-bottom: none; }
.learned-conf { font-size: 10px; font-weight: 700; margin-left: 6px; }

/* ── Vitals Mini Card ── */
.vitals-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; backdrop-filter: blur(12px); padding: 14px 16px; }
.vitals-row { display: flex; gap: 16px; align-items: center; }
.vital-item { flex: 1; text-align: center; }
.vital-item .vital-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); margin-bottom: 4px; }
.vital-item .vital-bar { height: 4px; background: var(--bg3); border-radius: 2px; overflow: hidden; }
.vital-item .vital-bar-fill { height: 100%; border-radius: 2px; transition: width 1s ease; }
.vital-item .vital-val { font-size: 11px; font-weight: 700; margin-top: 3px; }

/* ── Command Bar (Spotlight-style, bottom) ── */
.command-bar { position: fixed; bottom: 48px; left: 50%; transform: translateX(-50%); width: min(700px, 90vw); z-index: 50; }
.command-bar-inner { background: rgba(17,19,26,0.95); border: 1px solid var(--border); border-radius: 16px; backdrop-filter: blur(20px); box-shadow: 0 8px 40px rgba(0,0,0,0.5); display: flex; align-items: center; padding: 6px 8px; gap: 8px; transition: all 0.3s; }
.command-bar-inner:focus-within { border-color: var(--purple); box-shadow: 0 8px 40px rgba(0,0,0,0.5), 0 0 0 1px var(--purple); }
.cmd-input { flex: 1; background: none; border: none; color: var(--text); font-size: 15px; padding: 10px 12px; outline: none; font-family: inherit; }
.cmd-input::placeholder { color: var(--text2); opacity: 0.5; }
.cmd-send { background: linear-gradient(135deg, var(--accent), var(--purple)); border: none; border-radius: 12px; width: 38px; height: 38px; color: #fff; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
.cmd-send:hover { opacity: 0.9; transform: scale(1.05); }
.cmd-mic { background: none; border: 1px solid var(--border); border-radius: 12px; width: 38px; height: 38px; color: var(--text2); font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
.cmd-mic:hover { border-color: var(--green); color: var(--green); }
.cmd-mic.recording { border-color: var(--red); color: var(--red); animation: pulse 1s infinite; }

/* ── Suggested Prompts ── */
.prompt-chips { display: flex; gap: 6px; justify-content: center; margin-top: 8px; flex-wrap: wrap; }
.prompt-chip { background: rgba(17,19,26,0.8); border: 1px solid var(--border); border-radius: 20px; padding: 5px 14px; font-size: 11px; color: var(--text2); cursor: pointer; transition: all 0.15s; white-space: nowrap; }
.prompt-chip:hover { border-color: var(--purple); color: var(--text); background: rgba(168,85,247,0.08); }

/* ── Chat Overlay (slides up from command bar) ── */
.chat-backdrop { display: none; position: fixed; inset: 0; z-index: 48; }
.chat-backdrop.active { display: block; }
.chat-overlay { position: fixed; bottom: 100px; left: 50%; transform: translateX(-50%); width: min(700px, 90vw); max-height: 60vh; background: rgba(17,19,26,0.95); border: 1px solid var(--border); border-radius: 16px 16px 0 0; backdrop-filter: blur(20px); box-shadow: 0 -8px 40px rgba(0,0,0,0.4); z-index: 49; display: none; flex-direction: column; overflow: hidden; }
.chat-overlay.active { display: flex; animation: slideUp 0.3s ease; }
.chat-overlay-header { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.chat-overlay-header span { font-size: 12px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }
.chat-overlay-close { background: none; border: none; color: var(--text2); cursor: pointer; font-size: 16px; padding: 2px 6px; }
.chat-overlay-close:hover { color: var(--text); }
.chat-messages { flex: 1; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.chat-messages::-webkit-scrollbar { width: 3px; }
.chat-messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.chat-user { align-self: flex-end; max-width: 75%; background: var(--purple); color: #fff; padding: 10px 14px; border-radius: 16px 16px 4px 16px; font-size: 14px; word-wrap: break-word; }
.chat-os { align-self: flex-start; max-width: 80%; background: var(--bg3); border: 1px solid var(--border); padding: 10px 14px; border-radius: 16px 16px 16px 4px; font-size: 13px; color: var(--text); line-height: 1.5; }
.chat-os.error { border-color: var(--red); background: rgba(248,81,73,0.08); }
.chat-os.success { border-color: var(--green); }

/* ── Dock (bottom bar, like macOS dock) ── */
.dock { position: fixed; bottom: 0; left: 0; right: 0; height: 48px; background: var(--dock-bg); border-top: 1px solid var(--border); backdrop-filter: blur(20px); display: flex; align-items: center; gap: 6px; padding: 0 20px; z-index: 100; overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; }
.dock::-webkit-scrollbar { height: 3px; }
.dock::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.dock-item { display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 8px; font-size: 12px; color: var(--text2); cursor: pointer; transition: all 0.15s; position: relative; white-space: nowrap; flex-shrink: 0; }
.dock-item:hover { background: rgba(255,255,255,0.04); color: var(--text); }
.dock-item .dock-icon { font-size: 16px; }
.dock-item .dock-label { font-size: 11px; font-weight: 500; max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
.dock-group { display: flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 8px; font-size: 12px; color: var(--text2); cursor: pointer; transition: all 0.15s; position: relative; white-space: nowrap; flex-shrink: 0; background: rgba(255,255,255,0.02); border: 1px solid var(--border); }
.dock-group:hover { background: rgba(255,255,255,0.06); color: var(--text); }
.dock-group .dock-count { font-size: 10px; color: var(--green); font-weight: 600; }
.dock-group-popup { position: absolute; bottom: 52px; left: 0; background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 8px; min-width: 200px; box-shadow: 0 -4px 20px rgba(0,0,0,0.5); z-index: 200; display: none; }
.dock-group-popup.active { display: block; }
.dock-group-popup .dock-item { padding: 5px 10px; width: 100%; }
.dock-dot { width: 5px; height: 5px; border-radius: 50%; position: absolute; bottom: 2px; left: 50%; transform: translateX(-50%); }
.dock-dot.running { background: var(--green); box-shadow: 0 0 4px var(--glow-green); }
.dock-dot.stopped { background: var(--text2); }
.dock-dot.error { background: var(--red); }
.dock-sep { width: 1px; height: 24px; background: var(--border); margin: 0 4px; }
.dock-vitals { display: flex; align-items: center; gap: 10px; margin-left: auto; font-size: 10px; font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace; color: var(--text2); }
.dock-vitals span { display: flex; align-items: center; gap: 3px; }

/* ── Notification Toasts ── */
.toast-stack { position: fixed; top: 42px; right: 16px; z-index: 200; display: flex; flex-direction: column; gap: 6px; pointer-events: none; }
.toast { padding: 10px 16px; background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; color: var(--text); font-size: 12px; pointer-events: auto; box-shadow: 0 4px 16px rgba(0,0,0,0.4); backdrop-filter: blur(12px); animation: slideUp 0.3s ease; max-width: 340px; }
.toast.success { border-color: rgba(67,233,123,0.3); }
.toast.error { border-color: rgba(248,81,73,0.3); }
.toast.warning { border-color: rgba(245,175,25,0.3); }
.toast.info { border-color: rgba(79,172,254,0.3); }

/* ── Expand Button (on cards) ── */
.expand-btn { position: absolute; top: 10px; right: 10px; background: none; border: 1px solid var(--border); color: var(--text2); width: 24px; height: 24px; border-radius: 6px; cursor: pointer; font-size: 12px; display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.2s; z-index: 5; }
.goal-card:hover .expand-btn, .special-card:hover .expand-btn, .vitals-card:hover .expand-btn { opacity: 1; }
.expand-btn:hover { background: rgba(255,255,255,0.06); color: var(--text); border-color: var(--blue); }

/* ── Detail Modal (expanded card view) ── */
.detail-modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); z-index: 250; backdrop-filter: blur(6px); align-items: center; justify-content: center; }
.detail-modal.active { display: flex; }
.detail-panel { background: var(--bg2); border: 1px solid var(--border); border-radius: 16px; width: 700px; max-width: 90vw; max-height: 85vh; overflow-y: auto; box-shadow: 0 24px 80px rgba(0,0,0,0.5); }
.detail-panel::-webkit-scrollbar { width: 4px; }
.detail-panel::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.detail-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; background: var(--bg2); z-index: 1; }
.detail-header h2 { font-size: 15px; font-weight: 700; }
.detail-body { padding: 20px; }

/* ── Settings Modal ── */
.modal-bg { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 300; backdrop-filter: blur(4px); align-items: center; justify-content: center; }
.modal-bg.active { display: flex; }
.modal { background: var(--bg2); border: 1px solid var(--border); border-radius: 16px; width: 440px; max-width: 90vw; max-height: 80vh; overflow-y: auto; box-shadow: 0 24px 80px rgba(0,0,0,0.5); }
.modal-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.modal-header h2 { font-size: 14px; font-weight: 700; }
.modal-close { background: none; border: none; color: var(--text2); font-size: 18px; cursor: pointer; }
.modal-body { padding: 20px; }

/* ── Welcome State ── */
.welcome { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 20px; text-align: center; grid-column: 1 / -1; }
.welcome h2 { font-size: 28px; font-weight: 800; margin-bottom: 8px; background: linear-gradient(135deg, var(--accent), var(--purple)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.welcome p { color: var(--text2); font-size: 14px; max-width: 400px; }

/* ── Badges ── */
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; }
.badge-running { background: rgba(67,233,123,0.12); color: var(--green); }
.badge-complete { background: rgba(67,233,123,0.12); color: var(--green); }
.badge-operating { background: rgba(34,211,238,0.12); color: var(--cyan); }
.badge-active { background: rgba(168,85,247,0.12); color: var(--purple); }
.badge-failed { background: rgba(248,81,73,0.12); color: var(--red); }

/* ── Evolution Nudge Banner ── */
.evo-nudge { position: fixed; top: 32px; left: 0; right: 0; height: 36px; background: linear-gradient(90deg, var(--evo-bg), rgba(155,122,237,0.04)); border-bottom: 1px solid var(--evo-border); display: none; align-items: center; padding: 0 20px; gap: 12px; z-index: 99; font-size: 12px; }
.evo-nudge.active { display: flex; }
.evo-nudge-icon { font-size: 14px; }
.evo-nudge-text { color: var(--accent); font-weight: 600; flex: 1; }
.evo-nudge-btn { background: none; border: 1px solid var(--accent); color: var(--accent); border-radius: 6px; padding: 3px 12px; font-size: 11px; cursor: pointer; font-weight: 600; font-family: inherit; transition: all 0.15s; }
.evo-nudge-btn:hover { background: rgba(232,164,74,0.1); }
.evo-nudge-copy { background: var(--accent); border: none; color: var(--bg); border-radius: 6px; padding: 3px 12px; font-size: 11px; cursor: pointer; font-weight: 700; font-family: inherit; transition: all 0.15s; }
.evo-nudge-copy:hover { opacity: 0.85; }

/* ── Responsive ── */
@media (max-width: 800px) {
    .desktop { grid-template-columns: 1fr; padding: 12px; gap: 14px; }
    .command-bar { width: calc(100vw - 16px); }
    .chat-overlay { width: calc(100vw - 16px); }
    .topbar-right #tb-cost, .topbar-right #tb-services { display: none; }
    .dock-vitals { display: none; }
    .evo-nudge { padding: 0 12px; gap: 8px; font-size: 11px; }
}
@media (max-width: 480px) {
    .desktop { padding: 8px; gap: 10px; }
    .goal-card-header { padding: 10px 12px 8px; }
    .goal-card-phases { padding: 0 12px 10px; }
    .goal-card-ring { width: 40px; height: 40px; }
    .prompt-chips { display: none; }
    .topbar { padding: 0 8px; }
    .evo-nudge-btn { display: none; }
}

/* ── Hidden compat elements (for Playwright tests — must be clickable) ── */
.compat-hidden { position: fixed; top: 0; left: 0; z-index: 9999; opacity: 0.01; overflow: visible; }
.compat-hidden nav { display: flex; }
.compat-hidden [data-tab] { width: 20px; height: 20px; opacity: 0.01; border: none; background: transparent; cursor: pointer; padding: 0; font-size: 1px; }
.compat-hidden .tab-panel { display: none; position: fixed; top: 0; left: 0; }
.compat-hidden .tab-panel.active { display: block; width: 1px; height: 1px; overflow: hidden; opacity: 0.01; }

/* ═══════════════════════════════════════════════════════════════════
   SETUP WIZARD — first-run overlay (redesigned)
   ═══════════════════════════════════════════════════════════════════ */
.wizard-overlay { position: fixed; inset: 0; background: var(--bg); z-index: 10000; display: none; align-items: center; justify-content: center; overflow-y: auto; }
.wizard-overlay.active { display: flex; }
.wizard-overlay::before { content: ''; position: fixed; inset: 0; background: radial-gradient(ellipse 600px 400px at 50% 30%, rgba(232,164,74,0.04), transparent 70%), radial-gradient(ellipse 400px 300px at 70% 60%, rgba(155,122,237,0.03), transparent 60%); pointer-events: none; }
.wizard-box { width: min(520px, 92vw); max-height: 88vh; overflow-y: auto; padding: 0; animation: wizSlideUp 0.5s cubic-bezier(0.16,1,0.3,1); position: relative; }
.wizard-box::-webkit-scrollbar { width: 3px; }
.wizard-box::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }
@keyframes wizSlideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
@keyframes wizFadeSwap { from { opacity: 0; transform: translateX(12px); } to { opacity: 1; transform: translateX(0); } }

/* Compact header — shown once, shrinks after step 0 */
.wizard-header { text-align: center; padding: 32px 0 0; transition: all 0.4s ease; }
.wizard-header.compact { padding: 16px 0 0; }
.wizard-header.compact img { width: 36px; height: 36px; }
.wizard-header.compact h1 { font-size: 18px; margin-top: 4px; }
.wizard-header.compact p { display: none; }
.wizard-header img { width: 56px; height: 56px; border-radius: 14px; object-fit: cover; transition: all 0.4s ease; }
.wizard-header h1 { font-family: 'DM Sans', sans-serif; font-size: 26px; font-weight: 700; margin-top: 10px; background: linear-gradient(135deg, var(--accent2) 0%, var(--purple) 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -0.3px; }
.wizard-header p { color: var(--text2); font-size: 12px; margin-top: 2px; letter-spacing: 0.3px; }

/* Step indicator with labels */
.wizard-stepper { display: flex; justify-content: center; align-items: center; gap: 0; padding: 20px 32px 24px; }
.wiz-step-item { display: flex; align-items: center; gap: 0; }
.wiz-step-pip { width: 24px; height: 24px; border-radius: 50%; background: var(--bg3); border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; color: var(--text-dim); transition: all 0.3s ease; flex-shrink: 0; }
.wiz-step-pip.active { background: rgba(232,164,74,0.15); border-color: var(--accent); color: var(--accent); box-shadow: 0 0 12px rgba(232,164,74,0.2); }
.wiz-step-pip.done { background: var(--green); border-color: var(--green); color: #fff; }
.wiz-step-label { font-size: 9px; color: var(--text-dim); margin-left: 6px; letter-spacing: 0.3px; text-transform: uppercase; font-weight: 600; transition: color 0.3s; white-space: nowrap; }
.wiz-step-item.active .wiz-step-label { color: var(--accent); }
.wiz-step-item.done .wiz-step-label { color: var(--green); }
.wiz-step-line { width: 32px; height: 1px; background: var(--border); margin: 0 8px; flex-shrink: 0; transition: background 0.3s; }
.wiz-step-line.done { background: var(--green); }

/* Content area */
.wizard-content { padding: 0 36px 28px; }
.wizard-section { display: none; }
.wizard-section.active { display: block; animation: wizFadeSwap 0.3s ease; }
.wizard-section h2 { font-family: 'DM Sans', sans-serif; font-size: 16px; font-weight: 700; margin-bottom: 3px; color: var(--text); }
.wizard-section .wiz-subtitle { color: var(--text2); font-size: 11.5px; margin-bottom: 18px; line-height: 1.4; }
.wizard-scanning { color: var(--accent); font-size: 12px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
.wizard-scanning .spin { display: inline-block; animation: pulse 1s infinite; }

/* Item list */
.wiz-items { display: flex; flex-direction: column; gap: 5px; margin-bottom: 16px; max-height: 300px; overflow-y: auto; padding-right: 4px; }
.wiz-items::-webkit-scrollbar { width: 3px; }
.wiz-items::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }
.wiz-group-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: var(--text2); padding: 10px 4px 4px; }
.wiz-item { display: flex; align-items: center; gap: 10px; padding: 9px 12px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; transition: all 0.15s ease; position: relative; }
.wiz-item:hover { border-color: var(--border-focus); background: var(--bg3); }
.wiz-item.selected { border-color: var(--accent); background: rgba(232,164,74,0.05); box-shadow: 0 0 0 1px rgba(232,164,74,0.15); }
.wiz-item.detected { border-left: 2px solid var(--green); }
.wiz-item .wiz-check { width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid var(--border); flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 10px; transition: all 0.15s; color: transparent; }
.wiz-item.selected .wiz-check { background: var(--accent); border-color: var(--accent); color: #fff; }
.wiz-item .wiz-icon { font-size: 16px; width: 24px; text-align: center; flex-shrink: 0; }
.wiz-item .wiz-info { flex: 1; min-width: 0; }
.wiz-item .wiz-name { font-size: 12.5px; font-weight: 600; }
.wiz-item .wiz-detail { font-size: 10.5px; color: var(--text2); margin-top: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wiz-item .wiz-badge { font-size: 8px; padding: 2px 7px; border-radius: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; }
.wiz-badge.cloud { background: rgba(96,165,250,0.12); color: var(--blue); }
.wiz-badge.local { background: rgba(74,222,128,0.12); color: var(--green); }
.wiz-badge.cli { background: rgba(155,122,237,0.12); color: var(--purple); }
.wiz-badge.ide { background: rgba(232,164,74,0.12); color: var(--accent); }
.wiz-badge.extension { background: rgba(103,232,249,0.12); color: var(--cyan); }
.wiz-badge.high { background: rgba(74,222,128,0.12); color: var(--green); }
.wiz-badge.medium { background: rgba(251,191,36,0.12); color: var(--yellow); }
.wiz-badge.low { background: rgba(248,113,113,0.1); color: var(--red); }

/* Collapsible "more providers" toggle */
.wiz-more-toggle { display: flex; align-items: center; gap: 6px; padding: 6px 4px; cursor: pointer; color: var(--text2); font-size: 10px; font-weight: 600; letter-spacing: 0.3px; transition: color 0.15s; }
.wiz-more-toggle:hover { color: var(--text); }
.wiz-more-toggle .arrow { transition: transform 0.2s; font-size: 8px; }
.wiz-more-toggle.open .arrow { transform: rotate(90deg); }
.wiz-more-items { display: none; }
.wiz-more-items.open { display: flex; flex-direction: column; gap: 5px; }

.wiz-empty { text-align: center; padding: 24px; color: var(--text2); font-size: 12px; }
.wiz-env-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 16px; }
.wiz-env-item { padding: 10px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; }
.wiz-env-label { font-size: 8px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text2); margin-bottom: 2px; font-weight: 600; }
.wiz-env-val { font-size: 13px; font-weight: 600; }

.wiz-input { width: 100%; padding: 9px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 12px; font-family: inherit; outline: none; margin-top: 6px; transition: border-color 0.15s; box-sizing: border-box; }
.wiz-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(232,164,74,0.1); }
.wiz-input::placeholder { color: var(--text-dim); }

.wiz-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px; }
.wiz-btn { padding: 9px 22px; border-radius: 8px; font-size: 12px; font-weight: 600; font-family: inherit; cursor: pointer; transition: all 0.15s; }
.wiz-btn-secondary { background: var(--bg3); border: 1px solid var(--border); color: var(--text2); }
.wiz-btn-secondary:hover { border-color: var(--border-focus); color: var(--text); }
.wiz-btn-primary { background: linear-gradient(135deg, var(--accent), var(--purple)); border: none; color: #fff; letter-spacing: 0.2px; }
.wiz-btn-primary:hover { opacity: 0.92; transform: translateY(-1px); box-shadow: 0 4px 16px rgba(232,164,74,0.2); }
.wiz-btn-primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none; box-shadow: none; }
.wiz-btn-skip { background: none; border: none; color: var(--text2); font-size: 11px; cursor: pointer; padding: 8px 12px; }
.wiz-btn-skip:hover { color: var(--text); }

.wiz-summary { padding: 14px 16px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 14px; }
.wiz-summary-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; font-size: 11.5px; }
.wiz-summary-row .label { color: var(--text2); }
.wiz-summary-row .value { font-weight: 600; }

/* Launch button special treatment */
.wiz-btn-launch { background: linear-gradient(135deg, var(--accent), #d97706, var(--purple)); border: none; color: #fff; padding: 12px 32px; font-size: 14px; font-weight: 700; border-radius: 10px; letter-spacing: 0.3px; cursor: pointer; transition: all 0.2s; position: relative; overflow: hidden; }
.wiz-btn-launch:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(232,164,74,0.3); }
.wiz-btn-launch::after { content: ''; position: absolute; inset: 0; background: linear-gradient(135deg, transparent 40%, rgba(255,255,255,0.1) 50%, transparent 60%); animation: wizShimmer 3s ease-in-out infinite; }
@keyframes wizShimmer { 0%,100% { transform: translateX(-100%); } 50% { transform: translateX(100%); } }
</style>
</head>
<body>

<!-- ═══ TOP BAR (macOS-style menu bar) ═══ -->
<header>
<div class="topbar">
    <div class="topbar-left">
        <img src="/logo.jpg" alt="OpenSculpt" style="height:22px;width:22px;border-radius:4px;object-fit:cover;vertical-align:middle;margin-right:6px">
        <span class="topbar-brand">OpenSculpt</span>
        <span style="color:var(--text2);font-size:10px" id="h-uptime">00:00:00</span>
    </div>
    <div class="topbar-right">
        <span id="tb-services" style="font-size:10px;color:var(--text2)"></span>
        <span id="tb-cost" style="font-size:11px"></span>
        <span id="tb-nodes" style="display:none"></span>
        <span><span class="topbar-dot" id="key-pulse" style="background:var(--green);box-shadow:0 0 4px var(--glow-green)"></span></span>
        <button class="topbar-btn" onclick="openSettings()" title="Settings">Settings</button>
    </div>
</div>
</header>

<!-- ═══ EVOLUTION NUDGE BANNER (shows when OS needs help) ═══ -->
<div class="evo-nudge" id="evo-nudge">
    <span class="evo-nudge-icon">&#9889;</span>
    <span class="evo-nudge-text" id="evo-nudge-text">Your OS needs help evolving</span>
    <button class="evo-nudge-btn" onclick="expandEvolution()">View demands</button>
    <button class="evo-nudge-copy" onclick="copyEvolutionPrompt()">Copy prompt</button>
</div>

<!-- ═══ DESKTOP (the main surface — goal cards live here) ═══ -->
<div class="desktop" id="desktop" onclick="closeChatOverlay()">
    <!-- Welcome state (shown when no goals) -->
    <div class="welcome" id="welcome-state">
        <h2>What do you want me to handle?</h2>
        <p>Type a command below. Try "run sales for my startup" or "set up monitoring"</p>
    </div>
</div>

<!-- ═══ CHAT OVERLAY (slides up from command bar) ═══ -->
<div class="chat-backdrop" id="chat-backdrop" onclick="closeChatOverlay()"></div>
<div class="chat-overlay" id="chat-overlay" onclick="event.stopPropagation()">
    <div class="chat-overlay-header">
        <span>Conversation</span>
        <div style="display:flex;gap:8px;align-items:center">
            <button onclick="clearChat()" style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:4px;padding:2px 8px;font-size:10px;cursor:pointer">Clear</button>
            <button class="chat-overlay-close" onclick="closeChatOverlay()">&times;</button>
        </div>
    </div>
    <div class="chat-messages" id="chat-messages"></div>
</div>

<!-- ═══ STATUS LINE (above command bar) ═══ -->
<div id="status-line" style="position:fixed;bottom:92px;left:50%;transform:translateX(-50%);font-size:11px;color:var(--text2);z-index:50;text-align:center;max-width:600px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis"></div>

<!-- ═══ COMMAND BAR (Spotlight-style, always visible) ═══ -->
<div class="command-bar" id="command-bar" onclick="event.stopPropagation()">
    <div class="command-bar-inner">
        <img src="/logo.jpg" alt="OpenSculpt" style="height:28px;width:28px;border-radius:6px;object-fit:cover;padding-left:4px;cursor:pointer" onclick="toggleChatOverlay()" title="Toggle conversation">
        <input type="text" class="cmd-input" id="os-cmd" placeholder="Ask OpenSculpt anything..." autocomplete="off"
               onkeydown="if(event.key==='Enter')runCommand()" onfocus="onCmdFocus()" />
        <button class="cmd-send" onclick="runCommand()" title="Send">&#9654;</button>
        <button class="cmd-mic" id="mic-btn" onclick="toggleVoice()" title="Voice">&#x1F3A4;</button>
    </div>
    <div class="prompt-chips" id="prompt-chips">
        <span class="prompt-chip" onclick="quickCmd('handle sales for my startup')">Sales CRM</span>
        <span class="prompt-chip" onclick="quickCmd('set up customer support')">Support</span>
        <span class="prompt-chip" onclick="quickCmd('build internal knowledge base')">Knowledge</span>
        <span class="prompt-chip" onclick="quickCmd('set up CI/CD and monitoring')">DevOps</span>
        <span class="prompt-chip" onclick="quickCmd(&quot;what's running?&quot;)">Status</span>
    </div>
</div>

<!-- ═══ DOCK (bottom bar — running daemons + vitals) ═══ -->
<div class="dock" id="dock">
    <div id="dock-daemons" style="display:flex;align-items:center;gap:4px"></div>
    <div class="dock-sep"></div>
    <div class="dock-vitals">
        <span>CPU <span id="dk-cpu">-</span></span>
        <span>RAM <span id="dk-ram">-</span></span>
        <span id="dk-nodes-label" style="display:none">Nodes <span id="dk-nodes">-</span></span>
    </div>
</div>

<!-- ═══ DETAIL MODAL (expanded card view) ═══ -->
<div class="detail-modal" id="detail-modal" onclick="if(event.target===this)closeDetail()">
    <div class="detail-panel">
        <div class="detail-header">
            <h2 id="detail-title">Details</h2>
            <button class="modal-close" onclick="closeDetail()">&times;</button>
        </div>
        <div class="detail-body" id="detail-body"></div>
    </div>
</div>

<!-- ═══ TOAST CONTAINER ═══ -->
<div class="toast-stack" id="toast-stack"></div>

<!-- ═══ SETTINGS MODAL ═══ -->
<div class="modal-bg" id="settings-modal" onclick="if(event.target===this)closeSettings()">
    <div class="modal">
        <div class="modal-header">
            <h2>Settings</h2>
            <button class="modal-close" onclick="closeSettings()">&times;</button>
        </div>
        <div class="modal-body">
            <!-- LLM Provider -->
            <div style="margin-bottom:14px">
                <label style="display:block;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;margin-bottom:6px;font-weight:600">Provider</label>
                <select id="provider-select" onchange="onProviderChange()" style="width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-size:12px;outline:none;cursor:pointer">
                    <optgroup label="Cloud APIs">
                        <option value="anthropic">Anthropic (direct)</option>
                        <option value="openrouter">OpenRouter (proxy, cost tracking)</option>
                        <option value="openai">OpenAI</option>
                        <option value="google">Google Gemini</option>
                        <option value="mistral">Mistral</option>
                        <option value="groq">Groq</option>
                        <option value="together">Together AI</option>
                        <option value="fireworks">Fireworks AI</option>
                        <option value="deepseek">DeepSeek</option>
                        <option value="perplexity">Perplexity</option>
                        <option value="cohere">Cohere</option>
                    </optgroup>
                    <optgroup label="Local / Self-hosted">
                        <option value="lmstudio">LM Studio (local)</option>
                        <option value="ollama">Ollama (local)</option>
                        <option value="custom">Custom OpenAI-compatible</option>
                    </optgroup>
                </select>
                <div id="provider-hint" style="font-size:10px;color:var(--text2);margin-top:4px"></div>
            </div>

            <!-- Base URL (shown for custom/local providers) -->
            <div id="base-url-row" style="margin-bottom:14px;display:none">
                <label style="display:block;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;margin-bottom:6px;font-weight:600">Base URL</label>
                <input id="base-url-input" type="text" placeholder="http://localhost:1234/v1" style="width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;outline:none" />
            </div>

            <!-- API Key -->
            <div style="margin-bottom:14px">
                <label style="display:block;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;margin-bottom:6px;font-weight:600">API Key</label>
                <div style="display:flex;gap:6px">
                    <input id="api-key-input" type="password" placeholder="sk-..." style="flex:1;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;outline:none" />
                    <button onclick="toggleKeyVis()" style="background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:0 10px;color:var(--text2);cursor:pointer;font-size:13px">&#128065;</button>
                </div>
                <div id="api-key-status" style="margin-top:6px;font-size:11px;color:var(--text2)"></div>
            </div>

            <!-- Model -->
            <div style="margin-bottom:14px">
                <label style="display:block;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;margin-bottom:6px;font-weight:600">Model</label>
                <div style="position:relative">
                    <input id="model-input" type="text" list="model-suggestions" placeholder="claude-haiku-4-5-20251001" style="width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;outline:none" />
                    <datalist id="model-suggestions"></datalist>
                </div>
                <div id="model-status" style="margin-top:4px;font-size:10px;color:var(--text2)"></div>
            </div>

            <!-- Save + Test -->
            <div style="display:flex;gap:8px;margin-bottom:16px">
                <button onclick="saveApiKey()" style="flex:2;padding:8px;background:linear-gradient(135deg,var(--blue),var(--blue2));border:none;border-radius:8px;color:#0a0e14;font-weight:700;font-size:12px;cursor:pointer">Save</button>
                <button onclick="testConnection()" id="test-conn-btn" style="flex:1;padding:8px;background:var(--bg3);border:1px solid var(--border);border-radius:8px;color:var(--text);font-weight:600;font-size:12px;cursor:pointer">Test</button>
            </div>

            <!-- GitHub Token (collapsed) -->
            <details style="padding-top:14px;border-top:1px solid var(--border)">
                <summary style="font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;font-weight:600;cursor:pointer;margin-bottom:6px">GitHub Token</summary>
                <div style="display:flex;gap:6px;margin-top:6px">
                    <input id="gh-token-input" type="password" placeholder="ghp_..." style="flex:1;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-family:monospace;font-size:12px;outline:none" />
                    <button onclick="saveGHToken()" style="background:linear-gradient(135deg,var(--purple),var(--blue));border:none;border-radius:8px;padding:0 14px;color:#fff;font-weight:700;font-size:11px;cursor:pointer">Save</button>
                </div>
                <div id="gh-token-status" style="margin-top:6px;font-size:11px;color:var(--text2)"></div>
            </details>

            <!-- Fleet Sync (collapsed) -->
            <details style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
                <summary style="font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px;font-weight:600;cursor:pointer;margin-bottom:6px">Fleet Sync</summary>
                <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--bg3);border-radius:8px;margin-top:6px">
                    <div><div style="font-size:12px;font-weight:600">Auto-share learnings</div><div style="font-size:10px;color:var(--text2)">Share with fleet peers</div></div>
                    <label style="position:relative;display:inline-block;width:40px;height:22px;cursor:pointer">
                        <input type="checkbox" id="fed-toggle" onchange="toggleFederated()" style="opacity:0;width:0;height:0">
                        <span id="fed-slider" style="position:absolute;top:0;left:0;right:0;bottom:0;background:var(--border);border-radius:11px;transition:0.3s"></span>
                        <span id="fed-dot" style="position:absolute;height:16px;width:16px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:0.3s"></span>
                    </label>
                </div>
                <div id="fed-reciprocity" style="font-size:10px;padding:6px 8px;margin-top:6px;background:var(--bg);border-radius:6px;border:1px solid var(--border)"></div>
            </details>
        </div>
    </div>
</div>

<!-- ═══ HIDDEN COMPAT ELEMENTS (data targets for old JS) ═══ -->
<div class="compat-hidden" aria-hidden="true">
    <div id="os-output"></div>
    <div id="tab-shell" class="tab-panel"></div>
    <div id="tab-overview" class="tab-panel active">Overview</div>
    <div id="tab-agents" class="tab-panel">Agents</div>
    <div id="tab-events" class="tab-panel">Events</div>
    <div id="tab-evolution" class="tab-panel">Evolution</div>
    <div id="tab-daemons" class="tab-panel">Daemons</div>
    <div id="tab-hands" class="tab-panel">Hands</div>
    <div id="tab-setup" class="tab-panel">Setup</div>
    <div id="tab-system" class="tab-panel">System</div>
    <div id="ov-ag-table"></div>
    <div id="ov-ag-empty"></div>
    <div id="ov-goals-table"></div>
    <div id="ov-resources-table"></div>
    <div id="ov-resources-empty"></div>
    <div id="ov-daemons-list"></div>
    <div id="ov-evo-cycles"></div>
    <div id="ov-evo-strats"></div>
    <div id="ov-version"></div>
    <div id="v-agents"></div>
    <div id="v-agents-sub"></div>
    <div id="v-events"></div>
    <div id="v-audit"></div>
    <div id="v-uptime"></div>
    <div id="v-resources"></div>
    <div id="v-resources-sub"></div>
    <div id="v-procs"></div>
    <div id="ag-table"></div>
    <div id="ag-empty"></div>
    <div id="proc-table"></div>
    <div id="proc-empty"></div>
    <div id="ev-feed"></div>
    <div id="ev-empty"></div>
    <div id="au-table"></div>
    <div id="au-empty"></div>
    <div id="evo-cycles"></div>
    <div id="evo-strategies"></div>
    <div id="evo-patterns"></div>
    <div id="evo-saved"></div>
    <div id="evo-strat-list"></div>
    <div id="evo-strat-empty"></div>
    <div id="evo-pat-list"></div>
    <div id="evo-pat-empty"></div>
    <div id="evo-changelog"></div>
    <div id="evo-changelog-empty"></div>
    <div id="evo-demands-list"></div>
    <div id="evo-demands-empty"></div>
    <div id="evo-demands-count"></div>
    <div id="evo-insights-list"></div>
    <div id="evo-insights-empty"></div>
    <div id="evo-learned-list"></div>
    <div id="evo-learned-empty"></div>
    <div id="evo-blockers-card"></div>
    <div id="evo-blockers-list"></div>
    <div id="fed-status"></div>
    <div id="fed-last-pr"></div>
    <div id="fed-interval"><option value="3">3</option></div>
    <div id="share-status"></div>
    <div id="meta-genomes"></div>
    <div id="meta-mutations"></div>
    <div id="meta-signals"></div>
    <div id="meta-underperf"></div>
    <div id="meta-genome-list"></div>
    <div id="meta-genome-empty"></div>
    <div id="meta-mutation-list"></div>
    <div id="meta-mut-empty"></div>
    <div id="daemons-list"></div>
    <div id="hand-select"></div>
    <div id="hand-results"></div>
    <div id="hand-config"></div>
    <div id="cb-types"></div>
    <div id="cb-largest"></div>
    <div id="cb-todos"></div>
    <div id="cb-todos-empty"></div>
    <div id="cb-modules"></div>
    <div id="dep-grid"></div>
    <div id="v-pyfiles"></div>
    <div id="v-loc"></div>
    <div id="v-classes"></div>
    <div id="v-funcs"></div>
    <div id="v-health"></div>
    <div id="cb-score"></div>
    <div id="health-ring-fill"></div>
    <div id="gh-repo-url"></div>
    <div id="gh-install-status"></div>
    <div id="setup-providers-list"></div>
    <div id="setup-channels-list"></div>
    <div id="setup-tools-list"></div>
    <div id="setup-config-modal"></div>
</div>

<!-- ═══ SETUP WIZARD (first-run overlay, above everything) ═══ -->
<div class="wizard-overlay" id="wizard-overlay">
<div class="wizard-box">
    <div class="wizard-header" id="wizard-header">
        <img src="/logo.jpg" alt="OpenSculpt">
        <h1>OpenSculpt</h1>
        <p>The Self-Evolving Agentic OS</p>
    </div>

    <div class="wizard-stepper" id="wizard-stepper">
        <div class="wiz-step-item active" data-step="0">
            <div class="wiz-step-pip active">1</div>
            <span class="wiz-step-label">Scan</span>
        </div>
        <div class="wiz-step-line"></div>
        <div class="wiz-step-item" data-step="1">
            <div class="wiz-step-pip">2</div>
            <span class="wiz-step-label">Provider</span>
        </div>
        <div class="wiz-step-line"></div>
        <div class="wiz-step-item" data-step="2">
            <div class="wiz-step-pip">3</div>
            <span class="wiz-step-label">Tools</span>
        </div>
        <div class="wiz-step-line"></div>
        <div class="wiz-step-item" data-step="3">
            <div class="wiz-step-pip">4</div>
            <span class="wiz-step-label">Launch</span>
        </div>
    </div>

    <div class="wizard-content">
    <!-- STEP 0: Scanning -->
    <div class="wizard-section active" id="wiz-step-0">
        <h2>Scanning your environment...</h2>
        <p class="wiz-subtitle">Detecting LLM providers, coding tools, and system capabilities</p>
        <div class="wizard-scanning"><span class="spin">&#9672;</span> Auto-detecting...</div>
        <div id="wiz-scan-progress" style="color:var(--text2);font-size:11px;line-height:1.8"></div>
    </div>

    <!-- STEP 1: LLM Provider -->
    <div class="wizard-section" id="wiz-step-1">
        <h2>LLM Provider</h2>
        <p class="wiz-subtitle">Choose how OpenSculpt talks to AI. Detected providers appear first.</p>
        <div class="wiz-items" id="wiz-providers"></div>
        <div id="wiz-api-key-input" style="display:none">
            <input type="password" class="wiz-input" id="wiz-key" placeholder="Paste API key here..." />
        </div>
        <div class="wiz-btns">
            <button class="wiz-btn-skip" onclick="wizStep(2)">Skip for now</button>
            <button class="wiz-btn wiz-btn-primary" onclick="wizSelectProvider()">Next</button>
        </div>
    </div>

    <!-- STEP 2: Vibe Coding Tools -->
    <div class="wizard-section" id="wiz-step-2">
        <h2>Vibe Coding Tools</h2>
        <p class="wiz-subtitle">The chisels that evolve your OS. We auto-selected what we found.</p>
        <div class="wiz-items" id="wiz-vibe-tools"></div>
        <div class="wiz-btns">
            <button class="wiz-btn wiz-btn-secondary" onclick="wizStep(1)">Back</button>
            <button class="wiz-btn wiz-btn-primary" onclick="wizStep(3)">Next</button>
        </div>
    </div>

    <!-- STEP 3: Summary + Launch -->
    <div class="wizard-section" id="wiz-step-3">
        <h2>Ready to go</h2>
        <p class="wiz-subtitle">Here's your setup. Change anything later in Settings.</p>
        <div class="wiz-env-grid" id="wiz-env-grid"></div>
        <div class="wiz-summary" id="wiz-summary"></div>
        <div class="wiz-btns" style="justify-content:center;gap:12px;margin-top:24px">
            <button class="wiz-btn wiz-btn-secondary" onclick="wizStep(2)">Back</button>
            <button class="wiz-btn-launch" onclick="wizFinish()">Launch OpenSculpt</button>
        </div>
    </div>
    </div>
</div>
</div>



<script>
/* ═══════════════════════════════════════════════════════════════════
   OPENSCULPT — LIVING DESKTOP UI
   ═══════════════════════════════════════════════════════════════════ */

/* ── Auth key (injected at serve time) ── */
/*__SCULPT_API_KEY__*/
if (typeof _SCULPT_API_KEY === 'undefined') var _SCULPT_API_KEY = '';

/* ── Auth-aware fetch: auto-inject API key into all requests ── */
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
    opts = opts || {};
    if (_SCULPT_API_KEY && typeof url === 'string' && url.startsWith('/api')) {
        opts.headers = opts.headers || {};
        if (opts.headers instanceof Headers) {
            opts.headers.set('X-API-Key', _SCULPT_API_KEY);
        } else {
            opts.headers['X-API-Key'] = _SCULPT_API_KEY;
        }
    }
    return _origFetch.call(window, url, opts);
};

/* ── Globals ── */
const RING_CIRC = 2 * Math.PI * 20; // for goal card rings (r=20)
const GAUGE_CIRC = 2 * Math.PI * 52;
let eventCount = 0;
let _goalData = [];
let _daemonData = [];
let _learnedData = [];
let _chatHistory = [];
let _collapsedGoals = new Set();  // tracks which goal cards have phases collapsed
let _expandedResources = new Set();  // tracks which resource sections are expanded
// auto-share removed — users share via git PRs

/* ── Helpers ── */
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function fmtUptime(s) {
    const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
    return [h,m,sec].map(v => String(v).padStart(2,'0')).join(':');
}
function _timeAgo(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return Math.floor(diff/60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff/3600000) + 'h ago';
    return Math.floor(diff/86400000) + 'd ago';
}
function cleanPhaseResult(raw) {
    // Extract a clean one-line summary from LLM markdown output
    if (!raw) return '';
    // Strip markdown formatting
    let text = raw
        .replace(/\*\*/g, '')           // bold
        .replace(/\[Verified:.*?\]/gs, '')  // verification blocks
        .replace(/\[VERIFICATION FAILED:.*?\]/gs, '')
        .replace(/\[Accepted:.*?\]/gs, '')
        .replace(/\[Diagnosis:.*?\]/gs, '')
        .replace(/\(token budget.*?\)/gi, '')
        .replace(/\d{4,} tokens (used|remaining)/gi, '')
        .replace(/\|[-\s|]+\|/g, '')    // table separators
        .replace(/\|/g, ' ')           // table pipes
        .replace(/#{1,4}\s/g, '')       // headers
        .replace(/```[\s\S]*?```/g, '') // code blocks
        .replace(/\n+/g, ' ')          // newlines
        .replace(/\s{2,}/g, ' ')        // multiple spaces
        .trim();
    // Prefer sentences with outcome words
    const sentences = text.split(/[.!]\s/);
    const outcomes = sentences.filter(s => s.match(/created|deployed|started|installed|configured|built|running|ready|complete/i));
    if (outcomes.length) {
        const s = outcomes[0].trim();
        return s.slice(0, 100) + (s.length > 100 ? '...' : '');
    }
    for (const s of sentences) {
        const clean = s.trim();
        if (clean.length > 15 && !clean.match(/^[-\s]+$/)) {
            return clean.slice(0, 100) + (clean.length > 100 ? '...' : '');
        }
    }
    return text.slice(0, 80);
}
function extractTitle(desc) {
    if (!desc) return '';
    const colon = desc.indexOf(':');
    if (colon > 5 && colon < 60) return desc.slice(0, colon).trim();
    const sent = desc.search(/[.!?]\s/);
    if (sent > 5 && sent < 80) return desc.slice(0, sent + 1).trim();
    if (desc.length > 55) {
        const cut = desc.lastIndexOf(' ', 55);
        return desc.slice(0, cut > 20 ? cut : 55) + '...';
    }
    return desc;
}
function dedupeServices(svcs) {
    const byPort = {};
    svcs.forEach(s => {
        const key = s.port || s.name;
        if (!byPort[key] || s.status === 'healthy' ||
            (byPort[key].name.startsWith('goal_') && !s.name.startsWith('goal_')))
            byPort[key] = s;
    });
    return Object.values(byPort);
}
function cleanServiceName(name) {
    if (!name) return 'Service';
    if (name.match(/^goal_\d+_\d+_/))
        return name.replace(/^goal_\d+_\d+_/, '').replace(/_/g, ' ');
    return name;
}
function statusLabel(s) {
    if (s === 'needs_user') return 'Needs setup';
    return s || 'unknown';
}
let _connectionLost = false;
async function fetchJSON(url) {
    try {
        const _headers = {};
        if (_SCULPT_API_KEY) _headers['X-API-Key'] = _SCULPT_API_KEY;
        const resp = await fetch(url, {headers: _headers});
        if (_connectionLost) {
            _connectionLost = false;
            const ind = document.getElementById('conn-lost');
            if (ind) ind.style.display = 'none';
        }
        return await resp.json();
    } catch(e) {
        if (!_connectionLost) {
            _connectionLost = true;
            let ind = document.getElementById('conn-lost');
            if (!ind) {
                ind = document.createElement('div');
                ind.id = 'conn-lost';
                ind.style.cssText = 'position:fixed;top:36px;left:50%;transform:translateX(-50%);background:rgba(248,113,113,0.9);color:#fff;padding:6px 16px;border-radius:0 0 8px 8px;font-size:11px;z-index:200;font-weight:600';
                ind.textContent = 'Connection lost — retrying...';
                document.body.appendChild(ind);
            }
            ind.style.display = '';
        }
        return null;
    }
}

/* ═══════════════════════════════════════════════════════════════════
   DESKTOP RENDERING — Goal Cards
   ═══════════════════════════════════════════════════════════════════ */

function renderDesktop(goals, resources, daemons, learned, services) {
    const desktop = document.getElementById('desktop');
    if (!desktop) return;
    goals = goals || []; resources = resources || []; daemons = daemons || []; learned = learned || []; services = services || [];
    const welcome = document.getElementById('welcome-state');

    if (!goals.length && !learned.length) {
        desktop.innerHTML = '<div class="welcome" id="welcome-state"><h2>What do you want me to handle?</h2><p>Type a command below. Try "run sales for my startup" or "set up monitoring"</p></div>';
        // Keep chips visible on welcome screen
        const chips = document.getElementById('prompt-chips');
        if (chips) chips.style.display = '';
        return;
    }

    // Hide welcome but keep chips visible when conversation panel is open
    const welcomeEl = document.getElementById('welcome-state');
    if (welcomeEl) welcomeEl.style.display = 'none';

    // Sort: active goals first, then completed (user cares about what's happening NOW)
    const activeGoals = goals.filter(g => g.status === 'active' || g.status === 'operating' || g.status === 'planning');
    const completedGoals = goals.filter(g => g.status === 'complete' || g.status === 'stale');
    const sortedGoals = [...activeGoals, ...completedGoals];

    let html = '';

    // Summary bar
    if (goals.length > 0) {
        const nComplete = completedGoals.length;
        const nActive = activeGoals.length;
        html += '<div style="grid-column:1/-1;display:flex;gap:12px;align-items:center;padding:8px 0;font-size:12px;color:var(--text2)">';
        if (nActive) html += '<span style="color:var(--green)">' + nActive + ' active</span>';
        if (nComplete) html += '<span>' + nComplete + ' completed</span>';
        html += '</div>';
    }

    // Map goal IDs to their index in _goalData for expandGoal()
    const goalIdToIdx = {};
    (goals || []).forEach((g, i) => { goalIdToIdx[g.id] = i; });

    // Map services to goals — so goal cards can show "Open" buttons
    const servicesByGoalId = {};
    (services || []).forEach(svc => {
        if (svc.goal_id) {
            if (!servicesByGoalId[svc.goal_id]) servicesByGoalId[svc.goal_id] = [];
            servicesByGoalId[svc.goal_id].push(svc);
        }
    });

    // Active goals as full cards
    activeGoals.forEach((g, gi) => {
        const idx = goalIdToIdx[g.id] !== undefined ? goalIdToIdx[g.id] : gi;
        const phases = g.phases || [];
        const done = phases.filter(p => p.status === 'done' || p.status === 'done_unverified').length;
        const total = phases.length;
        const failed = phases.filter(p => p.status === 'failed').length;
        const running = phases.filter(p => p.status === 'running').length;
        const pct = total > 0 ? Math.round(done / total * 100) : 0;
        const isComplete = done === total && total > 0;
        const isActive = !isComplete && !failed;
        const cardClass = isComplete ? 'complete' : failed ? 'failed' : 'active-goal';
        const statusColor = isComplete ? 'var(--green)' : g.status === 'operating' ? 'var(--cyan)' : failed ? 'var(--red)' : 'var(--purple)';
        // Enforce: active goals always expanded, completed always collapsed (unless user toggled)
        if (isActive) _collapsedGoals.delete(idx);
        if (isComplete && !_expandedResources.has('gphases-shown-' + idx)) _collapsedGoals.add(idx);
        const statusText = isComplete ? 'COMPLETE' : g.status || 'active';
        const ringColor = isComplete ? '#43e97b' : failed ? '#f85149' : '#a855f7';

        // Group resources for this goal
        const goalRes = (resources || []).filter(r => r.goal_id === g.id);
        const containers = goalRes.filter(r => r.type === 'container');
        const files = goalRes.filter(r => r.type === 'file');

        html += '<div class="goal-card ' + cardClass + '" id="gcard-' + idx + '" style="position:relative">';
        html += '<button class="expand-btn" onclick="event.stopPropagation();expandGoal(' + idx + ')" title="Expand">&#x2922;</button>';

        // Header with ring
        html += '<div class="goal-card-header" onclick="toggleGoalCard(' + idx + ')">';
        html += '<div class="goal-card-ring"><svg viewBox="0 0 48 48">';
        html += '<circle class="ring-bg" cx="24" cy="24" r="20"/>';
        const dashOffset = RING_CIRC * (1 - pct / 100);
        html += '<circle class="ring-fill" cx="24" cy="24" r="20" stroke="' + ringColor + '" stroke-dasharray="' + RING_CIRC.toFixed(1) + '" stroke-dashoffset="' + dashOffset.toFixed(1) + '" style="' + (running ? 'animation:ringPulse 2s infinite' : '') + '"/>';
        html += '<text class="ring-text" x="24" y="24">' + done + '/' + total + '</text>';
        html += '</svg></div>';
        html += '<div class="goal-card-title">';
        html += '<h3 title="' + esc(g.description || '') + '">' + esc(extractTitle(g.description || '')) + '</h3>';
        const tokenCount = g._total_tokens || 0;
        const tokenLabel = tokenCount > 1000 ? Math.round(tokenCount / 1000) + 'K tokens' : '';
        html += '<span class="goal-status" style="color:' + statusColor + '">' + statusText + ' &middot; ' + done + '/' + total + (tokenLabel ? ' &middot; ' + tokenLabel : '') + '</span>';
        // Show what's happening RIGHT NOW for active goals
        if (!isComplete) {
            const currentPhase = phases.find(p => p.status === 'running' || p.status === 'retrying') || phases.find(p => p.status === 'pending');
            if (currentPhase) {
                html += '<div style="font-size:10px;color:var(--cyan);margin-top:2px">&#x25B6; ' + esc(currentPhase.name || '').replace(/_/g, ' ') + '</div>';
            }
        }
        html += '</div>';
        // Cancel button for stale/failed goals
        if (g.status === 'stale' || failed > 0) {
            html += '<button onclick="event.stopPropagation();cancelGoal(\'' + esc(g.id) + '\')" style="background:none;border:1px solid var(--red);color:var(--red);border-radius:6px;padding:3px 10px;font-size:10px;cursor:pointer;white-space:nowrap" title="Stop this goal from retrying">Cancel</button>';
        }
        html += '</div>';

        // === COMPLETION SUMMARY (Pattern 3) — show result + next steps for completed goals ===
        if (isComplete) {
            // Use completion_summary from goal_runner if available, else extract from last phase
            const lastResult = g.completion_summary || phases.filter(p => p.result).map(p => cleanPhaseResult(p.result)).pop() || '';
            const desc = (g.description || '').toLowerCase();
            // Generate context-aware next-step chips
            let nextChips = '';
            if (desc.includes('support') || desc.includes('ticket')) {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'send a test support ticket\')">Send test ticket</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'connect email to support system\')">Connect email</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'import past tickets from CSV\')">Import tickets</span>';
            } else if (desc.includes('sales') || desc.includes('crm')) {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'import leads from CSV\')">Import leads</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'set up sales email automation\')">Email automation</span>';
            } else if (desc.includes('knowledge') || desc.includes('document')) {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'upload documents to knowledge base\')">Upload docs</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'search the knowledge base\')">Search</span>';
            } else if (desc.includes('devops') || desc.includes('monitor') || desc.includes('ci/cd')) {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'trigger a test build\')">Test build</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'configure alerting\')">Set up alerts</span>';
            } else if (desc.includes('finance') || desc.includes('expense') || desc.includes('invoice')) {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'add a test expense\')">Add expense</span>' +
                    '<span class="prompt-chip" onclick="quickCmd(\'generate monthly report\')">Generate report</span>';
            } else {
                nextChips = '<span class="prompt-chip" onclick="quickCmd(\'what can I do with this?\')">What next?</span>';
            }
            // Service health from goal_runner's verify loop
            const sh = g.service_health || 'unknown';
            const shDetail = g.service_health_detail || '';
            let healthColor = 'var(--text2)', healthIcon = '&#9675;', healthLabel = 'No services';
            if (sh === 'up') { healthColor = 'var(--green)'; healthIcon = '&#9679;'; healthLabel = 'Services running'; }
            else if (sh === 'degraded') { healthColor = 'var(--yellow)'; healthIcon = '&#9679;'; healthLabel = 'Partially running'; }
            else if (sh === 'down') { healthColor = 'var(--red)'; healthIcon = '&#9679;'; healthLabel = 'Services down'; }
            else if (sh === 'no_services') { healthColor = 'var(--text2)'; healthIcon = '&#9675;'; healthLabel = 'No services to monitor'; }
            const bgColor = sh === 'down' ? 'rgba(248,81,73,0.06)' : 'rgba(67,233,123,0.06)';
            const borderColor = sh === 'down' ? 'rgba(248,81,73,0.15)' : 'rgba(67,233,123,0.15)';

            // Service URLs — the "Open" button for this goal's services
            const goalSvcs = servicesByGoalId[g.id] || [];
            html += '<div style="padding:10px 16px;background:' + bgColor + ';border-bottom:1px solid ' + borderColor + '">';
            // Service links — prominent "Open" buttons
            if (goalSvcs.length > 0) {
                html += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px">';
                goalSvcs.forEach(svc => {
                    const svcColor = svc.status === 'healthy' ? 'var(--green)' : svc.status === 'crashed' ? 'var(--red)' : 'var(--yellow)';
                    const svcUrl = svc.url || (svc.port ? 'http://localhost:' + svc.port : '');
                    if (svcUrl) {
                        html += '<a href="' + esc(svcUrl) + '" target="_blank" style="display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:rgba(74,222,128,0.1);border:1px solid rgba(74,222,128,0.3);border-radius:8px;color:var(--green);font-size:12px;font-weight:600;text-decoration:none;cursor:pointer;transition:all 0.15s" onmouseover="this.style.background=\'rgba(74,222,128,0.2)\'" onmouseout="this.style.background=\'rgba(74,222,128,0.1)\'">';
                        html += '<span style="color:' + svcColor + '">&#9679;</span> ';
                        html += '&#128279; ' + esc(svc.name || 'Service') + ' :' + (svc.port || '?');
                        html += '</a>';
                    }
                    if (svc.status === 'crashed') {
                        html += '<button onclick="fetch(\'/api/services/' + esc(svc.name) + '/restart\',{method:\'POST\'}).then(()=>refreshDesktop())" style="padding:6px 12px;background:rgba(248,113,113,0.1);border:1px solid rgba(248,113,113,0.3);border-radius:8px;color:var(--red);font-size:11px;font-weight:600;cursor:pointer">Restart</button>';
                    }
                });
                html += '</div>';
            }
            // Health status line
            html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">';
            html += '<span style="font-size:12px;font-weight:600;color:' + (sh === 'down' ? 'var(--red)' : 'var(--green)') + '">' + (sh === 'down' ? 'Service Down' : 'Ready!') + '</span>';
            html += '<span style="font-size:10px;color:' + healthColor + '">' + healthIcon + ' ' + healthLabel + (shDetail ? ' (' + esc(shDetail) + ')' : '') + '</span></div>';
            if (lastResult) html += '<div style="font-size:12px;color:var(--text);margin-bottom:8px">' + esc(lastResult) + '</div>';
            html += '<div style="display:flex;gap:6px;flex-wrap:wrap">' + nextChips + '</div>';
            html += '</div>';
        }

        // === NEEDS HELP section for failed/stale goals ===
        if (!isComplete && (failed > 0 || g.status === 'stale')) {
            const failedPhase = phases.find(p => p.status === 'failed' || p.status === 'retrying');
            const errorMsg = failedPhase ? cleanPhaseResult(failedPhase.result || '') : 'Goal is stuck';
            html += '<div style="padding:10px 16px;background:rgba(248,81,73,0.06);border-bottom:1px solid rgba(248,81,73,0.15)">';
            html += '<div style="font-size:12px;font-weight:600;color:var(--red);margin-bottom:4px">Needs your help</div>';
            html += '<div style="font-size:11px;color:var(--text2);margin-bottom:6px">' + esc(errorMsg || 'A phase is stuck. Expand to see details.') + '</div>';
            html += '<div style="display:flex;gap:6px;flex-wrap:wrap">';
            html += '<span class="prompt-chip" onclick="quickCmd(\'retry ' + esc((g.description||'').slice(0,30)) + '\')">Retry</span>';
            html += '<span class="prompt-chip" onclick="quickCmd(\'skip the failed phase and continue\')">Skip phase</span>';
            html += '</div></div>';
        }

        // Phases (collapsible) — auto-collapsed for completed, expanded for active
        const collapsed = _collapsedGoals.has(idx);
        html += '<div class="goal-card-phases" id="gphases-' + idx + '"' + (collapsed ? ' style="display:none"' : '') + '>';
        phases.forEach((p, pi) => {
            let pColor = 'var(--text2)', pIcon = '&#9675;';
            if (p.status === 'done') { pColor = 'var(--green)'; pIcon = '&#10003;'; }
            else if (p.status === 'done_unverified') { pColor = 'var(--yellow)'; pIcon = '&#9888;'; }
            else if (p.status === 'retrying') { pColor = 'var(--red)'; pIcon = '&#8635;'; }
            else if (p.status === 'failed') { pColor = 'var(--red)'; pIcon = '&#10007;'; }
            else if (p.status === 'running') { pColor = 'var(--cyan)'; pIcon = '&#9881;'; }
            html += '<div class="goal-phase" style="flex-wrap:wrap">';
            html += '<span class="goal-phase-icon" style="color:' + pColor + '">' + pIcon + '</span>';
            html += '<span class="goal-phase-name" style="color:' + pColor + '">' + esc(p.name || '') + '</span>';
            html += '<span class="goal-phase-status">' + (p.status || 'pending') + '</span>';
            // Phase result — extract clean summary, hide raw markdown
            if (p.result) {
                const summary = cleanPhaseResult(p.result);
                if (summary) {
                    html += '<div style="width:100%;padding-left:24px;font-size:11px;color:var(--text2);margin-top:2px;line-height:1.4;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:help" title="' + esc(p.result.replace(/\n/g, ' ').slice(0, 300)) + '">' + esc(summary) + '</div>';
                }
            }
            // Daemon spawned
            if (p.creates_hand) {
                html += '<div style="width:100%;padding-left:24px;font-size:10px;color:var(--purple);margin-top:1px">&#128268; Daemon: ' + esc(p.creates_hand) + '</div>';
            }
            html += '</div>';
        });

        // Progress bar
        const barColor = isComplete ? 'var(--green)' : failed ? 'var(--red)' : 'var(--purple)';
        html += '<div style="height:3px;background:rgba(255,255,255,0.06);border-radius:2px;margin-top:6px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + barColor + ';border-radius:2px;transition:width 0.5s"></div></div>';
        html += '</div>';

        // Footer — grouped resources with cross-linking
        const resId = 'gres-' + idx;
        html += '<div class="goal-card-footer" style="cursor:pointer" onclick="toggleResources(\'' + resId + '\')">';
        if (containers.length) {
            const up = containers.filter(c => c.status === 'active').length;
            html += '<span style="color:' + (up === containers.length ? 'var(--green)' : 'var(--yellow)') + '">&#9650; ' + up + '/' + containers.length + ' containers</span>';
        }
        if (files.length) {
            // Group files by purpose
            // Group files — check frontend FIRST (more specific), then config, then API
            const frontendFiles = files.filter(f => (f.name||'').match(/\.html$|\.css$|static|template|frontend|index\./i));
            const configFiles = files.filter(f => !frontendFiles.includes(f) && (f.name||'').match(/config|\.env$|\.yml$|\.yaml$|schema|requirements|setup\./i));
            const apiFiles = files.filter(f => !frontendFiles.includes(f) && !configFiles.includes(f) && (f.name||'').match(/api|app\.py|server|route|flask|handler/i));
            const otherFiles = files.filter(f => !apiFiles.includes(f) && !configFiles.includes(f) && !frontendFiles.includes(f));
            const groups = [];
            if (apiFiles.length) groups.push(apiFiles.length + ' API');
            if (frontendFiles.length) groups.push(frontendFiles.length + ' frontend');
            if (configFiles.length) groups.push(configFiles.length + ' config');
            if (otherFiles.length) groups.push(otherFiles.length + ' other');
            html += '<span>&#128196; ' + (groups.length ? groups.join(', ') : files.length + ' files') + '</span>';
        }
        if (!containers.length && !files.length) html += '<span>' + (g.strategy || 'sequential') + '</span>';
        html += '</div>';

        // Resource detail (hidden by default) — grouped + cross-linked
        if (goalRes.length) {
            html += '<div id="' + resId + '" style="display:none;padding:8px 16px 12px;border-top:1px solid rgba(35,45,63,0.3)">';
            if (containers.length) {
                html += '<div style="font-size:10px;font-weight:600;color:var(--text2);margin-bottom:4px">SERVICES</div>';
                containers.forEach(c => {
                    const cColor = c.status === 'active' ? 'var(--green)' : 'var(--red)';
                    // Cross-link: find which phase created this container
                    const linkedPhase = phases.find(p => (p.result||'').toLowerCase().includes((c.name||'').split('_')[0].toLowerCase()));
                    html += '<div style="font-size:11px;padding:3px 0;display:flex;justify-content:space-between;align-items:center">';
                    html += '<span><span style="color:' + cColor + '">&#9679;</span> ' + esc(c.name) + '</span>';
                    html += '<span style="font-size:10px;color:var(--text2)">' + (linkedPhase ? 'from ' + linkedPhase.name : c.status) + '</span>';
                    html += '</div>';
                });
            }
            if (files.length) {
                // Group files by type
                const groups = {};
                files.forEach(f => {
                    const name = f.name || '';
                    let group = 'Other';
                    if (name.match(/\.html$|\.css$|static|template|frontend|index\./i)) group = 'Frontend';
                    else if (name.match(/config|\.env$|\.yml$|\.yaml$|schema|requirements|setup\./i)) group = 'Config';
                    else if (name.match(/api|app\.py|server|route|flask|handler/i)) group = 'API';
                    else if (name.match(/test|spec/i)) group = 'Tests';
                    if (!groups[group]) groups[group] = [];
                    groups[group].push(f);
                });
                Object.entries(groups).forEach(([group, gFiles]) => {
                    html += '<div style="font-size:10px;font-weight:600;color:var(--text2);margin-top:6px;margin-bottom:2px">' + group.toUpperCase() + ' (' + gFiles.length + ')</div>';
                    gFiles.slice(0, 4).forEach(f => {
                        const short = (f.name || '').split('/').slice(-1)[0];
                        html += '<div style="font-size:11px;padding:1px 0;padding-left:8px;color:var(--text2)">&#128196; ' + esc(short) + '</div>';
                    });
                    if (gFiles.length > 4) html += '<div style="font-size:10px;padding-left:8px;color:var(--text2)">+' + (gFiles.length - 4) + ' more</div>';
                });
            }
            const others = goalRes.filter(r => r.type !== 'container' && r.type !== 'file');
            if (others.length) {
                html += '<div style="font-size:10px;font-weight:600;color:var(--text2);margin-top:6px;margin-bottom:2px">OTHER (' + others.length + ')</div>';
                others.forEach(r => {
                    html += '<div style="font-size:11px;padding:1px 0;padding-left:8px;color:var(--text2)">' + esc(r.name) + ' <span style="color:var(--text2)">(' + r.type + ')</span></div>';
                });
            }
            html += '</div>';
        }
        html += '</div>';
    });

    // ── Services (from completed goals that deployed something) — OS shows what's RUNNING ──
    const allServices = dedupeServices(services || []);
    const goalServices = completedGoals.filter(g => g.service_health && g.service_health !== 'no_services' && g.service_health !== 'unknown');
    if (goalServices.length > 0 || allServices.length > 0) {
        html += '<div class="special-card" style="grid-column:1/-1">';
        html += '<div class="sc-header"><span style="font-size:14px">&#127760;</span> Running Services</div>';
        html += '<div class="sc-body" style="padding:8px 16px">';
        goalServices.forEach((g, ci) => {
            const sh = g.service_health || 'unknown';
            const shColor = sh === 'up' ? 'var(--green)' : sh === 'down' ? 'var(--red)' : 'var(--yellow)';
            const shIcon = sh === 'up' ? '&#9679;' : sh === 'down' ? '&#9679;' : '&#9675;';
            const shLabel = sh === 'up' ? 'Healthy' : sh === 'down' ? 'Down' : 'Unknown';
            const gIdx = goalIdToIdx[g.id] !== undefined ? goalIdToIdx[g.id] : ci;
            html += '<div style="display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--border);cursor:pointer" onclick="expandGoal(' + gIdx + ')">';
            html += '<span style="color:' + shColor + ';font-size:12px">' + shIcon + '</span>';
            html += '<span style="flex:1;font-size:13px;color:var(--text);font-weight:500">' + esc(extractTitle(g.description || '')) + '</span>';
            html += '<span style="font-size:11px;color:' + shColor + ';font-weight:600">' + shLabel + '</span>';
            html += '</div>';
        });
        if (!goalServices.length && allServices.length) {
            allServices.forEach(s => {
                const sColor = s.status === 'healthy' || s.status === 'running' ? 'var(--green)' : s.status === 'needs_user' ? 'var(--yellow)' : 'var(--red)';
                html += '<div style="display:flex;align-items:center;gap:10px;padding:6px 4px;font-size:12px">';
                html += '<span style="color:' + sColor + '">&#9679;</span>';
                html += '<span style="flex:1;color:var(--text)">' + esc(cleanServiceName(s.name || s.container || '')) + '</span>';
                html += '<span style="color:' + sColor + ';margin-left:auto">' + statusLabel(s.status) + '</span>';
                if (s.status === 'needs_user') html += '<button onclick="quickCmd(\'help me set up ' + esc(cleanServiceName(s.name || '')) + '\')" style="background:var(--yellow);color:#000;border:none;border-radius:6px;padding:2px 10px;font-size:10px;font-weight:600;cursor:pointer;margin-left:6px">Get Help</button>';
                html += '</div>';
            });
        }
        html += '</div></div>';
    }

    // ── Completed goals: collapsed history (click to expand) ──
    if (completedGoals.length > 0) {
        html += '<div style="grid-column:1/-1">';
        html += '<div onclick="var el=document.getElementById(\'completed-list\');el.style.display=el.style.display===\'none\'?\'\':\'none\';this.querySelector(\'span\').textContent=el.style.display===\'none\'?\'\\u25B6\':\'\\u25BC\'" style="cursor:pointer;display:flex;align-items:center;gap:8px;padding:6px 4px;font-size:11px;color:var(--text-dim);user-select:none">';
        html += '<span>&#9654;</span> ' + completedGoals.length + ' completed tasks (history)';
        html += '</div>';
        html += '<div id="completed-list" style="display:none">';
        completedGoals.forEach((g, ci) => {
            const phases = g.phases || [];
            const done = phases.filter(p => p.status === 'done' || p.status === 'done_unverified').length;
            const total = phases.length;
            const idx = goalIdToIdx[g.id] !== undefined ? goalIdToIdx[g.id] : ci;
            html += '<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;font-size:11px;color:var(--text2);cursor:pointer" onmouseover="this.style.color=\'var(--text)\'" onmouseout="this.style.color=\'var(--text2)\'" onclick="expandGoal(' + idx + ')">';
            html += '<span style="color:var(--green)">&#10003;</span> ';
            html += '<span style="flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">' + esc(g.description || '') + '</span>';
            html += '<span>' + done + '/' + total + '</span>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // Vitals card
    html += '<div class="vitals-card" id="vitals-card" style="position:relative">';
    html += '<button class="expand-btn" onclick="expandVitals()" title="Expand">&#x2922;</button>';
    html += '<div style="font-size:10px;text-transform:uppercase;color:var(--text2);letter-spacing:0.5px;font-weight:600;margin-bottom:8px">System Vitals</div>';
    html += '<div class="vitals-row">';
    html += '<div class="vital-item"><div class="vital-label">CPU</div><div class="vital-bar"><div class="vital-bar-fill" id="vb-cpu" style="width:0%;background:linear-gradient(90deg,var(--blue),var(--cyan))"></div></div><div class="vital-val" id="vv-cpu">-</div></div>';
    html += '<div class="vital-item"><div class="vital-label">RAM</div><div class="vital-bar"><div class="vital-bar-fill" id="vb-ram" style="width:0%;background:linear-gradient(90deg,var(--green),var(--green2))"></div></div><div class="vital-val" id="vv-ram">-</div></div>';
    html += '<div class="vital-item"><div class="vital-label">Disk</div><div class="vital-bar"><div class="vital-bar-fill" id="vb-disk" style="width:0%;background:linear-gradient(90deg,var(--purple),var(--red))"></div></div><div class="vital-val" id="vv-disk">-</div></div>';
    html += '</div></div>';

    // What I Learned card
    if (learned.length) {
        html += '<div class="special-card" id="learned-card" style="position:relative">';
        html += '<button class="expand-btn" onclick="expandLearned()" title="Expand">&#x2922;</button>';
        html += '<div class="sc-header"><span style="font-size:14px">&#x1F4A1;</span> What I Learned</div>';
        html += '<div class="sc-body">';
        learned.slice(0, 8).forEach(p => {
            let text = p.what_worked || p.principle || p.recommendation || p.reason || '';
            // Strip fleet sync prefixes like [from 19a97d76]
            text = text.replace(/\[from [a-f0-9]+\]\s*/gi, '').trim();
            if (!text) return;
            const conf = p.confidence || 1.0;
            const confColor = conf >= 1.0 ? 'var(--green)' : conf >= 0.5 ? 'var(--yellow)' : 'var(--red)';
            html += '<div class="learned-item">';
            html += '<div style="color:var(--text)">' + esc(text.slice(0, 120)) + '</div>';
            html += '<div style="margin-top:3px;font-size:10px;color:var(--text2)">';
            if (p.applies_when) html += 'matches: ' + esc(p.applies_when) + ' &middot; ';
            html += 'env: ' + esc(p.environment_match || 'any');
            html += '<span class="learned-conf" style="color:' + confColor + '">' + conf.toFixed(1) + '</span>';
            html += '</div></div>';
        });
        html += '</div></div>';
    }

    // Evolution card (mini)
    html += '<div class="special-card" id="evo-card" style="display:none;position:relative">';
    html += '<button class="expand-btn" onclick="expandEvolution()" title="Expand">&#x2922;</button>';
    html += '<div class="sc-header"><span style="font-size:14px">&#x2B50;</span> Evolution <span id="evo-badge" style="margin-left:auto;font-size:10px;color:var(--text2)"></span></div>';
    html += '<div class="sc-body" id="evo-card-body"></div>';
    html += '</div>';

    // Activity feed card (live events)
    html += '<div class="special-card" id="activity-card" style="position:relative">';
    html += '<button class="expand-btn" onclick="expandActivity()" title="Expand">&#x2922;</button>';
    html += '<div class="sc-header"><span style="font-size:14px">&#x26A1;</span> Activity <span id="event-count-badge" style="margin-left:auto;font-size:10px;color:var(--text2)">' + eventCount + ' events</span></div>';
    html += '<div class="sc-body" id="activity-feed" style="max-height:200px">';
    html += '<div style="color:var(--text2);font-size:11px;text-align:center;padding:12px">Listening for events...</div>';
    html += '</div></div>';

    // Only re-render if data actually changed (prevents destroying clickable elements)
    const newHash = html.length + '_' + goals.length + '_' + goals.map(g => g.status + (g.phases||[]).map(p => p.status).join('')).join('|');
    if (desktop.dataset.hash === newHash) {
        // Data unchanged — just update vitals/activity without destroying DOM
        return;
    }
    desktop.dataset.hash = newHash;
    desktop.innerHTML = html;

    // Restore collapsed/expanded state after re-render
    _collapsedGoals.forEach(idx => {
        const el = document.getElementById('gphases-' + idx);
        if (el) el.style.display = 'none';
    });
    _expandedResources.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = '';
    });
}

function toggleResources(id) {
    const el = document.getElementById(id);
    if (el) {
        const show = el.style.display === 'none';
        el.style.display = show ? '' : 'none';
        if (show) _expandedResources.add(id); else _expandedResources.delete(id);
    }
}

function toggleGoalCard(idx) {
    const phases = document.getElementById('gphases-' + idx);
    if (phases) {
        const hide = phases.style.display !== 'none';
        phases.style.display = hide ? 'none' : '';
        if (hide) { _collapsedGoals.add(idx); _expandedResources.delete('gphases-shown-' + idx); }
        else { _collapsedGoals.delete(idx); _expandedResources.add('gphases-shown-' + idx); }
    }
}

/* ═══════════════════════════════════════════════════════════════════
   DOCK — Running Daemons
   ═══════════════════════════════════════════════════════════════════ */

function renderDock(daemons) {
    const container = document.getElementById('dock-daemons');
    if (!container) return;

    // Build dock hash including goals for change detection
    const activeGoalNames = (_goalData || []).filter(g => g.status === 'active' || g.status === 'operating' || g.status === 'planning').map(g => g.id + ':' + g.status);
    const dockHash = daemons.map(d => d.name + ':' + d.status).join('|') + '|G:' + activeGoalNames.join(',');
    if (container.dataset.hash === dockHash) return;
    container.dataset.hash = dockHash;

    let html = '';

    // ── ACTIVE GOALS first — these are the "running apps" in the taskbar ──
    const dockGoalIdx = {};
    (_goalData || []).forEach((g, i) => { dockGoalIdx[g.id] = i; });
    const activeGoals = (_goalData || []).filter(g => g.status === 'active' || g.status === 'operating' || g.status === 'planning');
    activeGoals.forEach((g, gi) => {
        const idx = dockGoalIdx[g.id] !== undefined ? dockGoalIdx[g.id] : gi;
        const phases = g.phases || [];
        const done = phases.filter(p => p.status === 'done' || p.status === 'done_unverified').length;
        const total = phases.length;
        const running = phases.find(p => p.status === 'running');
        const shortDesc = extractTitle(g.description || '').slice(0, 30) + (extractTitle(g.description || '').length > 30 ? '...' : '');
        html += '<div class="dock-item" onclick="expandGoal(' + idx + ')" title="' + esc(g.description || '') + '" style="background:rgba(155,122,237,0.1);border:1px solid rgba(155,122,237,0.3);font-weight:600">';
        html += '<span class="dock-icon" style="font-size:12px">' + done + '/' + total + '</span>';
        html += '<span class="dock-label" style="color:var(--text);max-width:160px">' + esc(shortDesc) + '</span>';
        if (running) html += '<span style="display:inline-block;width:6px;height:6px;background:var(--cyan);border-radius:50%;animation:dotPulse 1s infinite;margin-left:4px"></span>';
        else html += '<span class="dock-dot running"></span>';
        html += '</div>';
    });

    // ── Separator if goals exist ──
    if (activeGoals.length > 0 && daemons.length > 0) {
        html += '<div class="dock-sep"></div>';
    }

    // ── System daemons — compact, clickable for actions ──
    const SYSTEM = new Set(['goal_runner', 'gc', 'researcher', 'monitor', 'digest', 'scheduler']);
    const system = daemons.filter(d => SYSTEM.has(d.name));
    const domain = daemons.filter(d => !SYSTEM.has(d.name));

    system.forEach(d => {
        const dotClass = d.status === 'running' ? 'running' : d.status === 'error' ? 'error' : 'stopped';
        html += '<div class="dock-item" onclick="toggleDaemonMenu(\'' + esc(d.name) + '\')" title="' + esc(d.description || d.name) + ' (click for actions)">' +
            '<span class="dock-icon">' + (d.icon || '&#x2699;') + '</span>' +
            '<span class="dock-label">' + esc(d.name) + '</span>' +
            '<span class="dock-dot ' + dotClass + '"></span></div>';
    });

    // ── Services pill ──
    if (domain.length > 0) {
        const running = domain.filter(d => d.status === 'running').length;
        html += '<div class="dock-group" onclick="toggleServicesPanel()" title="Domain services: ' + running + '/' + domain.length + ' running">' +
            '<span class="dock-icon">&#x1F310;</span>' +
            '<span class="dock-label">Services</span>' +
            '<span class="dock-count">' + running + '/' + domain.length + '</span>' +
            '<span class="dock-dot ' + (running > 0 ? 'running' : 'stopped') + '"></span></div>';
    }

    container.innerHTML = html;

    // Services pill from /api/services
    fetchJSON('/api/services').then(data => {
        const svcs = (data && data.services) || [];
        if (svcs.length > 0) {
            const healthy = svcs.filter(s => s.status === 'healthy').length;
            const dotClass = healthy === svcs.length ? 'running' : healthy > 0 ? 'running' : 'stopped';
            const pill = document.createElement('div');
            pill.className = 'dock-group';
            pill.onclick = toggleServicesPanel;
            pill.title = 'Deployed services: ' + healthy + '/' + svcs.length + ' healthy';
            pill.style.cursor = 'pointer';
            pill.innerHTML = '<span class="dock-icon">&#x1F310;</span>' +
                '<span class="dock-label">Services</span>' +
                '<span class="dock-count">' + healthy + '/' + svcs.length + '</span>' +
                '<span class="dock-dot ' + dotClass + '"></span>';
            container.appendChild(pill);
        }
    });
}

function toggleDockGroup(id) {
    const popup = document.getElementById(id);
    if (!popup) return;
    // Close all other popups first
    document.querySelectorAll('.dock-group-popup.active').forEach(p => {
        if (p.id !== id) p.classList.remove('active');
    });
    popup.classList.toggle('active');
}
// Close dock popups when clicking elsewhere
document.addEventListener('click', e => {
    if (!e.target.closest('.dock-group') && !e.target.closest('#services-panel')) {
        document.querySelectorAll('.dock-group-popup.active').forEach(p => p.classList.remove('active'));
        const sp = document.getElementById('services-panel');
        if (sp) sp.style.display = 'none';
    }
});

// ── Services Panel (OS-style popup from dock) ──
async function toggleServicesPanel() {
    let panel = document.getElementById('services-panel');
    if (panel && panel.style.display !== 'none') { panel.style.display = 'none'; return; }

    const data = await fetchJSON('/api/services');
    const services = (data && data.services) || [];
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'services-panel';
        document.body.appendChild(panel);
    }
    panel.style.cssText = 'position:fixed;bottom:52px;right:20px;width:380px;max-height:70vh;background:rgba(17,19,26,0.97);border:1px solid var(--border);border-radius:14px;backdrop-filter:blur(20px);box-shadow:0 -8px 40px rgba(0,0,0,0.5);z-index:200;display:flex;flex-direction:column;overflow:hidden;animation:slideUp 0.2s ease';

    // Filter out useless services, then deduplicate by port
    const usable = dedupeServices(services.filter(s => s.port && s.port !== 0 && s.port !== 'N/A'));

    // Group services by goal
    const byGoal = {};
    usable.forEach(s => {
        const gid = s.goal_id || 'unlinked';
        if (!byGoal[gid]) byGoal[gid] = [];
        byGoal[gid].push(s);
    });

    // Find goal descriptions
    const goalNames = {};
    (_goalData || []).forEach(g => { goalNames[g.id] = (g.description || '').slice(0, 50); });

    let html = '<div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">';
    html += '<span style="font-size:13px;font-weight:700;color:var(--text)">&#x1F310; Deployed Services</span>';
    html += '<button onclick="document.getElementById(\'services-panel\').style.display=\'none\'" style="background:none;border:none;color:var(--text2);cursor:pointer;font-size:16px">&times;</button>';
    html += '</div>';
    html += '<div style="overflow-y:auto;flex:1;padding:8px 16px">';

    if (!usable.length) {
        html += '<div style="color:var(--text2);font-size:12px;text-align:center;padding:20px">No services deployed yet.<br>Send a command to build something!</div>';
    }

    Object.entries(byGoal).forEach(([gid, svcs]) => {
        const goalName = goalNames[gid] ? extractTitle(goalNames[gid]) : (gid === 'unlinked' ? 'Standalone' : gid.slice(0, 20));
        html += '<div style="font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px;padding:10px 0 4px;border-bottom:1px solid rgba(35,45,63,0.3)">' + esc(goalName) + '</div>';

        svcs.forEach(svc => {
            const sColor = svc.status === 'healthy' || svc.status === 'running' ? 'var(--green)' : svc.status === 'crashed' ? 'var(--red)' : svc.status === 'needs_user' ? 'var(--yellow)' : svc.status === 'debugging' ? 'var(--yellow)' : 'var(--text2)';
            const sDot = svc.status === 'healthy' || svc.status === 'running' ? '&#9679;' : svc.status === 'crashed' ? '&#10007;' : svc.status === 'debugging' ? '&#8635;' : '&#9675;';

            html += '<div style="padding:8px 0;display:flex;align-items:flex-start;gap:10px">';
            html += '<span style="color:' + sColor + ';font-size:12px;margin-top:2px">' + sDot + '</span>';
            html += '<div style="flex:1;min-width:0">';
            html += '<div style="font-size:12px;font-weight:600;color:var(--text)">' + esc(cleanServiceName(svc.name)) + '</div>';

            if (svc.port && svc.port !== 0 && svc.port !== 'N/A') {
                html += '<a href="http://localhost:' + svc.port + '" target="_blank" style="font-size:11px;color:var(--blue);text-decoration:none;display:block;margin-top:2px">&#x1F517; localhost:' + svc.port + '</a>';
            }
            if (svc.credentials_hint) {
                html += '<div style="font-size:10px;color:var(--yellow);margin-top:2px">&#x1F511; ' + esc(svc.credentials_hint) + '</div>';
            }
            const uptime = svc.last_healthy ? 'since ' + svc.last_healthy.slice(11, 16) : '';
            html += '<div style="font-size:10px;color:' + sColor + ';margin-top:2px">' + statusLabel(svc.status);
            if (svc.restart_count > 0) html += ' &middot; ' + svc.restart_count + ' restarts';
            if (uptime) html += ' &middot; ' + uptime;
            html += '</div>';
            html += '</div>';

            // Action buttons
            html += '<div style="display:flex;gap:4px;flex-shrink:0;margin-top:2px">';
            if (svc.status === 'healthy' || svc.status === 'running') {
                html += '<button onclick="fetch(\'/api/services/' + encodeURIComponent(svc.name) + '/stop\',{method:\'POST\'}).then(()=>toggleServicesPanel())" style="background:none;border:1px solid var(--red);color:var(--red);border-radius:6px;padding:2px 8px;font-size:9px;cursor:pointer">Stop</button>';
            } else if (svc.status === 'needs_user') {
                html += '<button onclick="quickCmd(\'help me set up ' + esc(cleanServiceName(svc.name)) + '\')" style="background:var(--yellow);color:#000;border:none;border-radius:6px;padding:2px 8px;font-size:9px;font-weight:600;cursor:pointer">Get Help</button>';
            } else {
                html += '<button onclick="fetch(\'/api/services/' + encodeURIComponent(svc.name) + '/restart\',{method:\'POST\'}).then(()=>toggleServicesPanel())" style="background:none;border:1px solid var(--green);color:var(--green);border-radius:6px;padding:2px 8px;font-size:9px;cursor:pointer">Restart</button>';
            }
            html += '</div>';
            html += '</div>';
        });
    });

    html += '</div>';
    panel.innerHTML = html;
}

/* ═══════════════════════════════════════════════════════════════════
   COMMAND BAR + CHAT OVERLAY
   ═══════════════════════════════════════════════════════════════════ */

function onCmdFocus() {
    // Show chat overlay if there's history
    if (_chatHistory.length > 0) {
        openChatOverlay();
    }
}

function toggleChatOverlay() {
    const overlay = document.getElementById('chat-overlay');
    if (overlay.classList.contains('active')) {
        overlay.classList.remove('active');
    } else if (_chatHistory.length > 0) {
        overlay.classList.add('active');
    }
}

function openChatOverlay() {
    const overlay = document.getElementById('chat-overlay');
    overlay.classList.add('active');
}

function closeChatOverlay() {
    const overlay = document.getElementById('chat-overlay');
    overlay.classList.remove('active');
}
function clearChat() {
    _chatHistory = [];
    const area = document.getElementById('chat-messages');
    if (area) area.innerHTML = '';
    const badge = document.getElementById('chat-badge');
    if (badge) badge.remove();
    closeChatOverlay();
}

function quickCmd(cmd) {
    closeDetail(); // Close any open detail modal
    document.getElementById('os-cmd').value = cmd;
    runCommand();
    // Always show chat overlay for chip clicks — user needs feedback
    setTimeout(() => openChatOverlay(), 300);
}

async function runCommand() {
    const input = document.getElementById('os-cmd');
    const cmd = input.value.trim();
    if (!cmd) return;
    input.value = '';

    // Hide welcome screen but keep chips for quick actions
    const welcomeEl = document.getElementById('welcome-state');
    if (welcomeEl) welcomeEl.style.display = 'none';

    // Auto-open chat when there are no goals yet (user needs feedback on first command)
    // Once goals exist, the desktop cards ARE the primary feedback.
    if (!_goalData || _goalData.length === 0) {
        setTimeout(() => openChatOverlay(), 300);
    }
    const chatArea = document.getElementById('chat-messages');

    // Record in chat history (accessible via octopus icon)
    const userBubble = document.createElement('div');
    userBubble.className = 'chat-user';
    userBubble.textContent = cmd;
    chatArea.appendChild(userBubble);
    _chatHistory.push({role: 'user', text: cmd});

    // Show a toast instead of blocking overlay
    showToast('Command sent: ' + cmd.slice(0, 50) + (cmd.length > 50 ? '...' : ''), 'info');

    // Add thinking indicator in chat (user can open chat to see details)
    const thinkBubble = document.createElement('div');
    thinkBubble.className = 'chat-os';
    thinkBubble.innerHTML = '<div style="display:flex;align-items:center;gap:8px"><span style="display:inline-block;width:6px;height:6px;background:var(--cyan);border-radius:50%;animation:dotPulse 1s infinite"></span> Processing...</div><div id="live-events" style="margin-top:6px;font-size:11px;color:var(--text2)"></div>';
    chatArea.appendChild(thinkBubble);
    chatArea.scrollTop = chatArea.scrollHeight;

    const liveLines = [];
    function onLiveEvent(event) {
        let ev;
        try { ev = JSON.parse(event.data); } catch { return; }
        const t = ev.topic;
        const d = ev.data || {};
        let line = '';
        if (t === 'os.thinking') line = '\u{1F9E0} ' + (d.text || '').slice(0, 120);
        else if (t === 'os.sub_agent.spawned') line = '\u{1F680} Spawning: ' + (d.name||'') + ' \u2014 ' + (d.task||'').slice(0,80);
        else if (t === 'os.tool_call') line = '\u{1F527} ' + (d.tool||'') + '...';
        else if (t === 'os.tool_result' && d.ok) line = '\u2705 ' + (d.tool||'') + ': ' + (d.preview||'').slice(0,80);
        else if (t === 'os.tool_result' && !d.ok) line = '\u274C ' + (d.tool||'') + ' failed';
        else if (t === 'hand.goal_runner.goal_created') line = '\u{1F3AF} Goal: ' + (d.description||'').slice(0,60) + ' (' + (d.phases||'?') + ' phases)';
        else if (t === 'hand.goal_runner.phase_started') line = '\u{1F4CB} Phase: ' + (d.phase||'');
        else if (t === 'hand.goal_runner.phase_completed') line = (d.status==='done' ? '\u2705' : '\u274C') + ' Phase done: ' + (d.phase||'');
        if (line) {
            liveLines.push(line);
            if (liveLines.length > 10) liveLines.shift();
        }
        const liveEl = document.getElementById('live-events');
        if (liveEl && line) {
            liveEl.innerHTML = liveLines.slice(-4).map(l => '<div>' + esc(l) + '</div>').join('');
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    }
    let liveWs;
    try { liveWs = new WebSocket('ws://'+location.host+'/ws/events'); liveWs.onmessage = onLiveEvent; } catch(e) {}

    try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 300000);
        const res = await fetch('/api/os/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd}),
            signal: ctrl.signal,
        });
        clearTimeout(timer);
        const data = await res.json();
        thinkBubble.className = data.ok ? 'chat-os success' : 'chat-os error';
        let msg = data.message || JSON.stringify(data);
        let rendered = esc(msg)
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.1);padding:1px 4px;border-radius:3px;font-size:12px">$1</code>')
            .replace(/^#{3,}\s+(.+)$/gm, '<div style="font-size:12px;font-weight:700;color:var(--accent);margin-top:8px;margin-bottom:2px">$1</div>')
            .replace(/^##\s+(.+)$/gm, '<div style="font-size:13px;font-weight:700;color:var(--text);margin-top:10px;margin-bottom:4px;border-bottom:1px solid var(--border);padding-bottom:3px">$1</div>')
            .replace(/^#\s+(.+)$/gm, '<div style="font-size:15px;font-weight:700;color:var(--accent2);margin-top:10px;margin-bottom:6px">$1</div>')
            .replace(/^[-*] (.+)$/gm, '<div style="padding-left:12px">&#8226; $1</div>')
            .split('\n').map(line => {
                if (line.match(/^\|.+\|$/) && !line.match(/^\|[\s-:|]+\|$/)) {
                    const cells = line.split('|').filter(c => c.trim());
                    return '<div style="display:flex;gap:8px;font-size:12px;padding:1px 0">' +
                        cells.map((c, i) => '<span style="' + (i === 0 ? 'font-weight:600;min-width:100px' : 'flex:1;color:var(--text2)') + '">' + c.trim() + '</span>').join('') + '</div>';
                }
                if (line.match(/^\|[\s-:|]+\|$/)) return '';
                return line;
            }).join('<br>').replace(/(<br>){3,}/g, '<br><br>');
        let meta = '';
        if (data.data && data.data.turns) meta = '<div style="font-size:10px;color:var(--text2);margin-top:6px">' + data.data.turns + ' turn' + (data.data.turns !== 1 ? 's' : '') + ', ' + (data.data.tokens_used||0).toLocaleString() + ' tokens</div>';
        thinkBubble.innerHTML = rendered + meta;
        _chatHistory.push({role: 'os', text: msg});
    } catch(e) {
        thinkBubble.className = 'chat-os error';
        thinkBubble.innerHTML = e.name === 'AbortError' ? 'Timed out — check the desktop for progress.' : 'Failed: ' + esc(e.message);
    } finally {
        if (liveWs) try { liveWs.close(); } catch(e) {}
    }
    chatArea.scrollTop = chatArea.scrollHeight;
    // Refresh desktop after command
    refreshDesktop();
}

/* ── Voice Input ── */
let _mediaRecorder = null;
let _audioChunks = [];
let _isRecording = false;
let _audioStream = null;

async function toggleVoice() {
    const btn = document.getElementById('mic-btn');
    const input = document.getElementById('os-cmd');
    if (_isRecording && _mediaRecorder) { _mediaRecorder.stop(); return; }
    try { _audioStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch(e) { alert('Microphone access denied.'); return; }
    _audioChunks = [];
    _mediaRecorder = new MediaRecorder(_audioStream, { mimeType: 'audio/webm' });
    _mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) _audioChunks.push(e.data); };
    _mediaRecorder.onstart = () => { _isRecording = true; btn.classList.add('recording'); input.placeholder = 'Listening...'; };
    _mediaRecorder.onstop = async () => {
        _isRecording = false; btn.classList.remove('recording');
        _audioStream.getTracks().forEach(t => t.stop());
        if (!_audioChunks.length) { input.placeholder = 'Ask OpenSculpt anything...'; return; }
        input.placeholder = 'Transcribing...';
        const blob = new Blob(_audioChunks, { type: 'audio/webm' });
        try {
            const wavBlob = await webmToWav(blob);
            const form = new FormData(); form.append('audio', wavBlob, 'voice.wav');
            const res = await fetch('/api/voice/transcribe', { method: 'POST', body: form });
            const data = await res.json();
            input.placeholder = 'Ask OpenSculpt anything...';
            if (data.ok && data.text) { input.value = data.text; runCommand(); }
            else showToast('Voice: ' + (data.error || 'No speech detected'), 'warning');
        } catch(e) { input.placeholder = 'Ask OpenSculpt anything...'; showToast('Voice error: ' + e.message, 'error'); }
    };
    _mediaRecorder.start();
    setTimeout(() => { if (_isRecording && _mediaRecorder) _mediaRecorder.stop(); }, 15000);
}

async function webmToWav(webmBlob) {
    const arrayBuf = await webmBlob.arrayBuffer();
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await audioCtx.decodeAudioData(arrayBuf);
    const samples = decoded.getChannelData(0);
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeStr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
    writeStr(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeStr(8, 'WAVE'); writeStr(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, decoded.sampleRate, true);
    view.setUint32(28, decoded.sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    writeStr(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    audioCtx.close();
    return new Blob([buffer], { type: 'audio/wav' });
}

/* ═══════════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
   ═══════════════════════════════════════════════════════════════════ */

function showToast(msg, type) {
    const stack = document.getElementById('toast-stack');
    const el = document.createElement('div');
    el.className = 'toast ' + (type || 'info');
    const icons = { success:'\u2705', error:'\u274C', warning:'\u26A0\uFE0F', info:'\u2139\uFE0F' };
    el.innerHTML = '<span style="margin-right:6px">' + (icons[type]||'') + '</span>' + esc(msg);
    stack.appendChild(el);
    while (stack.children.length > 3) stack.firstChild.remove();
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.5s'; setTimeout(() => el.remove(), 500); }, 3000);
}

/* ═══════════════════════════════════════════════════════════════════
   DATA REFRESH — Main Loop
   ═══════════════════════════════════════════════════════════════════ */

async function refreshDesktop() {
    const [goals, resources, daemonData, changelog, status, vitals, servicesData] = await Promise.all([
        fetchJSON('/api/goals'),
        fetchJSON('/api/resources'),
        fetchJSON('/api/daemons'),
        fetchJSON('/api/evolution/changelog'),
        fetchJSON('/api/status'),
        fetchJSON('/api/vitals'),
        fetchJSON('/api/services'),
    ]);

    // Goals
    const goalList = goals ? (goals.goals || []) : [];
    _goalData = goalList;

    // Resources
    const resList = resources ? (resources.resources || []) : [];

    // Daemons
    const daemons = daemonData ? (daemonData.daemons || []) : [];
    _daemonData = daemons;

    // Learned principles
    let learned = [];
    if (changelog && changelog.recent_insights) {
        learned = changelog.recent_insights.filter(i =>
            i.outcome === 'success' && (i.what_worked || i.principle || i.recommendation || i.reason));
    }
    _learnedData = learned;

    // Services
    const servicesList = servicesData ? (servicesData.services || []) : [];

    // Render desktop
    renderDesktop(goalList, resList, daemons, learned, servicesList);

    // Update vitals in the desktop card
    if (vitals) {
        const cpu = document.getElementById('vb-cpu');
        const ram = document.getElementById('vb-ram');
        const disk = document.getElementById('vb-disk');
        if (cpu) cpu.style.width = vitals.cpu_percent + '%';
        if (ram) ram.style.width = vitals.mem_percent + '%';
        if (disk) disk.style.width = vitals.disk_percent + '%';
        const cpuV = document.getElementById('vv-cpu');
        const ramV = document.getElementById('vv-ram');
        const diskV = document.getElementById('vv-disk');
        if (cpuV) cpuV.textContent = vitals.cpu_percent + '%';
        if (ramV) ramV.textContent = vitals.mem_percent + '%';
        if (diskV) diskV.textContent = vitals.disk_percent + '%';
        // Dock vitals
        const dkCpu = document.getElementById('dk-cpu');
        const dkRam = document.getElementById('dk-ram');
        if (dkCpu) dkCpu.textContent = vitals.cpu_percent + '%';
        if (dkRam) dkRam.textContent = vitals.mem_percent + '%';
    }

    // Update status
    if (status) {
        document.getElementById('h-uptime').textContent = fmtUptime(status.uptime_s || 0);
        // Node count
        if (status.node_role) {
            const tbNodes = document.getElementById('tb-nodes');
            if (tbNodes) { tbNodes.textContent = status.node_role; tbNodes.style.display = ''; }
        }
    }

    // Status line — show what the OS is doing right now
    const statusLine = document.getElementById('status-line');
    if (statusLine) {
        const activeGoal = goalList.find(g => g.status === 'active' || g.status === 'operating');
        if (activeGoal) {
            const ap = (activeGoal.phases || []).find(p => p.status === 'running');
            const doneCnt = (activeGoal.phases || []).filter(p => p.status === 'done' || p.status === 'done_unverified').length;
            const totalCnt = (activeGoal.phases || []).length;
            if (ap) {
                statusLine.textContent = 'Working on: ' + ap.name + ' (' + doneCnt + '/' + totalCnt + ')';
                statusLine.style.color = 'var(--cyan)';
            } else {
                statusLine.textContent = activeGoal.description.slice(0, 60) + ' (' + doneCnt + '/' + totalCnt + ' phases)';
                statusLine.style.color = 'var(--text2)';
            }
        } else if (goalList.length) {
            const allDone = goalList.every(g => {
                const p = g.phases || [];
                return p.length > 0 && p.every(ph => ph.status === 'done' || ph.status === 'done_unverified');
            });
            statusLine.textContent = allDone ? 'All goals complete' : 'Idle';
            statusLine.style.color = 'var(--green)';
        } else {
            statusLine.textContent = '';
        }
    }

    // Render dock
    renderDock(daemons);

    // Evolution mini card
    if (changelog) {
        const evoCard = document.getElementById('evo-card');
        const evoBody = document.getElementById('evo-card-body');
        const evoBadge = document.getElementById('evo-badge');
        const demands = changelog.active_demands || [];
        const insights = changelog.recent_insights || [];
        if (demands.length || insights.length) {
            if (evoCard) evoCard.style.display = '';
            // Show useful evolution stats, not raw cycle count
            const realImprovements = (changelog.recent_insights || []).filter(i => i.outcome === 'success' && (i.what_worked || i.principle)).length;
            const evolvedFiles = (changelog.evolved_files || []).length;
            const totalDemands = (changelog.active_demands || []).length;
            if (evoBadge) {
                const parts = [];
                if (realImprovements) parts.push(realImprovements + ' learned');
                if (evolvedFiles) parts.push(evolvedFiles + ' tools');
                if (totalDemands) parts.push(totalDemands + ' demands');
                evoBadge.textContent = parts.length ? parts.join(' · ') : (changelog.cycles_completed || 0) + ' cycles';
            }
            let evoHtml = '';
            if (demands.length) {
                evoHtml += '<div style="font-size:10px;font-weight:600;color:var(--yellow);margin-bottom:6px">DEMANDS (' + demands.length + ')</div>';
                demands.slice(0, 3).forEach(d => {
                    const prioColor = d.priority > 0.7 ? 'var(--red)' : d.priority > 0.4 ? 'var(--yellow)' : 'var(--text2)';
                    // Human-readable demand summary instead of raw text
                    let summary = d.description || '';
                    if (summary.startsWith('Phase ')) summary = summary.replace(/^Phase '([^']+)' failed.*$/, 'Task "$1" needs retry');
                    if (summary.startsWith('IMPASSE')) summary = summary.replace(/^IMPASSE:.*Phase '([^']+)'.*$/, 'Stuck on "$1" — may need help');
                    if (summary.startsWith('Agent ')) summary = summary.replace(/^Agent '([^']+)' crashed.*$/, 'Worker "$1" crashed');
                    if (summary.startsWith('Command ')) summary = 'OS agent struggled with a command';
                    if (summary.startsWith('Tool ')) summary = summary.replace(/^Tool '([^']+)' failed.*$/, 'Tool "$1" had errors');
                    summary = summary.slice(0, 50);
                    evoHtml += '<div style="font-size:11px;padding:4px 0;border-bottom:1px solid rgba(35,45,63,0.3);color:var(--text2);display:flex;justify-content:space-between;gap:8px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(summary) + '</span><span style="color:' + prioColor + ';font-size:10px;font-weight:700;flex-shrink:0">' + (d.priority || '') + '</span></div>';
                });
            }
            if (insights.length) {
                evoHtml += '<div style="font-size:10px;font-weight:600;color:var(--cyan);margin-top:8px;margin-bottom:6px">RECENT</div>';
                insights.slice(0, 3).forEach(i => {
                    const icon = i.outcome === 'success' ? '\u2705' : '\u274C';
                    evoHtml += '<div style="font-size:11px;padding:3px 0;color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + icon + ' ' + esc(i.what).slice(0, 55) + '</div>';
                });
            }
            if (evoBody) evoBody.innerHTML = evoHtml;
        }
    }

    // Fetch and show evolution blockers
    const blockers = await fetchJSON('/api/evolution/blockers');
    if (blockers && blockers.blockers && blockers.blockers.length) {
        const evoBody2 = document.getElementById('evo-card-body');
        if (evoBody2) {
            let bHtml = '<div style="font-size:10px;font-weight:600;color:var(--yellow);margin-top:8px;margin-bottom:4px">NEEDS YOUR HELP (' + blockers.blockers.length + ')</div>';
            blockers.blockers.slice(0, 3).forEach((b, bi) => {
                bHtml += '<div style="font-size:11px;padding:4px 0;border-bottom:1px solid rgba(35,45,63,0.3);display:flex;justify-content:space-between;align-items:center;gap:6px">' +
                    '<span style="color:var(--yellow);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">&#9888; ' + esc(b.message).slice(0, 50) + '</span>' +
                    '<button onclick="dismissBlocker(' + bi + ')" style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:4px;padding:1px 6px;font-size:9px;cursor:pointer;flex-shrink:0">dismiss</button></div>';
            });
            evoBody2.innerHTML += bHtml;
        }
    }

    // Token/cost in topbar — REAL cost from actual API usage
    if (status) {
        const tokens = status.session_tokens || 0;
        const realCost = status.session_cost_usd || 0;
        const lifetimeCost = (status.lifetime_cost && status.lifetime_cost.total_usd) || 0;
        const totalCost = realCost + lifetimeCost;
        if (tokens > 0 || totalCost > 0) {
            const tbCost = document.getElementById('tb-cost');
            if (tbCost) {
                const tokStr = tokens >= 1000 ? (tokens / 1000).toFixed(0) + 'K' : tokens;
                const costStr = totalCost >= 0.01 ? '$' + totalCost.toFixed(2) : '$' + totalCost.toFixed(4);
                tbCost.textContent = tokStr + ' tok (' + costStr + ')';
                tbCost.title = 'Session: $' + realCost.toFixed(4) + ' | Lifetime: $' + lifetimeCost.toFixed(2) + ' | In: ' + (status.session_input_tokens||0) + ' | Out: ' + (status.session_output_tokens||0);
            }
        }
    }

    // Service monitoring indicator in topbar
    const tbServices = document.getElementById('tb-services');
    if (tbServices && goalList.length) {
        const monitored = goalList.filter(g => g.service_health && g.service_health !== 'no_services' && g.service_health !== 'unknown');
        const down = monitored.filter(g => g.service_health === 'down');
        if (monitored.length > 0) {
            if (down.length > 0) {
                const downNames = down.map(g => (g.description || '').split(' ').slice(0, 4).join(' ')).join(', ');
                tbServices.innerHTML = '<span style="color:var(--red)">&#9679;</span> ' + esc(downNames.slice(0, 40)) + ' down';
                tbServices.style.color = 'var(--red)';
                tbServices.title = downNames;
            } else {
                tbServices.innerHTML = '<span style="color:var(--green)">&#9679;</span> ' + monitored.length + ' services';
                tbServices.style.color = 'var(--green)';
            }
        } else {
            tbServices.textContent = '';
        }
    }

    // Chat badge
    if (_chatHistory.length > 0) {
        const cmdIcon = document.querySelector('.command-bar-inner > img') || document.querySelector('.command-bar-inner > span');
        if (cmdIcon && !document.getElementById('chat-badge')) {
            const badge = document.createElement('span');
            badge.id = 'chat-badge';
            badge.style.cssText = 'position:absolute;top:-2px;right:-2px;background:var(--purple);color:#fff;font-size:8px;font-weight:700;border-radius:50%;width:14px;height:14px;display:flex;align-items:center;justify-content:center';
            badge.textContent = _chatHistory.length;
            cmdIcon.style.position = 'relative';
            cmdIcon.appendChild(badge);
        }
        const existing = document.getElementById('chat-badge');
        if (existing) existing.textContent = _chatHistory.length;
    }
}

/* ═══════════════════════════════════════════════════════════════════
   WEBSOCKET — Live Events
   ═══════════════════════════════════════════════════════════════════ */

try {
    const ws = new WebSocket('ws://'+location.host+'/ws/events');
    ws.onmessage = e => {
        const ev = JSON.parse(e.data);
        eventCount++;
        const t = ev.topic || '';
        const d = ev.data || {};
        // Toast notifications for goal events
        if (t === 'hand.goal_runner.phase_started') showToast('Starting: ' + (d.phase||''), 'info');
        else if (t === 'hand.goal_runner.phase_completed') showToast('Completed: ' + (d.phase||'') + ' (' + (d.status||'done') + ')', d.status === 'done' ? 'success' : 'error');
        else if (t === 'hand.goal_runner.goal_created') showToast('Goal: ' + (d.description||'').slice(0,50) + ' (' + (d.phases||'?') + ' phases)', 'info');
        else if (t === 'hand.goal_runner.hand_created') showToast('Daemon: ' + (d.hand_name||''), 'success');
        else if (t === 'hand.goal_runner.phase_retrying') showToast('Retrying: ' + (d.phase||''), 'warning');
        else if (t === 'hand.goal_runner.goal_needs_help' || t === 'daemon.goal_runner.goal_needs_help' || t === 'goal_needs_help') {
            showToast('Needs help: ' + (d.phase||'') + ' — ' + (d.error||'').slice(0,60), 'error');
        }
        else if (t === 'hand.goal_runner.goal_completed' || t === 'daemon.goal_runner.goal_completed' || t === 'goal_completed') {
            showToast('Goal complete: ' + (d.description||'').slice(0,50), 'success');
        }
        // auto-share events removed
        // Auto-refresh on goal events
        if (t.startsWith('hand.goal_runner.')) refreshDesktop();
        // Activity feed — push events to the feed card
        addActivityEvent(t, d, ev.timestamp || '');
    };
} catch(err) {}

function addActivityEvent(topic, data, ts) {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;
    // Remove "listening" placeholder
    if (feed.children.length === 1 && feed.children[0].textContent.includes('Listening')) {
        feed.innerHTML = '';
    }
    const time = ts ? ts.slice(11, 19) : '';
    // Filter out noisy internal events
    if (topic.includes('network.dns') || topic.includes('disk.large_file') || topic.includes('network.self_check')) return;
    if (topic.includes('quality.') || topic.includes('codebase.')) return; // filter evolution code scan noise

    let icon = '', text = '', color = 'var(--text2)';
    if (topic.includes('phase_started')) { icon = '&#9654;'; text = 'Starting: ' + (data.phase || '').replace(/_/g, ' '); color = 'var(--cyan)'; }
    else if (topic.includes('phase_completed') && data.status === 'done') { icon = '&#10003;'; text = 'Completed: ' + (data.phase || '').replace(/_/g, ' '); color = 'var(--green)'; }
    else if (topic.includes('phase_completed')) { icon = '&#10007;'; text = 'Failed: ' + (data.phase || '').replace(/_/g, ' '); color = 'var(--red)'; }
    else if (topic.includes('phase_retrying')) { icon = '&#8635;'; text = 'Retrying: ' + (data.phase || '').replace(/_/g, ' '); color = 'var(--yellow)'; }
    else if (topic.includes('goal_created')) { icon = '&#x1F3AF;'; text = 'New goal: ' + (data.description || '').slice(0, 40); color = 'var(--purple)'; }
    else if (topic.includes('hand_created') || topic.includes('daemon')) { icon = '&#x2699;'; text = 'Daemon started: ' + (data.hand_name || data.name || ''); }
    else if (topic.includes('tool_call')) { icon = '&#x1F527;'; text = 'Using ' + (data.tool || ''); }
    else if (topic.includes('tool_result') && data.ok) { icon = '&#10003;'; text = (data.tool || '') + ' done'; color = 'var(--green)'; }
    else if (topic.includes('tool_result') && !data.ok) { icon = '&#10007;'; text = (data.tool || '') + ' failed'; color = 'var(--red)'; }
    else if (topic.includes('sub_agent.spawned')) { icon = '&#x1F680;'; text = 'Agent: ' + (data.name || ''); color = 'var(--cyan)'; }
    else if (topic.includes('sub_agent.done')) { icon = '&#10003;'; text = 'Agent finished: ' + (data.name || ''); color = 'var(--green)'; }
    else if (topic.includes('evolution')) { icon = '&#x2B50;'; text = 'Evolution: ' + topic.split('.').pop().replace(/_/g, ' '); }
    else if (topic.includes('agent.spawned')) { icon = '&#x1F680;'; text = 'Agent spawned'; }
    else if (topic.includes('agent.completed')) { icon = '&#10003;'; text = 'Agent completed'; }
    else if (topic.includes('sync')) { icon = '&#x1F310;'; text = 'Fleet sync'; }
    else if (topic.includes('goal_needs_help')) { icon = '&#9888;'; text = 'Needs help: ' + (data.phase || '').replace(/_/g, ' '); color = 'var(--yellow)'; }
    else if (topic.includes('goal_completed')) { icon = '&#10003;'; text = 'Goal done: ' + (data.description || '').slice(0, 40); color = 'var(--green)'; }
    else if (topic.includes('capability_gap')) { icon = '&#x26A0;'; text = 'Gap: ' + (data.tool || data.detail || '').slice(0, 40); color = 'var(--yellow)'; }
    else if (topic.includes('demand')) { icon = '&#x26A1;'; text = 'Demand: ' + (data.description || topic.split('.').pop()).slice(0, 40); }
    else if (topic.includes('os.thinking')) { icon = '&#x1F9E0;'; text = (data.text || '').slice(0, 40); color = 'var(--text2)'; }
    else if (topic.includes('resource')) { icon = '&#x1F4E6;'; text = 'Resource: ' + (data.name || '').slice(0, 30); }
    else { text = topic.split('.').slice(-2).join(' ').replace(/_/g, ' '); icon = '&#x2022;'; } // Show with readable name instead of skipping
    const el = document.createElement('div');
    el.style.cssText = 'font-size:11px;padding:3px 0;border-bottom:1px solid rgba(35,45,63,0.2);display:flex;gap:6px;align-items:baseline';
    el.innerHTML = '<span style="color:var(--text2);font-size:10px;font-family:monospace;min-width:55px">' + time + '</span><span>' + icon + '</span><span style="color:' + color + ';overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(text) + '</span>';
    feed.prepend(el);
    while (feed.children.length > 30) feed.lastChild.remove();
    // Update badge
    const badge = document.getElementById('event-count-badge');
    if (badge) badge.textContent = eventCount + ' events';
}

/* ── Daemon Popover ── */
async function toggleDaemonMenu(name) {
    // Remove existing popover if any
    const existing = document.getElementById('daemon-popover');
    if (existing) { existing.remove(); if (existing.dataset.daemon === name) return; }

    const daemon = _daemonData.find(d => d.name === name);
    if (!daemon) return;

    const isRunning = daemon.status === 'running';
    const actionLabel = isRunning ? 'Stop' : 'Start';
    const actionColor = isRunning ? 'var(--red)' : 'var(--green)';

    // Fetch last results
    let resultsHtml = '<div style="color:var(--text2);font-size:11px">No results yet</div>';
    try {
        const r = await fetchJSON('/api/daemons/' + name + '/results');
        if (r && r.results && r.results.length) {
            const last = r.results[r.results.length - 1];
            resultsHtml = '<div style="font-size:11px;color:' + (last.success ? 'var(--green)' : 'var(--red)') + '">' +
                (last.success ? '&#10003;' : '&#10007;') + ' ' + esc((last.summary || '').slice(0, 100)) + '</div>';
        }
    } catch(e) {}

    const pop = document.createElement('div');
    pop.id = 'daemon-popover';
    pop.dataset.daemon = name;
    pop.style.cssText = 'position:fixed;bottom:56px;left:50%;transform:translateX(-50%);width:320px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:14px;z-index:150;box-shadow:0 8px 32px rgba(0,0,0,0.5);animation:slideUp 0.2s ease';
    pop.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
        '<div><span style="font-size:16px;margin-right:6px">' + (daemon.icon || '') + '</span><strong>' + esc(daemon.name) + '</strong></div>' +
        '<span style="color:' + (isRunning ? 'var(--green)' : 'var(--text2)') + ';font-size:11px;font-weight:600">' + daemon.status.toUpperCase() + '</span></div>' +
        '<div style="font-size:12px;color:var(--text2);margin-bottom:8px">' + esc(daemon.description || '') + '</div>' +
        (daemon.ticks ? '<div style="font-size:10px;color:var(--text2);margin-bottom:6px">' + daemon.ticks + ' ticks</div>' : '') +
        '<div style="margin-bottom:10px">' + resultsHtml + '</div>' +
        '<div style="display:flex;gap:8px">' +
        '<button onclick="daemonAction(\'' + esc(name) + '\',\'' + (isRunning ? 'stop' : 'start') + '\')" style="flex:1;padding:6px;border:1px solid ' + actionColor + ';color:' + actionColor + ';background:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">' + actionLabel + '</button>' +
        '<button onclick="document.getElementById(\'daemon-popover\').remove()" style="padding:6px 12px;border:1px solid var(--border);color:var(--text2);background:none;border-radius:6px;cursor:pointer;font-size:12px">Close</button>' +
        '</div>';
    document.body.appendChild(pop);
    // Close on outside click
    setTimeout(() => document.addEventListener('click', function _closePop(e) {
        if (!pop.contains(e.target) && !e.target.closest('.dock-item')) { pop.remove(); document.removeEventListener('click', _closePop); }
    }), 100);
}

async function daemonAction(name, action) {
    const pop = document.getElementById('daemon-popover');
    if (pop) pop.remove();
    if (action === 'stop') {
        showToast('Stopping ' + name + '...', 'info');
        await fetch('/api/daemons/' + name + '/stop', { method: 'POST' });
        showToast(name + ' stopped', 'success');
    } else {
        showToast('Starting ' + name + '...', 'info');
        await fetch('/api/daemons/' + name + '/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({config: {}}) });
        showToast(name + ' started', 'success');
    }
    refreshDesktop();
}

/* ── Cancel Goal ── */
async function cancelGoal(goalId) {
    if (!confirm('Cancel this goal? It will stop retrying failed phases.')) return;
    const resp = await fetch('/api/goals/' + goalId + '/cancel', { method: 'POST' });
    const data = await resp.json();
    if (data.ok) showToast('Goal cancelled', 'success');
    else showToast('Failed: ' + (data.error || ''), 'error');
    refreshDesktop();
}

/* ── Dismiss Evolution Blocker ── */
async function dismissBlocker(idx) {
    await fetch('/api/evolution/blockers/' + idx + '/dismiss', { method: 'POST' });
    showToast('Blocker dismissed', 'success');
    refreshDesktop();
}

/* ── Expand/Detail Views ── */
function openDetail(title, bodyHtml) {
    document.getElementById('detail-title').textContent = title;
    document.getElementById('detail-body').innerHTML = bodyHtml;
    document.getElementById('detail-modal').classList.add('active');
}
function closeDetail() {
    document.getElementById('detail-modal').classList.remove('active');
}
// Close modals on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const detail = document.getElementById('detail-modal');
        if (detail && detail.classList.contains('active')) { closeDetail(); return; }
        const settings = document.getElementById('settings-modal');
        if (settings && settings.classList.contains('active')) { closeSettings(); return; }
        closeChatOverlay();
    }
});

async function checkServiceHealth(goalIdx) {
    const btn = document.getElementById('health-btn-' + goalIdx);
    if (!btn) return;
    btn.textContent = 'Checking...';
    btn.style.color = 'var(--text2)';
    try {
        // Ask the OS to check if the service is running
        const resp = await fetch('/api/os/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: 'check if the service from the completed goal is still running and healthy'}),
            signal: AbortSignal.timeout(30000),
        });
        const data = await resp.json();
        if (data.ok && data.message && (data.message.includes('running') || data.message.includes('healthy') || data.message.includes('200'))) {
            btn.textContent = 'Healthy';
            btn.style.color = 'var(--green)';
            btn.style.borderColor = 'var(--green)';
        } else {
            btn.textContent = 'Down';
            btn.style.color = 'var(--red)';
            btn.style.borderColor = 'var(--red)';
            showToast('Service may be down. Try: "restart the support system"', 'warning');
        }
    } catch(e) {
        btn.textContent = 'Error';
        btn.style.color = 'var(--red)';
    }
}

function expandGoal(idx) {
    const g = _goalData[idx];
    if (!g) return;
    const phases = g.phases || [];
    let html = '<div style="margin-bottom:16px;font-size:13px;color:var(--text)">' + esc(g.description || '') + '</div>';
    html += '<div style="font-size:11px;color:var(--text2);margin-bottom:12px">Status: <strong style="color:var(--green)">' + g.status + '</strong> | Strategy: ' + (g.strategy || 'sequential') + '</div>';
    phases.forEach(p => {
        let color = 'var(--text2)', icon = '&#9675;';
        if (p.status === 'done') { color = 'var(--green)'; icon = '&#10003;'; }
        else if (p.status === 'done_unverified') { color = 'var(--yellow)'; icon = '&#9888;'; }
        else if (p.status === 'retrying') { color = 'var(--red)'; icon = '&#8635;'; }
        else if (p.status === 'failed') { color = 'var(--red)'; icon = '&#10007;'; }
        else if (p.status === 'running') { color = 'var(--cyan)'; icon = '&#9881;'; }
        html += '<div style="padding:8px 0;border-bottom:1px solid var(--border)">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center"><span style="color:' + color + ';font-weight:600">' + icon + ' ' + esc(p.name || '') + '</span><span style="font-size:11px;color:' + color + '">' + (p.status || 'pending') + '</span></div>';
        if (p.result) {
            const cleaned = p.result.replace(/\*\*/g, '').replace(/\|[-\s|]+\|/g, '').replace(/\n{2,}/g, '\n').trim();
            html += '<div style="font-size:12px;color:var(--text2);margin-top:4px;white-space:pre-wrap;max-height:150px;overflow-y:auto;background:var(--bg);padding:8px;border-radius:6px;line-height:1.5">' + esc(cleaned.slice(0, 500)) + '</div>';
        }
        if (p.creates_hand) html += '<div style="font-size:11px;color:var(--purple);margin-top:4px">Daemon: ' + esc(p.creates_hand) + '</div>';
        html += '</div>';
    });
    if (g.completion_summary) {
        html += '<div style="margin-top:12px;padding:10px;background:rgba(67,233,123,0.06);border-radius:8px;border:1px solid rgba(67,233,123,0.2)"><div style="font-size:12px;font-weight:600;color:var(--green);margin-bottom:4px">Completion Summary</div><div style="font-size:12px;color:var(--text);white-space:pre-wrap">' + esc(g.completion_summary) + '</div></div>';
    }
    openDetail(esc(extractTitle(g.description || '')), html);
}

async function expandVitals() {
    const data = await fetchJSON('/api/vitals');
    if (!data) return;
    let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">';
    const items = [
        ['CPU', data.cpu_percent + '%', 'var(--blue)'],
        ['RAM', data.mem_used_mb + '/' + data.mem_total_mb + ' MB (' + data.mem_percent + '%)', 'var(--green)'],
        ['Disk', data.disk_used_gb + '/' + data.disk_total_gb + ' GB (' + data.disk_percent + '%)', 'var(--purple)'],
        ['Processes', data.processes, 'var(--cyan)'],
        ['Load Avg', (data.load_avg || []).join(', ') || 'N/A', 'var(--yellow)'],
        ['Uptime', fmtUptime(data.uptime_s || 0), 'var(--text)'],
    ];
    items.forEach(([label, val, color]) => {
        html += '<div style="padding:12px;background:var(--bg);border-radius:8px;border:1px solid var(--border)"><div style="font-size:10px;text-transform:uppercase;color:var(--text2);letter-spacing:0.5px">' + label + '</div><div style="font-size:18px;font-weight:700;color:' + color + ';margin-top:4px">' + val + '</div></div>';
    });
    html += '</div>';
    openDetail('System Vitals', html);
}

async function expandEvolution() {
    const [changelog, demands, blockers, nudge] = await Promise.all([
        fetchJSON('/api/evolution/changelog'),
        fetchJSON('/api/evolution/demands'),
        fetchJSON('/api/evolution/blockers'),
        fetchJSON('/api/evolution/nudge'),
    ]);
    let html = '';

    // ── Nudge banner: "Your OS needs help evolving" ──
    if (nudge && nudge.total > 0) {
        const urgency = nudge.escalated > 0 ? 'var(--red)' : 'var(--yellow)';
        html += '<div style="padding:14px;background:rgba(245,175,25,0.08);border:1px solid ' + urgency + ';border-radius:10px;margin-bottom:16px">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">';
        html += '<div style="font-size:13px;font-weight:700;color:' + urgency + '">&#9889; ' + nudge.total + ' evolution demand' + (nudge.total !== 1 ? 's' : '') + ' — the OS needs your help</div>';
        html += '<button onclick="copyEvolutionPrompt()" style="background:var(--blue);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:11px;font-weight:600;cursor:pointer">Copy Prompt</button>';
        html += '</div>';
        html += '<div style="font-size:11px;color:var(--text2);margin-bottom:10px">Paste the prompt into any AI coding tool to fix these demands:</div>';
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px">';
        (nudge.tools || []).forEach(t => {
            html += '<span style="font-size:10px;padding:3px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text2)">' + esc(t.name) + '</span>';
        });
        html += '</div>';
        html += '</div>';
    }

    if (changelog) {
        const realCount = (changelog.recent_insights || []).filter(i => i.outcome === 'success' && (i.what_worked || i.principle)).length;
        html += '<div style="font-size:11px;color:var(--text2);margin-bottom:12px">' + (changelog.cycles_completed || 0) + ' cycles, ' + realCount + ' useful learnings, ' + (changelog.evolved_files || []).length + ' tools evolved</div>';
        if (changelog.active_demands && changelog.active_demands.length) {
            html += '<div style="font-size:12px;font-weight:600;color:var(--yellow);margin-bottom:8px">Active Demands (' + changelog.active_demands.length + ')</div>';
            changelog.active_demands.forEach(d => {
                html += '<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;display:flex;justify-content:space-between"><span style="color:var(--text)">' + esc(d.description).slice(0, 80) + '</span><span style="color:var(--red);font-weight:700;font-size:11px">' + d.priority + '</span></div>';
            });
        }
        if (changelog.recent_insights && changelog.recent_insights.length) {
            html += '<div style="font-size:12px;font-weight:600;color:var(--cyan);margin-top:16px;margin-bottom:8px">Recent Insights (' + changelog.recent_insights.length + ')</div>';
            changelog.recent_insights.forEach(i => {
                const icon = i.outcome === 'success' ? '&#10003;' : '&#10007;';
                const color = i.outcome === 'success' ? 'var(--green)' : 'var(--red)';
                html += '<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px"><span style="color:' + color + '">' + icon + '</span> ' + esc(i.what || '') + '<div style="font-size:10px;color:var(--text2);margin-top:2px">' + esc(i.reason || '').slice(0, 100) + '</div></div>';
            });
        }
    }
    if (blockers && blockers.blockers && blockers.blockers.length) {
        html += '<div style="font-size:12px;font-weight:600;color:var(--yellow);margin-top:16px;margin-bottom:8px">Blockers (' + blockers.blockers.length + ')</div>';
        blockers.blockers.forEach((b, i) => {
            html += '<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:12px;display:flex;justify-content:space-between;align-items:center"><span style="color:var(--text)">' + esc(b.message).slice(0, 100) + '</span><button onclick="dismissBlocker(' + i + ');closeDetail()" style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:4px;padding:2px 8px;font-size:10px;cursor:pointer">dismiss</button></div>';
        });
    }
    openDetail('Evolution Engine', html);
}

async function copyEvolutionPrompt() {
    const nudge = await fetchJSON('/api/evolution/nudge');
    if (nudge && nudge.prompt) {
        try {
            await navigator.clipboard.writeText(nudge.prompt);
            showToast('Evolution prompt copied! Paste into your AI coding tool.', 'success');
        } catch(e) {
            // Fallback: select text in a textarea
            const ta = document.createElement('textarea');
            ta.value = nudge.prompt;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            showToast('Prompt copied!', 'success');
        }
    }
}

async function expandActivity() {
    const events = await fetchJSON('/api/events?limit=50');
    let html = '';
    if (events && events.length) {
        html += '<div style="font-size:11px;color:var(--text2);margin-bottom:12px">' + events.length + ' recent events</div>';
        events.forEach(e => {
            const ts = (e.timestamp || '').slice(11, 19);
            const topic = e.topic || '';
            let icon = '&#x2022;', text = topic, color = 'var(--text2)';
            if (topic.includes('phase_started')) { icon = '&#9654;'; text = 'Started: ' + ((e.data||{}).phase||''); color = 'var(--cyan)'; }
            else if (topic.includes('phase_completed')) { icon = (e.data||{}).status === 'done' ? '&#10003;' : '&#10007;'; text = 'Done: ' + ((e.data||{}).phase||''); color = (e.data||{}).status === 'done' ? 'var(--green)' : 'var(--red)'; }
            else if (topic.includes('goal_created')) { icon = '&#x1F3AF;'; text = 'Goal: ' + ((e.data||{}).description||'').slice(0,40); color = 'var(--purple)'; }
            else if (topic.includes('tool_call')) { icon = '&#x1F527;'; text = (e.data||{}).tool || topic; }
            else if (topic.includes('evolution')) { icon = '&#x2B50;'; text = topic.split('.').pop(); }
            else if (topic.includes('agent')) { icon = '&#x1F680;'; text = topic.split('.').pop(); }
            else { text = topic; }
            html += '<div style="font-size:12px;padding:4px 0;border-bottom:1px solid rgba(35,45,63,0.2);display:flex;gap:8px"><span style="color:var(--text2);font-family:monospace;min-width:60px;font-size:10px">' + ts + '</span><span>' + icon + '</span><span style="color:' + color + '">' + esc(text) + '</span></div>';
        });
    } else {
        html = '<div style="color:var(--text2)">No events yet</div>';
    }
    openDetail('Activity Log', html);
}

function expandLearned() {
    let html = '';
    if (_learnedData.length) {
        _learnedData.forEach(p => {
            let text = p.what_worked || p.principle || p.recommendation || p.reason || '';
            text = text.replace(/\[from [a-f0-9]+\]\s*/gi, '').trim();
            if (!text) return;
            const conf = p.confidence || 1.0;
            const confColor = conf >= 1.0 ? 'var(--green)' : conf >= 0.5 ? 'var(--yellow)' : 'var(--red)';
            html += '<div style="padding:10px 0;border-bottom:1px solid var(--border)">';
            html += '<div style="font-size:13px;color:var(--text);line-height:1.5">' + esc(text) + '</div>';
            html += '<div style="font-size:11px;color:var(--text2);margin-top:4px;display:flex;gap:12px">';
            if (p.applies_when) html += '<span>Keywords: ' + esc(p.applies_when) + '</span>';
            html += '<span>Env: ' + esc(p.environment_match || 'any') + '</span>';
            html += '<span style="color:' + confColor + ';font-weight:700">Confidence: ' + conf.toFixed(1) + '</span>';
            html += '</div></div>';
        });
    } else {
        html = '<div style="color:var(--text2)">No operational principles learned yet. The OS learns from phase failures and success patterns.</div>';
    }
    openDetail('What OpenSculpt Learned', html);
}

/* ═══════════════════════════════════════════════════════════════════
   SETTINGS MODAL
   ═══════════════════════════════════════════════════════════════════ */

function openSettings() {
    document.getElementById('settings-modal').classList.add('active');
    fetchJSON('/api/settings').then(s => {
        if (!s) return;
        // Set active provider in dropdown
        const activeProvider = s.active_provider || 'anthropic';
        const provSel = document.getElementById('provider-select');
        if (provSel) { provSel.value = activeProvider; onProviderChange(); }
        // Set current model
        if (s.model) {
            const modelInput = document.getElementById('model-input');
            if (modelInput) modelInput.value = s.model;
            document.getElementById('model-status').textContent = 'Current: ' + s.model;
        }
        // API key status
        const akStatus = document.getElementById('api-key-status');
        if (s.has_api_key) {
            akStatus.innerHTML = '<span style="color:var(--green)">&#10003; Active:</span> ' + esc(activeProvider) + ' <code style="color:var(--cyan)">' + esc(s.api_key_preview) + '</code>';
        } else {
            akStatus.innerHTML = '<span style="color:var(--yellow)">&#x26A0; No API key — set one to use the OS</span>';
        }
        // GitHub token
        const ghStatus = document.getElementById('gh-token-status');
        if (s.has_github_token) {
            ghStatus.innerHTML = '<span style="color:var(--green)">&#10003; Configured:</span> <code style="color:var(--cyan)">' + esc(s.github_token_preview) + '</code>';
        }
        // Sharing model: git PRs (auto-share removed)
    });
}
function closeSettings() { document.getElementById('settings-modal').classList.remove('active'); }
function toggleKeyVis() { const inp = document.getElementById('api-key-input'); inp.type = inp.type === 'password' ? 'text' : 'password'; }

const _providerMeta = {
    anthropic:  { hint: 'console.anthropic.com', placeholder: 'sk-ant-api03-...', local: false, baseUrl: '',
                  models: ['claude-haiku-4-5-20251001','claude-sonnet-4-20250514','claude-opus-4-20250514'] },
    openrouter: { hint: 'openrouter.ai — tracks spend', placeholder: 'sk-or-v1-...', local: false, baseUrl: 'https://openrouter.ai/api/v1',
                  models: ['anthropic/claude-haiku-4-5','anthropic/claude-sonnet-4','anthropic/claude-opus-4','google/gemini-2.5-flash','google/gemini-2.5-pro','meta-llama/llama-4-maverick','deepseek/deepseek-chat-v3','mistralai/mistral-medium'] },
    openai:     { hint: 'platform.openai.com', placeholder: 'sk-...', local: false, baseUrl: 'https://api.openai.com/v1',
                  models: ['gpt-4o','gpt-4o-mini','gpt-4.1','gpt-4.1-mini','o3','o4-mini'] },
    google:     { hint: 'aistudio.google.com', placeholder: 'AIza...', local: false, baseUrl: '',
                  models: ['gemini-2.5-flash','gemini-2.5-pro','gemini-2.0-flash'] },
    mistral:    { hint: 'console.mistral.ai', placeholder: 'sk-...', local: false, baseUrl: 'https://api.mistral.ai/v1',
                  models: ['mistral-large-latest','mistral-medium-latest','mistral-small-latest','codestral-latest'] },
    groq:       { hint: 'console.groq.com — fastest inference', placeholder: 'gsk_...', local: false, baseUrl: 'https://api.groq.com/openai/v1',
                  models: ['llama-3.3-70b-versatile','llama-3.1-8b-instant','mixtral-8x7b-32768','gemma2-9b-it'] },
    together:   { hint: 'api.together.xyz', placeholder: 'sk-...', local: false, baseUrl: 'https://api.together.xyz/v1',
                  models: ['meta-llama/Llama-3.3-70B-Instruct-Turbo','Qwen/Qwen2.5-72B-Instruct-Turbo','deepseek-ai/DeepSeek-V3'] },
    fireworks:  { hint: 'fireworks.ai', placeholder: 'fw_...', local: false, baseUrl: 'https://api.fireworks.ai/inference/v1',
                  models: ['accounts/fireworks/models/llama-v3p3-70b-instruct','accounts/fireworks/models/qwen2p5-72b-instruct'] },
    deepseek:   { hint: 'platform.deepseek.com', placeholder: 'sk-...', local: false, baseUrl: 'https://api.deepseek.com/v1',
                  models: ['deepseek-chat','deepseek-reasoner'] },
    perplexity: { hint: 'perplexity.ai — with search', placeholder: 'pplx-...', local: false, baseUrl: 'https://api.perplexity.ai',
                  models: ['sonar-pro','sonar','sonar-reasoning-pro'] },
    cohere:     { hint: 'dashboard.cohere.com', placeholder: 'sk-...', local: false, baseUrl: 'https://api.cohere.com/v2',
                  models: ['command-r-plus','command-r','command-a-03-2025'] },
    lmstudio:   { hint: 'Free, local. Start LM Studio first.', placeholder: 'not needed', local: true, baseUrl: 'http://host.docker.internal:1234/v1',
                  models: ['local-model'] },
    ollama:     { hint: 'Free, local. Start Ollama first.', placeholder: 'not needed', local: true, baseUrl: 'http://host.docker.internal:11434/v1',
                  models: ['llama3.3','qwen2.5','deepseek-r1','gemma2'] },
    custom:     { hint: 'Any OpenAI-compatible API', placeholder: 'your-api-key', local: false, baseUrl: '',
                  models: [] },
};
function onProviderChange() {
    const sel = document.getElementById('provider-select');
    const name = sel.value;
    const meta = _providerMeta[name] || {};
    // Hint
    document.getElementById('provider-hint').textContent = meta.hint || '';
    // Key placeholder
    document.getElementById('api-key-input').placeholder = meta.placeholder || 'API key...';
    // Base URL row — show for local/custom providers
    const baseRow = document.getElementById('base-url-row');
    if (meta.local || name === 'custom') {
        baseRow.style.display = '';
        document.getElementById('base-url-input').value = meta.baseUrl || '';
    } else {
        baseRow.style.display = 'none';
    }
    // Model suggestions
    const dl = document.getElementById('model-suggestions');
    dl.innerHTML = '';
    (meta.models || []).forEach(m => { const o = document.createElement('option'); o.value = m; dl.appendChild(o); });
    // Pre-fill first model
    const modelInput = document.getElementById('model-input');
    if (meta.models && meta.models.length > 0) {
        modelInput.value = meta.models[0];
        modelInput.placeholder = meta.models[0];
    } else {
        modelInput.value = '';
        modelInput.placeholder = 'type model name...';
    }
}
async function testConnection() {
    const btn = document.getElementById('test-conn-btn');
    btn.textContent = 'Testing...';
    btn.disabled = true;
    try {
        const resp = await fetch('/api/os/command', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({command: 'ping'})
        });
        const data = await resp.json();
        if (data.response && !data.response.includes('error') && !data.response.includes('No LLM')) {
            btn.textContent = 'Connected!';
            btn.style.borderColor = 'var(--green)';
            btn.style.color = 'var(--green)';
        } else {
            btn.textContent = 'Failed';
            btn.style.borderColor = 'var(--red)';
            btn.style.color = 'var(--red)';
        }
    } catch(e) {
        btn.textContent = 'Error';
        btn.style.borderColor = 'var(--red)';
    }
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Test'; btn.style.borderColor = ''; btn.style.color = ''; }, 3000);
}
function updateToggleUI(on) {
    const slider = document.getElementById('fed-slider');
    const dot = document.getElementById('fed-dot');
    if (on) { slider.style.background = 'var(--purple)'; dot.style.transform = 'translateX(18px)'; }
    else { slider.style.background = 'var(--border)'; dot.style.transform = 'translateX(0)'; }
}
// reciprocity removed — all users get community code equally
async function saveApiKey() {
    const provider = document.getElementById('provider-select').value;
    const key = document.getElementById('api-key-input').value.trim();
    const model = document.getElementById('model-input').value.trim();
    const baseUrl = document.getElementById('base-url-input')?.value?.trim() || '';
    const meta = _providerMeta[provider] || {};
    if (!key && !meta.local) {
        document.getElementById('api-key-status').innerHTML = '<span style="color:var(--red)">Enter an API key</span>';
        return;
    }
    if (!model) {
        document.getElementById('api-key-status').innerHTML = '<span style="color:var(--red)">Enter a model name</span>';
        return;
    }
    const resp = await fetch('/api/settings/apikey', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_key: key, provider: provider, model: model, base_url: baseUrl || meta.baseUrl || ''})
    });
    const data = await resp.json();
    const status = document.getElementById('api-key-status');
    if (data.ok) {
        status.innerHTML = '<span style="color:var(--green)">&#10003; Saved:</span> ' + esc(provider) + ' / ' + esc(model);
        document.getElementById('api-key-input').value = '';
        const pulse = document.getElementById('key-pulse');
        if (pulse) pulse.style.background = 'var(--green)';
    } else {
        status.innerHTML = '<span style="color:var(--red)">' + esc(data.error) + '</span>';
    }
}
async function saveGHToken() {
    const token = document.getElementById('gh-token-input').value.trim();
    if (!token) return;
    const resp = await fetch('/api/settings/github-token', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({github_token: token}) });
    const data = await resp.json();
    const st = document.getElementById('gh-token-status');
    if (data.ok) {
        st.innerHTML = '<span style="color:var(--green)">&#10003; Saved:</span> <code style="color:var(--cyan)">' + esc(data.preview) + '</code>';
        document.getElementById('gh-token-input').value = '';
    }
}
// auto-share toggle removed — sharing is via git PRs

/* ═══════════════════════════════════════════════════════════════════
   SETUP WIZARD — first-run auto-detection + configuration
   ═══════════════════════════════════════════════════════════════════ */

let _wizData = null;        // cached detect-all response
let _wizProvider = null;    // selected provider
let _wizVibeTools = [];     // selected vibe tools
let _wizNeedsKey = false;   // whether selected provider needs manual key
let _wizProviderMap = {};   // name -> provider data (avoids JSON in attributes)
let _wizVibeMap = {};       // name -> vibe tool data

const _providerIcons = {
    anthropic: '&#x1F9E0;', openai: '&#x2728;', groq: '&#9889;', deepseek: '&#x1F50D;',
    openrouter: '&#x1F310;', gemini: '&#x1F48E;', ollama: '&#x1F999;', lmstudio: '&#x1F4BB;',
    claude_code: '&#x1F419;', together: '&#x1F91D;', xai: '&#x1F916;', mistral: '&#x1F32A;',
    cohere: '&#x1F4AC;', vllm: '&#9889;',
};
const _vibeIcons = {
    claude_code: '&#x1F419;', cursor: '&#x1F4DD;', windsurf: '&#x1F3C4;', aider: '&#x1F527;',
    codex: '&#x2728;', github_copilot: '&#x1F4A1;', cline: '&#x1F50C;', roo_code: '&#x1F998;',
    continue_dev: '&#x27A1;', copilot_cli: '&#x1F4BB;', gemini_cli: '&#x1F48E;', amp: '&#x26A1;',
};

async function wizardCheck() {
    const d = await fetchJSON('/api/wizard/status');
    if (!d || !d.first_run) return;
    // Show wizard overlay
    document.getElementById('wizard-overlay').classList.add('active');
    // Start scanning
    await wizScan();
}

async function wizScan() {
    const progress = document.getElementById('wiz-scan-progress');
    progress.innerHTML = 'Checking LLM providers...<br>';

    _wizData = await fetchJSON('/api/wizard/detect-all');

    if (!_wizData) {
        progress.innerHTML += '<span style="color:var(--red)">Detection failed. You can configure manually in Settings.</span>';
        // Still advance — let user configure manually
        _wizData = { providers: [], vibe_tools: [], environment: {} };
        await new Promise(r => setTimeout(r, 1500));
        wizStep(1);
        return;
    }
    const d = _wizData;
    const pCount = d.providers ? d.providers.length : 0;
    const vInstalled = d.vibe_tools ? d.vibe_tools.filter(t => t.installed && t.confidence !== 'low').length : 0;
    progress.innerHTML = 'Checking LLM providers... <span style="color:var(--green)">' + pCount + ' found</span><br>';
    progress.innerHTML += 'Scanning vibe coding tools... <span style="color:var(--green)">' + vInstalled + ' found</span><br>';
    progress.innerHTML += 'Probing environment... <span style="color:var(--green)">' + (d.environment.os || 'OK') + '</span><br>';
    // Brief pause to show results, then advance
    await new Promise(r => setTimeout(r, 800));
    wizStep(1);
}

function wizStep(n) {
    // Update step indicator (labeled stepper)
    document.querySelectorAll('.wiz-step-item').forEach((item, i) => {
        const pip = item.querySelector('.wiz-step-pip');
        item.classList.remove('active', 'done');
        pip.classList.remove('active', 'done');
        if (i < n) { item.classList.add('done'); pip.classList.add('done'); pip.innerHTML = '&#x2713;'; }
        else if (i === n) { item.classList.add('active'); pip.classList.add('active'); pip.textContent = (i+1); }
        else { pip.textContent = (i+1); }
    });
    document.querySelectorAll('.wiz-step-line').forEach((line, i) => {
        line.classList.toggle('done', i < n);
    });
    // Compact header after step 0
    const hdr = document.getElementById('wizard-header');
    if (n > 0) hdr.classList.add('compact'); else hdr.classList.remove('compact');
    // Show section
    document.querySelectorAll('.wizard-section').forEach(s => s.classList.remove('active'));
    const section = document.getElementById('wiz-step-' + n);
    if (section) section.classList.add('active');
    // Populate on first show
    if (n === 1) wizPopulateProviders();
    if (n === 2) wizPopulateVibeTools();
    if (n === 3) wizPopulateSummary();
}

function wizPopulateProviders() {
    const container = document.getElementById('wiz-providers');
    if (container.dataset.populated === 'true') return;
    if (!_wizData) return;
    container.dataset.populated = 'true';
    const d = _wizData;
    const detected = new Set((d.providers || []).map(p => p.name));

    // All known providers — detected ones first, then the rest
    const allProviders = [
        { name: 'claude_code', label: 'Claude Code (free, your subscription)', type: 'local' },
        { name: 'anthropic', label: 'Anthropic (Claude)', type: 'cloud' },
        { name: 'openai', label: 'OpenAI', type: 'cloud' },
        { name: 'openrouter', label: 'OpenRouter', type: 'cloud' },
        { name: 'groq', label: 'Groq', type: 'cloud' },
        { name: 'deepseek', label: 'DeepSeek', type: 'cloud' },
        { name: 'gemini', label: 'Google Gemini', type: 'cloud' },
        { name: 'ollama', label: 'Ollama', type: 'local' },
        { name: 'lmstudio', label: 'LM Studio', type: 'local' },
        { name: 'xai', label: 'xAI / Grok', type: 'cloud' },
        { name: 'mistral', label: 'Mistral', type: 'cloud' },
        { name: 'together', label: 'Together AI', type: 'cloud' },
        { name: 'cohere', label: 'Cohere', type: 'cloud' },
    ];
    // Merge detected details (key_preview, models) into allProviders
    const detectedMap = {};
    for (const p of (d.providers || [])) detectedMap[p.name] = p;

    // Sort: detected first
    const sorted = [...allProviders].sort((a, b) => {
        const aD = detected.has(a.name) ? 1 : 0;
        const bD = detected.has(b.name) ? 1 : 0;
        return bD - aD;
    });

    let htmlDetected = '';
    let htmlOther = '';
    for (const p of sorted) {
        const isDetected = detected.has(p.name);
        const det = detectedMap[p.name];
        const icon = _providerIcons[p.name] || '&#x1F4E6;';
        const badge = p.type === 'local' ? '<span class="wiz-badge local">Local</span>' :
                      '<span class="wiz-badge cloud">Cloud</span>';
        const detBadge = isDetected ? ' <span class="wiz-badge high">Detected</span>' : '';
        let detail = '';
        if (isDetected && det) {
            detail = det.type === 'local'
                ? (det.models && det.models.length ? det.models.slice(0,3).join(', ') : 'Running')
                : (det.key_preview || 'API key detected');
        } else {
            detail = p.type === 'local' ? 'Not running' : 'Requires API key';
        }
        const needsKey = !isDetected && p.type === 'cloud';
        const cls = isDetected ? 'wiz-item detected' : 'wiz-item';
        _wizProviderMap[p.name] = det || p;
        const itemHtml = '<div class="' + cls + '" data-pname="' + esc(p.name) + '" data-needs-key="' + needsKey + '" onclick="wizToggleProvider(this)">' +
            '<div class="wiz-check">&#x2713;</div>' +
            '<div class="wiz-icon">' + icon + '</div>' +
            '<div class="wiz-info"><div class="wiz-name">' + esc(p.label) + '</div>' +
            '<div class="wiz-detail">' + esc(detail) + '</div></div>' +
            badge + detBadge + '</div>';
        if (isDetected) htmlDetected += itemHtml;
        else htmlOther += itemHtml;
    }
    // Show detected first, then collapsible "more" section
    let html = htmlDetected;
    if (htmlOther) {
        html += '<div class="wiz-more-toggle" onclick="this.classList.toggle(\'open\');this.nextElementSibling.classList.toggle(\'open\')"><span class="arrow">&#9654;</span> Show ' + (sorted.length - detected.size) + ' more providers</div>';
        html += '<div class="wiz-more-items">' + htmlOther + '</div>';
    }
    container.innerHTML = html;
    // Auto-select first detected provider (not requiring API key)
    const firstDetected = container.querySelector('.wiz-item.detected');
    const first = firstDetected || container.querySelector('.wiz-item');
    if (first) {
        first.classList.add('selected');
        _wizProvider = _wizProviderMap[first.dataset.pname] || null;
        _wizNeedsKey = first.dataset.needsKey === 'true';
    }
    // Only show API key input if the selected provider needs it
    if (_wizNeedsKey) document.getElementById('wiz-api-key-input').style.display = 'block';
}

function wizToggleProvider(el) {
    document.querySelectorAll('#wiz-providers .wiz-item').forEach(i => i.classList.remove('selected'));
    el.classList.add('selected');
    _wizProvider = _wizProviderMap[el.dataset.pname] || null;
    _wizNeedsKey = el.dataset.needsKey === 'true';
    document.getElementById('wiz-api-key-input').style.display = _wizNeedsKey ? 'block' : 'none';
}

function wizSelectProvider() {
    // Re-read the currently selected provider from DOM (in case user changed it)
    const sel = document.querySelector('#wiz-providers .wiz-item.selected');
    if (sel) {
        const pname = sel.dataset.pname;
        _wizProvider = _wizProviderMap[pname] || _wizProvider;
        _wizNeedsKey = sel.dataset.needsKey === 'true';
    }
    if (_wizNeedsKey) {
        const key = document.getElementById('wiz-key').value.trim();
        if (!key) { document.getElementById('wiz-key').focus(); return; }
        // Attach key to selected provider
        const selName = sel ? sel.dataset.pname : 'openai';
        // Also infer from key prefix as fallback
        let name = selName;
        if (!sel) {
            if (key.startsWith('sk-ant')) name = 'anthropic';
            else if (key.startsWith('gsk_')) name = 'groq';
            else if (key.startsWith('sk-or-')) name = 'openrouter';
        }
        _wizProvider = { name, label: _wizProvider ? _wizProvider.label : name, type: 'cloud', api_key: key };
    }
    wizStep(2);
}

function wizPopulateVibeTools() {
    const container = document.getElementById('wiz-vibe-tools');
    if (container.dataset.populated === 'true') return;
    if (!_wizData) return;
    container.dataset.populated = 'true';
    const d = _wizData;
    if (!d || !d.vibe_tools) { container.innerHTML = '<div class="wiz-empty">No tools available.</div>'; return; }

    // Sort: installed (high/medium) first, then low, then not installed
    const confRank = {high: 3, medium: 2, low: 1, '': 0};
    const sorted = [...d.vibe_tools].sort((a, b) => {
        const aR = a.installed ? confRank[a.confidence] || 0 : -1;
        const bR = b.installed ? confRank[b.confidence] || 0 : -1;
        return bR - aR;
    });

    _wizVibeTools = [];
    let html = '';
    let section = '';
    for (const t of sorted) {
        const isDetected = t.installed && t.confidence !== 'low';
        const isMaybe = t.installed && t.confidence === 'low';

        // Group labels
        const newSection = isDetected ? 'detected' : (isMaybe ? 'maybe' : 'available');
        if (newSection !== section) {
            section = newSection;
            if (section === 'detected') html += '<div class="wiz-group-label">Detected on your machine</div>';
            else if (section === 'maybe') html += '<div class="wiz-group-label">Possibly installed</div>';
            else html += '<div class="wiz-group-label">Available to install</div>';
        }

        const icon = _vibeIcons[t.name] || '&#x1F527;';
        const badge = '<span class="wiz-badge ' + esc(t.category) + '">' + esc(t.category) + '</span>';

        let detail = '';
        if (isDetected) {
            detail = t.version ? 'v' + t.version : (t.path ? t.path.split(/[/\\]/).pop() : 'Installed');
            _wizVibeTools.push(t);
        } else if (isMaybe) {
            detail = 'Config found, binary not';
        } else {
            detail = 'Not installed';
        }

        const cls = isDetected ? 'wiz-item detected selected' : 'wiz-item';
        _wizVibeMap[t.name] = t;
        html += '<div class="' + cls + '" data-tool="' + esc(t.name) + '" onclick="wizToggleVibeTool(this)">' +
            '<div class="wiz-check">&#x2713;</div>' +
            '<div class="wiz-icon">' + icon + '</div>' +
            '<div class="wiz-info"><div class="wiz-name">' + esc(t.label) + '</div>' +
            '<div class="wiz-detail">' + esc(detail || '') + '</div></div>' +
            badge + '</div>';
    }
    container.innerHTML = html;
}

function wizToggleVibeTool(el) {
    el.classList.toggle('selected');
    // Rebuild _wizVibeTools from selected items
    _wizVibeTools = [];
    document.querySelectorAll('#wiz-vibe-tools .wiz-item.selected').forEach(item => {
        const t = _wizVibeMap[item.dataset.tool];
        if (t) _wizVibeTools.push(t);
    });
}

function wizPopulateSummary() {
    const d = _wizData;
    const env = d ? d.environment : {};

    // Environment grid
    const grid = document.getElementById('wiz-env-grid');
    grid.innerHTML =
        '<div class="wiz-env-item"><div class="wiz-env-label">OS</div><div class="wiz-env-val">' + esc(env.os || '?') + ' ' + esc(env.arch || '') + '</div></div>' +
        '<div class="wiz-env-item"><div class="wiz-env-label">Docker</div><div class="wiz-env-val">' + (env.docker ? '<span style="color:var(--green)">Available</span>' : '<span style="color:var(--text2)">Not available</span>') + '</div></div>' +
        '<div class="wiz-env-item"><div class="wiz-env-label">Memory</div><div class="wiz-env-val">' + (env.memory_mb ? Math.round(env.memory_mb / 1024) + ' GB' : '?') + '</div></div>' +
        '<div class="wiz-env-item"><div class="wiz-env-label">Internet</div><div class="wiz-env-val">' + (env.internet ? '<span style="color:var(--green)">Connected</span>' : '<span style="color:var(--red)">Offline</span>') + '</div></div>';

    // Summary
    const summary = document.getElementById('wiz-summary');
    let rows = '';
    // Provider
    const provLabel = _wizProvider ? _wizProvider.label : '<span style="color:var(--text2)">None selected</span>';
    rows += '<div class="wiz-summary-row"><span class="label">LLM Provider</span><span class="value">' + provLabel + '</span></div>';
    // Vibe tools
    const vibeLabels = _wizVibeTools.map(t => t.label).join(', ') || '<span style="color:var(--text2)">None</span>';
    rows += '<div class="wiz-summary-row"><span class="label">Vibe Coding Tools</span><span class="value">' + vibeLabels + '</span></div>';
    // Strategy
    const strat = {docker: 'Docker Compose', apt_install: 'Direct Install', pip_python: 'Python/pip', minimal: 'Minimal'};
    rows += '<div class="wiz-summary-row"><span class="label">Deploy Strategy</span><span class="value">' + (strat[env.strategy] || env.strategy || '?') + '</span></div>';
    if (env.in_container) {
        rows += '<div class="wiz-summary-row"><span class="label">Container</span><span class="value"><span style="color:var(--cyan)">Running inside container</span></span></div>';
    }
    summary.innerHTML = rows;
}

async function wizFinish() {
    // Save selections to backend
    const body = {
        provider: _wizProvider,
        vibe_tools: _wizVibeTools,
        preferred_vibe_tool: _wizVibeTools.length > 0 ? _wizVibeTools[0].name : null,
    };
    try {
        await fetch('/api/wizard/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
    } catch(e) {
        console.error('Wizard save failed:', e);
    }
    // Hide wizard, show desktop
    document.getElementById('wizard-overlay').classList.remove('active');
    refreshDesktop();
}
fetchJSON('/api/settings').then(s => {
    if (s && !s.has_api_key) document.getElementById('key-pulse').style.background = 'var(--yellow)';
});

/* ═══════════════════════════════════════════════════════════════════
   COMPAT — stub functions for old code that references these
   ═══════════════════════════════════════════════════════════════════ */
function refreshFast() { refreshDesktop(); }
function refreshEvolution() {}
function refreshChangelog() {}
function refreshBlockers() {}
function refreshMeta() {}
function refreshDaemons() {}
function refreshCodebase() {}
function refreshAudit() {}
function refreshDeps() {}
function refreshProviders() {}
function refreshChannels() {}
function refreshTools() {}
function setGauge() {}
function agentAction() {}

/* ── Tab compat for Playwright tests ── */
function switchTab(name) {
    document.querySelectorAll('.compat-hidden .tab-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById('tab-' + name);
    if (panel) panel.classList.add('active');
}

/* ── Evolution nudge show/hide ── */
async function updateNudgeBanner() {
    try {
        const data = await fetchJSON('/api/evolution/changelog');
        const demands = (data && data.active_demands) ? data.active_demands.length : 0;
        const nudge = document.getElementById('evo-nudge');
        const desktop = document.getElementById('desktop');
        if (demands > 0) {
            nudge.classList.add('active');
            desktop.classList.add('has-nudge');
            const escalated = data.active_demands.filter(d => d.priority >= 0.8).length;
            document.getElementById('evo-nudge-text').textContent =
                demands + ' evolution demand' + (demands !== 1 ? 's' : '') +
                (escalated > 0 ? ' (' + escalated + ' urgent)' : '') +
                ' \u2014 your OS needs help';
        } else {
            nudge.classList.remove('active');
            desktop.classList.remove('has-nudge');
        }
    } catch(e) {}
}

/* ═══════════════════════════════════════════════════════════════════
   BOOT
   ═══════════════════════════════════════════════════════════════════ */
// Wizard runs in isolated async context so other boot errors can't kill it
(async () => { try { await wizardCheck(); } catch(e) { console.error('Wizard error:', e); } })();
refreshDesktop();
updateNudgeBanner();
setInterval(updateNudgeBanner, 30000);
setInterval(refreshDesktop, 3000);
// Preload recent events into activity feed
(async () => {
    const ev = await fetchJSON('/api/events?limit=20');
    if (ev && ev.length) ev.reverse().forEach(e => addActivityEvent(e.topic, e.data || {}, e.timestamp || ''));
})();
</script>
</body>
</html>"""

