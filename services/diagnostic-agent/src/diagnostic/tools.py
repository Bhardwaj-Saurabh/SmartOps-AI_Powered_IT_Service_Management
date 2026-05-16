"""HTTP clients for the Diagnostic Agent's three sidecars."""
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
        raise ToolError(f"{url} -> {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@dataclass
class LogAggregator:
    base_url: str
    timeout: float = 5.0

    async def search(
        self, *, service: str,
        minutes_before: int = 15, minutes_after: int = 5,
        levels: list[str] | None = None,
        contains: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "service": service, "minutes_before": minutes_before, "minutes_after": minutes_after,
        }
        if levels is not None:
            body["levels"] = levels
        if contains is not None:
            body["contains"] = contains
        return await _post(f"{self.base_url}/search", body, self.timeout)


@dataclass
class MetricsQuery:
    base_url: str
    timeout: float = 5.0

    async def query(self, *, service: str, only_metrics: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"service": service}
        if only_metrics is not None:
            body["only_metrics"] = only_metrics
        return await _post(f"{self.base_url}/query", body, self.timeout)


@dataclass
class TopologyWalker:
    base_url: str
    timeout: float = 5.0

    async def walk(self, *, service: str, max_depth: int = 5) -> dict[str, Any]:
        return await _post(f"{self.base_url}/walk", {"service": service, "max_depth": max_depth}, self.timeout)
