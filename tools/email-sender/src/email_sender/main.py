"""Email sender sidecar (synthetic).

Records every "send" to an in-memory log so demos can inspect what would
have gone out. ``GET /sent`` returns the log. Real SMTP/SendGrid integration
swaps in here without changing the agent.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="email-sender", version="0.1.0")


class SendRequest(BaseModel):
    to: list[str]
    subject: str
    body: str
    cc: list[str] = []


class SendResponse(BaseModel):
    message_id: str
    delivered: bool
    queued_at_epoch: int


class SentEntry(BaseModel):
    message_id: str
    to: list[str]
    subject: str
    body_preview: str
    sent_at_epoch: int


_lock = threading.Lock()
_sent: list[SentEntry] = []


@app.post("/send", response_model=SendResponse)
async def send(req: SendRequest) -> SendResponse:
    mid = f"msg-{uuid.uuid4().hex[:10]}"
    now = int(time.time())
    with _lock:
        _sent.append(SentEntry(
            message_id=mid, to=req.to, subject=req.subject,
            body_preview=req.body[:200], sent_at_epoch=now,
        ))
        if len(_sent) > 500:
            del _sent[: len(_sent) - 500]    # bound the log
    return SendResponse(message_id=mid, delivered=True, queued_at_epoch=now)


@app.get("/sent")
async def sent_log(limit: int = 50) -> dict[str, Any]:
    with _lock:
        items = list(_sent[-limit:])
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@app.get("/health")
async def health() -> dict[str, Any]:
    with _lock:
        count = len(_sent)
    return {"status": "healthy", "tool": "email-sender", "version": "0.1.0", "sent_log_entries": count}
