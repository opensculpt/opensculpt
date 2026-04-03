"""Peer-to-peer evolution sync — share learnings directly between nodes.

Replaces GitHub PRs for fleet-scale evolution sharing. Each node exposes
a lightweight sync API (via the dashboard FastAPI app) and periodically
pushes/pulls evolution state to/from peers.

What gets synced:
  - Evolution memory (what worked / what failed across cycles)
  - Evolved code files (.agos/evolved/*.py)
  - Design archive entries (fitness-scored strategies)

What does NOT get synced (stays local):
  - Raw fitness signals (node-specific)
  - Meta-evolver genome state (each node adapts independently)
  - Pending insights queue (each node scouts its own papers)

GitHub PRs remain as a fallback for:
  - Nodes without direct network access to peers
  - Cross-organization sharing (public contributions)
  - Permanent record of evolution history

Protocol:
  1. Node A calls GET /api/sync/manifest on Node B
  2. Manifest contains: instance_id, cycles, file hashes, memory size
  3. Node A compares with local state, requests only what's new
  4. Node A calls GET /api/sync/pull to get the delta
  5. Node A merges: memory (dedup), code (sandbox-validate), archive (merge)
  6. Reverse direction: Node B pulls from Node A

This is gossip-style: each node syncs with its known peers, and
discoveries propagate transitively through the network.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from agos.config import settings
from agos.events.bus import EventBus
from agos.evolution.state import (
    EvolutionState, EvolutionMemory, DesignArchive, DesignEntry,
)

_logger = logging.getLogger(__name__)


class SyncManifest:
    """What a node advertises about its evolution state.

    Includes efficacy data (resolved demands, deployed tools, artifact
    scores) so the fleet can score artifacts cross-node.
    """

    def __init__(
        self,
        instance_id: str = "",
        cycles_completed: int = 0,
        memory_size: int = 0,
        archive_size: int = 0,
        evolved_file_hashes: dict[str, str] | None = None,
        timestamp: float = 0.0,
    ) -> None:
        self.instance_id = instance_id
        self.cycles_completed = cycles_completed
        self.memory_size = memory_size
        self.archive_size = archive_size
        self.evolved_file_hashes = evolved_file_hashes or {}  # kept for backward compat with older peers
        self.timestamp = timestamp or time.time()

        self.constraints_count: int = 0
        self.resolutions_count: int = 0
        self.active_domains: list[str] = []

        # Efficacy data for cross-fleet scoring
        self.resolved_demands: list[str] = []       # demand keys this node resolved
        self.tools_deployed: list[str] = []          # tool names actively deployed
        self.artifact_scores: dict[str, dict] = {}   # artifact_id → score dict

        # Environment tags — so peers only send constraints matching OUR environment
        self.environment_tags: list[str] = []

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "cycles_completed": self.cycles_completed,
            "memory_size": self.memory_size,
            "archive_size": self.archive_size,
            "evolved_file_hashes": self.evolved_file_hashes,
            "timestamp": self.timestamp,
            "constraints_count": self.constraints_count,
            "resolutions_count": self.resolutions_count,
            "active_domains": self.active_domains,
            "resolved_demands": self.resolved_demands,
            "tools_deployed": self.tools_deployed,
            "artifact_scores": self.artifact_scores,
            "environment_tags": self.environment_tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SyncManifest:
        init_keys = {"instance_id", "cycles_completed", "memory_size",
                     "archive_size", "evolved_file_hashes", "timestamp"}
        m = cls(**{k: v for k, v in data.items() if k in init_keys})
        m.constraints_count = data.get("constraints_count", 0)
        m.resolutions_count = data.get("resolutions_count", 0)
        m.active_domains = data.get("active_domains", [])
        m.resolved_demands = data.get("resolved_demands", [])
        m.tools_deployed = data.get("tools_deployed", [])
        m.artifact_scores = data.get("artifact_scores", {})
        m.environment_tags = data.get("environment_tags", [])
        return m


def build_local_manifest(
    evolution_state: EvolutionState,
    demand_collector=None,
    local_scorer=None,
) -> SyncManifest:
    """Build a manifest from local evolution state, including efficacy data."""
    data = evolution_state.data

    # No evolved file hashes — code stays local, only knowledge syncs
    manifest = SyncManifest(
        instance_id=data.instance_id,
        cycles_completed=data.cycles_completed,
        memory_size=len(data.evolution_memory.get("insights", [])) if data.evolution_memory else 0,
        archive_size=len(data.design_archive.get("entries", [])) if data.design_archive else 0,
    )

    # Environment tags — tells peers what kind of constraints we need
    try:
        from agos.knowledge.tagged_store import environment_tags
        manifest.environment_tags = environment_tags()
    except Exception:
        pass

    return manifest


def build_sync_payload(
    evolution_state: EvolutionState,
    remote_manifest: SyncManifest,
) -> dict:
    """Build a delta payload — only data the remote doesn't have."""
    data = evolution_state.data
    payload: dict = {
        "instance_id": data.instance_id,
        "cycles_completed": data.cycles_completed,
    }

    # Evolution memory (remote will dedup on merge)
    if data.evolution_memory:
        payload["evolution_memory"] = data.evolution_memory

    # Design archive entries the remote doesn't have
    if data.design_archive and data.design_archive.get("entries"):
        payload["design_archive_entries"] = data.design_archive["entries"]

    # Code stays local — only knowledge syncs via P2P.
    # Users share code via git PRs (standard open source workflow).

    # Share tagged knowledge files — only send tags matching REMOTE environment
    remote_tags = set(remote_manifest.environment_tags or ["general"])
    constraint_files: dict[str, str] = {}
    resolution_files: dict[str, str] = {}
    try:
        _cdir = Path(settings.workspace_dir) / "constraints"
        if _cdir.exists():
            for f in _cdir.glob("*.md"):
                # Send general + files matching remote's environment
                if f.stem == "general" or f.stem in remote_tags or f.stem == "_index":
                    constraint_files[f.stem] = f.read_text(encoding="utf-8")
        _rdir = Path(settings.workspace_dir) / "resolutions"
        if _rdir.exists():
            for f in _rdir.glob("*.md"):
                resolution_files[f.stem] = f.read_text(encoding="utf-8")
    except Exception as e:
        _logger.debug("Failed to read tagged knowledge for sync: %s", e)
    payload["constraint_files"] = constraint_files
    payload["resolution_files"] = resolution_files

    # Legacy flat files as fallback (for peers that haven't upgraded)
    try:
        _constraints_path = Path(settings.workspace_dir) / "constraints.md"
        _resolutions_path = Path(settings.workspace_dir) / "resolutions.md"
        payload["constraints_md"] = _constraints_path.read_text(encoding="utf-8") if _constraints_path.exists() else ""
        payload["resolutions_md"] = _resolutions_path.read_text(encoding="utf-8") if _resolutions_path.exists() else ""
    except Exception:
        payload["constraints_md"] = ""
        payload["resolutions_md"] = ""

    return payload


async def pull_from_peer(
    peer_url: str,
    evolution_state: EvolutionState,
    evo_memory: EvolutionMemory,
    design_archive: DesignArchive,
    bus: EventBus,
) -> dict:
    """Pull evolution data from a single peer.

    1. GET /api/sync/manifest — what does the peer have?
    2. Compare with local state
    3. GET /api/sync/pull — get the delta
    4. Merge locally (memory dedup, code sandbox-validate, archive merge)

    Returns {"merged_memory": N, "merged_files": N, "merged_archive": N}.
    """
    result = {"merged_memory": 0, "merged_files": 0, "merged_archive": 0, "peer": peer_url}

    api_key = settings.dashboard_api_key
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        # Step 1: Get peer's manifest
        try:
            resp = await client.get(f"{peer_url}/api/sync/manifest")
            if resp.status_code != 200:
                _logger.debug("Sync manifest failed from %s: %d", peer_url, resp.status_code)
                return result
            _remote_manifest = SyncManifest.from_dict(resp.json())
        except Exception as e:
            _logger.debug("Sync manifest error from %s: %s", peer_url, e)
            return result

        # Step 2: Get the sync payload
        try:
            local_manifest = build_local_manifest(evolution_state)
            resp = await client.post(
                f"{peer_url}/api/sync/pull",
                json=local_manifest.to_dict(),
            )
            if resp.status_code != 200:
                _logger.debug("Sync pull failed from %s: %d", peer_url, resp.status_code)
                return result
            payload = resp.json()
        except Exception as e:
            _logger.debug("Sync pull error from %s: %s", peer_url, e)
            return result

    # Security: reject unsigned payloads (C3 fix)
    if "signature" not in payload:
        _logger.warning(
            "Rejecting unsigned sync payload from %s. "
            "Peer must upgrade to support payload signing.", peer_url
        )
        await bus.emit("sync.unsigned_payload_rejected", {
            "peer": peer_url,
            "instance_id": payload.get("instance_id", ""),
        }, source="fleet_sync")
        return result

    # Step 3: Merge evolution memory
    remote_memory = payload.get("evolution_memory")
    if remote_memory and evo_memory is not None:
        merged = evo_memory.merge_remote(
            remote_memory,
            source_instance=payload.get("instance_id", ""),
        )
        result["merged_memory"] = merged

    # Code no longer syncs P2P — users share via git PRs.
    # Ignore evolved_files from older peers silently.

    # Step 5: Merge design archive entries
    remote_entries = payload.get("design_archive_entries", [])
    if remote_entries and design_archive is not None:
        existing_hashes = {e.code_hash for e in design_archive.entries if e.code_hash}
        for entry_data in remote_entries:
            code_hash = entry_data.get("code_hash", "")
            if code_hash and code_hash not in existing_hashes:
                try:
                    entry = DesignEntry(**entry_data)
                    design_archive.add(entry)
                    result["merged_archive"] += 1
                    existing_hashes.add(code_hash)
                except Exception:
                    pass

    # Step 6: Merge federated knowledge (tagged .md files with fingerprint dedup)
    result["merged_constraints"] = 0
    result["merged_resolutions"] = 0
    try:
        from agos.knowledge.tagged_store import TaggedConstraintStore, TaggedResolutionStore

        # Prefer tagged files (new format) over flat .md (legacy)
        remote_constraint_files = payload.get("constraint_files", {})
        remote_resolution_files = payload.get("resolution_files", {})

        if remote_constraint_files:
            _cs = TaggedConstraintStore()
            for tag, content in remote_constraint_files.items():
                if tag == "_index":
                    continue  # Don't merge indexes, they're auto-generated
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        if _cs.add(line[2:], source=f"federated:{payload.get('instance_id', peer_url)}"):
                            result["merged_constraints"] += 1

        if remote_resolution_files:
            _rs = TaggedResolutionStore()
            for cat, content in remote_resolution_files.items():
                if cat == "_index":
                    continue
                for section in content.split("\n## ")[1:]:
                    lines = section.strip().split("\n")
                    if not lines:
                        continue
                    symptom = lines[0].strip()
                    fix = ""
                    root_cause = ""
                    for line in lines[1:]:
                        if line.strip().startswith("- Fix:"):
                            fix = line.strip()[6:].strip()
                        elif line.strip().startswith("- Root cause:"):
                            root_cause = line.strip()[13:].strip()
                    if symptom and fix:
                        if _rs.add(symptom, fix, root_cause, source=f"federated:{payload.get('instance_id', peer_url)}"):
                            result["merged_resolutions"] += 1

        # Legacy fallback: flat .md files from peers that haven't upgraded
        if not remote_constraint_files:
            remote_constraints = payload.get("constraints_md", "")
            if remote_constraints:
                _cs = TaggedConstraintStore()
                for line in remote_constraints.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        if _cs.add(line[2:], source=f"federated:{payload.get('instance_id', peer_url)}"):
                            result["merged_constraints"] += 1

        if not remote_resolution_files:
            remote_resolutions = payload.get("resolutions_md", "")
            if remote_resolutions:
                _rs = TaggedResolutionStore()
                for section in remote_resolutions.split("\n## ")[1:]:
                    lines = section.strip().split("\n")
                    if not lines:
                        continue
                    symptom = lines[0].strip()
                    fix = ""
                    for line in lines[1:]:
                        if line.strip().startswith("- Fix:"):
                            fix = line.strip()[6:].strip()
                    if symptom and fix:
                        if _rs.add(symptom, fix, source=f"federated:{payload.get('instance_id', peer_url)}"):
                            result["merged_resolutions"] += 1

    except Exception as e:
        _logger.debug("Failed to merge federated knowledge from %s: %s", peer_url, e)

    # Persist merged state
    if result["merged_memory"] > 0:
        evolution_state.save_evolution_memory(evo_memory)
    if result["merged_archive"] > 0:
        evolution_state.save_design_archive(design_archive)

    total = (result["merged_memory"] + result["merged_files"] +
             result["merged_archive"] + result["merged_constraints"] +
             result["merged_resolutions"])
    if total > 0:
        await bus.emit("sync.pull_complete", result, source="fleet_sync")
        _logger.info(
            "Synced from %s: %d memory, %d files, %d archive, %d constraints, %d resolutions",
            peer_url, result["merged_memory"], result["merged_files"],
            result["merged_archive"], result["merged_constraints"],
            result["merged_resolutions"],
        )

    return result


async def sync_loop(
    bus: EventBus,
    evolution_state: EvolutionState,
    evo_memory: EvolutionMemory,
    design_archive: DesignArchive,
) -> None:
    """Background loop that periodically syncs with all known peers.

    Runs alongside the evolution loop. Gossip-style: each node syncs
    with its direct peers, discoveries propagate transitively.
    """
    if not settings.fleet_sync_enabled:
        return

    raw_peers = [p.strip() for p in settings.fleet_sync_peers.split(",") if p.strip()]

    # H3: Reject non-HTTPS peer URLs (plain HTTP exposes API keys and payloads)
    peers = []
    for p in raw_peers:
        if p.startswith("https://") or p.startswith("http://127.0.0.1") or p.startswith("http://localhost"):
            peers.append(p)
        else:
            _logger.warning("Rejecting non-HTTPS peer URL: %s (use HTTPS or localhost)", p)

    if not peers:
        _logger.info("Fleet sync enabled but no valid peers configured")
        return

    await asyncio.sleep(30)  # Let boot complete
    interval = max(30, settings.fleet_sync_interval)

    await bus.emit("sync.loop_started", {
        "peers": peers,
        "interval": interval,
    }, source="fleet_sync")

    while True:
        for peer_url in peers:
            try:
                if settings.fleet_sync_pull:
                    await pull_from_peer(
                        peer_url, evolution_state, evo_memory,
                        design_archive, bus,
                    )
            except Exception as e:
                _logger.debug("Sync error with %s: %s", peer_url, e)
            await asyncio.sleep(2)  # Don't hammer peers

        await asyncio.sleep(interval)
