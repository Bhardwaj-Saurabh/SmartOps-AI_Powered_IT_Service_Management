"""HTTP clients for the four sidecars (health-check-runner, synthetic-monitor,
comparison-tool, and the shared metrics-query-tool)."""
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
class HealthCheckRunner:
    base_url: str
    timeout: float = 5.0

    async def run(self, *, service: str, after_fix: bool = True) -> dict[str, Any]:
        return await _post(f"{self.base_url}/run", {"service": service, "after_fix": after_fix}, self.timeout)


@dataclass
class SyntheticMonitor:
    base_url: str
    timeout: float = 8.0

    async def replay(self, *, scenario_ids: list[str], after_fix: bool = True) -> dict[str, Any]:
        return await _post(
            f"{self.base_url}/replay",
            {"scenario_ids": scenario_ids, "after_fix": after_fix},
            self.timeout,
        )


@dataclass
class ComparisonTool:
    base_url: str
    timeout: float = 5.0

    async def compare(
        self, *, pre: dict[str, float], post: dict[str, float],
        improvement_required_pct: dict[str, float],
    ) -> dict[str, Any]:
        return await _post(
            f"{self.base_url}/compare",
            {"pre": pre, "post": post, "improvement_required_pct": improvement_required_pct},
            self.timeout,
        )


@dataclass
class MetricsQuery:
    base_url: str
    timeout: float = 5.0

    async def query(self, *, service: str) -> dict[str, Any]:
        return await _post(f"{self.base_url}/query", {"service": service}, self.timeout)
