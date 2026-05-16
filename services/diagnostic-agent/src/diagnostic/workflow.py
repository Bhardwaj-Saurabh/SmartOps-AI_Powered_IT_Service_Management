"""Diagnostic workflow — Anthropic evaluator-optimizer pattern.

The loop:
  generate ──▶ collect validation evidence ──▶ evaluate
     ▲                                              │
     └────── refine (next iteration) ◀──────────────┘
              (only if score < min_to_emit AND iterations remaining)

The number of iterations is decided at run time by the evaluator's score
crossing the SBCA-supplied threshold. That LLM-driven control flow is why
this is a true "agent" by Anthropic's strict definition.
"""
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

from diagnostic.agent import evaluate_with_gateway, generate_with_gateway
from diagnostic.config import AgentConfig
from diagnostic.models import (
    CandidateCause,
    Diagnosis,
    DiagnosticInput,
    HypothesisHistoryEntry,
)
from diagnostic.tools import LogAggregator, MetricsQuery, TopologyWalker


def _matches_known_issue(rules: list[dict[str, Any]], service: str, symptoms: str) -> dict[str, Any] | None:
    haystack = (symptoms or "").lower()
    for rule in rules or []:
        if str(rule.get("service", "")).lower() != service.lower():
            continue
        pat = str(rule.get("pattern", "")).lower()
        if pat and pat in haystack:
            return rule
    return None


class DiagnosticRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        log_aggregator: LogAggregator, metrics_query: MetricsQuery, topology_walker: TopologyWalker,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.log_aggregator = log_aggregator
        self.metrics_query = metrics_query
        self.topology_walker = topology_walker
        self._gen_system = Path(cfg.prompts.hypothesise_system_path).read_text()
        self._gen_user = Path(cfg.prompts.hypothesise_user_path).read_text()
        self._eval_system = Path(cfg.prompts.evaluate_system_path).read_text()
        self._eval_user = Path(cfg.prompts.evaluate_user_path).read_text()

    async def _collect_evidence(self, service: str, *, minutes_before: int, minutes_after: int) -> tuple[dict, dict, dict]:
        """Logs + metrics + topology, in parallel."""
        async def logs():
            return await self.log_aggregator.search(
                service=service, minutes_before=minutes_before, minutes_after=minutes_after,
            )
        async def metrics():
            return await self.metrics_query.query(service=service)
        async def topology():
            return await self.topology_walker.walk(service=service)

        return await asyncio.gather(logs(), metrics(), topology())  # type: ignore[return-value]

    async def run(self, payload: DiagnosticInput) -> Diagnosis:
        started = time.perf_counter()
        history: list[HypothesisHistoryEntry] = []
        affected = payload.incident.affected_service or "unknown"
        symptoms = f"{payload.incident.symptoms_summary} {payload.incident.symptoms_verbatim}"

        # ─── known-issue short-circuit ──────────────────────────────────────
        known_rules = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.known_issues,
            process="i2r", step="resolution.diagnose",
        )
        matched = _matches_known_issue(known_rules or [], affected, symptoms)
        if matched is not None:
            pst_event("known_issue_short_circuit", service=affected, pattern=str(matched.get("pattern")))
            return Diagnosis(
                incident_id=payload.incident.incident_id,
                state="completed",
                root_cause=str(matched.get("canonical_cause", "Unknown")),
                cause_type="configuration",
                confidence=float(matched.get("confidence", 0.9)),
                iterations=0,
                supporting_evidence=[f"Known-issue match: {matched.get('pattern')}"],
                workaround=matched.get("workaround"),
                matched_known_issue=True,
                decision_chain=[],
            )

        # ─── load loop thresholds ───────────────────────────────────────────
        conf_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.diagnostic_confidence,
            process="i2r", step="resolution.diagnose",
        )
        win_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.diagnostic_window,
            process="i2r", step="resolution.diagnose",
        )
        try:
            min_to_emit = float(conf_rule["min_to_emit"])
            min_to_accept = float(conf_rule["min_to_accept"])
            max_iterations = int(conf_rule["max_iterations"])
            mins_before = int(win_rule["minutes_before"])
            mins_after = int(win_rule["minutes_after"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed diagnostic thresholds: {exc}") from exc

        # ─── evaluator-optimizer loop ───────────────────────────────────────
        best_cause: CandidateCause | None = None
        best_confidence: float = 0.0
        best_evidence: list[str] = []
        iterations_used = 0
        prior_validation_summary: list[str] = []

        for iteration in range(1, max_iterations + 1):
            iterations_used = iteration
            with audit_span(
                f"diagnose.iter{iteration}.collect",
                audit_type=AuditType.PLATFORM,
                attributes={"diagnose.iteration": iteration},
            ):
                logs, metrics, topology = await self._collect_evidence(
                    affected, minutes_before=mins_before, minutes_after=mins_after,
                )

            prior_block = ""
            if prior_validation_summary:
                prior_block = "Validation evidence from earlier iterations:\n" + "\n".join(
                    f"- {item}" for item in prior_validation_summary
                )

            user_prompt = self._gen_user.format(
                affected_service=affected,
                window_minutes_before=mins_before,
                window_minutes_after=mins_after,
                symptoms_summary=payload.incident.symptoms_summary,
                logs_jsonl=json.dumps(logs.get("entries") or []),
                metrics_json=json.dumps({"baseline": metrics.get("baseline"), "at_incident": metrics.get("at_incident"), "deltas": metrics.get("deltas")}),
                topology_json=json.dumps(topology.get("upstream_layers") or []),
                iteration=iteration,
                max_iterations=max_iterations,
                prior_evidence_block=prior_block,
            )

            with audit_span(f"diagnose.iter{iteration}.generate", audit_type=AuditType.PLATFORM):
                cat_event("llm_prompt", role="generator", iteration=iteration, prompt=user_prompt)
                try:
                    hyp = await generate_with_gateway(
                        gateway=self.gateway, cfg=self.cfg,
                        system_prompt=self._gen_system, user_prompt=user_prompt,
                    )
                except Exception as exc:
                    raise AgentError(f"Generator LLM failed (iteration {iteration}): {exc}", step=iteration, cause=exc) from exc
                cat_event("llm_response", role="generator", iteration=iteration, response=json.dumps(hyp))

            candidates = hyp.get("candidate_causes") or []
            best_index = int(hyp.get("best_index", -1))
            if not candidates or best_index < 0 or best_index >= len(candidates):
                history.append(HypothesisHistoryEntry(
                    iteration=iteration, candidate_count=len(candidates),
                    best=None, evaluator_score=None,
                    evaluator_reasoning="generator returned no usable candidate",
                ))
                continue

            best_raw = candidates[best_index]
            current = CandidateCause(
                cause=best_raw.get("cause", ""),
                cause_type=best_raw.get("cause_type"),
                evidence=list(best_raw.get("evidence") or []),
                validation_idea=best_raw.get("validation_idea"),
            )

            # ─── validation queries scoped to the hypothesis ─────────────
            validation_evidence: list[str] = []
            with audit_span(f"diagnose.iter{iteration}.validate", audit_type=AuditType.PLATFORM):
                # Cheap, deterministic validation: re-query metrics, fetch ERROR-level logs only
                try:
                    errs = await self.log_aggregator.search(
                        service=affected, minutes_before=mins_before, minutes_after=mins_after,
                        levels=["ERROR"],
                    )
                    validation_evidence.append(f"error_log_count={len(errs.get('entries') or [])}")
                    deltas = await self.metrics_query.query(service=affected)
                    big_deltas = {k: v for k, v in (deltas.get("deltas") or {}).items() if abs(v) >= 50.0}
                    if big_deltas:
                        validation_evidence.append(f"metric_deltas≥50%={json.dumps(big_deltas)}")
                except Exception as exc:
                    # Validation failure is non-fatal; the evaluator will just see no extra evidence.
                    validation_evidence.append(f"validation_error={type(exc).__name__}")

            prior_validation_summary.extend(validation_evidence)
            cat_event("validation_evidence", iteration=iteration, evidence=json.dumps(validation_evidence))

            # ─── evaluator ───────────────────────────────────────────────
            eval_user = self._eval_user.format(
                hypothesis_json=json.dumps(current.model_dump()),
                validation_evidence_json=json.dumps(validation_evidence),
            )
            with audit_span(f"diagnose.iter{iteration}.evaluate", audit_type=AuditType.PLATFORM):
                cat_event("llm_prompt", role="evaluator", iteration=iteration, prompt=eval_user)
                try:
                    evald = await evaluate_with_gateway(
                        gateway=self.gateway, cfg=self.cfg,
                        system_prompt=self._eval_system, user_prompt=eval_user,
                    )
                except Exception as exc:
                    raise AgentError(f"Evaluator LLM failed (iteration {iteration}): {exc}", step=iteration, cause=exc) from exc
                cat_event("llm_response", role="evaluator", iteration=iteration, response=json.dumps(evald))

            score = float(evald.get("confidence", 0.0))
            reasoning = str(evald.get("reasoning", ""))
            history.append(HypothesisHistoryEntry(
                iteration=iteration,
                candidate_count=len(candidates),
                best=current,
                evaluator_score=score,
                evaluator_reasoning=reasoning,
            ))

            if score > best_confidence:
                best_confidence = score
                best_cause = current
                best_evidence = list(current.evidence) + validation_evidence

            if score >= min_to_emit:
                pst_event("diagnose_converged", iteration=iteration, confidence=score)
                break

        # ─── emit ───────────────────────────────────────────────────────────
        if best_cause is None:
            pst_event("diagnose_failed", iterations=iterations_used)
            return Diagnosis(
                incident_id=payload.incident.incident_id, state="failed",
                root_cause=None, cause_type=None, confidence=0.0,
                iterations=iterations_used, supporting_evidence=[],
                decision_chain=history,
            )

        state: str
        if best_confidence < min_to_accept:
            state = "failed"
            pst_event("diagnose_low_confidence_below_accept", confidence=best_confidence)
        elif best_confidence < min_to_emit:
            state = "low_confidence"
            pst_event("diagnose_low_confidence_emit", confidence=best_confidence)
        else:
            state = "completed"

        pst_event("diagnose_complete", duration_ms=(time.perf_counter() - started) * 1000.0, iterations=iterations_used)
        return Diagnosis(
            incident_id=payload.incident.incident_id, state=state,
            root_cause=best_cause.cause, cause_type=best_cause.cause_type,
            confidence=best_confidence, iterations=iterations_used,
            supporting_evidence=best_evidence, matched_known_issue=False,
            decision_chain=history,
        )
