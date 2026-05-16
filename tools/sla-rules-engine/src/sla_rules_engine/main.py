"""SLA rules engine sidecar.

Given a sequence of state transitions and a list of states that pause the
SLA clock, computes the total paused-minutes that should be subtracted
from elapsed time.

Pure function — no business policy here. Pause-state list comes from the
SBCA (the agent passes it in).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="sla-rules-engine", version="0.1.0")


class StateTransition(BaseModel):
    state: str
    at_epoch: int


class PauseRequest(BaseModel):
    transitions: list[StateTransition] = Field(..., description="Chronologically sorted state transitions")
    pause_states: list[str] = Field(default_factory=list)
    end_epoch: int


class PauseResponse(BaseModel):
    paused_minutes: float
    currently_paused: bool
    pause_segments: list[dict[str, Any]]


@app.post("/pauses", response_model=PauseResponse)
async def pauses(req: PauseRequest) -> PauseResponse:
    pause_set = set(req.pause_states or [])
    paused_total = 0.0
    segments: list[dict[str, Any]] = []
    open_segment: dict[str, Any] | None = None

    # Walk transitions in order. Each transition has a state and a time;
    # state is in effect from this transition's at_epoch until the next
    # transition's at_epoch (or end_epoch).
    tx = sorted(req.transitions, key=lambda t: t.at_epoch)
    for i, t in enumerate(tx):
        next_epoch = tx[i + 1].at_epoch if i + 1 < len(tx) else req.end_epoch
        if t.state in pause_set:
            seg_minutes = max(0.0, (next_epoch - t.at_epoch) / 60.0)
            paused_total += seg_minutes
            segments.append({"state": t.state, "started_epoch": t.at_epoch,
                             "ended_epoch": next_epoch, "minutes": round(seg_minutes, 2)})
            if i + 1 == len(tx):
                open_segment = {"state": t.state, "started_epoch": t.at_epoch}

    return PauseResponse(
        paused_minutes=round(paused_total, 2),
        currently_paused=open_segment is not None,
        pause_segments=segments,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "sla-rules-engine", "version": "0.1.0"}
