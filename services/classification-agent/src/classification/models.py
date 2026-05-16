"""Domain models for the Classification Agent."""
from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentInput(BaseModel):
    """The minimum shape Classification needs from an upstream agent.

    Matches the subset of ``incident_intake.models.Incident`` the orchestrator
    forwards. Anything not used here is intentionally dropped — agents stay
    independent."""

    incident_id: str
    affected_service: str | None = None
    service_area_hint: str | None = None       # if Intake already guessed
    symptoms_summary: str
    symptoms_verbatim: str


class LabelCandidate(BaseModel):
    service_area: str
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str                                 # "llm" | "history"


class HistoryEvidence(BaseModel):
    incident_id: str | None
    similarity: float
    service_area: str | None
    category: str | None


class DecisionStep(BaseModel):
    step: str
    detail: str


class Classification(BaseModel):
    incident_id: str
    service_area: str
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    override_reason: str | None = None
    llm_candidate: LabelCandidate | None = None
    history_candidates: list[HistoryEvidence] = Field(default_factory=list)
    taxonomy_version: str
    decision_chain: list[DecisionStep] = Field(default_factory=list)
