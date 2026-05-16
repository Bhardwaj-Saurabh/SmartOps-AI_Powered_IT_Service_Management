"""8-step Routing Agent workflow.

Anthropic parallelization pattern: team-directory + skill-matrix calls
run concurrently via ``asyncio.gather``. The LLM ranking step uses the
merged candidate pool.
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

from routing.agent import rank_with_gateway
from routing.config import AgentConfig
from routing.models import (
    DecisionStep,
    Routing,
    RoutingInput,
    TeamCandidate,
)
from routing.tools import SkillMatrix, TeamDirectory


class RoutingRunner:
    def __init__(
        self,
        *,
        cfg: AgentConfig,
        gateway: GatewayClient,
        semantic: SemanticClient,
        directory: TeamDirectory,
        skill_matrix: SkillMatrix,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.directory = directory
        self.skill_matrix = skill_matrix
        self._system_prompt = Path(cfg.prompts.rank_system_path).read_text()
        self._user_prompt = Path(cfg.prompts.rank_user_path).read_text()

    async def run(self, payload: RoutingInput) -> Routing:
        start = time.perf_counter()
        decisions: list[DecisionStep] = []
        decisions.append(DecisionStep(step="01_receive", detail=f"incident_id={payload.incident.incident_id}"))

        sa = payload.classification.service_area
        cat = payload.classification.category
        priority = payload.priority.priority

        # Step 2 — base candidates from routing_rules
        rules = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.routing_rules,
            process="i2r", step="triage.route",
        )
        base_candidates: list[str] = list(((rules or {}).get("by_service_area") or {}).get(sa, []))
        decisions.append(DecisionStep(step="02_candidates_from_rules", detail=f"{len(base_candidates)} candidates"))

        # Step 3 — priority overrides
        overrides_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.routing_priority_overrides,
            process="i2r", step="triage.route",
        )
        extras: list[str] = list((overrides_rule or {}).get(priority, []) or [])
        candidates = list(dict.fromkeys(base_candidates + extras))  # preserve order, dedupe
        decisions.append(DecisionStep(step="03_priority_overrides", detail=f"+{len(extras)}"))

        if not candidates:
            raise AgentError(f"No candidate teams for service_area={sa} priority={priority}", step=3)

        # Step 4 — PARALLEL: team directory + skill matrix
        async def dir_branch():
            with audit_span("route.04_directory", audit_type=AuditType.PLATFORM):
                return await self.directory.lookup(candidates)

        async def skill_branch():
            with audit_span("route.04_skills", audit_type=AuditType.PLATFORM):
                return await self.skill_matrix.score(
                    service_area=sa, category=cat, candidate_team_ids=candidates,
                )

        try:
            directory_rows, skill_resp = await asyncio.gather(dir_branch(), skill_branch())
        except SemanticPlaneError:
            raise
        except Exception as exc:
            raise AgentError(f"Parallel routing lookups failed: {exc}", step=4, cause=exc) from exc

        directory_by_id = {row["team_id"]: row for row in directory_rows}
        skill_by_id = {row["team_id"]: row for row in (skill_resp.get("team_scores") or [])}
        decisions.append(DecisionStep(step="04_parallel_lookups", detail=f"directory={len(directory_rows)} skill_scored={len(skill_by_id)}"))

        # Step 5 — load_balancing filter
        lb = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.load_balancing,
            process="i2r", step="triage.route",
        )
        max_queue = int((lb or {}).get("max_queue_depth", 99))

        pool: list[TeamCandidate] = []
        for team_id in candidates:
            d = directory_by_id.get(team_id, {})
            s = skill_by_id.get(team_id, {})
            tc = TeamCandidate(
                team_id=team_id,
                available=bool(d.get("available", False)),
                queue_depth=int(d.get("queue_depth", 99)),
                match_score=float(s.get("match_score", 0.0)),
                final_score=0.0,
                matched_skills=list(s.get("matched_skills") or []),
                missing_skills=list(s.get("missing_skills") or []),
            )
            if not tc.available:
                tc.excluded_reason = "unavailable"
            elif tc.queue_depth > max_queue:
                tc.excluded_reason = f"queue_depth>{max_queue}"
            pool.append(tc)

        eligible = [c for c in pool if c.excluded_reason is None]
        decisions.append(DecisionStep(step="05_load_balance_filter", detail=f"eligible={len(eligible)} of {len(pool)}"))
        if not eligible:
            # Phase 1: still emit a routing decision but mark assigned_team=None.
            return self._emit(payload, pool, None, decisions, started=start)

        # Step 6 — LLM ranking on the eligible pool
        candidates_for_prompt = [
            {
                "team_id": c.team_id,
                "queue_depth": c.queue_depth,
                "match_score": round(c.match_score, 3),
                "matched_skills": c.matched_skills,
            }
            for c in eligible
        ]
        user_prompt = self._user_prompt.format(
            affected_service=payload.incident.affected_service or "",
            service_area=sa, category=cat, priority=priority,
            symptoms_summary=payload.incident.symptoms_summary,
            candidates_json=json.dumps(candidates_for_prompt),
        )
        with audit_span("route.06_llm_rank", audit_type=AuditType.PLATFORM):
            cat_event("llm_prompt", prompt=user_prompt)
            try:
                ranked_raw = await rank_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._system_prompt, user_prompt=user_prompt,
                )
            except Exception as exc:
                raise AgentError(f"LLM ranking failed: {exc}", step=6, cause=exc) from exc
            cat_event("llm_response", response=json.dumps(ranked_raw))
        llm_scores: dict[str, float] = {
            str(r.get("team_id")): float(r.get("score", 0.0)) for r in (ranked_raw.get("ranked") or [])
        }
        decisions.append(DecisionStep(step="06_llm_rank", detail=f"scored={len(llm_scores)}"))

        # Step 7 — weighted final score, pick the max
        llm_weight = float(
            await self.semantic.query_rule(
                domain=self.cfg.semantic_queries.routing_llm_weight,
                process="i2r", step="triage.route",
            )
        )
        skill_weight = max(0.0, 1.0 - llm_weight)
        for c in eligible:
            c.llm_score = llm_scores.get(c.team_id, 0.0)
            c.final_score = c.llm_score * llm_weight + c.match_score * skill_weight
        eligible.sort(key=lambda c: c.final_score, reverse=True)
        winner = eligible[0].team_id
        # carry final_score back into the excluded entries (still useful for audit)
        for c in pool:
            if c.excluded_reason is not None:
                c.final_score = 0.0
        decisions.append(DecisionStep(step="07_weighted_final", detail=f"winner={winner} score={eligible[0].final_score:.3f}"))

        return self._emit(payload, pool, winner, decisions, started=start)

    def _emit(
        self,
        payload: RoutingInput,
        pool: list[TeamCandidate],
        winner: str | None,
        decisions: list[DecisionStep],
        *,
        started: float,
    ) -> Routing:
        # Step 8
        ranked = sorted(pool, key=lambda c: c.final_score, reverse=True)
        pst_event("routing_emit", chosen=winner or "none")
        decisions.append(DecisionStep(step="08_emit", detail=str(winner)))
        pst_event("routing_complete", duration_ms=(time.perf_counter() - started) * 1000.0)
        return Routing(
            incident_id=payload.incident.incident_id,
            assigned_team=winner,
            candidate_ranking=ranked,
            decision_chain=decisions,
        )
