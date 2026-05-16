"""HTTP clients for the SLA agent's two sidecars."""
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
class ClockTimer:
    base_url: str
    timeout: float = 5.0

    async def elapsed_24x7(self, *, started_at_epoch: int, now_at_epoch: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"started_at_epoch": started_at_epoch}
        if now_at_epoch is not None:
            body["now_at_epoch"] = now_at_epoch
        return await _post(f"{self.base_url}/elapsed_24x7", body, self.timeout)

    async def elapsed_business(
        self, *, started_at_epoch: int, now_at_epoch: int | None,
        timezone: str, weekdays: list[int], start: str, end: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "started_at_epoch": started_at_epoch,
            "timezone": timezone, "weekdays": weekdays, "start": start, "end": end,
        }
        if now_at_epoch is not None:
            body["now_at_epoch"] = now_at_epoch
        return await _post(f"{self.base_url}/elapsed_business", body, self.timeout)


@dataclass
class SLARulesEngine:
    base_url: str
    timeout: float = 5.0

    async def pauses(
        self, *, transitions: list[dict], pause_states: list[str], end_epoch: int,
    ) -> dict[str, Any]:
        return await _post(
            f"{self.base_url}/pauses",
            {"transitions": transitions, "pause_states": pause_states, "end_epoch": end_epoch},
            self.timeout,
        )
