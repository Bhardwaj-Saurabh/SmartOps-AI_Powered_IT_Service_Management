"""10-step Priority Scorer chain.

Anthropic pattern: prompt chaining (workflow). One LLM call (step 3) — the
rest is deterministic tool + SBCA orchestration.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from priority_scorer.agent import estimate_with_gateway
from priority_scorer.config import AgentConfig
from priority_scorer.models import (
    DecisionStep,
    Priority,
    PriorityInput,
)
from priority_scorer.tools import ImpactAnalyser, ServiceDependencyMapper


_ORDER = ["low", "medium", "high", "critical"]


def _max_bucket(a: str, b: str) -> str:
    return a if _ORDER.index(a) >= _ORDER.index(b) else b


def _floor_urgency(blast_radius: int, thresholds: list[dict]) -> str:
    """Walk the SBCA thresholds in order, return the urgency_floor for the
    first matching bracket (``lt`` is exclusive upper bound)."""
    for entry in thresholds or []:
        if blast_radius < int(entry.get("lt", 0)):
            return str(entry.get("urgency_floor", "low"))
    return "low"


class PriorityRunner:
    def __init__(
        self,
        *,
        cfg: AgentConfig,
        gateway: GatewayClient,
        semantic: SemanticClient,
        impact: ImpactAnalyser,
        dependency_mapper: ServiceDependencyMapper,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.impact = impact
        self.deps = dependency_mapper
        self._system_prompt = Path(cfg.prompts.impact_system_path).read_text()
        self._user_prompt = Path(cfg.prompts.impact_user_path).read_text()

    async def run(self, payload: PriorityInput) -> Priority:
        start = time.perf_counter()
        decisions: list[DecisionStep] = []

        # Step 1
        decisions.append(DecisionStep(step="01_receive", detail=f"incident_id={payload.incident.incident_id}"))

        # Step 2 — blast radius
        with audit_span("priority.02_dependency_walk", audit_type=AuditType.PLATFORM):
            walk = await self.deps.walk(service=payload.incident.affected_service or "unknown")
        blast_radius = int(walk.get("blast_radius", 0))
        service_tier = walk.get("tier")
        decisions.append(DecisionStep(step="02_blast_radius", detail=f"radius={blast_radius} tier={service_tier}"))

        # Step 3 — LLM impact + urgency
        user_prompt = self._user_prompt.format(
            affected_service=payload.incident.affected_service or "",
            service_area=payload.classification.service_area,
            category=payload.classification.category,
            reporter_vip=payload.incident.reporter_vip,
            symptoms_summary=payload.incident.symptoms_summary,
            symptoms_verbatim=payload.incident.symptoms_verbatim,
        )
        with audit_span("priority.03_llm_impact", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=user_prompt)
            try:
                llm_raw = await estimate_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._system_prompt, user_prompt=user_prompt,
                )
            except Exception as exc:
                raise AgentError(f"LLM impact estimation failed: {exc}", step=3, cause=exc) from exc
            cat_event("llm_response", response=json.dumps(llm_raw))
        llm_impact = str(llm_raw.get("impact", "medium"))
        llm_urgency = str(llm_raw.get("urgency", "medium"))
        decisions.append(DecisionStep(step="03_llm", detail=f"impact={llm_impact} urgency={llm_urgency}"))

        # Step 4 — SBCA blast-radius thresholds → urgency floor
        thresholds = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.blast_radius_thresholds,
            process="i2r", step="triage.prioritise",
        )
        urgency_floor = _floor_urgency(blast_radius, thresholds)
        urgency = _max_bucket(llm_urgency, urgency_floor)
        decisions.append(DecisionStep(step="04_urgency_floor", detail=f"floor={urgency_floor} final={urgency}"))

        # Step 5 — impact-analyser
        with audit_span("priority.05_impact_analyser", audit_type=AuditType.PLATFORM):
            impact_resp = await self.impact.analyse(
                affected_users=None,                # not yet wired in Phase 1
                blast_radius=blast_radius,
                reporter_vip=payload.incident.reporter_vip,
                service_tier=service_tier,
            )
        analyser_impact = str(impact_resp.get("impact_bucket", "medium"))
        impact_score = float(impact_resp.get("impact_score", 0.0))
        decisions.append(DecisionStep(step="05_impact_analyser", detail=f"bucket={analyser_impact} score={impact_score:.2f}"))

        # Step 6 — pick max impact bucket
        impact = _max_bucket(llm_impact, analyser_impact)
        decisions.append(DecisionStep(step="06_impact_merge", detail=f"llm={llm_impact} analyser={analyser_impact} → {impact}"))

        # Step 7 — SBCA priority matrix
        matrix = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.priority_matrix,
            process="i2r", step="triage.prioritise",
        )
        try:
            priority = str(((matrix or {}).get(impact) or {}).get(urgency))
            if not priority.startswith("P"):
                raise KeyError(priority)
        except KeyError as exc:
            raise SemanticPlaneError(f"priority_matrix missing cell for {impact}/{urgency}") from exc
        decisions.append(DecisionStep(step="07_matrix", detail=f"{impact}×{urgency}→{priority}"))

        # Step 8 — VIP override
        vip_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.vip_priority_overrides,
            process="i2r", step="triage.prioritise",
        )
        vip_reason: str | None = None
        dept = (payload.incident.reporter_department or "").lower()
        by_dept = (vip_rule or {}).get("by_department") or {}
        if dept and dept in by_dept:
            override_priority = str(by_dept[dept])
            # min-priority effect: only override if forced is *higher* (lower number)
            if _p_rank(override_priority) < _p_rank(priority):
                vip_reason = f"vip:{dept}->{override_priority}"
                priority = override_priority
        decisions.append(DecisionStep(step="08_vip_override", detail=vip_reason or "none"))

        # Step 9 — change freeze annotation
        freeze_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.change_freeze,
            process="i2r", step="triage.prioritise",
        )
        freeze_active = bool((freeze_rule or {}).get("active", False))
        decisions.append(DecisionStep(step="09_change_freeze", detail=f"active={freeze_active}"))

        # Step 10 — emit
        pst_event("priority_emit", priority=priority, blast_radius=blast_radius)
        if priority == "P1":
            pst_event("p1_count", value=1)
        elif priority == "P2":
            pst_event("p2_count", value=1)
        decisions.append(DecisionStep(step="10_emit", detail=priority))
        pst_event("priority_complete", duration_ms=(time.perf_counter() - start) * 1000.0)

        return Priority(
            incident_id=payload.incident.incident_id,
            priority=priority,  # type: ignore[arg-type]
            impact=impact,      # type: ignore[arg-type]
            urgency=urgency,    # type: ignore[arg-type]
            blast_radius=blast_radius,
            service_tier=service_tier,
            impact_score=impact_score,
            vip_override=vip_reason,
            change_freeze_active=freeze_active,
            decision_chain=decisions,
        )


def _p_rank(p: str) -> int:
    """P1 < P2 < P3 < P4 (lower rank = higher priority)."""
    return {"P1": 1, "P2": 2, "P3": 3, "P4": 4}.get(p, 99)
