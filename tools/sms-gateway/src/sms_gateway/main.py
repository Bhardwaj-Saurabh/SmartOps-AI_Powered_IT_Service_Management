"""SMS gateway sidecar (synthetic Twilio shape)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="sms-gateway", version="0.1.0")


class SendRequest(BaseModel):
    to: list[str]
    body: str
    from_number: str | None = None


class SendResponse(BaseModel):
    ok: bool
    message_id: str
    sent_at_epoch: int


class SentEntry(BaseModel):
    message_id: str
    to: list[str]
    body_preview: str
    sent_at_epoch: int


_lock = threading.Lock()
_log: list[SentEntry] = []


@app.post("/send", response_model=SendResponse)
async def send(req: SendRequest) -> SendResponse:
    mid = f"sms-{uuid.uuid4().hex[:10]}"
    now = int(time.time())
    with _lock:
        _log.append(SentEntry(message_id=mid, to=req.to,
                              body_preview=req.body[:160], sent_at_epoch=now))
        if len(_log) > 500:
            del _log[: len(_log) - 500]
    return SendResponse(ok=True, message_id=mid, sent_at_epoch=now)


@app.get("/sent")
async def sent(limit: int = 50) -> dict[str, Any]:
    with _lock:
        items = list(_log[-limit:])
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@app.get("/health")
async def health() -> dict[str, Any]:
    with _lock:
        count = len(_log)
    return {"status": "healthy", "tool": "sms-gateway", "version": "0.1.0", "sent_log_entries": count}
