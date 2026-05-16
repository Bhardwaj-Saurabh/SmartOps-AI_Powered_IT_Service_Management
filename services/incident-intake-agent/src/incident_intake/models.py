"""Internal pydantic models for the Incident Intake workflow.

Distinct from the A2A wire types in libs/a2a_server. These are the *canonical
incident shape* the agent emits as a Task artifact.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Channel(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    FORM = "form"
    MONITORING = "monitoring"
    UNKNOWN = "unknown"


class RawInput(BaseModel):
    """What callers send. One of the three payload kinds must be non-null."""

    channel: Channel | None = None  # if missing, agent detects in step 2
    email_raw: str | None = None
    slack_event: dict | None = None
    form: dict | None = None        # {schema_id, payload}


class ExtractedIncident(BaseModel):
    """Step 3 output — LLM-extracted entities. Strict schema by design so
    json_schema response_format on Azure Foundry validates server-side."""

    reporter: str | None
    affected_service: str | None
    service_area: str | None              # network/application/security/...
    symptoms_verbatim: str
    symptoms_summary: str
    urgency: Literal["low", "medium", "high", "critical"] | None
    reported_at: str | None


class DuplicateCheck(BaseModel):
    similarity: float
    matched_incident_id: str | None
    matched_title: str | None
    is_duplicate: bool


class Incident(BaseModel):
    """Step 11 output — the structured record handed off downstream."""

    incident_id: str
    state: Literal["new", "duplicate", "needs_clarification"] = "new"
    channel: Channel
    reporter: str | None
    reporter_vip: bool = False
    reporter_department: str | None = None
    affected_service: str | None
    service_area: str | None
    symptoms_verbatim: str
    symptoms_summary: str
    urgency: str | None
    reported_at: str | None
    correlation_id: str | None
    duplicate_of: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    clarification_questions: str | None = None
