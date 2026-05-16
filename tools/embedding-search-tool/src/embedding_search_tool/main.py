"""Embedding-search tool sidecar.

Like ``historical-pattern-matcher`` but pointed at the ``knowledge_articles``
collection. Takes a precomputed vector (caller embeds via gateway_client)
and returns the nearest articles with their stored metadata.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

app = FastAPI(title="embedding-search-tool", version="0.1.0")

_QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
_COLLECTION = os.environ.get("QDRANT_COLLECTION", "knowledge_articles")
_client = AsyncQdrantClient(url=_QDRANT_URL)


class SearchRequest(BaseModel):
    vector: list[float]
    limit: int = Field(default=10, ge=1, le=20)


class Hit(BaseModel):
    article_id: str | None
    title: str | None
    similarity: float
    service: str | None
    category: str | None
    updated_at_days_ago: int | None
    effectiveness_score: float | None


class SearchResponse(BaseModel):
    matches: list[Hit]
    collection: str


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        hits = await _client.search(
            collection_name=_COLLECTION,
            query_vector=req.vector,
            limit=req.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant query failed: {exc}") from exc
    return SearchResponse(
        collection=_COLLECTION,
        matches=[
            Hit(
                article_id=(h.payload or {}).get("article_id"),
                title=(h.payload or {}).get("title"),
                similarity=float(h.score),
                service=(h.payload or {}).get("service"),
                category=(h.payload or {}).get("category"),
                updated_at_days_ago=(h.payload or {}).get("updated_at_days_ago"),
                effectiveness_score=(h.payload or {}).get("effectiveness_score"),
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
    return {"status": "healthy" if ready else "degraded", "tool": "embedding-search-tool",
            "version": "0.1.0", "qdrant_reachable": ready, "collection": _COLLECTION}
