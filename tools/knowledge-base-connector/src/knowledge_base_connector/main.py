"""Knowledge base connector sidecar (keyword search over synthetic corpus)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="knowledge-base-connector", version="0.1.0")

_KB_PATH = os.environ.get("KB_PATH", "/app/data/articles.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_KB_PATH).read_text()) or {}

_WORD_RE = re.compile(r"[a-zA-Z0-9_-]+")


def _tokenise(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if len(t) >= 3}


def _keyword_score(query_tokens: set[str], article: dict) -> float:
    text = f"{article.get('title','')} {article.get('body','')}"
    article_tokens = _tokenise(text)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & article_tokens)
    return overlap / max(1, len(query_tokens))


class SearchRequest(BaseModel):
    query: str
    service_filter: str | None = None
    category_filter: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class Article(BaseModel):
    article_id: str
    title: str
    category: str
    service: str
    keyword_score: float = Field(ge=0.0, le=1.0)
    effectiveness_score: float = Field(ge=0.0, le=1.0)
    updated_at_days_ago: int
    excerpt: str


class SearchResponse(BaseModel):
    matched: int
    articles: list[Article]


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    items: list[dict[str, Any]] = list(_corpus.get("articles") or [])
    if req.service_filter:
        items = [a for a in items if (a.get("service") or "").lower() == req.service_filter.lower()]
    if req.category_filter:
        items = [a for a in items if (a.get("category") or "").lower() == req.category_filter.lower()]

    qtokens = _tokenise(req.query)
    scored = [(a, _keyword_score(qtokens, a)) for a in items]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    out: list[Article] = []
    for a, score in scored[: req.limit]:
        body = a.get("body") or ""
        excerpt = body.strip().split("\n", 1)[0]
        out.append(Article(
            article_id=a["article_id"], title=a["title"],
            category=a["category"], service=a["service"],
            keyword_score=round(score, 3),
            effectiveness_score=float(a.get("effectiveness_score", 0.0)),
            updated_at_days_ago=int(a.get("updated_at_days_ago", 0)),
            excerpt=excerpt[:280],
        ))
    return SearchResponse(matched=len(out), articles=out)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy", "tool": "knowledge-base-connector", "version": "0.1.0",
        "article_count": len(_corpus.get("articles") or []),
    }
