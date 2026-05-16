"""Verification workflow — Anthropic parallelization pattern."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from verification.agent import evaluate_with_gateway
from verification.config import AgentConfig
from verification.models import (
    ComparisonEvidence,
    HealthEvidence,
    SyntheticEvidence,
    VerificationInput,
    VerificationResult,
)
from verification.tools import (
    ComparisonTool,
    HealthCheckRunner,
    MetricsQuery,
    SyntheticMonitor,
)


_DEFAULT_SCENARIOS_BY_CATEGORY: dict[str, list[str]] = {
    "vpn": ["vpn-handshake", "rekey-stability"],
    "okta-sso": ["okta-saml-roundtrip"],
    "salesforce": ["salesforce-sso-roundtrip"],
    "printer": ["print-roundtrip"],
}


class VerificationRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        health: HealthCheckRunner, synthetic: SyntheticMonitor,
        comparison: ComparisonTool, metrics: MetricsQuery,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.health = health
        self.synthetic = synthetic
        self.comparison = comparison
        self.metrics = metrics
        self._eval_system = Path(cfg.prompts.evaluate_system_path).read_text()
        self._eval_user = Path(cfg.prompts.evaluate_user_path).read_text()

    async def run(self, payload: VerificationInput) -> VerificationResult:
        started = time.perf_counter()
        affected = payload.incident.affected_service or "unknown"
        priority = (payload.priority.priority if payload.priority else "P3")

        thresholds_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.verification_thresholds,
            process="i2r", step="resolution.verify",
        )
        confidence_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.verification_confidence,
            process="i2r", step="resolution.verify",
        )
        soak_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.verification_soak_period,
            process="i2r", step="resolution.verify",
        )
        try:
            improvement_required = dict((thresholds_rule or {}).get("improvement_required") or {})
            min_to_emit_verified = float((confidence_rule or {}).get("min_to_emit_verified", 0.7))
            soak_minutes = int(((soak_rule or {}).get("by_priority") or {}).get(priority, 0))
        except (TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed verification rules: {exc}") from exc

        scenarios = payload.scenario_ids or _DEFAULT_SCENARIOS_BY_CATEGORY.get(
            payload.classification.category, []
        )

        # ─── Parallel evidence collection ───────────────────────────────────
        async def health_branch():
            with audit_span("verify.health", audit_type=AuditType.PLATFORM):
                return await self.health.run(service=affected, after_fix=True)

        async def synthetic_branch():
            with audit_span("verify.synthetic", audit_type=AuditType.PLATFORM):
                if not scenarios:
                    return {"results": [], "overall_passed": True}
                return await self.synthetic.replay(scenario_ids=scenarios, after_fix=True)

        async def metrics_branch():
            with audit_span("verify.metrics", audit_type=AuditType.PLATFORM):
                return await self.metrics.query(service=affected)

        try:
            health_resp, synthetic_resp, metrics_resp = await asyncio.gather(
                health_branch(), synthetic_branch(), metrics_branch(),
            )
        except SemanticPlaneError:
            raise
        except Exception as exc:
            raise AgentError(f"Parallel verification evidence collection failed: {exc}", cause=exc) from exc

        # ─── Comparison (uses pre = baseline, post = at_incident — these are
        # already the "pre-fix" + "actual" snapshots in our synthetic data) ─
        with audit_span("verify.compare", audit_type=AuditType.PLATFORM):
            cmp_resp = await self.comparison.compare(
                pre=dict(metrics_resp.get("baseline") or {}),
                post=dict(metrics_resp.get("at_incident") or {}),
                improvement_required_pct=improvement_required,
            )

        health_ev = HealthEvidence(
            overall_passed=bool(health_resp.get("overall_passed", False)),
            probes=list(health_resp.get("probes") or []),
        )
        syn_ev = SyntheticEvidence(
            overall_passed=bool(synthetic_resp.get("overall_passed", False)),
            results=list(synthetic_resp.get("results") or []),
        )
        cmp_ev = ComparisonEvidence(
            overall_improved=bool(cmp_resp.get("overall_improved", False)),
            improved_count=int(cmp_resp.get("improved_count", 0)),
            regressed_count=int(cmp_resp.get("regressed_count", 0)),
            metrics=list(cmp_resp.get("metrics") or []),
        )

        # ─── LLM evaluator ──────────────────────────────────────────────────
        evidence_user = self._eval_user.format(
            affected_service=affected,
            symptoms_summary=payload.incident.symptoms_summary,
            runbook_id=payload.fix_result.selected_runbook_id or "",
            fix_summary=payload.fix_result.what_changed or "",
            health_checks_json=json.dumps(health_ev.model_dump()),
            synthetic_results_json=json.dumps(syn_ev.model_dump()),
            comparison_json=json.dumps(cmp_ev.model_dump()),
        )
        with audit_span("verify.evaluate_llm", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=evidence_user)
            try:
                verdict = await evaluate_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._eval_system, user_prompt=evidence_user,
                )
            except Exception as exc:
                raise AgentError(f"Verification evaluator LLM failed: {exc}", cause=exc) from exc
            cat_event("llm_response", response=json.dumps(verdict))

        llm_verified = bool(verdict.get("fix_verified", False))
        llm_confidence = float(verdict.get("confidence", 0.0))

        # Deterministic floor: if the comparison-tool says nothing measurably
        # improved AND health/synthetic also failed, we override the LLM to
        # NOT verified. We never override the other direction (the LLM saying
        # "not verified" is always respected).
        det_supports = health_ev.overall_passed and syn_ev.overall_passed and cmp_ev.overall_improved
        if not det_supports and llm_verified:
            llm_verified = False
            llm_confidence = max(llm_confidence, 0.7)
            verdict["reasoning"] = f"Overridden by deterministic floor: {verdict.get('reasoning', '')}"

        # Final verdict: requires both LLM verified=true AND llm_confidence >= threshold
        final_verified = llm_verified and llm_confidence >= min_to_emit_verified

        pst_event("verification_complete",
                  duration_ms=(time.perf_counter() - started) * 1000.0,
                  fix_verified=final_verified,
                  health_passed=health_ev.overall_passed,
                  synthetic_passed=syn_ev.overall_passed,
                  comparison_improved=cmp_ev.overall_improved)

        return VerificationResult(
            incident_id=payload.incident.incident_id,
            fix_verified=final_verified,
            confidence=llm_confidence,
            reasoning=str(verdict.get("reasoning", "")),
            residual_concerns=list(verdict.get("residual_concerns") or []),
            soak_period_minutes=soak_minutes,
            health=health_ev,
            synthetic=syn_ev,
            comparison=cmp_ev,
        )
