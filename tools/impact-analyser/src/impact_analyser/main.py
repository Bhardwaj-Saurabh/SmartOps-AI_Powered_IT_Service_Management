"""Impact analyser sidecar.

Takes a few measurable inputs (blast radius from the dependency mapper,
reporter VIP flag, service tier) and produces a numeric impact score plus
a categorical bucket (low/medium/high/critical). Phase 1 weights are
hardcoded here — they're operational defaults, not business rules. The
priority *matrix* (impact × urgency → P-level) lives in the semantic
plane where it belongs.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="impact-analyser", version="0.1.0")


class AnalyseRequest(BaseModel):
    affected_users: int | None = Field(default=None, ge=0)
    blast_radius: int = Field(default=1, ge=1, description="Number of services downstream of the affected one")
    reporter_vip: bool = False
    service_tier: str | None = Field(default=None, description="Optional service tier; gold/silver/bronze")


class AnalyseResponse(BaseModel):
    impact_score: float = Field(ge=0.0, le=1.0)
    impact_bucket: str
    blast_radius_factor: float
    vip_factor: float
    tier_factor: float


def _bucket(score: float) -> str:
    if score < 0.25:
        return "low"
    if score < 0.5:
        return "medium"
    if score < 0.8:
        return "high"
    return "critical"


@app.post("/analyse", response_model=AnalyseResponse)
async def analyse(req: AnalyseRequest) -> AnalyseResponse:
    # Synthetic weighting — these are *operational defaults*, not business
    # policy. The downstream priority matrix in SBCA decides P-levels.
    users = req.affected_users or 1
    users_score = min(1.0, 0.05 + (users / 200.0))
    blast_score = min(1.0, 0.1 + (req.blast_radius / 15.0))
    vip_factor = 1.5 if req.reporter_vip else 1.0
    tier_factor = {"gold": 1.4, "silver": 1.1, "bronze": 0.9}.get((req.service_tier or "").lower(), 1.0)
    raw = (users_score * 0.4 + blast_score * 0.6) * vip_factor * tier_factor
    score = min(1.0, raw)
    return AnalyseResponse(
        impact_score=score,
        impact_bucket=_bucket(score),
        blast_radius_factor=blast_score,
        vip_factor=vip_factor,
        tier_factor=tier_factor,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "impact-analyser", "version": "0.1.0"}
