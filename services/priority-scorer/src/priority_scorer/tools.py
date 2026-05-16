"""HTTP clients for the Priority Scorer's two sidecars."""
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
class ImpactAnalyser:
    base_url: str
    timeout: float = 5.0

    async def analyse(
        self,
        *,
        affected_users: int | None,
        blast_radius: int,
        reporter_vip: bool,
        service_tier: str | None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/analyse",
                    json={
                        "affected_users": affected_users,
                        "blast_radius": blast_radius,
                        "reporter_vip": reporter_vip,
                        "service_tier": service_tier,
                    },
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"impact-analyser unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"impact-analyser /analyse -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()


@dataclass
class ServiceDependencyMapper:
    base_url: str
    timeout: float = 5.0

    async def walk(self, *, service: str, max_depth: int = 5) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/walk",
                    json={"service": service, "max_depth": max_depth},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"service-dependency-mapper unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"service-dependency-mapper /walk -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()
