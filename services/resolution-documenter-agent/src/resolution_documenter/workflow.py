"""Resolution Documenter workflow — Anthropic chain.

LLM composes a structured note; SBCA-driven policy decides create-vs-update
against the knowledge base; document-formatter renders to markdown.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from resolution_documenter.agent import compose_with_gateway
from resolution_documenter.config import AgentConfig
from resolution_documenter.models import (
    DocumentationResult,
    DocumenterInput,
)
from resolution_documenter.tools import (
    DocumentFormatter,
    KnowledgeBaseSearch,
    KnowledgeBaseWriter,
)


class DocumenterRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        formatter: DocumentFormatter, kb_writer: KnowledgeBaseWriter, kb_search: KnowledgeBaseSearch,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.formatter = formatter
        self.kb_writer = kb_writer
        self.kb_search = kb_search
        self._system = Path(cfg.prompts.notes_system_path).read_text()
        self._user = Path(cfg.prompts.notes_user_path).read_text()

    async def run(self, payload: DocumenterInput) -> DocumentationResult:
        started = time.perf_counter()
        category = payload.classification.category

        # ─── SBCA rules ─────────────────────────────────────────────────────
        template_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.documentation_template_by_category,
            process="i2r", step="closure.document",
        )
        update_policy = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.kb_update_policy,
            process="i2r", step="closure.document",
        )
        publishing = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.documentation_publishing,
            process="i2r", step="closure.document",
        )
        try:
            template_id = str((template_rule or {}).get(category) or (template_rule or {}).get("default", "resolution-note-default"))
            update_threshold = float((update_policy or {}).get("update_when_effectiveness_above", 0.75))
            create_below = float((update_policy or {}).get("create_when_no_close_match_below", 0.50))
            draft_below = float((update_policy or {}).get("draft_when_low_confidence_below", 0.40))
            publish_auto = bool((publishing or {}).get("publish_automatically", False))
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed documentation rules: {exc}") from exc

        # ─── LLM composes the structured note ──────────────────────────────
        user_prompt = self._user.format(
            incident_id=payload.incident.incident_id,
            affected_service=payload.incident.affected_service or "",
            service_area=payload.classification.service_area,
            category=category,
            symptoms_summary=payload.incident.symptoms_summary,
            symptoms_verbatim=payload.incident.symptoms_verbatim or "",
            root_cause=(payload.diagnosis.root_cause if payload.diagnosis else "") or "",
            cause_type=(payload.diagnosis.cause_type if payload.diagnosis else "") or "",
            diagnosis_confidence=(payload.diagnosis.confidence if payload.diagnosis else 0.0) or 0.0,
            runbook_id=payload.fix_result.selected_runbook_id or "",
            what_changed=payload.fix_result.what_changed or "",
            changed_resources=", ".join(payload.fix_result.changed_resources or []),
            rollback_token=payload.fix_result.rollback_token or "",
            fix_verified=(payload.verification.fix_verified if payload.verification else False),
            verification_reasoning=(payload.verification.reasoning if payload.verification else "") or "",
        )
        with audit_span("doc.compose", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=user_prompt)
            try:
                note = await compose_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._system, user_prompt=user_prompt,
                )
            except Exception as exc:
                raise AgentError(f"Documenter LLM failed: {exc}", cause=exc) from exc
            cat_event("llm_response", response=json.dumps(note))

        # ─── Find a candidate existing article ─────────────────────────────
        candidate: dict[str, Any] | None = None
        with audit_span("doc.kb_search", audit_type=AuditType.PLATFORM):
            try:
                hits = await self.kb_search.search(
                    query=f"{note.get('title','')} {note.get('symptoms_seen_by_user','')}",
                    service_filter=payload.incident.affected_service,
                    category_filter=category, limit=3,
                )
                if hits:
                    candidate = hits[0]
            except Exception as exc:
                # KB search failure is non-fatal — fall back to create-as-draft.
                cat_event("kb_search_failed", error=str(exc))

        # ─── Render markdown ───────────────────────────────────────────────
        with audit_span("doc.render", audit_type=AuditType.PLATFORM,
                       attributes={"doc.template_id": template_id}):
            rendered = await self.formatter.render(
                template_id=template_id,
                note={**note, "service_area": payload.classification.service_area,
                      "affected_service": payload.incident.affected_service or ""},
            )
        markdown = str(rendered.get("markdown", ""))

        # ─── Decide: update / create / draft ───────────────────────────────
        candidate_effectiveness = float(candidate.get("effectiveness_score", 0.0)) if candidate else 0.0
        decision: str
        article_id: str | None = None
        article_is_draft = not publish_auto

        if candidate and candidate_effectiveness >= update_threshold:
            with audit_span("doc.kb_update", audit_type=AuditType.PLATFORM):
                resp = await self.kb_writer.update(
                    article_id=str(candidate["article_id"]),
                    append_section=markdown,
                    source_incident_id=payload.incident.incident_id,
                )
            decision = "updated"
            article_id = str(resp.get("article_id"))
        elif (not candidate) or candidate_effectiveness < create_below:
            # No close match — create fresh.
            with audit_span("doc.kb_create", audit_type=AuditType.PLATFORM):
                resp = await self.kb_writer.create(
                    title=str(note.get("title", f"Incident {payload.incident.incident_id} resolution")),
                    category=category,
                    service=payload.incident.affected_service or "",
                    body_markdown=markdown,
                    keywords=list(note.get("applicable_keywords") or []),
                    draft=article_is_draft,
                    source_incident_id=payload.incident.incident_id,
                )
            decision = "drafted" if article_is_draft else "created"
            article_id = str(resp.get("article_id"))
        else:
            # Middle band: close enough to be related but not strong enough to
            # extend — emit a draft article tagged "needs_review".
            decision = "drafted"
            article_is_draft = True
            with audit_span("doc.kb_draft", audit_type=AuditType.PLATFORM):
                resp = await self.kb_writer.create(
                    title=f"DRAFT — {note.get('title', 'New resolution note')}",
                    category=category,
                    service=payload.incident.affected_service or "",
                    body_markdown=markdown,
                    keywords=list(note.get("applicable_keywords") or []),
                    draft=True,
                    source_incident_id=payload.incident.incident_id,
                )
            article_id = str(resp.get("article_id"))

        cat_event("kb_decision", decision=decision, article_id=article_id, candidate=str(candidate))
        pst_event("doc_complete",
                  duration_ms=(time.perf_counter() - started) * 1000.0,
                  decision=decision, kb_article_id=article_id)

        return DocumentationResult(
            incident_id=payload.incident.incident_id,
            decision=decision,  # type: ignore[arg-type]
            template_id=template_id,
            article_id=article_id,
            article_is_draft=article_is_draft,
            rendered_markdown=markdown,
            note_title=str(note.get("title", "")),
            keywords=list(note.get("applicable_keywords") or []),
        )
