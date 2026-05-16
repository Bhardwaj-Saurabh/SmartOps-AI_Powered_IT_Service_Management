"""Problem Linker workflow.

Detect recurrence patterns + decide: link to an existing open problem,
recommend a new one, or stay below threshold.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from problem_linker.agent import assess_with_gateway
from problem_linker.config import AgentConfig
from problem_linker.models import (
    ClusterSnapshot,
    LinkerInput,
    LinkerResult,
)
from problem_linker.tools import ClusteringTool, IncidentHistory


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _derive_signature(root_cause: str | None) -> str:
    """Synthetic Phase 1: lowercase + first-three-token slug of the root_cause.
    Real impl: semantic embedding cluster id."""
    if not root_cause:
        return "unknown"
    tokens = _TOKEN_RE.findall(root_cause.lower())[:3]
    return "-".join(tokens) if tokens else "unknown"


def _is_eligible(rule: dict[str, Any], service_area: str, category: str) -> bool:
    for entry in (rule or {}).get("eligible", []) or []:
        if entry.get("service_area") == service_area and entry.get("category") == category:
            return True
    return bool(rule.get("default", False))


class LinkerRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        incident_history: IncidentHistory, clustering: ClusteringTool,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.incident_history = incident_history
        self.clustering = clustering
        self._system = Path(cfg.prompts.assess_system_path).read_text()
        self._user = Path(cfg.prompts.assess_user_path).read_text()

    async def run(self, payload: LinkerInput) -> LinkerResult:
        started = time.perf_counter()
        incident_id = payload.incident.incident_id
        service_area = payload.classification.service_area
        category = payload.classification.category
        signature = payload.similarity_signature or _derive_signature(
            payload.diagnosis.root_cause if payload.diagnosis else None,
        )

        # ─── SBCA rules ─────────────────────────────────────────────────────
        threshold_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.problem_creation_threshold,
            process="i2r", step="closure.problem_link",
        )
        min_sim_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.cluster_min_similarity,
            process="i2r", step="closure.problem_link",
        )
        categories_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.problem_link_categories,
            process="i2r", step="closure.problem_link",
        )
        try:
            window_days = int((threshold_rule or {}).get("window_days", 30))
            min_count = int(
                ((threshold_rule or {}).get("by_service_area") or {}).get(service_area,
                (threshold_rule or {}).get("default", 3))
            )
            min_similarity = float((min_sim_rule or {}).get("default", 0.65))
        except (TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed problem-linker rules: {exc}") from exc

        eligible = _is_eligible(categories_rule or {}, service_area, category)

        # ─── Fetch history + open problems ─────────────────────────────────
        with audit_span("plinker.history", audit_type=AuditType.PLATFORM):
            history_resp = await self.incident_history.query(
                service_area=service_area, category=category, window_days=window_days,
            )
        history = list(history_resp.get("incidents") or [])
        open_problems = list(history_resp.get("open_problems") or [])

        # Include the current incident in the clustering input so signature
        # matches that lone-incident scenarios cluster with their history.
        cluster_input = [
            {
                "incident_id": h["incident_id"],
                "similarity_signature": h.get("similarity_signature", "unknown"),
                "reporter_department": h.get("reporter_department"),
                "affected_service": h.get("affected_service"),
            }
            for h in history
        ] + [{
            "incident_id": incident_id, "similarity_signature": signature,
            "reporter_department": None, "affected_service": payload.incident.affected_service,
        }]

        # ─── Cluster ────────────────────────────────────────────────────────
        with audit_span("plinker.cluster", audit_type=AuditType.PLATFORM):
            cluster_resp = await self.clustering.cluster(incidents=cluster_input)
        raw_clusters = list(cluster_resp.get("clusters") or [])
        eligible_clusters = [c for c in raw_clusters if float(c.get("cohesion", 0.0)) >= min_similarity]
        clusters_for_artifact = [
            ClusterSnapshot(
                signature=str(c["signature"]),
                incident_ids=list(c.get("incident_ids") or []),
                size=int(c.get("size", 0)),
                cohesion=float(c.get("cohesion", 0.0)),
                distinct_reporter_departments=list(c.get("distinct_reporter_departments") or []),
            )
            for c in eligible_clusters
        ]

        # ─── If history was empty, escape early ─────────────────────────────
        if not history:
            pst_event("plinker_no_history", incident_id=incident_id)
            return LinkerResult(
                incident_id=incident_id, decision="no_history", clusters=[],
            )

        # ─── Match against open problems first (cheapest decision) ─────────
        for prob in open_problems:
            if str(prob.get("similarity_signature")) == signature:
                pst_event("plinker_linked", problem_id=prob.get("problem_id"))
                cat_event("problem_linked", problem_id=prob.get("problem_id"), signature=signature)
                return LinkerResult(
                    incident_id=incident_id, decision="linked",
                    linked_problem_id=str(prob.get("problem_id")),
                    clusters=clusters_for_artifact,
                )

        # ─── Else: maybe recommend a NEW problem ───────────────────────────
        # Find the cluster that matches this incident's signature.
        my_cluster = next((c for c in eligible_clusters if str(c["signature"]) == signature), None)
        if not eligible:
            pst_event("plinker_not_eligible", service_area=service_area, category=category)
            return LinkerResult(
                incident_id=incident_id, decision="not_eligible",
                clusters=clusters_for_artifact,
                scope_note=f"({service_area}, {category}) is not on the eligibility allow-list",
            )

        if my_cluster is None or int(my_cluster.get("size", 0)) < min_count:
            pst_event("plinker_below_threshold",
                      cluster_size=int(my_cluster.get("size", 0)) if my_cluster else 0,
                      threshold=min_count)
            return LinkerResult(
                incident_id=incident_id, decision="below_threshold",
                clusters=clusters_for_artifact,
            )

        # ─── LLM assesses whether the cluster is truly systemic ─────────────
        user_prompt = self._user.format(
            incident_id=incident_id,
            service_area=service_area, category=category,
            root_cause=(payload.diagnosis.root_cause if payload.diagnosis else "") or "",
            symptoms_summary=payload.incident.symptoms_summary,
            cluster_json=json.dumps([c.model_dump() for c in clusters_for_artifact]),
            threshold=min_count, window_days=window_days, min_similarity=min_similarity,
        )
        with audit_span("plinker.assess", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=user_prompt)
            try:
                assessment = await assess_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._system, user_prompt=user_prompt,
                )
            except Exception as exc:
                raise AgentError(f"Problem Linker LLM failed: {exc}", cause=exc) from exc
            cat_event("llm_response", response=json.dumps(assessment))

        is_systemic = bool(assessment.get("is_systemic", False))
        if is_systemic:
            pst_event("plinker_new_problem_recommended", cluster_size=my_cluster["size"])
            return LinkerResult(
                incident_id=incident_id, decision="new_problem_recommended",
                recommended_problem_title=str(assessment.get("recommended_problem_title") or ""),
                recommended_recurrence_pattern=str(assessment.get("recurrence_pattern") or ""),
                llm_confidence=float(assessment.get("confidence", 0.0)),
                llm_reasoning=str(assessment.get("reasoning") or ""),
                scope_note=str(assessment.get("scope_note") or ""),
                clusters=clusters_for_artifact,
            )

        pst_event("plinker_assessed_not_systemic")
        pst_event("plinker_complete", duration_ms=(time.perf_counter() - started) * 1000.0)
        return LinkerResult(
            incident_id=incident_id, decision="below_threshold",
            llm_confidence=float(assessment.get("confidence", 0.0)),
            llm_reasoning=str(assessment.get("reasoning") or ""),
            scope_note=str(assessment.get("scope_note") or ""),
            clusters=clusters_for_artifact,
        )
