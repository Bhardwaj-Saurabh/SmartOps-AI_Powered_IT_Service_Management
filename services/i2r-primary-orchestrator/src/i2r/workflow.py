"""I2R Primary Orchestrator runner.

Composes the three sub-process orchestrators into the full Incident-to-
Resolution business process. Reuses the closure-style nested ref syntax
(``input.<...>`` and ``<idx>.<artifact>.<...>``). Adds three pieces of
business-process-level decision-making the lower orchestrators don't have:

  * Triage short-circuit — if Triage returns INPUT_REQUIRED (e.g. Intake
    needs clarification), stop the chain cleanly with a partial result.
  * Early escalation — after Triage but before Resolution, SBCA-gated
    decision to fire a Communication agent call as an early-warning.
  * Closure on failed resolution — SBCA-gated decision to still run
    Closure when Resolution ended in `failed` (so the reporter is
    notified and SLA + drafts are recorded).
"""
from __future__ import annotations

import time
from typing import Any

from a2a_client import A2AClient, A2AClientError
from di_framework_core import AuditType, SemanticPlaneError
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from i2r.config import ChainStep, OrchestratorConfig


_TRIAGE_STEP_IDX = 0
_RESOLVE_STEP_IDX = 1
_CLOSE_STEP_IDX = 2


class I2RRunner:
    def __init__(
        self, *, cfg: OrchestratorConfig, registry_url: str, bearer_provider,
        semantic: SemanticClient,
    ) -> None:
        self.cfg = cfg
        self.registry_url = registry_url
        self.bearer_provider = bearer_provider
        self.semantic = semantic

    async def _client_for(self, capability: str) -> A2AClient:
        token = None
        if self.bearer_provider is not None:
            token = await self.bearer_provider()
        return await A2AClient.from_capability(
            capability, registry_url=self.registry_url, bearer=token,
        )

    async def _call_step(
        self, *, step: ChainStep, payload: dict[str, Any], process: str,
    ) -> dict[str, Any]:
        t_start = time.perf_counter()
        try:
            client = await self._client_for(step.capability)
            sub_task = await client.message_send(
                capability=step.skill,
                parts=[{"kind": "data", "data": payload}],
                process=process, step=step.step_label,
            )
        except A2AClientError as exc:
            return {
                "step": step.step_label, "capability": step.capability, "skill": step.skill,
                "state": "client_error", "error": str(exc),
                "duration_ms": (time.perf_counter() - t_start) * 1000.0,
                "artifacts": [],
            }
        return {
            "step": step.step_label,
            "capability": step.capability, "skill": step.skill,
            "state": sub_task.status.state.value,
            "task_id": sub_task.id,
            "artifacts": [
                {"name": a.name, "data": next((p.data for p in a.parts if p.kind == "data"), None)}  # type: ignore[union-attr]
                for a in sub_task.artifacts
            ],
            "duration_ms": (time.perf_counter() - t_start) * 1000.0,
            "downstream_correlation_id": sub_task.metadata.di.correlation_id,
            "requires_human": sub_task.metadata.di.requires_human,
        }

    async def run(
        self, *, process: str, initial_payload: dict[str, Any], correlation_id: str | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        chain_outputs: list[dict[str, Any]] = []
        escalation_triggered = False
        escalation_reason: str | None = None
        i2r_state = "submitted"

        # ─── Step 0: Triage ─────────────────────────────────────────────────
        triage_step = self.cfg.chain[_TRIAGE_STEP_IDX]
        with audit_span(f"i2r.{triage_step.step_label}", audit_type=AuditType.PLATFORM,
                       attributes={"i2r.capability": triage_step.capability}):
            triage_out = await self._call_step(
                step=triage_step,
                payload=self._payload_for_step(triage_step, initial_payload, chain_outputs),
                process=process,
            )
            chain_outputs.append(triage_out)
            cat_event("i2r_step", step="triage", state=triage_out.get("state"))

        if triage_out.get("state") == "input-required":
            i2r_state = "triage_needs_input"
            pst_event("i2r_triage_short_circuit")
            return self._finalise(
                started=started, process=process, correlation_id=correlation_id,
                chain_outputs=chain_outputs, i2r_state=i2r_state,
                escalation_triggered=False, escalation_reason=None,
            )
        if triage_out.get("state") not in {"completed"}:
            i2r_state = "failed"
            return self._finalise(
                started=started, process=process, correlation_id=correlation_id,
                chain_outputs=chain_outputs, i2r_state=i2r_state,
                escalation_triggered=False, escalation_reason=None,
                failed_step_index=_TRIAGE_STEP_IDX,
            )

        i2r_state = "triaged"

        # ─── Maybe early escalation (between Triage and Resolution) ────────
        triage_summary = self._artifact_data(triage_out, "triage_summary") or {}
        try:
            escalation_rule = await self.semantic.query_rule(
                domain=self.cfg.semantic_queries.i2r_escalation_criteria,
                process=process, step="i2r.escalation_decision",
            )
        except SemanticPlaneError:
            raise   # hard-fail on SBCA — never invent escalation policy
        if self._should_escalate(triage_summary, escalation_rule):
            try:
                escalation_triggered, escalation_reason = await self._fire_escalation(
                    triage_summary=triage_summary, process=process,
                )
            except A2AClientError as exc:
                cat_event("escalation_failed", error=str(exc))
                # Don't block the main chain on escalation comms failure.
                escalation_triggered = False
                escalation_reason = f"escalation comms failed: {exc}"

        # ─── Step 1: Resolution ─────────────────────────────────────────────
        i2r_state = "resolving"
        resolve_step = self.cfg.chain[_RESOLVE_STEP_IDX]
        with audit_span(f"i2r.{resolve_step.step_label}", audit_type=AuditType.PLATFORM):
            resolve_out = await self._call_step(
                step=resolve_step,
                payload=self._payload_for_step(resolve_step, initial_payload, chain_outputs),
                process=process,
            )
            chain_outputs.append(resolve_out)
            cat_event("i2r_step", step="resolve", state=resolve_out.get("state"))

        resolution_state = resolve_out.get("state")
        run_closure_anyway = False

        if resolution_state == "failed":
            i2r_state = "resolution_failed"
            try:
                fail_rule = await self.semantic.query_rule(
                    domain=self.cfg.semantic_queries.i2r_run_closure_on_failed_resolution,
                    process=process, step="i2r.closure_decision",
                )
            except SemanticPlaneError:
                raise
            run_closure_anyway = bool((fail_rule or {}).get("default", True))
            pst_event("i2r_resolution_failed", run_closure=run_closure_anyway)
            cat_event("i2r_closure_on_failure_decision", run_closure=run_closure_anyway)
            if not run_closure_anyway:
                return self._finalise(
                    started=started, process=process, correlation_id=correlation_id,
                    chain_outputs=chain_outputs, i2r_state=i2r_state,
                    escalation_triggered=escalation_triggered, escalation_reason=escalation_reason,
                    failed_step_index=_RESOLVE_STEP_IDX,
                )
        elif resolution_state == "completed":
            i2r_state = "resolution_completed"
        else:
            i2r_state = "failed"
            return self._finalise(
                started=started, process=process, correlation_id=correlation_id,
                chain_outputs=chain_outputs, i2r_state=i2r_state,
                escalation_triggered=escalation_triggered, escalation_reason=escalation_reason,
                failed_step_index=_RESOLVE_STEP_IDX,
            )

        # ─── Step 2: Closure ────────────────────────────────────────────────
        close_step = self.cfg.chain[_CLOSE_STEP_IDX]
        with audit_span(f"i2r.{close_step.step_label}", audit_type=AuditType.PLATFORM):
            close_out = await self._call_step(
                step=close_step,
                payload=self._payload_for_step(close_step, initial_payload, chain_outputs),
                process=process,
            )
            chain_outputs.append(close_out)
            cat_event("i2r_step", step="close", state=close_out.get("state"))

        if close_out.get("state") != "completed":
            i2r_state = "failed"
            return self._finalise(
                started=started, process=process, correlation_id=correlation_id,
                chain_outputs=chain_outputs, i2r_state=i2r_state,
                escalation_triggered=escalation_triggered, escalation_reason=escalation_reason,
                failed_step_index=_CLOSE_STEP_IDX,
            )

        i2r_state = "closed"
        return self._finalise(
            started=started, process=process, correlation_id=correlation_id,
            chain_outputs=chain_outputs, i2r_state=i2r_state,
            escalation_triggered=escalation_triggered, escalation_reason=escalation_reason,
        )

    # ─── helpers ────────────────────────────────────────────────────────────
    def _finalise(
        self, *, started: float, process: str, correlation_id: str | None,
        chain_outputs: list[dict[str, Any]], i2r_state: str,
        escalation_triggered: bool, escalation_reason: str | None,
        failed_step_index: int | None = None,
    ) -> dict[str, Any]:
        total_ms = (time.perf_counter() - started) * 1000.0
        pst_event("i2r_complete", duration_ms=total_ms, state=i2r_state,
                  step_count=len(chain_outputs))
        return {
            "process": process,
            "correlation_id": correlation_id,
            "i2r_state": i2r_state,
            "steps": chain_outputs,
            "failed_step_index": failed_step_index,
            "escalation_triggered": escalation_triggered,
            "escalation_reason": escalation_reason,
            "duration_ms": total_ms,
        }

    @staticmethod
    def _should_escalate(triage_summary: dict[str, Any], rule: dict[str, Any] | None) -> bool:
        if not rule:
            return False
        priority_obj = triage_summary.get("priority") or {}
        priority = priority_obj.get("priority")
        blast = int(priority_obj.get("blast_radius", 0) or 0)
        if priority in (rule.get("priorities") or []):
            return True
        if blast >= int(rule.get("blast_radius_min", 9999)):
            return True
        incident = triage_summary.get("incident") or {}
        dept = (incident.get("reporter_department") or "").lower()
        vip_depts = {d.lower() for d in (rule.get("vip_reporter_departments") or [])}
        if dept and dept in vip_depts:
            return True
        return False

    async def _fire_escalation(
        self, *, triage_summary: dict[str, Any], process: str,
    ) -> tuple[bool, str]:
        """Call Communication directly with trigger=escalation. Returns
        (fired, reason). Best-effort — failure logged but not chain-fatal."""
        incident = triage_summary.get("incident") or {}
        classification = triage_summary.get("classification") or {}
        priority = triage_summary.get("priority") or {}

        comm_payload: dict[str, Any] = {
            "incident": incident,
            "classification": classification,
            "priority": priority,
            "trigger": "escalation",
            "current_state": "triaged",
        }
        with audit_span("i2r.escalation_notify", audit_type=AuditType.PLATFORM):
            try:
                client = await self._client_for("send_status_update")
                sub_task = await client.message_send(
                    capability="send_status_update",
                    parts=[{"kind": "data", "data": comm_payload}],
                    process=process, step="i2r.escalate",
                )
            except A2AClientError as exc:
                cat_event("escalation_send_failed", error=str(exc))
                pst_event("i2r_escalation_failed", error_class=type(exc).__name__)
                raise
            pst_event("i2r_escalation_triggered",
                      priority=priority.get("priority"),
                      blast_radius=priority.get("blast_radius"))
            cat_event("escalation_sent",
                      task_id=sub_task.id,
                      downstream_state=sub_task.status.state.value)
        return True, "matched i2r_escalation_criteria"

    @staticmethod
    def _artifact_data(step_output: dict[str, Any], artifact_name: str) -> dict[str, Any] | None:
        for a in step_output.get("artifacts") or []:
            if a.get("name") == artifact_name:
                return a.get("data") or {}
        return None

    def _payload_for_step(
        self, step: ChainStep,
        initial_payload: dict[str, Any], chain_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if step.compose_inputs:
            composite: dict[str, Any] = {}
            for key, ref in step.compose_inputs.items():
                value = self._resolve_ref(ref, initial_payload, chain_outputs)
                if value is not None:
                    composite[key] = value
            return composite if composite else initial_payload
        if chain_outputs and step.forward_field is not None:
            prev = chain_outputs[-1]
            for a in prev.get("artifacts") or []:
                if a.get("name") == step.forward_field and a.get("data"):
                    return a["data"]
        return initial_payload

    @staticmethod
    def _resolve_ref(
        ref: str, initial_payload: dict[str, Any], chain_outputs: list[dict[str, Any]],
    ) -> Any:
        """Same syntax as the Closure runner — dot-walks into both nested input
        fields and prior-step artifacts."""
        parts = ref.split(".")
        if not parts:
            return None
        if parts[0] == "input":
            cur: Any = initial_payload
            for p in parts[1:]:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(p)
                if cur is None:
                    return None
            return cur
        try:
            step_idx = int(parts[0])
        except ValueError:
            return None
        if step_idx >= len(chain_outputs):
            return None
        step_out = chain_outputs[step_idx]
        if len(parts) == 1:
            return step_out
        artifact_name = parts[1]
        data: Any = None
        for a in step_out.get("artifacts") or []:
            if a.get("name") == artifact_name:
                data = a.get("data")
                break
        if data is None:
            return None
        for p in parts[2:]:
            if not isinstance(data, dict):
                return None
            data = data.get(p)
            if data is None:
                return None
        return data
