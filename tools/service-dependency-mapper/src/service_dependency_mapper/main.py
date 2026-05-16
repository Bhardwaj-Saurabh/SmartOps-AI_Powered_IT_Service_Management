"""Service dependency mapper sidecar.

Returns the transitive downstream services for an affected service, plus a
tier label. Used by the Priority Scorer to compute blast radius.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="service-dependency-mapper", version="0.1.0")

_TOPOLOGY_PATH = os.environ.get("TOPOLOGY_PATH", "/app/data/topology.yaml")
_topology: dict[str, Any] = yaml.safe_load(Path(_TOPOLOGY_PATH).read_text()) or {}


def _bfs_downstream(service: str, max_depth: int = 5) -> list[str]:
    services = (_topology.get("services") or {})
    seen: set[str] = set()
    frontier: list[tuple[str, int]] = [(service, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        if cur in seen or depth > max_depth:
            continue
        seen.add(cur)
        for nxt in (services.get(cur) or {}).get("downstream", []) or []:
            if nxt not in seen:
                frontier.append((nxt, depth + 1))
    seen.discard(service)
    return sorted(seen)


class WalkRequest(BaseModel):
    service: str
    max_depth: int = 5


class WalkResponse(BaseModel):
    service: str
    matched: bool
    tier: str | None
    upstream: list[str]
    direct_downstream: list[str]
    transitive_downstream: list[str]
    blast_radius: int


@app.post("/walk", response_model=WalkResponse)
async def walk(req: WalkRequest) -> WalkResponse:
    services = _topology.get("services") or {}
    entry = services.get(req.service.lower())
    if entry is None:
        return WalkResponse(
            service=req.service, matched=False, tier=None,
            upstream=[], direct_downstream=[], transitive_downstream=[], blast_radius=0,
        )
    transitive = _bfs_downstream(req.service.lower(), max_depth=req.max_depth)
    return WalkResponse(
        service=req.service,
        matched=True,
        tier=entry.get("tier"),
        upstream=list(entry.get("upstream") or []),
        direct_downstream=list(entry.get("downstream") or []),
        transitive_downstream=transitive,
        blast_radius=len(transitive),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "tool": "service-dependency-mapper",
        "version": "0.1.0",
        "known_services": len(_topology.get("services") or {}),
    }
