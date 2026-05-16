"""HTTP clients for the three dispatcher sidecars."""
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
class EmailSender:
    base_url: str
    timeout: float = 5.0

    async def send(self, *, to: list[str], subject: str, body: str) -> dict[str, Any]:
        return await _post(f"{self.base_url}/send", {"to": to, "subject": subject, "body": body}, self.timeout)


@dataclass
class SlackPoster:
    base_url: str
    timeout: float = 5.0

    async def post(self, *, channel: str, text: str) -> dict[str, Any]:
        return await _post(f"{self.base_url}/post", {"channel": channel, "text": text}, self.timeout)


@dataclass
class SmsGateway:
    base_url: str
    timeout: float = 5.0

    async def send(self, *, to: list[str], body: str) -> dict[str, Any]:
        return await _post(f"{self.base_url}/send", {"to": to, "body": body}, self.timeout)
