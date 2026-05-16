"""Knowledge Search workflow — Anthropic parallelization pattern.

The vector + keyword searches run concurrently. The merge applies SBCA
weights, filters below ``min_score``, flags stale articles, and lets the
LLM re-rank with the diagnosed root cause in context.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from knowledge_search.agent import rerank_with_gateway
from knowledge_search.config import AgentConfig
from knowledge_search.models import (
    ArticleResult,
    DecisionStep,
    KnowledgeInput,
    KnowledgeResult,
)
from knowledge_search.tools import EmbeddingSearch, KnowledgeBase


class KnowledgeRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        knowledge_base: KnowledgeBase, embedding_search: EmbeddingSearch,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.knowledge_base = knowledge_base
        self.embedding_search = embedding_search
        self._rerank_system = Path(cfg.prompts.rerank_system_path).read_text()
        self._rerank_user = Path(cfg.prompts.rerank_user_path).read_text()

    async def run(self, payload: KnowledgeInput) -> KnowledgeResult:
        start = time.perf_counter()
        decisions: list[DecisionStep] = [DecisionStep(step="01_receive", detail=f"incident_id={payload.incident.incident_id}")]

        # ─── SBCA rules ─────────────────────────────────────────────────────
        relevance_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.knowledge_relevance,
            process="i2r", step="resolution.knowledge_search",
        )
        freshness_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.knowledge_freshness,
            process="i2r", step="resolution.knowledge_search",
        )
        try:
            min_score = float(relevance_rule["min_score"])
            vector_weight = float(relevance_rule["vector_weight"])
            keyword_weight = float(relevance_rule["keyword_weight"])
            max_age = int(freshness_rule["max_days_for_recommendation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed knowledge rules: {exc}") from exc

        # ─── Parallel: embedding for vector search + keyword query string ──
        query_text = f"{payload.incident.symptoms_summary} {payload.classification.service_area} {payload.classification.category}"
        if payload.diagnosis and payload.diagnosis.root_cause:
            query_text = f"{query_text} {payload.diagnosis.root_cause}"

        async def vector_branch() -> list[dict]:
            with audit_span("knowledge.vector_search", audit_type=AuditType.PLATFORM):
                embed = await self.gateway.embedding(model=self.cfg.embedding.alias, input=query_text)
                vec = embed.vectors[0] if embed.vectors else []
                if not vec:
                    raise AgentError("Empty embedding from gateway", step=3)
                return await self.embedding_search.search(vector=vec, limit=10)

        async def keyword_branch() -> list[dict]:
            with audit_span("knowledge.keyword_search", audit_type=AuditType.PLATFORM):
                return await self.knowledge_base.search(query=query_text, limit=10)

        try:
            vector_hits, keyword_hits = await asyncio.gather(vector_branch(), keyword_branch())
        except SemanticPlaneError:
            raise
        except Exception as exc:
            raise AgentError(f"Parallel knowledge search failed: {exc}", step=3, cause=exc) from exc

        decisions.append(DecisionStep(step="03_parallel", detail=f"vector={len(vector_hits)} keyword={len(keyword_hits)}"))
        pst_event("knowledge_search_hits", vector=len(vector_hits), keyword=len(keyword_hits))

        # ─── Merge ──────────────────────────────────────────────────────────
        merged: dict[str, ArticleResult] = {}
        for h in vector_hits:
            aid = h.get("article_id")
            if not aid:
                continue
            merged[aid] = ArticleResult(
                article_id=aid,
                title=h.get("title") or "",
                service=h.get("service") or "",
                category=h.get("category") or "",
                vector_score=float(h.get("similarity", 0.0)),
                keyword_score=None,
                combined_score=0.0,
                relevance_score=0.0,
                updated_at_days_ago=int(h.get("updated_at_days_ago", 0)),
            )
        for k in keyword_hits:
            aid = k.get("article_id")
            if not aid:
                continue
            kscore = float(k.get("keyword_score", 0.0))
            if aid in merged:
                merged[aid].keyword_score = kscore
                merged[aid].excerpt = k.get("excerpt", "")
            else:
                merged[aid] = ArticleResult(
                    article_id=aid,
                    title=k.get("title") or "",
                    service=k.get("service") or "",
                    category=k.get("category") or "",
                    vector_score=None,
                    keyword_score=kscore,
                    combined_score=0.0,
                    relevance_score=0.0,
                    updated_at_days_ago=int(k.get("updated_at_days_ago", 0)),
                    excerpt=k.get("excerpt", ""),
                )

        for art in merged.values():
            v = art.vector_score or 0.0
            k = art.keyword_score or 0.0
            art.combined_score = min(1.0, v * vector_weight + k * keyword_weight)

        # ─── Filter by min_score + flag stale ───────────────────────────────
        kept = [a for a in merged.values() if a.combined_score >= min_score]
        stale_count = 0
        for a in kept:
            if a.updated_at_days_ago > max_age:
                a.is_stale = True
                stale_count += 1
        decisions.append(DecisionStep(step="05_filter_freshness", detail=f"kept={len(kept)} stale={stale_count}"))

        if not kept:
            pst_event("knowledge_empty_result", min_score=min_score)
            return KnowledgeResult(
                incident_id=payload.incident.incident_id,
                articles=[], applicability_summary=None,
                stale_flagged_count=0, decision_chain=decisions,
            )

        # ─── LLM re-rank with diagnosis context ─────────────────────────────
        candidates_for_prompt = [
            {
                "article_id": a.article_id,
                "title": a.title,
                "service": a.service,
                "category": a.category,
                "combined_score": round(a.combined_score, 3),
                "is_stale": a.is_stale,
                "excerpt": a.excerpt[:200],
            }
            for a in kept
        ]
        diag_cause = (payload.diagnosis.root_cause if payload.diagnosis else None) or "n/a"
        user_prompt = self._rerank_user.format(
            affected_service=payload.incident.affected_service or "",
            service_area=payload.classification.service_area,
            category=payload.classification.category,
            symptoms_summary=payload.incident.symptoms_summary,
            hypothesised_cause=diag_cause,
            candidates_json=json.dumps(candidates_for_prompt),
        )
        with audit_span("knowledge.07_rerank", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=user_prompt)
            try:
                ranking = await rerank_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._rerank_system, user_prompt=user_prompt,
                )
            except Exception as exc:
                raise AgentError(f"Re-rank LLM failed: {exc}", step=7, cause=exc) from exc
            cat_event("llm_response", response=json.dumps(ranking))

        by_id: dict[str, dict] = {str(r.get("article_id")): r for r in (ranking.get("ranked") or [])}
        for a in kept:
            r = by_id.get(a.article_id)
            if r:
                a.relevance_score = float(r.get("relevance_score", a.combined_score))
                a.reasoning = str(r.get("reasoning", ""))
            else:
                # LLM dropped this article — keep it but lower the score.
                a.relevance_score = a.combined_score * 0.5
                a.reasoning = "not ranked by re-ranker"

        kept.sort(key=lambda x: x.relevance_score, reverse=True)
        kept = kept[: payload.limit]
        decisions.append(DecisionStep(step="07_rerank", detail=f"final_count={len(kept)}"))

        pst_event("knowledge_complete", duration_ms=(time.perf_counter() - start) * 1000.0,
                  result_count=len(kept), stale=stale_count)

        return KnowledgeResult(
            incident_id=payload.incident.incident_id,
            articles=kept,
            applicability_summary=str(ranking.get("applicability_summary", "")),
            stale_flagged_count=stale_count,
            decision_chain=decisions,
        )
