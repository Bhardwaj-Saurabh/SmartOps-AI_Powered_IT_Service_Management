"""Metrics query tool sidecar (synthetic Prometheus-shape).

Returns baseline + at-incident snapshots. Anomaly detection lives in the
caller (Diagnostic Agent) — this tool just serves the data.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="metrics-query-tool", version="0.1.0")

_METRICS_PATH = os.environ.get("METRICS_PATH", "/app/data/metrics.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_METRICS_PATH).read_text()) or {}


class QueryRequest(BaseModel):
    service: str
    only_metrics: list[str] | None = None


class QueryResponse(BaseModel):
    service: str
    matched: bool
    baseline: dict[str, float] = {}
    at_incident: dict[str, float] = {}
    deltas: dict[str, float] = {}


def _deltas(baseline: dict[str, Any], at_incident: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in set(baseline) & set(at_incident):
        b, c = float(baseline[key]), float(at_incident[key])
        if b == 0:
            out[key] = c
        else:
            out[key] = round((c - b) / b * 100.0, 2)   # percentage change
    return out


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    entry: dict | None = _corpus.get(req.service.lower())
    if entry is None:
        return QueryResponse(service=req.service, matched=False)
    baseline = dict(entry.get("baseline") or {})
    at_incident = dict(entry.get("at_incident") or {})
    if req.only_metrics:
        keep = set(req.only_metrics)
        baseline = {k: v for k, v in baseline.items() if k in keep}
        at_incident = {k: v for k, v in at_incident.items() if k in keep}
    return QueryResponse(
        service=req.service, matched=True,
        baseline=baseline, at_incident=at_incident,
        deltas=_deltas(baseline, at_incident),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "metrics-query-tool", "version": "0.1.0", "services_indexed": len(_corpus)}
