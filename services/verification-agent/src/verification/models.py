"""Domain models for the Verification Agent."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    symptoms_summary: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class PrioritySlice(BaseModel):
    priority: str = "P3"


class FixResultSlice(BaseModel):
    state: str = ""
    selected_runbook_id: str | None = None
    rollback_token: str | None = None
    what_changed: str | None = None


class VerificationInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    priority: PrioritySlice | None = None
    fix_result: FixResultSlice
    scenario_ids: list[str] = Field(default_factory=list)
    """Synthetic-monitor scenarios to replay. If empty the agent will pick
    sensible defaults from the runbook category."""


class HealthEvidence(BaseModel):
    overall_passed: bool
    probes: list[dict[str, Any]]


class SyntheticEvidence(BaseModel):
    overall_passed: bool
    results: list[dict[str, Any]]


class ComparisonEvidence(BaseModel):
    overall_improved: bool
    improved_count: int
    regressed_count: int
    metrics: list[dict[str, Any]]


class VerificationResult(BaseModel):
    incident_id: str
    fix_verified: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    residual_concerns: list[str] = Field(default_factory=list)
    soak_period_minutes: int = 0
    health: HealthEvidence | None = None
    synthetic: SyntheticEvidence | None = None
    comparison: ComparisonEvidence | None = None
