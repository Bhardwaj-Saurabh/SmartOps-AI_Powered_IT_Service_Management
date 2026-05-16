"""Log aggregator connector sidecar (synthetic backend).

Production version would proxy to Elasticsearch/Loki. Phase 1 reads a YAML
corpus that's mounted into the container. The API shape mirrors what a
real connector would expose, so swapping the backend is a one-file change.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="log-aggregator-connector", version="0.1.0")

_LOGS_PATH = os.environ.get("LOGS_PATH", "/app/data/logs.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_LOGS_PATH).read_text()) or {}


class SearchRequest(BaseModel):
    service: str
    minutes_before: int = Field(default=15, ge=1, le=180)
    minutes_after: int = Field(default=5, ge=0, le=60)
    levels: list[str] | None = Field(default=None, description="Filter to these levels (e.g. ['ERROR'])")
    contains: str | None = Field(default=None, description="Free-text substring filter")
    limit: int = Field(default=200, ge=1, le=1000)


class LogEntry(BaseModel):
    t: int
    level: str
    source: str
    message: str


class SearchResponse(BaseModel):
    service: str
    matched: bool
    entries: list[LogEntry]
    summary: dict[str, int]


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    service_entries: list[dict] = _corpus.get(req.service.lower()) or []
    entries = [LogEntry(**e) for e in service_entries]

    entries = [e for e in entries if -req.minutes_before <= e.t <= req.minutes_after]
    if req.levels:
        levels = {lvl.upper() for lvl in req.levels}
        entries = [e for e in entries if e.level.upper() in levels]
    if req.contains:
        needle = req.contains.lower()
        entries = [e for e in entries if needle in e.message.lower()]
    entries = entries[: req.limit]

    summary: dict[str, int] = {}
    for e in entries:
        summary[e.level] = summary.get(e.level, 0) + 1

    return SearchResponse(
        service=req.service, matched=bool(service_entries),
        entries=entries, summary=summary,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "log-aggregator-connector", "version": "0.1.0", "services_indexed": len(_corpus)}
