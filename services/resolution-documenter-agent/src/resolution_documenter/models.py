"""Domain models for the Resolution Documenter Agent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    symptoms_summary: str = ""
    symptoms_verbatim: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class DiagnosisSlice(BaseModel):
    root_cause: str | None = None
    cause_type: str | None = None
    confidence: float | None = None


class FixResultSlice(BaseModel):
    selected_runbook_id: str | None = None
    rollback_token: str | None = None
    what_changed: str | None = None
    changed_resources: list[str] = Field(default_factory=list)


class VerificationSlice(BaseModel):
    fix_verified: bool = False
    reasoning: str = ""


class DocumenterInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    diagnosis: DiagnosisSlice | None = None
    fix_result: FixResultSlice
    verification: VerificationSlice | None = None


class DocumentationResult(BaseModel):
    incident_id: str
    decision: Literal["created", "updated", "drafted", "skipped"]
    template_id: str
    article_id: str | None = None
    article_is_draft: bool = True
    rendered_markdown: str = ""
    note_title: str = ""
    keywords: list[str] = Field(default_factory=list)
