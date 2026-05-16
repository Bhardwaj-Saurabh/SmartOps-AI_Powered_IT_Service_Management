"""Slack connector sidecar.

Accepts a Slack ``message`` event payload (subset of Slack Events API shape)
and returns canonical fields the Incident Intake Agent expects: user
identifier, text, timestamp, thread root, attachments.

No Slack API calls — Phase 1 just normalises the JSON shape. A real
connector would also resolve user IDs via Slack Web API.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="slack-connector", version="0.1.0")


class SlackParseRequest(BaseModel):
    event: dict[str, Any] = Field(..., description="A Slack 'message' event payload")


class NormalisedSlackMessage(BaseModel):
    sender_user_id: str
    text: str
    received_at: str
    channel: str | None
    thread_ts: str | None
    files: list[str]


@app.post("/parse", response_model=NormalisedSlackMessage)
async def parse(req: SlackParseRequest) -> NormalisedSlackMessage:
    ev = req.event
    if ev.get("type") != "message":
        raise HTTPException(status_code=400, detail="event.type must be 'message'")
    try:
        ts_value = float(ev["ts"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"event.ts missing or invalid: {exc}") from exc
    return NormalisedSlackMessage(
        sender_user_id=ev.get("user", ""),
        text=ev.get("text", ""),
        received_at=datetime.fromtimestamp(ts_value, tz=UTC).isoformat(),
        channel=ev.get("channel"),
        thread_ts=ev.get("thread_ts"),
        files=[f.get("name", "") for f in ev.get("files", []) if isinstance(f, dict)],
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "slack-connector", "version": "0.1.0"}
