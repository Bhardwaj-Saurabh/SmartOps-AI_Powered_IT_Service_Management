class AgentError(Exception):
    """Base for all in-agent failures."""

    def __init__(self, message: str, *, step: int | None = None, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.__cause__ = cause


class ConfigError(AgentError):
    pass


class GatewayError(AgentError):
    """Raised when the AI Gateway (LiteLLM) call fails after retries."""


class ToolError(AgentError):
    """Raised when a tool sidecar HTTP call fails after retries."""


class SemanticPlaneError(AgentError):
    """Raised when the Strategic Business Context Agent cannot answer.

    DI AI Framework §5: agents MUST hard-fail on SBCA errors. Never fall back
    to hardcoded thresholds — that defeats the dynamic-governance design.
    """
