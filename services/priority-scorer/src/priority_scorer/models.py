"""Composite input + output models for the Priority Scorer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    """Subset of the Incident the scorer needs."""
    incident_id: str
    affected_service: str | None = None
    service_area: str | None = None
    reporter_vip: bool = False
    reporter_department: str | None = None
    symptoms_summary: str
    symptoms_verbatim: str = ""


class ClassificationSlice(BaseModel):
    """Subset of the Classification artifact."""
    service_area: str
    category: str
    confidence: float = 0.0
    override_reason: str | None = None


class PriorityInput(BaseModel):
    """Composite input shape — assembled by the Triage Orchestrator from
    the artifacts emitted by Incident Intake + Classification."""
    incident: IncidentSlice
    classification: ClassificationSlice


class DecisionStep(BaseModel):
    step: str
    detail: str


class Priority(BaseModel):
    incident_id: str
    priority: Literal["P1", "P2", "P3", "P4"]
    impact: Literal["low", "medium", "high", "critical"]
    urgency: Literal["low", "medium", "high", "critical"]
    blast_radius: int
    service_tier: str | None = None
    impact_score: float = Field(ge=0.0, le=1.0)
    vip_override: str | None = None
    change_freeze_active: bool = False
    decision_chain: list[DecisionStep]
