"""HTTP clients for the Knowledge Search sidecars."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from di_framework_core import (
    CORRELATION_ID_HEADER,
    ToolError,
    current_correlation_id,
)


def _headers() -> dict[str, str]:
    cid = current_correlation_id()
    return {CORRELATION_ID_HEADER: cid} if cid else {}


@dataclass
class KnowledgeBase:
    base_url: str
    timeout: float = 5.0

    async def search(
        self, *, query: str,
        service_filter: str | None = None,
        category_filter: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if service_filter:
            body["service_filter"] = service_filter
        if category_filter:
            body["category_filter"] = category_filter
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(f"{self.base_url}/search", json=body, headers=_headers())
            except httpx.HTTPError as exc:
                raise ToolError(f"knowledge-base-connector unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"knowledge-base-connector /search -> {resp.status_code}: {resp.text[:200]}")
        return list(resp.json().get("articles") or [])


@dataclass
class EmbeddingSearch:
    base_url: str
    timeout: float = 5.0

    async def search(self, *, vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/search",
                    json={"vector": vector, "limit": limit},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"embedding-search-tool unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"embedding-search-tool /search -> {resp.status_code}: {resp.text[:200]}")
        return list(resp.json().get("matches") or [])
