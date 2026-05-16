"""Topology walker sidecar.

Walks the request path (upstream traversal) from an affected service.
Returns the upstream chain — different shape from service-dependency-mapper
(which returns downstream blast radius for impact scoring).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="topology-walker", version="0.1.0")

_TOPOLOGY_PATH = os.environ.get("TOPOLOGY_PATH", "/app/data/topology.yaml")
_topology: dict[str, Any] = yaml.safe_load(Path(_TOPOLOGY_PATH).read_text()) or {}


def _upstream_chain(service: str, max_depth: int = 5) -> list[list[str]]:
    """Return the upstream graph from ``service`` as a list of layers."""
    services = (_topology.get("services") or {})
    layers: list[list[str]] = []
    frontier = [service]
    seen: set[str] = {service}
    for _ in range(max_depth):
        next_layer: list[str] = []
        for node in frontier:
            for parent in (services.get(node) or {}).get("upstream", []) or []:
                if parent not in seen:
                    next_layer.append(parent)
                    seen.add(parent)
        if not next_layer:
            break
        layers.append(sorted(next_layer))
        frontier = next_layer
    return layers


class WalkRequest(BaseModel):
    service: str
    max_depth: int = 5


class WalkResponse(BaseModel):
    service: str
    matched: bool
    upstream_layers: list[list[str]]
    tier: str | None


@app.post("/walk", response_model=WalkResponse)
async def walk(req: WalkRequest) -> WalkResponse:
    services = _topology.get("services") or {}
    entry = services.get(req.service.lower())
    if entry is None:
        return WalkResponse(service=req.service, matched=False, upstream_layers=[], tier=None)
    return WalkResponse(
        service=req.service,
        matched=True,
        upstream_layers=_upstream_chain(req.service.lower(), max_depth=req.max_depth),
        tier=entry.get("tier"),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "topology-walker", "version": "0.1.0", "services_indexed": len(_topology.get("services") or {})}
