"""Rollback handler sidecar.

Fetches a snapshot from configuration-manager and "restores" it. Phase 1
is synthetic: we report what would have been restored without actually
touching anything. The interface contract is the same as a production
rollback handler so the orchestrator's Saga compensation works identically.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="rollback-handler", version="0.1.0")

_CONFIG_MANAGER_URL = os.environ.get(
    "CONFIGURATION_MANAGER_URL", "http://configuration-manager:9002"
)


class RollbackRequest(BaseModel):
    snapshot_id: str
    reason: str = ""


class RollbackResponse(BaseModel):
    snapshot_id: str
    target_service: str
    restored: bool
    restored_state_keys: list[str]
    note: str


@app.post("/rollback", response_model=RollbackResponse)
async def rollback(req: RollbackRequest) -> RollbackResponse:
    async with httpx.AsyncClient(timeout=5.0) as c:
        try:
            resp = await c.get(f"{_CONFIG_MANAGER_URL.rstrip('/')}/snapshot/{req.snapshot_id}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"configuration-manager unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"snapshot fetch failed: {resp.text[:200]}")
    entry = resp.json()
    # Phase 1: no real restore — just enumerate what would have been touched.
    return RollbackResponse(
        snapshot_id=req.snapshot_id,
        target_service=entry.get("target_service", ""),
        restored=True,
        restored_state_keys=list((entry.get("config_state") or {}).keys()),
        note="Phase 1 synthetic restore — no real configuration change. Reason: " + req.reason,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "rollback-handler", "version": "0.1.0"}
