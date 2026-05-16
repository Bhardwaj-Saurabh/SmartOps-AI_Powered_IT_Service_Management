"""Domain models for the Routing Agent.

Input is the rolling composite from the Triage Orchestrator: incident +
classification + priority. Output is a routing decision with full ranking.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    reporter_vip: bool = False
    reporter_department: str | None = None
    symptoms_summary: str
    symptoms_verbatim: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class PrioritySlice(BaseModel):
    priority: str           # P1..P4


class RoutingInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    priority: PrioritySlice


class TeamCandidate(BaseModel):
    team_id: str
    available: bool
    queue_depth: int
    match_score: float = Field(ge=0.0, le=1.0)
    llm_score: float | None = Field(default=None, ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    excluded_reason: str | None = None


class DecisionStep(BaseModel):
    step: str
    detail: str


class Routing(BaseModel):
    incident_id: str
    assigned_team: str | None
    candidate_ranking: list[TeamCandidate]
    decision_chain: list[DecisionStep]
