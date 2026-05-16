"""Historical pattern matcher sidecar.

Takes a pre-computed embedding vector for a new incident, returns the
nearest historical incidents with their stored classifications. The
Classification Agent uses this as a second opinion alongside the LLM call.

The embedding is computed by the caller (via LiteLLM/gateway_client) and
passed in — this sidecar does not call the AI Gateway itself. Tools are
single-purpose; embedding generation belongs to the caller.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

app = FastAPI(title="historical-pattern-matcher", version="0.1.0")


_QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
_COLLECTION = os.environ.get("QDRANT_COLLECTION", "historical_incidents")
_client = AsyncQdrantClient(url=_QDRANT_URL)


class MatchRequest(BaseModel):
    vector: list[float]
    limit: int = Field(default=5, ge=1, le=20)


class Match(BaseModel):
    incident_id: str | None
    title: str | None
    similarity: float
    service_area: str | None
    category: str | None
    reporter_department: str | None


class MatchResponse(BaseModel):
    matches: list[Match]
    collection: str


@app.post("/match", response_model=MatchResponse)
async def match(req: MatchRequest) -> MatchResponse:
    try:
        hits = await _client.search(
            collection_name=_COLLECTION,
            query_vector=req.vector,
            limit=req.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant query failed: {exc}") from exc
    return MatchResponse(
        collection=_COLLECTION,
        matches=[
            Match(
                incident_id=(h.payload or {}).get("incident_id"),
                title=(h.payload or {}).get("title"),
                similarity=float(h.score),
                service_area=(h.payload or {}).get("category"),  # seed labels by 'category' (=area)
                category=(h.payload or {}).get("category"),
                reporter_department=(h.payload or {}).get("reporter_department"),
            )
            for h in hits
        ],
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        await _client.get_collections()
        ready = True
    except Exception:
        ready = False
    return {
        "status": "healthy" if ready else "degraded",
        "tool": "historical-pattern-matcher",
        "version": "0.1.0",
        "qdrant_reachable": ready,
        "collection": _COLLECTION,
    }
