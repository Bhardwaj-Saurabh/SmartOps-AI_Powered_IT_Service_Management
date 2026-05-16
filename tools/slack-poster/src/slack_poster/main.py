"""Slack poster sidecar (synthetic). Logs posts to an in-memory ring buffer."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="slack-poster", version="0.1.0")


class PostRequest(BaseModel):
    channel: str
    text: str
    thread_ts: str | None = None


class PostResponse(BaseModel):
    ok: bool
    message_id: str
    posted_at_epoch: int


class PostedEntry(BaseModel):
    message_id: str
    channel: str
    text_preview: str
    posted_at_epoch: int
    thread_ts: str | None = None


_lock = threading.Lock()
_log: list[PostedEntry] = []


@app.post("/post", response_model=PostResponse)
async def post(req: PostRequest) -> PostResponse:
    mid = f"slk-{uuid.uuid4().hex[:10]}"
    now = int(time.time())
    with _lock:
        _log.append(PostedEntry(message_id=mid, channel=req.channel,
                                text_preview=req.text[:200], posted_at_epoch=now,
                                thread_ts=req.thread_ts))
        if len(_log) > 500:
            del _log[: len(_log) - 500]
    return PostResponse(ok=True, message_id=mid, posted_at_epoch=now)


@app.get("/posted")
async def posted(limit: int = 50) -> dict[str, Any]:
    with _lock:
        items = list(_log[-limit:])
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@app.get("/health")
async def health() -> dict[str, Any]:
    with _lock:
        count = len(_log)
    return {"status": "healthy", "tool": "slack-poster", "version": "0.1.0", "posted_log_entries": count}
