"""HTTP clients for the three Automated Fix sidecars."""
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


async def _request(method: str, url: str, body: dict | None, timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        try:
            if method == "POST":
                resp = await c.post(url, json=body, headers=_headers())
            else:
                resp = await c.get(url, headers=_headers())
        except httpx.HTTPError as exc:
            raise ToolError(f"{url} unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ToolError(f"{url} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


@dataclass
class ScriptExecutor:
    base_url: str
    timeout: float = 30.0

    async def catalogue(self) -> list[dict[str, Any]]:
        body = await _request("GET", f"{self.base_url}/catalogue", None, self.timeout)
        return list(body.get("runbooks") or [])

    async def execute(self, *, runbook_id: str, parameters: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
        return await _request(
            "POST", f"{self.base_url}/execute",
            {"runbook_id": runbook_id, "parameters": parameters, "snapshot_id": snapshot_id},
            self.timeout,
        )


@dataclass
class ConfigurationManager:
    base_url: str
    timeout: float = 5.0

    async def snapshot(self, *, target_service: str, runbook_id: str | None, config_state: dict[str, Any] | None) -> dict[str, Any]:
        return await _request(
            "POST", f"{self.base_url}/snapshot",
            {"target_service": target_service, "runbook_id": runbook_id, "config_state": config_state or {}},
            self.timeout,
        )


@dataclass
class RollbackHandler:
    base_url: str
    timeout: float = 10.0

    async def rollback(self, *, snapshot_id: str, reason: str) -> dict[str, Any]:
        return await _request(
            "POST", f"{self.base_url}/rollback",
            {"snapshot_id": snapshot_id, "reason": reason},
            self.timeout,
        )
