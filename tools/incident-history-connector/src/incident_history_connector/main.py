"""Incident history connector sidecar (synthetic).

Returns past incidents filtered by service_area / category / window and
open problem records. Real production swap-in: incident-tracking system
REST.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="incident-history-connector", version="0.1.0")

_PATH = os.environ.get("INCIDENTS_PATH", "/app/data/incidents.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_PATH).read_text()) or {}


class QueryRequest(BaseModel):
    service_area: str | None = None
    category: str | None = None
    window_days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=200)


class HistoryEntry(BaseModel):
    incident_id: str
    service_area: str
    category: str
    affected_service: str
    root_cause: str
    reporter_department: str
    closed_at_days_ago: int
    similarity_signature: str


class ProblemRecord(BaseModel):
    problem_id: str
    title: str
    similarity_signature: str
    linked_incidents: list[str]
    state: str


class QueryResponse(BaseModel):
    incidents: list[HistoryEntry]
    open_problems: list[ProblemRecord]


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    incidents: list[dict[str, Any]] = list(_corpus.get("incidents") or [])
    if req.service_area is not None:
        incidents = [i for i in incidents if i.get("service_area") == req.service_area]
    if req.category is not None:
        incidents = [i for i in incidents if i.get("category") == req.category]
    incidents = [i for i in incidents if int(i.get("closed_at_days_ago", 9999)) <= req.window_days]
    incidents = incidents[: req.limit]

    return QueryResponse(
        incidents=[HistoryEntry(**i) for i in incidents],
        open_problems=[ProblemRecord(**p) for p in (_corpus.get("problems") or [])],
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy", "tool": "incident-history-connector", "version": "0.1.0",
        "incident_count": len(_corpus.get("incidents") or []),
        "open_problem_count": len(_corpus.get("problems") or []),
    }
