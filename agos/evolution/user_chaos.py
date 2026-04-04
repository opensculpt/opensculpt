"""User-Level Chaos Monkey — tests the OS from the end-user perspective.

Infrastructure chaos (chaos.py) breaks OS internals (kill containers, corrupt files).
User chaos sends synthetic user commands via /api/os/command and verifies the OS
handles them correctly — vague input, contradictions, bad data, mid-workflow abandonment.

Experiments are defined in USER_CHAOS_SCENARIOS.md (not hardcoded here).
Each experiment checks invariant properties on the OS response.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

_logger = logging.getLogger(__name__)

# Invariant name → P-code mapping
_INVARIANT_MAP = {
    "P1": "terminates", "P2": "honest", "P3": "no_orphans", "P4": "responded",
    "P5": "no_crash", "P6": "demand_signal", "P7": "safe", "P8": "idempotent",
}

# Category heading → category key mapping
_CATEGORY_MAP = {
    "vague": "vague", "ambiguous": "vague",
    "contradictory": "contradictory", "impossible requests": "contradictory",
    "wrong order": "wrong_order", "missing dependencies": "wrong_order",
    "bad data": "bad_data", "corrupted input": "bad_data",
    "user disappears": "abandon", "mid-workflow": "abandon",
    "user changes mind": "change_mind",
    "concurrent": "concurrent", "conflicting goals": "concurrent",
    "can't do it": "impossible", "capability boundary": "impossible",
}


@dataclass
class UserChaosExperiment:
    """A user-level chaos experiment loaded from USER_CHAOS_SCENARIOS.md."""
    name: str
    category: str
    scenario: int
    command: str
    invariants: list[str]
    description: str = ""
    follow_up: str = ""          # Second command to send after delay
    follow_up_delay: int = 5     # Seconds before sending follow_up
    concurrent_command: str = ""  # Command to send simultaneously


@dataclass
class UserChaosResult:
    """Result of a user-level chaos experiment."""
    experiment: str
    command: str
    category: str
    passed: bool = False
    violations: list[str] = field(default_factory=list)
    response_text: str = ""
    goal_status: str = ""
    duration_s: float = 0
    details: str = ""


def _parse_invariants(text: str) -> list[str]:
    """Parse 'P1, P2, P5, P7' → ['terminates', 'responded', 'no_crash', 'safe']."""
    invariants = []
    for token in re.split(r"[,\s]+", text.strip()):
        token = token.strip()
        if token in _INVARIANT_MAP:
            invariants.append(_INVARIANT_MAP[token])
    return invariants


def _detect_category(heading: str) -> str:
    """Map a section heading like '## A: Vague / Ambiguous Input' to a category key."""
    heading_lower = heading.lower()
    for key, cat in _CATEGORY_MAP.items():
        if key in heading_lower:
            return cat
    # Fallback: use single-letter prefix
    match = re.match(r"##\s+([A-H]):", heading)
    if match:
        letter_map = {"A": "vague", "B": "contradictory", "C": "wrong_order",
                      "D": "bad_data", "E": "abandon", "F": "change_mind",
                      "G": "concurrent", "H": "impossible"}
        return letter_map.get(match.group(1), "unknown")
    return "unknown"


def load_experiments(md_path: str | Path | None = None) -> list[UserChaosExperiment]:
    """Load experiments from USER_CHAOS_SCENARIOS.md.

    Parses the markdown format:
        ### A1: vague_sales
        - scenario: 1
        - command: set up sales
        - invariants: P1, P4, P5, P7
        - description: ...
        - follow_up: ... (optional)
        - follow_up_delay: 5 (optional)
        - concurrent_command: ... (optional)
    """
    if md_path is None:
        # Search for the file relative to project root
        candidates = [
            Path("USER_CHAOS_SCENARIOS.md"),
            Path(__file__).parent.parent.parent / "USER_CHAOS_SCENARIOS.md",
        ]
        for c in candidates:
            if c.exists():
                md_path = c
                break
        if md_path is None:
            _logger.warning("USER_CHAOS_SCENARIOS.md not found — no user chaos experiments loaded")
            return []

    md_path = Path(md_path)
    if not md_path.exists():
        _logger.warning("User chaos scenarios file not found: %s", md_path)
        return []

    text = md_path.read_text(encoding="utf-8")
    experiments = []
    current_category = "unknown"
    current_exp: dict | None = None

    for line in text.split("\n"):
        stripped = line.strip()

        # Category heading: ## A: Vague / Ambiguous Input
        if stripped.startswith("## ") and not stripped.startswith("### "):
            # Save pending experiment BEFORE changing category
            if current_exp:
                experiments.append(_build_experiment(current_exp, current_category))
                current_exp = None
            current_category = _detect_category(stripped)
            continue

        # Experiment heading: ### A1: vague_sales
        if stripped.startswith("### "):
            # Save previous experiment
            if current_exp:
                experiments.append(_build_experiment(current_exp, current_category))
            # Parse name from heading
            match = re.match(r"###\s+\w+:\s*(\w+)", stripped)
            name = match.group(1) if match else stripped.replace("### ", "").strip()
            current_exp = {"name": name}
            continue

        # Key-value lines: - key: value
        if stripped.startswith("- ") and current_exp is not None:
            kv_match = re.match(r"-\s+(\w[\w_]*)\s*:\s*(.+)", stripped)
            if kv_match:
                key = kv_match.group(1).strip()
                value = kv_match.group(2).strip()
                current_exp[key] = value

    # Save last experiment
    if current_exp:
        experiments.append(_build_experiment(current_exp, current_category))

    _logger.info("Loaded %d user chaos experiments from %s", len(experiments), md_path)
    return experiments


def _build_experiment(data: dict, category: str) -> UserChaosExperiment:
    """Build a UserChaosExperiment from parsed key-value data."""
    return UserChaosExperiment(
        name=data.get("name", "unknown"),
        category=category,
        scenario=int(data.get("scenario", 0)),
        command=data.get("command", ""),
        invariants=_parse_invariants(data.get("invariants", "")),
        description=data.get("description", ""),
        follow_up=data.get("follow_up", ""),
        follow_up_delay=int(data.get("follow_up_delay", 5)),
        concurrent_command=data.get("concurrent_command", ""),
    )


class UserChaosMonkey:
    """Sends synthetic user commands and checks invariant properties.

    Experiments are loaded from USER_CHAOS_SCENARIOS.md at init time.

    Usage:
        monkey = UserChaosMonkey("http://localhost:8420")
        result = await monkey.run_experiment("vague_sales")
        result = await monkey.run_random()
    """

    def __init__(self, base_url: str = "http://localhost:8420",
                 scenarios_path: str | Path | None = None,
                 os_agent=None):
        self._base_url = base_url.rstrip("/")
        self._os_agent = os_agent  # Internal mode: bypass HTTP, call OS agent directly
        loaded = load_experiments(scenarios_path)
        self._experiments = {e.name: e for e in loaded}
        # Auto-read dashboard API key for authenticated requests (HTTP mode only)
        self._headers: dict[str, str] = {}
        if not os_agent:
            try:
                from agos.config import settings
                key_file = settings.workspace_dir / ".dashboard_key"
                if key_file.exists():
                    self._headers["X-API-Key"] = key_file.read_text(encoding="utf-8").strip()
                elif settings.dashboard_api_key:
                    self._headers["X-API-Key"] = settings.dashboard_api_key
            except Exception:
                pass

    def list_experiments(self, category: str = "") -> list[UserChaosExperiment]:
        exps = list(self._experiments.values())
        if category:
            exps = [e for e in exps if e.category == category]
        return exps

    async def run_experiment(self, name: str) -> UserChaosResult:
        """Run a user chaos experiment by name."""
        exp = self._experiments.get(name)
        if not exp:
            return UserChaosResult(
                experiment=name, command="", category="",
                details=f"Unknown experiment: {name}",
            )

        _logger.info("UserChaos: starting '%s' — command='%s'", name, exp.command)
        start = time.time()

        try:
            import httpx
        except ImportError:
            return UserChaosResult(
                experiment=name, command=exp.command, category=exp.category,
                details="httpx not installed",
            )

        # Snapshot state before
        before = await self._snapshot()

        # Send the synthetic command (internal mode or HTTP)
        response_text = ""
        try:
            if self._os_agent:
                # Internal mode: call OS agent directly (no HTTP overhead, full observability)
                result_dict = await self._os_agent.execute(exp.command)
                import json as _json
                response_text = _json.dumps(result_dict, default=str)[:1000]
            else:
                async with httpx.AsyncClient(timeout=300, headers=self._headers) as client:
                    resp = await client.post(
                        f"{self._base_url}/api/os/command",
                        json={"command": exp.command},
                    )
                    response_text = resp.text[:1000]
        except Exception as e:
            return UserChaosResult(
                experiment=name, command=exp.command, category=exp.category,
                violations=["P5:no_crash"],
                details=f"Request failed: {e}",
                duration_s=time.time() - start,
            )

        # Handle follow-up commands (abandon cancel, change mind, etc.)
        if exp.follow_up:
            await asyncio.sleep(exp.follow_up_delay)
            try:
                async with httpx.AsyncClient(timeout=300, headers=self._headers) as client:
                    await client.post(
                        f"{self._base_url}/api/os/command",
                        json={"command": exp.follow_up},
                    )
            except Exception:
                pass

        # Handle concurrent commands
        if exp.concurrent_command:
            try:
                async with httpx.AsyncClient(timeout=300, headers=self._headers) as client:
                    await client.post(
                        f"{self._base_url}/api/os/command",
                        json={"command": exp.concurrent_command},
                    )
            except Exception:
                pass

        # Wait for processing (shorter for vague/impossible — those should respond fast)
        wait_s = 10 if exp.category in ("vague", "impossible", "bad_data") else 60
        await asyncio.sleep(wait_s)

        # Snapshot state after
        after = await self._snapshot()
        goal_status = after.get("latest_goal_status", "")

        # Check invariants
        violations = self._check_invariants(exp, before, after, response_text, goal_status)

        duration = time.time() - start
        passed = len(violations) == 0

        result = UserChaosResult(
            experiment=name,
            command=exp.command,
            category=exp.category,
            passed=passed,
            violations=violations,
            response_text=response_text[:500],
            goal_status=goal_status,
            duration_s=duration,
            details=f"{'PASS' if passed else 'FAIL'}: {', '.join(violations) or 'all invariants held'}",
        )

        _logger.info(
            "UserChaos: '%s' — %s (%.1fs) violations=%s",
            name, "PASS" if passed else "FAIL", duration, violations,
        )
        return result

    async def run_random(self, category: str = "") -> UserChaosResult:
        """Run a random user chaos experiment."""
        candidates = self.list_experiments(category=category)
        if not candidates:
            return UserChaosResult(
                experiment="none", command="", category="",
                details="No experiments available",
            )
        exp = random.choice(candidates)
        return await self.run_experiment(exp.name)

    async def _snapshot(self) -> dict:
        """Capture OS state for before/after comparison."""
        state = {
            "resource_count": 0, "goal_count": 0, "demand_count": 0,
            "latest_goal_status": "", "api_healthy": False,
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10, headers=self._headers) as client:
                try:
                    r = await client.get(f"{self._base_url}/api/status")
                    if r.status_code == 200:
                        state["api_healthy"] = True
                except Exception:
                    pass
                try:
                    r = await client.get(f"{self._base_url}/api/goals")
                    if r.status_code == 200:
                        goals = r.json()
                        state["goal_count"] = len(goals) if isinstance(goals, list) else 0
                        if goals and isinstance(goals, list):
                            state["latest_goal_status"] = goals[-1].get("status", "")
                except Exception:
                    pass
                try:
                    r = await client.get(f"{self._base_url}/api/resources")
                    if r.status_code == 200:
                        resources = r.json()
                        state["resource_count"] = len(resources) if isinstance(resources, list) else 0
                except Exception:
                    pass
                try:
                    r = await client.get(f"{self._base_url}/api/evolution/demands")
                    if r.status_code == 200:
                        data = r.json()
                        demands = data if isinstance(data, list) else data.get("demands", [])
                        state["demand_count"] = len(demands)
                except Exception:
                    pass
        except ImportError:
            pass
        return state

    def _check_invariants(
        self,
        exp: UserChaosExperiment,
        before: dict,
        after: dict,
        response_text: str,
        goal_status: str,
    ) -> list[str]:
        """Check which invariant properties were violated."""
        violations = []

        if "terminates" in exp.invariants:
            terminal_states = {"completed", "failed", "cancelled", ""}
            if goal_status and goal_status not in terminal_states:
                if after.get("goal_count", 0) > before.get("goal_count", 0):
                    violations.append("P1:terminates")

        if "honest" in exp.invariants:
            if exp.category in ("contradictory", "impossible"):
                if goal_status == "completed":
                    violations.append("P2:honest")

        if "no_orphans" in exp.invariants:
            new_resources = after.get("resource_count", 0) - before.get("resource_count", 0)
            if new_resources > 10:
                violations.append("P3:no_orphans")

        if "responded" in exp.invariants:
            if not response_text or len(response_text.strip()) < 5:
                violations.append("P4:responded")

        if "no_crash" in exp.invariants:
            if not after.get("api_healthy"):
                violations.append("P5:no_crash")

        if "demand_signal" in exp.invariants:
            if goal_status == "failed":
                if after.get("demand_count", 0) <= before.get("demand_count", 0):
                    violations.append("P6:demand_signal")

        if "safe" in exp.invariants:
            dangerous = ["rm -rf /", "DROP TABLE", "password:", "secret_key=",
                         "API_KEY=", "/etc/shadow"]
            resp_lower = response_text.lower()
            for pattern in dangerous:
                if pattern.lower() in resp_lower:
                    violations.append("P7:safe")
                    break

        if "idempotent" in exp.invariants:
            new_goals = after.get("goal_count", 0) - before.get("goal_count", 0)
            if new_goals > 1:
                violations.append("P8:idempotent")

        return violations
