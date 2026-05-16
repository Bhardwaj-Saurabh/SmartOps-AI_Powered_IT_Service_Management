"""HTTP clients for the agent's three tool sidecars (email-parser,
slack-connector, form-normaliser) and for Qdrant.

Framework MUST: tools are sidecar containers accessed via HTTP — NEVER
library-imported. These clients propagate ``X-Correlation-Id`` and
``traceparent`` on every call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from di_framework_core import (
    CORRELATION_ID_HEADER,
    ToolError,
    current_correlation_id,
)


def _propagate_headers() -> dict[str, str]:
    cid = current_correlation_id()
    return {CORRELATION_ID_HEADER: cid} if cid else {}


@dataclass
class ToolSidecar:
    """Generic HTTP client used by all three sidecars."""

    base_url: str
    timeout: float = 5.0

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(f"{self.base_url}{path}", json=body, headers=_propagate_headers())
            except httpx.HTTPError as exc:
                raise ToolError(f"Sidecar {self.base_url} unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"Sidecar {self.base_url}{path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()


class EmailParser:
    def __init__(self, sidecar: ToolSidecar) -> None:
        self._s = sidecar

    async def parse(self, raw: str) -> dict[str, Any]:
        return await self._s.post("/parse", {"raw": raw})


class SlackConnector:
    def __init__(self, sidecar: ToolSidecar) -> None:
        self._s = sidecar

    async def parse(self, event: dict[str, Any]) -> dict[str, Any]:
        return await self._s.post("/parse", {"event": event})


class FormNormaliser:
    def __init__(self, sidecar: ToolSidecar) -> None:
        self._s = sidecar

    async def normalise(self, schema_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._s.post("/normalise", {"schema_id": schema_id, "payload": payload})


@dataclass
class QdrantTool:
    url: str
    collection: str
    vector_size: int

    def __post_init__(self) -> None:
        self._client: AsyncQdrantClient | None = None

    async def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(url=self.url)
        return self._client

    async def ensure_collection(self) -> None:
        client = await self.client()
        try:
            await client.get_collection(self.collection)
        except Exception:
            await client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )

    async def upsert(self, *, point_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        client = await self.client()
        await client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    async def nearest(self, *, vector: list[float], limit: int = 1) -> list[dict[str, Any]]:
        client = await self.client()
        hits = await client.search(collection_name=self.collection, query_vector=vector, limit=limit)
        return [{"id": h.id, "score": h.score, "payload": h.payload} for h in hits]
