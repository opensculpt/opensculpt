"""Artifact scoring engine for federated evolution.

Scores evolved artifacts on 3 dimensions:
  - Efficacy: did it resolve its triggering demand?
  - Adoption: how many fleet nodes use it?
  - Stability: time since deploy without regression?

Composite score feeds into DesignArchive.current_fitness for ALMA
softmax selection and into SyncManifest for cross-fleet scoring.

Research basis:
  - SICA multi-objective utility (arxiv:2504.15228)
  - ALMA archive selection (arxiv:2602.07755)
  - HyperAgents novelty + fitness (arxiv:2603.19461)
  - OpenSpace skill lifecycle (HKUDS)
  - crates.io / npm trust-by-usage signals
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

# ── Scoring weights (SICA-inspired multi-objective) ──────────────

W_EFFICACY = 0.5   # Did the artifact resolve its demand?
W_ADOPTION = 0.3   # How many fleet nodes adopted it?
W_STABILITY = 0.2  # Time deployed without regression?


@dataclass
class ArtifactScore:
    """Score for a single evolved artifact."""

    artifact_id: str = ""
    efficacy: float = 0.0       # 0–1: demand resolved?
    adoption: float = 0.0       # 0–1: fraction of fleet using it
    stability: float = 0.0      # 0–1: time-based (1.0 = 7+ days stable)
    demand_key: str = ""        # which demand triggered this
    deployed_at: float = 0.0    # timestamp of deployment
    regressed: bool = False     # demand re-escalated after deploy?

    @property
    def composite(self) -> float:
        """Weighted composite score in [0, 1]."""
        return (
            W_EFFICACY * self.efficacy
            + W_ADOPTION * self.adoption
            + W_STABILITY * self.stability
        )

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "efficacy": round(self.efficacy, 3),
            "adoption": round(self.adoption, 3),
            "stability": round(self.stability, 3),
            "composite": round(self.composite, 3),
            "demand_key": self.demand_key,
            "deployed_at": self.deployed_at,
            "regressed": self.regressed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ArtifactScore:
        return cls(
            artifact_id=d.get("artifact_id", ""),
            efficacy=d.get("efficacy", 0.0),
            adoption=d.get("adoption", 0.0),
            stability=d.get("stability", 0.0),
            demand_key=d.get("demand_key", ""),
            deployed_at=d.get("deployed_at", 0.0),
            regressed=d.get("regressed", False),
        )


# ── Local Scorer — runs inside each node ─────────────────────────

class LocalScorer:
    """Scores artifacts using local demand resolution data.

    After each evolution cycle, call update() to refresh scores based
    on whether demands moved to 'resolved' after artifact deployment.
    """

    def __init__(self) -> None:
        self.scores: dict[str, ArtifactScore] = {}  # artifact_id → score

    def register_artifact(
        self,
        artifact_id: str,
        demand_key: str,
        deployed_at: float | None = None,
    ) -> None:
        """Register a newly deployed artifact for scoring."""
        self.scores[artifact_id] = ArtifactScore(
            artifact_id=artifact_id,
            demand_key=demand_key,
            deployed_at=deployed_at or time.time(),
        )

    def update(self, demand_collector) -> None:
        """Update scores based on current demand states.

        Call this at the end of each evolution cycle.
        """
        if demand_collector is None:
            return

        all_demands = {d.key: d for d in demand_collector.top_demands(limit=100, include_all=True)}

        for art_id, score in self.scores.items():
            if not score.demand_key:
                continue

            demand = all_demands.get(score.demand_key)
            if demand is None:
                # Demand was cleared entirely — strong signal of resolution
                score.efficacy = 1.0
            elif demand.status == "resolved":
                score.efficacy = 1.0
            elif demand.status == "escalated":
                # Demand escalated AFTER we deployed → regression
                if score.deployed_at and demand.last_attempt_at > score.deployed_at:
                    score.efficacy = 0.0
                    score.regressed = True
            elif demand.status == "attempting":
                score.efficacy = 0.3  # Still trying
            else:
                score.efficacy = 0.1  # Active, not yet tried

            # Stability: days since deploy without regression
            if score.deployed_at and not score.regressed:
                days = (time.time() - score.deployed_at) / 86400
                score.stability = min(1.0, days / 7.0)  # Caps at 7 days
            elif score.regressed:
                score.stability = 0.0

    def get_scores(self) -> dict[str, float]:
        """Return artifact_id → composite score mapping."""
        return {aid: s.composite for aid, s in self.scores.items()}

    def to_dict(self) -> dict:
        return {aid: s.to_dict() for aid, s in self.scores.items()}

    @classmethod
    def from_dict(cls, d: dict) -> LocalScorer:
        scorer = cls()
        for aid, sd in d.items():
            scorer.scores[aid] = ArtifactScore.from_dict(sd)
        return scorer


# ── Fleet Scorer — aggregates across nodes ───────────────────────

class FleetScorer:
    """Aggregates artifact scores across fleet peers.

    Reads peer SyncManifest data to compute cross-fleet scores.
    Used by the curator to rank artifacts for releases.
    """

    def score_artifact(
        self,
        artifact_id: str,
        local_score: ArtifactScore,
        peer_manifests: list[dict],
    ) -> ArtifactScore:
        """Compute fleet-wide score for an artifact.

        Args:
            artifact_id: The artifact to score
            local_score: This node's local score
            peer_manifests: List of SyncManifest.to_dict() from peers
        """
        # Adoption: how many peers have this artifact deployed?
        total_peers = max(len(peer_manifests), 1)
        adopters = sum(
            1 for m in peer_manifests
            if artifact_id in m.get("tools_deployed", [])
            or artifact_id in m.get("artifact_scores", {})
        )
        adoption = min(1.0, adopters / total_peers)

        # Cross-fleet efficacy: average efficacy across all nodes that have it
        efficacies = [local_score.efficacy]
        for m in peer_manifests:
            peer_scores = m.get("artifact_scores", {})
            if artifact_id in peer_scores:
                peer_data = peer_scores[artifact_id]
                if isinstance(peer_data, dict):
                    efficacies.append(peer_data.get("efficacy", 0.0))
                elif isinstance(peer_data, (int, float)):
                    efficacies.append(float(peer_data))
        avg_efficacy = sum(efficacies) / len(efficacies)

        return ArtifactScore(
            artifact_id=artifact_id,
            efficacy=avg_efficacy,
            adoption=adoption,
            stability=local_score.stability,
            demand_key=local_score.demand_key,
            deployed_at=local_score.deployed_at,
            regressed=local_score.regressed,
        )

    def score_all(
        self,
        local_scorer: LocalScorer,
        peer_manifests: list[dict],
    ) -> dict[str, ArtifactScore]:
        """Score all local artifacts against fleet data."""
        results: dict[str, ArtifactScore] = {}
        for artifact_id, local_score in local_scorer.scores.items():
            results[artifact_id] = self.score_artifact(
                artifact_id, local_score, peer_manifests,
            )
        return results


# ── Wire into DesignArchive ──────────────────────────────────────

def update_archive_scores(
    archive,
    scorer: LocalScorer,
) -> int:
    """Push artifact scores into DesignArchive.current_fitness.

    Returns number of entries updated.
    """
    updated = 0
    scores = scorer.get_scores()
    for entry in archive.entries:
        if entry.id in scores:
            entry.current_fitness = scores[entry.id]
            updated += 1
    return updated
