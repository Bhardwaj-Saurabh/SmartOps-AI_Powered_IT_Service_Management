"""Domain models for the Problem Linker Agent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    symptoms_summary: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class DiagnosisSlice(BaseModel):
    root_cause: str | None = None


class LinkerInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    diagnosis: DiagnosisSlice | None = None
    similarity_signature: str | None = None
    """Optional caller-supplied signature. If omitted the agent derives it
    from the diagnosis root_cause string (synthetic Phase 1)."""


class ClusterSnapshot(BaseModel):
    signature: str
    incident_ids: list[str]
    size: int
    cohesion: float
    distinct_reporter_departments: list[str]


class LinkerResult(BaseModel):
    incident_id: str
    decision: Literal["linked", "new_problem_recommended", "below_threshold",
                       "not_eligible", "no_history"]
    linked_problem_id: str | None = None
    recommended_problem_title: str | None = None
    recommended_recurrence_pattern: str | None = None
    clusters: list[ClusterSnapshot] = Field(default_factory=list)
    llm_confidence: float = 0.0
    llm_reasoning: str = ""
    scope_note: str = ""
