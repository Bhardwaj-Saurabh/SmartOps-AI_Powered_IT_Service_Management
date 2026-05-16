"""Domain models for the SLA Monitor Agent."""
from __future__ import annotations

from pydantic import BaseModel, Field


class StateTransition(BaseModel):
    state: str
    at_epoch: int


class SLAInput(BaseModel):
    incident_id: str
    priority: str = "P3"
    customer_tier: str = "silver"
    region: str = "UK"
    started_at_epoch: int
    now_at_epoch: int | None = None
    state_transitions: list[StateTransition] = Field(default_factory=list)
    """Chronological state changes. Used by the rules engine to compute
    paused-minutes for states in ``sla_pause_conditions``."""


class SLATargets(BaseModel):
    response: int
    resolve: int


class SLAResult(BaseModel):
    incident_id: str
    priority: str
    customer_tier: str
    region: str
    business_hours_only: bool
    targets: SLATargets

    elapsed_raw_minutes: float
    paused_minutes: float
    elapsed_adjusted_minutes: float

    response_consumed_pct: float
    resolve_consumed_pct: float
    response_breached: bool
    resolve_breached: bool
    response_warning: bool
    resolve_warning: bool
    currently_paused: bool

    narrative: str = ""
    recommended_action: str = ""
