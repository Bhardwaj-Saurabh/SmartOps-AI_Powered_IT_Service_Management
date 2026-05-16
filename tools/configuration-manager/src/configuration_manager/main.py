"""Configuration manager sidecar — snapshot + restore.

In-memory store keyed by snapshot_id. Real impl would persist to encrypted
storage; for Phase 1 we just round-trip a JSON blob. Rollback-handler queries
this service to retrieve the snapshot for restore.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="configuration-manager", version="0.1.0")


class SnapshotRequest(BaseModel):
    target_service: str
    config_state: dict[str, Any] = Field(default_factory=dict)
    """Caller-provided pre-fix configuration capture. Empty {} is allowed —
    Phase 1 mostly tests the round-trip mechanism."""
    runbook_id: str | None = None


class SnapshotResponse(BaseModel):
    snapshot_id: str
    target_service: str
    captured_at_epoch: int


class RestoreResponse(BaseModel):
    snapshot_id: str
    target_service: str
    config_state: dict[str, Any]
    restored: bool


_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}


@app.post("/snapshot", response_model=SnapshotResponse)
async def snapshot(req: SnapshotRequest) -> SnapshotResponse:
    sid = f"snap-{uuid.uuid4().hex[:12]}"
    captured = int(time.time())
    with _lock:
        _store[sid] = {
            "target_service": req.target_service,
            "config_state": req.config_state,
            "runbook_id": req.runbook_id,
            "captured_at_epoch": captured,
        }
    return SnapshotResponse(snapshot_id=sid, target_service=req.target_service, captured_at_epoch=captured)


@app.get("/snapshot/{snapshot_id}", response_model=RestoreResponse)
async def get_snapshot(snapshot_id: str) -> RestoreResponse:
    with _lock:
        entry = _store.get(snapshot_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")
    return RestoreResponse(
        snapshot_id=snapshot_id,
        target_service=entry["target_service"],
        config_state=entry["config_state"],
        restored=False,           # raw fetch — restore happens via rollback-handler
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    with _lock:
        count = len(_store)
    return {"status": "healthy", "tool": "configuration-manager", "version": "0.1.0", "snapshots_stored": count}
