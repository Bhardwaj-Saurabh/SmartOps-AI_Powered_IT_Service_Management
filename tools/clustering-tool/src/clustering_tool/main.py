"""Clustering tool sidecar (synthetic).

Groups incidents by ``similarity_signature``. Real production would do
embedding-based clustering — Phase 1 uses string-equality + a synthetic
"cluster cohesion" score from the cluster size.

Returns one cluster per distinct signature, with the centroid signature
+ members + cohesion + distinct reporter_departments (a weak signal for
"is this multiple people experiencing the same thing").
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="clustering-tool", version="0.1.0")


class ClusterRequest(BaseModel):
    incidents: list[dict[str, Any]]
    """Each must have at minimum ``incident_id`` + ``similarity_signature``."""


class Cluster(BaseModel):
    signature: str
    incident_ids: list[str]
    size: int
    cohesion: float = Field(ge=0.0, le=1.0)
    distinct_reporter_departments: list[str]
    distinct_services: list[str]


class ClusterResponse(BaseModel):
    clusters: list[Cluster]


def _cohesion(size: int) -> float:
    # Synthetic: saturating function — 1 incident = 0.4, 2 = 0.7, 3+ = >=0.85.
    if size <= 0:
        return 0.0
    if size == 1:
        return 0.4
    if size == 2:
        return 0.7
    return min(1.0, 0.85 + (size - 3) * 0.03)


@app.post("/cluster", response_model=ClusterResponse)
async def cluster(req: ClusterRequest) -> ClusterResponse:
    by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inc in req.incidents:
        sig = str(inc.get("similarity_signature") or "")
        if not sig:
            continue
        by_sig[sig].append(inc)

    out: list[Cluster] = []
    for sig, members in sorted(by_sig.items(), key=lambda p: -len(p[1])):
        out.append(Cluster(
            signature=sig,
            incident_ids=[str(m.get("incident_id")) for m in members],
            size=len(members),
            cohesion=_cohesion(len(members)),
            distinct_reporter_departments=sorted({
                str(m.get("reporter_department"))
                for m in members if m.get("reporter_department")
            }),
            distinct_services=sorted({
                str(m.get("affected_service"))
                for m in members if m.get("affected_service")
            }),
        ))
    return ClusterResponse(clusters=out)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "clustering-tool", "version": "0.1.0"}
