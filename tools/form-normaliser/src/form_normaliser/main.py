"""Form normaliser sidecar.

Different web portals submit different field names for the same incident.
This sidecar maps a known set of source schemas to the canonical shape the
Incident Intake Agent consumes. The mapping is declarative below — no
code change required to add a new source schema; a future Phase 2 split
moves the mapping into ``configs/`` for hot-reload.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="form-normaliser", version="0.1.0")


# Each source-schema entry maps canonical key -> tuple of candidate source keys.
# First non-empty wins.
_SCHEMAS: dict[str, dict[str, tuple[str, ...]]] = {
    "smartops_portal_v1": {
        "reporter": ("reporter_email", "user_email"),
        "subject": ("title", "summary"),
        "body": ("description", "details"),
        "urgency": ("urgency", "priority_self"),
        "affected_service": ("service", "application"),
        "received_at": ("submitted_at",),
    },
    "legacy_intranet": {
        "reporter": ("Email",),
        "subject": ("Title",),
        "body": ("Description",),
        "urgency": ("Severity",),
        "affected_service": ("Application",),
        "received_at": ("Date",),
    },
}


class NormaliseRequest(BaseModel):
    schema_id: str = Field(..., description="Source schema identifier, e.g. 'smartops_portal_v1'")
    payload: dict[str, Any] = Field(..., description="Raw form submission")


class CanonicalForm(BaseModel):
    reporter: str | None
    subject: str | None
    body: str
    urgency: str | None
    affected_service: str | None
    received_at: str


def _first_non_empty(payload: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for key in candidates:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


@app.post("/normalise", response_model=CanonicalForm)
async def normalise(req: NormaliseRequest) -> CanonicalForm:
    mapping = _SCHEMAS.get(req.schema_id)
    if mapping is None:
        raise HTTPException(status_code=400, detail=f"Unknown schema_id: {req.schema_id}")
    extracted = {canonical: _first_non_empty(req.payload, candidates) for canonical, candidates in mapping.items()}
    body = extracted.get("body")
    if not body:
        raise HTTPException(status_code=400, detail="No body / description field found")
    return CanonicalForm(
        reporter=extracted.get("reporter"),
        subject=extracted.get("subject"),
        body=str(body),
        urgency=extracted.get("urgency"),
        affected_service=extracted.get("affected_service"),
        received_at=str(extracted.get("received_at") or datetime.now(tz=UTC).isoformat()),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "form-normaliser", "version": "0.1.0", "schemas": list(_SCHEMAS.keys())}
