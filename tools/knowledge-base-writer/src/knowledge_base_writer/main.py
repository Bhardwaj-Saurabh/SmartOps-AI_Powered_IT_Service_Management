"""Knowledge base writer sidecar (synthetic).

In-memory store with two modes:
  - create: new article (draft if ``draft=true``, otherwise published)
  - update: append to an existing article by article_id

Production swap-in: Confluence / ServiceNow KB REST.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="knowledge-base-writer", version="0.1.0")


class CreateRequest(BaseModel):
    title: str
    category: str
    service: str
    body_markdown: str
    keywords: list[str] = []
    draft: bool = True
    source_incident_id: str | None = None


class UpdateRequest(BaseModel):
    article_id: str
    append_section: str          # markdown to append
    source_incident_id: str | None = None


class ArticleEntry(BaseModel):
    article_id: str
    title: str
    category: str
    service: str
    body_markdown: str
    keywords: list[str]
    draft: bool
    created_at_epoch: int
    last_updated_epoch: int
    revisions: int = 0


_lock = threading.Lock()
_store: dict[str, ArticleEntry] = {}


@app.post("/create", response_model=ArticleEntry)
async def create(req: CreateRequest) -> ArticleEntry:
    aid = f"KB-NEW-{uuid.uuid4().hex[:8].upper()}"
    now = int(time.time())
    entry = ArticleEntry(
        article_id=aid, title=req.title, category=req.category, service=req.service,
        body_markdown=req.body_markdown, keywords=req.keywords, draft=req.draft,
        created_at_epoch=now, last_updated_epoch=now, revisions=0,
    )
    with _lock:
        _store[aid] = entry
    return entry


@app.post("/update", response_model=ArticleEntry)
async def update(req: UpdateRequest) -> ArticleEntry:
    with _lock:
        entry = _store.get(req.article_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Article {req.article_id} not found")
        suffix_marker = f"\n\n---\n*Updated from incident {req.source_incident_id or 'unknown'}*\n"
        entry.body_markdown = entry.body_markdown + suffix_marker + req.append_section
        entry.last_updated_epoch = int(time.time())
        entry.revisions += 1
    return entry


@app.get("/articles")
async def articles(limit: int = 50) -> dict[str, Any]:
    with _lock:
        items = list(_store.values())[-limit:]
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@app.get("/article/{aid}", response_model=ArticleEntry)
async def article(aid: str) -> ArticleEntry:
    with _lock:
        entry = _store.get(aid)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Article {aid} not found")
    return entry


@app.get("/health")
async def health() -> dict[str, Any]:
    with _lock:
        count = len(_store)
    return {"status": "healthy", "tool": "knowledge-base-writer", "version": "0.1.0",
            "stored_articles": count}
