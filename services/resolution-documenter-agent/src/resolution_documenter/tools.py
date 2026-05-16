"""HTTP clients for the Resolution Documenter sidecars + KB search."""
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


async def _post(url: str, body: dict, timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        try:
            resp = await c.post(url, json=body, headers=_headers())
        except httpx.HTTPError as exc:
            raise ToolError(f"{url} unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ToolError(f"{url} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


@dataclass
class DocumentFormatter:
    base_url: str
    timeout: float = 5.0

    async def render(self, *, template_id: str, note: dict[str, Any]) -> dict[str, Any]:
        return await _post(f"{self.base_url}/render",
                           {"template_id": template_id, "note": note}, self.timeout)


@dataclass
class KnowledgeBaseWriter:
    base_url: str
    timeout: float = 5.0

    async def create(
        self, *, title: str, category: str, service: str, body_markdown: str,
        keywords: list[str], draft: bool, source_incident_id: str | None,
    ) -> dict[str, Any]:
        return await _post(f"{self.base_url}/create", {
            "title": title, "category": category, "service": service,
            "body_markdown": body_markdown, "keywords": keywords, "draft": draft,
            "source_incident_id": source_incident_id,
        }, self.timeout)

    async def update(self, *, article_id: str, append_section: str,
                     source_incident_id: str | None) -> dict[str, Any]:
        return await _post(f"{self.base_url}/update", {
            "article_id": article_id, "append_section": append_section,
            "source_incident_id": source_incident_id,
        }, self.timeout)


@dataclass
class KnowledgeBaseSearch:
    """Reuses the Stage 4a knowledge-base-connector for the existing-article lookup."""

    base_url: str
    timeout: float = 5.0

    async def search(self, *, query: str, service_filter: str | None = None,
                     category_filter: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if service_filter:
            body["service_filter"] = service_filter
        if category_filter:
            body["category_filter"] = category_filter
        resp = await _post(f"{self.base_url}/search", body, self.timeout)
        return list(resp.get("articles") or [])
