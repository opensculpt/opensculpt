"""Global configuration — loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class AgosSettings(BaseSettings):
    llm_api_key: str = ""  # Provider-agnostic: works with any provider (Anthropic, OpenAI, etc.)
    default_model: str = "anthropic/claude-haiku-4-5"

    @property
    def anthropic_api_key(self) -> str:
        """Backward compat — maps to llm_api_key."""
        return self.llm_api_key

    @anthropic_api_key.setter
    def anthropic_api_key(self, value: str) -> None:
        self.llm_api_key = value
    workspace_dir: Path = Path(".opensculpt")
    db_path: Path = Path(".opensculpt/opensculpt.db")
    max_concurrent_agents: int = 50
    log_level: str = "INFO"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8420

    # Evolution settings
    evolution_auto_merge: bool = False
    evolution_interval_hours: int = 168  # weekly
    evolution_days_lookback: int = 7
    evolution_max_papers: int = 20

    # Node specialization (for multi-node fleet diversity)
    node_role: str = "general"  # knowledge|intent|orchestration|policy|general
    evolution_initial_delay: int = 0  # Seconds to wait before first evolution cycle (stagger fleet)

    # LLM provider for evolution ("auto", "lmstudio", "ollama", "anthropic", "template")
    evolution_llm_provider: str = "auto"
    # Stronger model for Evolution Agent (architectural reasoning needs capability)
    # If set, Evolution Agent uses this model instead of the default evolution LLM.
    # Recommended: "anthropic/claude-sonnet-4" or "anthropic/claude-haiku-4-5"
    evolution_agent_model: str = ""
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = ""  # empty = auto-pick best available model
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # ALMA-inspired evolution settings
    evolution_llm_ideation_interval: int = 5   # LLM ideation every Nth meta-cycle
    evolution_alma_iterate_interval: int = 5   # Iterate on archive every Nth evolution cycle

    # Regression test gate (run pytest after evolving code)
    evolution_test_gate: bool = True
    evolution_test_gate_timeout: int = 120     # seconds
    evolution_test_gate_path: str = "tests/"

    # Update settings
    auto_update_check: bool = True
    github_owner: str = "opensculpt"
    github_repo: str = "opensculpt"
    github_token: str = ""  # GitHub PAT for community contributions (optional)

    # Fleet sync (peer-to-peer evolution sharing — replaces GitHub PRs at scale)
    fleet_sync_enabled: bool = False  # Enable peer-to-peer evolution sync
    fleet_sync_peers: str = ""  # Comma-separated peer URLs (e.g. "http://node2:8420,http://node3:8420")
    fleet_sync_interval: int = 120  # Seconds between sync attempts
    fleet_sync_push: bool = True  # Push learnings to peers
    fleet_sync_pull: bool = True  # Pull learnings from peers

    # Federation: curator + seed + contribute
    seed_url: str = ""          # URL to fetch seed release (git repo or HTTP)
    registry_url: str = ""      # URL to submit contributions
    fleet_dir: str = ".opensculpt-fleet"  # Path to fleet node directories

    # MCP (Model Context Protocol) settings
    mcp_auto_connect: bool = True  # Auto-connect to configured MCP servers on startup

    # A2A (Agent-to-Agent) protocol settings
    a2a_enabled: bool = True  # Expose OpenSculpt as an A2A server
    a2a_remote_agents: str = ""  # Comma-separated URLs of remote A2A agents to auto-discover

    # Chaos Monkey — proactive resilience testing (Netflix pattern)
    chaos_enabled: bool = False  # Set SCULPT_CHAOS_ENABLED=true to activate

    # Dashboard security
    dashboard_api_key: str = ""  # Set SCULPT_DASHBOARD_API_KEY to require auth

    # LLM capability probe
    model_context_window: int = 0  # 0 = auto-detect from probe/known table; set to override (e.g. 8192)

    # Approval settings (dashboard human-in-the-loop)
    approval_mode: str = "auto"  # "auto" (default for desktop), "confirm-dangerous", "confirm-all"
    approval_timeout_seconds: int = 30  # 30 seconds (was 300 — too long for interactive use)

    model_config = {"env_prefix": "SCULPT_"}


settings = AgosSettings()
