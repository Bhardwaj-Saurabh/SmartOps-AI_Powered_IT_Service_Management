"""Sidecar HTTP clients for taxonomy-lookup + historical-pattern-matcher.

Tools live in their own containers (framework MUST — no library embedding).
Both clients propagate the correlation header.
"""
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
class TaxonomyLookup:
    base_url: str
    timeout: float = 5.0

    async def validate(self, *, service_area: str, category: str | None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/validate",
                    json={"service_area": service_area, "category": category},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"taxonomy-lookup unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"taxonomy-lookup /validate -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def lookup_by_service(self, service: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/lookup_by_service",
                    json={"service": service},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"taxonomy-lookup unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"taxonomy-lookup /lookup_by_service -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def full_taxonomy(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.get(f"{self.base_url}/taxonomy", headers=_headers())
            except httpx.HTTPError as exc:
                raise ToolError(f"taxonomy-lookup unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"taxonomy-lookup /taxonomy -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()


@dataclass
class HistoricalPatternMatcher:
    base_url: str
    timeout: float = 5.0

    async def match(self, *, vector: list[float], limit: int = 5) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/match",
                    json={"vector": vector, "limit": limit},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"historical-pattern-matcher unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"historical-pattern-matcher /match -> {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        return list(body.get("matches", []))
