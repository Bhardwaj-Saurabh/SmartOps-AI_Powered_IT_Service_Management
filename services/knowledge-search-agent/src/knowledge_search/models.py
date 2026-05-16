"""Domain models for the Knowledge Search Agent."""
from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentSlice(BaseModel):
    incident_id: str
    affected_service: str | None = None
    symptoms_summary: str
    symptoms_verbatim: str = ""


class ClassificationSlice(BaseModel):
    service_area: str
    category: str


class DiagnosisSlice(BaseModel):
    """Optional — accepted when chained after Diagnostic."""
    root_cause: str | None = None
    cause_type: str | None = None
    confidence: float | None = None


class KnowledgeInput(BaseModel):
    incident: IncidentSlice
    classification: ClassificationSlice
    diagnosis: DiagnosisSlice | None = None
    limit: int = Field(default=5, ge=1, le=20)


class ArticleResult(BaseModel):
    article_id: str
    title: str
    service: str
    category: str
    vector_score: float | None = None
    keyword_score: float | None = None
    combined_score: float = Field(ge=0.0, le=1.0)
    relevance_score: float = Field(ge=0.0, le=1.0, description="LLM-reranked final score")
    is_stale: bool = False
    updated_at_days_ago: int = 0
    excerpt: str = ""
    reasoning: str | None = None


class DecisionStep(BaseModel):
    step: str
    detail: str


class KnowledgeResult(BaseModel):
    incident_id: str
    articles: list[ArticleResult]
    applicability_summary: str | None = None
    stale_flagged_count: int = 0
    decision_chain: list[DecisionStep] = []
