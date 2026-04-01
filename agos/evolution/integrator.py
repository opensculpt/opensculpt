"""EvolutionIntegrator — applies accepted proposals to the running OS.

Takes an accepted EvolutionProposal, finds a matching IntegrationStrategy,
snapshots the current state for rollback, applies the changes, and
verifies health. If health check fails, auto-rolls back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.exceptions import IntegrationRollbackError
from agos.evolution.engine import EvolutionProposal
from agos.knowledge.manager import TheLoom
from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.evolution.sandbox import Sandbox


class IntegrationVersion(BaseModel):
    """A recorded version of an applied integration."""

    id: str = Field(default_factory=new_id)
    proposal_id: str
    strategy_name: str
    target_module: str
    applied_at: datetime = Field(default_factory=datetime.utcnow)
    rollback_data: dict[str, Any] = Field(default_factory=dict)
    status: str = "applied"  # applied, rolled_back
    changes: list[str] = Field(default_factory=list)


class IntegrationResult(BaseModel):
    """Result of applying a proposal."""

    success: bool = False
    version_id: str = ""
    changes: list[str] = Field(default_factory=list)
    error: str = ""


class IntegrationStrategy(ABC):
    """Base class for module-specific integration strategies."""

    name: str = ""
    target_module: str = ""

    @abstractmethod
    def validate(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        """Check if this strategy can apply the proposal.

        Returns (valid, reason).
        """
        ...

    @abstractmethod
    async def snapshot(self) -> dict[str, Any]:
        """Capture current state for rollback."""
        ...

    @abstractmethod
    async def apply(self, proposal: EvolutionProposal) -> list[str]:
        """Apply the proposal. Returns list of change descriptions."""
        ...

    @abstractmethod
    async def rollback(self, snapshot_data: dict[str, Any]) -> None:
        """Restore previous state from snapshot."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the target module still works after integration."""
        ...


class EvolutionIntegrator:
    """Dispatches accepted proposals to the right strategy and tracks versions."""

    def __init__(
        self,
        loom: TheLoom | None = None,
        event_bus: EventBus | None = None,
        audit_trail: AuditTrail | None = None,
        sandbox: Sandbox | None = None,
    ) -> None:
        self._loom = loom
        self._event_bus = event_bus
        self._audit = audit_trail
        self._sandbox = sandbox
        self._strategies: dict[str, IntegrationStrategy] = {}
        self._versions: dict[str, IntegrationVersion] = {}

    def register_strategy(self, strategy: IntegrationStrategy) -> None:
        """Register a strategy for a specific module."""
        self._strategies[strategy.name] = strategy

    def get_strategies(self) -> list[IntegrationStrategy]:
        """Return all registered strategies."""
        return list(self._strategies.values())

    async def apply(self, proposal: EvolutionProposal) -> IntegrationResult:
        """Apply an accepted proposal using its matching strategy."""
        if proposal.status != "accepted":
            return IntegrationResult(
                error=f"Proposal status is '{proposal.status}', must be 'accepted'"
            )

        # Find matching strategy
        strategy = self._find_strategy(proposal)
        if not strategy:
            return IntegrationResult(
                error=f"No strategy found for module '{proposal.insight.agos_module}'"
            )

        # Validate
        valid, reason = strategy.validate(proposal)
        if not valid:
            return IntegrationResult(error=f"Validation failed: {reason}")

        await self._emit("evolution.integration_started", {
            "proposal_id": proposal.id,
            "target_module": strategy.target_module,
            "strategy_name": strategy.name,
        })

        # Snapshot for rollback
        snapshot_data = await strategy.snapshot()

        # Apply
        try:
            changes = await strategy.apply(proposal)
        except Exception as e:
            await self._emit("evolution.integration_failed", {
                "proposal_id": proposal.id,
                "error": str(e),
            })
            return IntegrationResult(error=f"Apply failed: {e}")

        # Health check
        healthy = await strategy.health_check()
        if not healthy:
            # Auto-rollback
            try:
                await strategy.rollback(snapshot_data)
            except Exception:
                pass
            await self._emit("evolution.integration_failed", {
                "proposal_id": proposal.id,
                "error": "Health check failed, rolled back",
            })
            return IntegrationResult(error="Health check failed after apply, rolled back")

        # Record version
        version = IntegrationVersion(
            proposal_id=proposal.id,
            strategy_name=strategy.name,
            target_module=strategy.target_module,
            rollback_data=snapshot_data,
            changes=changes,
        )
        self._versions[version.id] = version

        # Update proposal status
        proposal.status = "integrated"

        await self._emit("evolution.integration_completed", {
            "proposal_id": proposal.id,
            "version_id": version.id,
            "changes": changes,
        })

        if self._audit:
            await self._audit.record(
                __import__("agos.policy.audit", fromlist=["AuditEntry"]).AuditEntry(
                    agent_name="evolution_integrator",
                    action="integration_applied",
                    detail=f"Applied: {strategy.name} (proposal {proposal.id[:8]})",
                )
            )

        return IntegrationResult(
            success=True,
            version_id=version.id,
            changes=changes,
        )

    async def rollback(self, version_id: str) -> bool:
        """Rollback a previously applied integration."""
        version = self._versions.get(version_id)
        if not version:
            return False

        if version.status == "rolled_back":
            return False

        strategy = self._strategies.get(version.strategy_name)
        if not strategy:
            return False

        try:
            await strategy.rollback(version.rollback_data)
        except Exception as e:
            raise IntegrationRollbackError(
                f"Rollback failed for version {version_id}: {e}"
            ) from e

        version.status = "rolled_back"

        await self._emit("evolution.integration_rollback", {
            "version_id": version_id,
            "proposal_id": version.proposal_id,
        })

        if self._audit:
            await self._audit.record(
                __import__("agos.policy.audit", fromlist=["AuditEntry"]).AuditEntry(
                    agent_name="evolution_integrator",
                    action="integration_rolled_back",
                    detail=f"Rolled back: {version.strategy_name} (version {version_id[:8]})",
                )
            )

        return True

    async def list_integrations(
        self, status: str = ""
    ) -> list[IntegrationVersion]:
        """List integration versions, optionally filtered by status."""
        versions = list(self._versions.values())
        if status:
            versions = [v for v in versions if v.status == status]
        versions.sort(key=lambda v: v.applied_at, reverse=True)
        return versions

    def _find_strategy(self, proposal: EvolutionProposal) -> IntegrationStrategy | None:
        """Find a strategy that matches the proposal's target module."""
        module = proposal.insight.agos_module

        # Direct name match first
        for strategy in self._strategies.values():
            if strategy.name == module or strategy.target_module == module:
                return strategy

        # Partial match (e.g., "knowledge" matches "knowledge.semantic")
        for strategy in self._strategies.values():
            if module in strategy.target_module or strategy.target_module in module:
                return strategy

        return None

    async def _emit(self, topic: str, data: dict) -> None:
        if self._event_bus:
            await self._event_bus.emit(topic, data, source="evolution_integrator")
