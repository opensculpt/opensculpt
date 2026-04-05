"""Setup persistence — channels, providers, tools config in .agos/setup.json.

Follows the same pattern as agos/mcp/config.py: JSON in workspace dir,
load/save via orjson, survives restarts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

_CONFIG_FILENAME = "setup.json"


def is_first_run(workspace_dir: Path | str) -> bool:
    """True if setup wizard has never completed."""
    path = Path(workspace_dir) / _CONFIG_FILENAME
    if not path.exists():
        return True
    try:
        data = orjson.loads(path.read_bytes())
        return not data.get("wizard_complete", False)
    except Exception:
        return True


def mark_wizard_complete(workspace_dir: Path | str) -> None:
    """Mark the setup wizard as completed."""
    data = load_setup(workspace_dir)
    data["wizard_complete"] = True
    save_setup(workspace_dir, data)


def _config_path(workspace_dir: Path) -> Path:
    return workspace_dir / _CONFIG_FILENAME


def load_setup(workspace_dir: Path | str) -> dict[str, Any]:
    """Load the full setup config from disk."""
    path = _config_path(Path(workspace_dir))
    if not path.exists():
        return {"providers": {}, "channels": {}, "tools": {}}
    try:
        return orjson.loads(path.read_bytes())
    except Exception:
        return {"providers": {}, "channels": {}, "tools": {}}


def save_setup(workspace_dir: Path | str, data: dict[str, Any]) -> None:
    """Write the full setup config to disk."""
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    path = _config_path(ws)
    path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))


# ── Provider helpers ─────────────────────────────────────────


def get_provider_config(workspace_dir: Path | str, name: str) -> dict[str, Any]:
    data = load_setup(workspace_dir)
    return data.get("providers", {}).get(name, {"enabled": False})


def set_provider_config(workspace_dir: Path | str, name: str, config: dict[str, Any]) -> None:
    data = load_setup(workspace_dir)
    data.setdefault("providers", {})[name] = config
    save_setup(workspace_dir, data)


# ── Channel helpers ──────────────────────────────────────────


def get_channel_config(workspace_dir: Path | str, name: str) -> dict[str, Any]:
    data = load_setup(workspace_dir)
    return data.get("channels", {}).get(name, {"enabled": False, "config": {}})


def set_channel_config(workspace_dir: Path | str, name: str, config: dict[str, Any]) -> None:
    data = load_setup(workspace_dir)
    data.setdefault("channels", {})[name] = config
    save_setup(workspace_dir, data)


# ── Tool helpers ─────────────────────────────────────────────


def get_tool_config(workspace_dir: Path | str, name: str) -> dict[str, Any]:
    data = load_setup(workspace_dir)
    return data.get("tools", {}).get(name, {"enabled": True})


def set_tool_config(workspace_dir: Path | str, name: str, config: dict[str, Any]) -> None:
    data = load_setup(workspace_dir)
    data.setdefault("tools", {})[name] = config
    save_setup(workspace_dir, data)


# ── Vibe coding tool helpers ────────────────────────────────


def get_vibe_tools_config(workspace_dir: Path | str) -> dict[str, Any]:
    """Get configured vibe coding tools. Keys are tool names, values are config dicts."""
    data = load_setup(workspace_dir)
    return data.get("vibe_tools", {})


def set_vibe_tool_config(workspace_dir: Path | str, name: str, config: dict[str, Any]) -> None:
    """Enable/disable/configure a specific vibe coding tool."""
    data = load_setup(workspace_dir)
    data.setdefault("vibe_tools", {})[name] = config
    save_setup(workspace_dir, data)


def get_preferred_vibe_tool(workspace_dir: Path | str) -> str | None:
    """Return the name of the preferred (default) vibe coding tool, or None."""
    data = load_setup(workspace_dir)
    return data.get("preferred_vibe_tool")


def set_preferred_vibe_tool(workspace_dir: Path | str, name: str) -> None:
    """Set the preferred vibe coding tool for nudge prompts."""
    data = load_setup(workspace_dir)
    data["preferred_vibe_tool"] = name
    save_setup(workspace_dir, data)


# ── LLM Capability helpers ─────────────────────────────────


def get_llm_capability(workspace_dir: Path | str) -> dict:
    """Get the cached LLM capability probe result."""
    data = load_setup(workspace_dir)
    return data.get("llm_capability", {})


def set_llm_capability(workspace_dir: Path | str, capability: dict) -> None:
    """Persist LLM capability probe result."""
    data = load_setup(workspace_dir)
    data["llm_capability"] = capability
    save_setup(workspace_dir, data)
