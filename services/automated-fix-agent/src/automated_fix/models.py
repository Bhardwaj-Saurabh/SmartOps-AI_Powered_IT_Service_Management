"""Domain models for the Automated Fix Agent."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    affected_users: list[str] = Field(default_factory=list)
    symptoms_summary: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class PrioritySlice(BaseModel):
    priority: str = "P3"
    blast_radius: int = 0
    service_tier: str | None = None    # set by Priority Scorer; required by approval matrix
    emergency: bool = False


class DiagnosisSlice(BaseModel):
    root_cause: str | None = None
    cause_type: str | None = None
    confidence: float | None = None


class KnowledgeArticleSlice(BaseModel):
    article_id: str
    title: str
    excerpt: str = ""
    relevance_score: float = 0.0


class FixInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    priority: PrioritySlice
    diagnosis: DiagnosisSlice
    knowledge_articles: list[KnowledgeArticleSlice] = Field(default_factory=list)


class StepRecord(BaseModel):
    step_index: int
    action: str
    outcome: str
    duration_ms: float
    detail: str | None = None


class FixOutcome(BaseModel):
    incident_id: str
    state: Literal["completed", "requires_human", "rolled_back", "failed"]
    requires_human_reason: str | None = None
    selected_runbook_id: str | None = None
    runbook_parameters: dict[str, Any] = Field(default_factory=dict)
    rollback_token: str | None = None     # = configuration-manager snapshot_id
    step_log: list[StepRecord] = Field(default_factory=list)
    rollback_invoked: bool = False
    rollback_detail: dict[str, Any] | None = None
    what_changed: str | None = None
    changed_resources: list[str] = Field(default_factory=list)
    user_visible_impact: str | None = None


class RollbackInput(BaseModel):
    rollback_token: str
    reason: str = "orchestrator-initiated"


class RollbackOutcome(BaseModel):
    rollback_token: str
    restored: bool
    restored_state_keys: list[str] = Field(default_factory=list)
    note: str | None = None
