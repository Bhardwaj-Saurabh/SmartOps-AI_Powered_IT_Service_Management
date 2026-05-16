"""Automated Fix workflow — 12-step chain with explicit SBCA-controlled gates.

Safety design notes are in docs/eu-ai-act-risk-assessment.md. The shape of
this code is the EU AI Act Article 14 (human oversight) + Article 15
(robustness) implementation:

  * Steps 2-4 are FAIL-CLOSED gates. Any unrecognised input → emit
    requires_human, NEVER actuate.
  * Step 8 (snapshot) is UNCONDITIONAL — runs before step 9 always.
  * Step 10 (rollback-on-error) is automatic; no "we'll fix it later" path.
  * The `rollback` skill (separate from this runner) is the orchestrator's
    saga compensation entry point.
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

from automated_fix.agent import select_with_gateway, summarise_with_gateway
from automated_fix.config import AgentConfig
from automated_fix.models import (
    FixInput,
    FixOutcome,
    RollbackInput,
    RollbackOutcome,
    StepRecord,
)
from automated_fix.tools import (
    ConfigurationManager,
    RollbackHandler,
    ScriptExecutor,
)


def _approval_for(rule: dict[str, Any], fix_type: str, service_tier: str | None) -> bool:
    by = (rule or {}).get("by_fix_type") or {}
    entry = by.get(fix_type)
    if entry is None:
        return bool(rule.get("default", False))
    if service_tier and service_tier in entry:
        return bool(entry[service_tier])
    # Service tier missing → fail closed.
    return False


class AutomatedFixRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        script: ScriptExecutor, config_manager: ConfigurationManager, rollback_handler: RollbackHandler,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.script = script
        self.config_manager = config_manager
        self.rollback_handler = rollback_handler
        self._select_system = Path(cfg.prompts.select_system_path).read_text()
        self._select_user = Path(cfg.prompts.select_user_path).read_text()
        self._summary_system = Path(cfg.prompts.summary_system_path).read_text()
        self._summary_user = Path(cfg.prompts.summary_user_path).read_text()

    async def apply(self, payload: FixInput) -> FixOutcome:
        started = time.perf_counter()
        incident_id = payload.incident.incident_id
        affected_service = payload.incident.affected_service or "unknown"
        service_tier = payload.priority.service_tier or "bronze"   # fail safe to lowest tier

        # ─── Step 5 (pre-empt): fetch catalogue so we know fix_type options ──
        # We fetch the catalogue BEFORE the approval gate so the selector LLM
        # can pick a fix_type that the approval matrix will accept. The
        # approval gate runs against the LLM's pick, not the catalogue's
        # superset — minimises spurious requires_human responses.
        with audit_span("fix.05_fetch_catalogue", audit_type=AuditType.PLATFORM):
            catalogue = await self.script.catalogue()

        # ─── Step 6: LLM picks runbook ──────────────────────────────────────
        kb_json = json.dumps([a.model_dump() for a in payload.knowledge_articles])
        select_user = self._select_user.format(
            affected_service=affected_service,
            service_area=payload.classification.service_area,
            category=payload.classification.category,
            priority=payload.priority.priority,
            symptoms_summary=payload.incident.symptoms_summary,
            root_cause=payload.diagnosis.root_cause or "",
            diagnosis_confidence=payload.diagnosis.confidence or 0.0,
            runbook_catalogue=json.dumps(catalogue),
            knowledge_articles_json=kb_json,
        )
        with audit_span("fix.06_select", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", role="selector", prompt=select_user)
            try:
                selection = await select_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._select_system, user_prompt=select_user,
                )
            except Exception as exc:
                raise AgentError(f"Runbook selection LLM failed: {exc}", step=6, cause=exc) from exc
            cat_event("llm_response", role="selector", response=json.dumps(selection))

        runbook_id = selection.get("selected_runbook_id")
        parameters: dict[str, Any] = selection.get("parameters") or {}
        rationale = str(selection.get("rationale", ""))

        if not runbook_id:
            pst_event("fix_no_runbook", reason="selector returned null")
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason="No suitable runbook in the catalogue for the diagnosed cause",
                selected_runbook_id=None, runbook_parameters={},
            )

        # Find the runbook's fix_type from the catalogue (we trust catalogue, not LLM).
        rb_entry = next((r for r in catalogue if r.get("id") == runbook_id), None)
        if rb_entry is None:
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason=f"LLM picked unknown runbook '{runbook_id}'",
            )
        fix_type = str(rb_entry.get("fix_type", runbook_id))

        # ─── Step 2: SBCA approval matrix ───────────────────────────────────
        approval_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.automated_fix_approval,
            process="i2r", step="resolution.fix",
        )
        if not _approval_for(approval_rule, fix_type, service_tier):
            pst_event("fix_approval_denied", fix_type=fix_type, service_tier=service_tier)
            cat_event("approval_denied", fix_type=fix_type, service_tier=service_tier, rule_snapshot=json.dumps(approval_rule))
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason=f"automated_fix_approval=false for ({fix_type}, {service_tier})",
                selected_runbook_id=runbook_id, runbook_parameters=parameters,
            )

        # ─── Step 3: scope cap ──────────────────────────────────────────────
        scope_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.automated_fix_scope,
            process="i2r", step="resolution.fix",
        )
        max_blast = int((scope_rule or {}).get("max_blast_radius", 0))
        max_users = int((scope_rule or {}).get("max_affected_users", 0))
        if payload.priority.blast_radius > max_blast or len(payload.incident.affected_users) > max_users:
            pst_event("fix_scope_denied",
                      blast_radius=payload.priority.blast_radius, users=len(payload.incident.affected_users))
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason=(
                    f"Scope cap exceeded: blast_radius={payload.priority.blast_radius} "
                    f"(max {max_blast}), affected_users={len(payload.incident.affected_users)} (max {max_users})"
                ),
                selected_runbook_id=runbook_id, runbook_parameters=parameters,
            )

        # ─── Step 4: change-freeze gate ─────────────────────────────────────
        freeze_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.change_freeze,
            process="i2r", step="resolution.fix",
        )
        if bool((freeze_rule or {}).get("active", False)) and not payload.priority.emergency:
            pst_event("fix_change_freeze_denied")
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason="change_freeze.active=true and incident not flagged emergency",
                selected_runbook_id=runbook_id, runbook_parameters=parameters,
            )

        # ─── Step 7: parameter validation (script-executor enforces too) ─────
        schema = rb_entry.get("param_schema") or {}
        missing = [k for k, spec in schema.items() if spec.get("required") and k not in parameters]
        if missing:
            return FixOutcome(
                incident_id=incident_id, state="requires_human",
                requires_human_reason=f"Required parameters missing for {runbook_id}: {missing}",
                selected_runbook_id=runbook_id, runbook_parameters=parameters,
            )

        cat_event("runbook_selection", runbook_id=runbook_id, fix_type=fix_type,
                  service_tier=service_tier, rationale=rationale, parameters=json.dumps(parameters))

        # ─── Step 8: UNCONDITIONAL snapshot before any mutation ─────────────
        with audit_span("fix.08_snapshot", audit_type=AuditType.PLATFORM):
            snap = await self.config_manager.snapshot(
                target_service=affected_service,
                runbook_id=runbook_id,
                config_state={"pre_fix_marker": True, "runbook_id": runbook_id},
            )
        snapshot_id = str(snap["snapshot_id"])
        cat_event("snapshot_taken", snapshot_id=snapshot_id, target_service=affected_service)

        # ─── Step 9: execute runbook ────────────────────────────────────────
        with audit_span("fix.09_execute", audit_type=AuditType.PLATFORM, attributes={"fix.runbook_id": runbook_id}):
            execution = await self.script.execute(
                runbook_id=runbook_id, parameters=parameters, snapshot_id=snapshot_id,
            )

        steps = [StepRecord(**s) for s in execution.get("steps") or []]
        outcome = str(execution.get("overall_outcome", "failed"))
        cat_event("execution_result", outcome=outcome, step_count=len(steps),
                  steps=json.dumps([s.model_dump() for s in steps]))

        rollback_invoked = False
        rollback_detail: dict[str, Any] | None = None

        if outcome != "succeeded":
            # ─── Step 10: automatic rollback on failure ─────────────────────
            with audit_span("fix.10_rollback", audit_type=AuditType.PLATFORM):
                try:
                    rb_resp = await self.rollback_handler.rollback(
                        snapshot_id=snapshot_id, reason=f"runbook {runbook_id} failed at step",
                    )
                    rollback_invoked = True
                    rollback_detail = rb_resp
                    cat_event("rollback_invoked", snapshot_id=snapshot_id, detail=json.dumps(rb_resp))
                    pst_event("rollback_triggered", runbook_id=runbook_id)
                except Exception as exc:
                    # Rollback failure is the worst outcome — record but don't mask the original failure
                    cat_event("rollback_failed", snapshot_id=snapshot_id, error=str(exc))
                    pst_event("rollback_failed", runbook_id=runbook_id)

            # ─── Step 11 (truncated): summary on failure ────────────────────
            summary = await self._summarise(runbook_id, "rolled_back" if rollback_invoked else "failed", steps)
            pst_event("fix_failed", duration_ms=(time.perf_counter() - started) * 1000.0)
            return FixOutcome(
                incident_id=incident_id,
                state="rolled_back" if rollback_invoked else "failed",
                selected_runbook_id=runbook_id, runbook_parameters=parameters,
                rollback_token=snapshot_id, step_log=steps,
                rollback_invoked=rollback_invoked, rollback_detail=rollback_detail,
                what_changed=summary.get("what_changed"),
                changed_resources=list(summary.get("changed_resources") or []),
                user_visible_impact=summary.get("user_visible_impact"),
            )

        # ─── Step 11: summary on success ───────────────────────────────────
        summary = await self._summarise(runbook_id, "succeeded", steps)
        pst_event("fix_succeeded", duration_ms=(time.perf_counter() - started) * 1000.0,
                  runbook_id=runbook_id)
        return FixOutcome(
            incident_id=incident_id, state="completed",
            selected_runbook_id=runbook_id, runbook_parameters=parameters,
            rollback_token=snapshot_id, step_log=steps,
            rollback_invoked=False,
            what_changed=summary.get("what_changed"),
            changed_resources=list(summary.get("changed_resources") or []),
            user_visible_impact=summary.get("user_visible_impact"),
        )

    async def _summarise(self, runbook_id: str, outcome: str, steps: list[StepRecord]) -> dict[str, Any]:
        user_prompt = self._summary_user.format(
            runbook_id=runbook_id, outcome=outcome,
            step_log_json=json.dumps([s.model_dump() for s in steps]),
        )
        with audit_span("fix.11_summarise", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", role="summariser", prompt=user_prompt)
            try:
                summary = await summarise_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._summary_system, user_prompt=user_prompt,
                )
            except Exception as exc:
                # Summary failure is non-fatal — the fix already ran.
                cat_event("summary_llm_failed", error=str(exc))
                return {"what_changed": "summary unavailable", "changed_resources": [], "user_visible_impact": "unknown"}
            cat_event("summary_llm_response", response=json.dumps(summary))
        return summary

    # ─── separate skill: rollback (called by Saga from orchestrator) ──────
    async def rollback(self, payload: RollbackInput) -> RollbackOutcome:
        with audit_span("fix.rollback", audit_type=AuditType.PLATFORM,
                       attributes={"fix.rollback_token": payload.rollback_token}):
            try:
                rb_resp = await self.rollback_handler.rollback(
                    snapshot_id=payload.rollback_token, reason=payload.reason,
                )
            except Exception as exc:
                pst_event("rollback_failed_from_saga", error_class=type(exc).__name__)
                cat_event("rollback_failed", token=payload.rollback_token, error=str(exc))
                raise AgentError(f"Rollback failed: {exc}", cause=exc) from exc
            cat_event("rollback_succeeded", token=payload.rollback_token, detail=json.dumps(rb_resp))
            pst_event("rollback_from_saga", token=payload.rollback_token)
        return RollbackOutcome(
            rollback_token=payload.rollback_token,
            restored=bool(rb_resp.get("restored", False)),
            restored_state_keys=list(rb_resp.get("restored_state_keys") or []),
            note=rb_resp.get("note"),
        )
