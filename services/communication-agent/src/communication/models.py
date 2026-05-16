"""Domain models for the Communication Agent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    reporter: str | None = None
    reporter_department: str | None = None
    symptoms_summary: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class PrioritySlice(BaseModel):
    priority: str = "P3"


class DiagnosisSlice(BaseModel):
    root_cause: str | None = None


class FixResultSlice(BaseModel):
    state: str = ""
    selected_runbook_id: str | None = None
    what_changed: str | None = None


class VerificationSlice(BaseModel):
    fix_verified: bool = False
    confidence: float = 0.0


class CommunicationInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice | None = None
    priority: PrioritySlice
    diagnosis: DiagnosisSlice | None = None
    fix_result: FixResultSlice | None = None
    verification: VerificationSlice | None = None
    trigger: Literal["state_change", "escalation", "resolution", "scheduled_update"] = "state_change"
    current_state: str = "new"
    """Optional resolver-team alias to include. Phase 1: synthetic mapping."""
    resolver_team_id: str | None = None


class DispatchAttempt(BaseModel):
    audience: str
    channel: Literal["email", "slack", "sms"]
    recipients: list[str]
    subject: str = ""
    body_preview: str = ""
    cta: str = ""
    delivered: bool = False
    error: str | None = None
    sidecar_message_id: str | None = None


class CommunicationResult(BaseModel):
    incident_id: str
    attempts: list[DispatchAttempt]
    audiences_reached: list[str] = Field(default_factory=list)
    channels_used: list[str] = Field(default_factory=list)
    deliveries_attempted: int = 0
    deliveries_failed: int = 0
