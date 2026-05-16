"""HTTP clients for the Routing Agent's two sidecars."""
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
class TeamDirectory:
    base_url: str
    timeout: float = 5.0

    async def lookup(self, team_ids: list[str]) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/lookup",
                    json={"team_ids": team_ids},
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"team-directory-connector unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"team-directory-connector /lookup -> {resp.status_code}: {resp.text[:200]}")
        return list(resp.json().get("teams") or [])


@dataclass
class SkillMatrix:
    base_url: str
    timeout: float = 5.0

    async def score(
        self,
        *,
        service_area: str,
        category: str,
        candidate_team_ids: list[str],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            try:
                resp = await c.post(
                    f"{self.base_url}/score",
                    json={
                        "service_area": service_area,
                        "category": category,
                        "candidate_team_ids": candidate_team_ids,
                    },
                    headers=_headers(),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"skill-matrix-lookup unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ToolError(f"skill-matrix-lookup /score -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()
