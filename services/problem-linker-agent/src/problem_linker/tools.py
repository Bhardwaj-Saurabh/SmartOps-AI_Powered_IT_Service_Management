"""HTTP clients for the Problem Linker sidecars."""
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
class IncidentHistory:
    base_url: str
    timeout: float = 5.0

    async def query(
        self, *, service_area: str | None = None, category: str | None = None,
        window_days: int = 30, limit: int = 50,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"window_days": window_days, "limit": limit}
        if service_area:
            body["service_area"] = service_area
        if category:
            body["category"] = category
        return await _post(f"{self.base_url}/query", body, self.timeout)


@dataclass
class ClusteringTool:
    base_url: str
    timeout: float = 5.0

    async def cluster(self, *, incidents: list[dict[str, Any]]) -> dict[str, Any]:
        return await _post(f"{self.base_url}/cluster", {"incidents": incidents}, self.timeout)
