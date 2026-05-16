"""Domain models for the Diagnostic Agent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    symptoms_summary: str
    symptoms_verbatim: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class PrioritySlice(BaseModel):
    priority: str = "P3"
    blast_radius: int = 0


class DiagnosticInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    priority: PrioritySlice = PrioritySlice()


CauseType = Literal["infrastructure", "application", "configuration", "external"]


class CandidateCause(BaseModel):
    cause: str
    cause_type: CauseType | None = None
    evidence: list[str] = Field(default_factory=list)
    validation_idea: str | None = None


class HypothesisHistoryEntry(BaseModel):
    iteration: int
    candidate_count: int
    best: CandidateCause | None
    evaluator_score: float | None = None
    evaluator_reasoning: str | None = None


class Diagnosis(BaseModel):
    incident_id: str
    state: Literal["completed", "low_confidence", "failed"]
    root_cause: str | None
    cause_type: CauseType | None
    confidence: float = Field(ge=0.0, le=1.0)
    iterations: int
    supporting_evidence: list[str] = Field(default_factory=list)
    workaround: str | None = None
    matched_known_issue: bool = False
    decision_chain: list[HypothesisHistoryEntry] = Field(default_factory=list)
