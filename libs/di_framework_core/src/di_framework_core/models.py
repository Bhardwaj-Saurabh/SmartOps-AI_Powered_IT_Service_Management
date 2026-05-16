from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Google A2A Task states (a2aproject.github.io/A2A specification).

    Note: the DI AI Framework's ``requires_human`` response status maps to
    ``INPUT_REQUIRED`` plus ``DIEnvelope.requires_human=True`` — never invent
    a non-spec state.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"


class DIEnvelope(BaseModel):
    """The DI-framework-specific metadata that rides inside
    ``Message.metadata.di`` / ``Task.metadata.di`` on every A2A exchange.

    See docs/architecture.md "A2A envelope contract" for the mapping.
    """

    model_config = ConfigDict(extra="ignore")

    capability: str | None = Field(default=None, description="Capability id; equals an Agent Card skill id")
    correlation_id: str | None = Field(default=None, description="Stable id propagated end-to-end")
    process: str | None = Field(default=None, description="Business process, e.g. 'i2r'")
    step: str | None = Field(default=None, description="Step within the process, e.g. 'triage.intake'")
    requires_human: bool = Field(default=False, description="True when paired with INPUT_REQUIRED state")
    reason: str | None = Field(default=None, description="Human-readable reason for requires_human / failure")
    duration_ms: float | None = Field(default=None, description="Response-side technical KPI")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    failed_step: int | None = Field(default=None, description="Step number that caused a failed task")
    error_class: str | None = Field(default=None, description="Upstream error class name (no stack)")


class FailureMetadata(BaseModel):
    """Convenience builder for failure responses."""

    failed_step: int
    error_class: str
    reason: str

    def to_envelope(self, *, correlation_id: str | None = None) -> DIEnvelope:
        return DIEnvelope(
            correlation_id=correlation_id,
            failed_step=self.failed_step,
            error_class=self.error_class,
            reason=self.reason,
        )
