"""Custom exception hierarchy for agos."""


class AgosError(Exception):
    """Base for all Agentic OS errors."""


class AgentNotFoundError(AgosError):
    """No agent with the given ID exists."""


class AgentStateError(AgosError):
    """Invalid agent state transition."""


class TokenBudgetExceededError(AgosError):
    """Agent exceeded its token budget."""


class ToolNotFoundError(AgosError):
    """Requested tool does not exist in the registry."""


class ToolExecutionError(AgosError):
    """A tool failed during execution."""


class PolicyViolationError(AgosError):
    """Action blocked by the policy engine."""


class IntentError(AgosError):
    """Failed to understand or plan for user intent."""


class IntegrationError(AgosError):
    """Failed to apply an evolution proposal."""


class IntegrationRollbackError(AgosError):
    """Failed to rollback an integration."""


class ApprovalTimeoutError(AgosError):
    """Tool call approval timed out."""


class ApprovalRejectedError(AgosError):
    """Tool call was rejected by human operator."""
