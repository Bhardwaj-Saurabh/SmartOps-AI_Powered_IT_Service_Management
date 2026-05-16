"""SLA Monitor workflow — deterministic math + optional one-line LLM narrative."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from sla_monitor.agent import narrate_with_gateway
from sla_monitor.config import AgentConfig
from sla_monitor.models import (
    SLAInput,
    SLAResult,
    SLATargets,
)
from sla_monitor.tools import ClockTimer, SLARulesEngine


class SLARunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        clock: ClockTimer, rules: SLARulesEngine,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.clock = clock
        self.rules = rules
        self._narr_system = Path(cfg.prompts.narrative_system_path).read_text()
        self._narr_user = Path(cfg.prompts.narrative_user_path).read_text()

    async def run(self, payload: SLAInput) -> SLAResult:
        started = time.perf_counter()

        # ─── SBCA rules ─────────────────────────────────────────────────────
        targets_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.sla_targets,
            process="i2r", step="closure.sla",
        )
        bh_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.business_hours,
            process="i2r", step="closure.sla",
        )
        bh_only_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.sla_business_hours_only,
            process="i2r", step="closure.sla",
        )
        pause_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.sla_pause_conditions,
            process="i2r", step="closure.sla",
        )
        warning_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.sla_breach_warning_pct,
            process="i2r", step="closure.sla",
        )

        try:
            tier_targets = (((targets_rule or {}).get("by_priority") or {}).get(payload.priority) or {})
            entry = tier_targets.get(payload.customer_tier)
            if entry is None:
                raise KeyError(payload.customer_tier)
            targets = SLATargets(response=int(entry["response"]), resolve=int(entry["resolve"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticPlaneError(
                f"No SLA target for priority={payload.priority} tier={payload.customer_tier}: {exc}"
            ) from exc

        bh_only = bool((bh_only_rule or {}).get(payload.priority, False))

        # ─── Clock: raw elapsed ─────────────────────────────────────────────
        with audit_span("sla.elapsed", audit_type=AuditType.PLATFORM):
            if bh_only:
                bh = (bh_rule or {}).get(payload.region) or {}
                elapsed_resp = await self.clock.elapsed_business(
                    started_at_epoch=payload.started_at_epoch,
                    now_at_epoch=payload.now_at_epoch,
                    timezone=str(bh.get("timezone", "UTC")),
                    weekdays=list(bh.get("weekdays") or [1, 2, 3, 4, 5]),
                    start=str(bh.get("start", "00:00")),
                    end=str(bh.get("end", "23:59")),
                )
            else:
                elapsed_resp = await self.clock.elapsed_24x7(
                    started_at_epoch=payload.started_at_epoch,
                    now_at_epoch=payload.now_at_epoch,
                )
        elapsed_raw = float(elapsed_resp.get("elapsed_minutes", 0.0))
        end_epoch = int(elapsed_resp.get("end_epoch", payload.started_at_epoch))

        # ─── Rules engine: paused-minutes ───────────────────────────────────
        pause_states = list((pause_rule or {}).get("states") or [])
        with audit_span("sla.pauses", audit_type=AuditType.PLATFORM):
            paused_resp = await self.rules.pauses(
                transitions=[t.model_dump() for t in payload.state_transitions],
                pause_states=pause_states,
                end_epoch=end_epoch,
            )
        paused = float(paused_resp.get("paused_minutes", 0.0))
        currently_paused = bool(paused_resp.get("currently_paused", False))

        adjusted = max(0.0, elapsed_raw - paused)
        response_pct = (adjusted / max(1, targets.response)) * 100.0
        resolve_pct = (adjusted / max(1, targets.resolve)) * 100.0

        # ─── Warning + breach flags ─────────────────────────────────────────
        warning_pct = float(
            ((warning_rule or {}).get("by_priority") or {}).get(payload.priority,
            (warning_rule or {}).get("default", 80))
        )
        response_breached = response_pct >= 100.0
        resolve_breached = resolve_pct >= 100.0
        response_warning = (not response_breached) and response_pct >= warning_pct
        resolve_warning = (not resolve_breached) and resolve_pct >= warning_pct

        # ─── Optional narrative ─────────────────────────────────────────────
        narrative = ""
        recommended_action = ""
        if self.cfg.narrative.enabled:
            user_prompt = self._narr_user.format(
                priority=payload.priority, customer_tier=payload.customer_tier,
                response_target=targets.response, resolve_target=targets.resolve,
                response_consumed=round(min(adjusted, targets.response), 2),
                resolve_consumed=round(min(adjusted, targets.resolve), 2),
                response_pct=round(response_pct, 1), resolve_pct=round(resolve_pct, 1),
                response_breached=str(response_breached),
                resolve_breached=str(resolve_breached),
                response_warning=str(response_warning),
                resolve_warning=str(resolve_warning),
                paused_state=str(currently_paused),
            )
            with audit_span("sla.narrative", audit_type=AuditType.PLATFORM):
                cat_event("llm_prompt", prompt=user_prompt)
                try:
                    body = await narrate_with_gateway(
                        gateway=self.gateway, cfg=self.cfg,
                        system_prompt=self._narr_system, user_prompt=user_prompt,
                    )
                    narrative = str(body.get("narrative", ""))
                    recommended_action = str(body.get("recommended_action", ""))
                    cat_event("llm_response", response=json.dumps(body))
                except Exception as exc:
                    # Narrative is non-critical; keep the deterministic answer.
                    cat_event("narrative_llm_failed", error=str(exc))

        pst_event("sla_complete",
                  duration_ms=(time.perf_counter() - started) * 1000.0,
                  response_pct=round(response_pct, 1), resolve_pct=round(resolve_pct, 1),
                  breached=(response_breached or resolve_breached),
                  warning=(response_warning or resolve_warning))

        return SLAResult(
            incident_id=payload.incident_id,
            priority=payload.priority, customer_tier=payload.customer_tier, region=payload.region,
            business_hours_only=bh_only, targets=targets,
            elapsed_raw_minutes=round(elapsed_raw, 2),
            paused_minutes=round(paused, 2),
            elapsed_adjusted_minutes=round(adjusted, 2),
            response_consumed_pct=round(response_pct, 2),
            resolve_consumed_pct=round(resolve_pct, 2),
            response_breached=response_breached, resolve_breached=resolve_breached,
            response_warning=response_warning, resolve_warning=resolve_warning,
            currently_paused=currently_paused,
            narrative=narrative, recommended_action=recommended_action,
        )
