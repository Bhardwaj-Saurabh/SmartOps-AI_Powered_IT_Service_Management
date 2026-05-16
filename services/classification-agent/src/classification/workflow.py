"""8-step classification workflow — Anthropic parallelization pattern.

Steps 2 and 3 run concurrently via ``asyncio.gather``. The rest is sequential
merge + validation + override + return.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from classification.agent import classify_with_gateway
from classification.config import AgentConfig
from classification.models import (
    Classification,
    DecisionStep,
    HistoryEvidence,
    IncidentInput,
    LabelCandidate,
)
from classification.tools import HistoricalPatternMatcher, TaxonomyLookup


def _read_prompt(path: str) -> str:
    return Path(path).read_text()


def _detect_override(text: str, overrides_rule: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first matching override entry, or None."""
    haystack = text.lower()
    for entry in overrides_rule.get("by_keyword", []) or []:
        for kw in entry.get("keywords", []) or []:
            if kw.lower() in haystack:
                return entry
    return None


def _majority_label(matches: list[dict[str, Any]]) -> tuple[str, str, float] | None:
    """Return (service_area, category, confidence) of the modal label across
    history matches, weighted by similarity. None if no usable matches."""
    if not matches:
        return None
    bucket: Counter[tuple[str, str]] = Counter()
    weight: dict[tuple[str, str], float] = {}
    for m in matches:
        sa = m.get("service_area")
        cat = m.get("category")
        if not sa or not cat:
            continue
        key = (sa, cat)
        bucket[key] += 1
        weight[key] = weight.get(key, 0.0) + float(m.get("similarity", 0.0))
    if not bucket:
        return None
    winner, _ = bucket.most_common(1)[0]
    avg_sim = weight[winner] / bucket[winner]
    return winner[0], winner[1], min(1.0, avg_sim)


class ClassificationRunner:
    def __init__(
        self,
        *,
        cfg: AgentConfig,
        gateway: GatewayClient,
        semantic: SemanticClient,
        taxonomy: TaxonomyLookup,
        history: HistoricalPatternMatcher,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.taxonomy = taxonomy
        self.history = history
        self._system_prompt = _read_prompt(cfg.prompts.classify_system_path)
        self._user_prompt = _read_prompt(cfg.prompts.classify_user_path)

    async def run(self, incident: IncidentInput) -> Classification:
        start = time.perf_counter()
        decision: list[DecisionStep] = []

        # ─── step 1: receive (validation of input shape is implicit by pydantic) ──
        decision.append(DecisionStep(step="01_receive", detail=f"incident_id={incident.incident_id}"))

        # ─── steps 2 + 3: PARALLEL — LLM classify + history match ───────────
        taxonomy = await self.taxonomy.full_taxonomy()
        service_areas = sorted((taxonomy.get("service_areas") or {}).keys())
        allowed = {area: (taxonomy["service_areas"][area].get("categories") or []) for area in service_areas}
        user_prompt = self._user_prompt.format(
            service_areas=", ".join(service_areas),
            allowed_categories_json=json.dumps(allowed),
            affected_service=incident.affected_service or "",
            symptoms_summary=incident.symptoms_summary,
            symptoms_verbatim=incident.symptoms_verbatim,
        )

        async def llm_branch() -> dict[str, Any]:
            with audit_span("classify.02_llm", audit_type=AuditType.PLATFORM):
                cat_event("llm_prompt", prompt=user_prompt)
                raw = await classify_with_gateway(
                    gateway=self.gateway, cfg=self.cfg,
                    system_prompt=self._system_prompt, user_prompt=user_prompt,
                )
                cat_event("llm_response", response=json.dumps(raw))
                return raw

        async def history_branch() -> list[dict[str, Any]]:
            with audit_span("classify.03_history", audit_type=AuditType.PLATFORM):
                embed = await self.gateway.embedding(model=self.cfg.embedding.alias, input=incident.symptoms_summary)
                vec = embed.vectors[0] if embed.vectors else []
                if not vec:
                    raise AgentError("Empty embedding from gateway", step=3)
                matches = await self.history.match(vector=vec, limit=5)
                cat_event("history_matches", matches=json.dumps(matches))
                return matches

        try:
            llm_raw, hist_matches = await asyncio.gather(llm_branch(), history_branch())
        except SemanticPlaneError:
            raise
        except Exception as exc:
            raise AgentError(f"Parallel classify failed: {exc}", step=2, cause=exc) from exc

        llm_candidate = LabelCandidate(
            service_area=llm_raw.get("service_area", ""),
            category=llm_raw.get("category", ""),
            confidence=float(llm_raw.get("confidence", 0.0)),
            source="llm",
        )
        history_evidence = [
            HistoryEvidence(
                incident_id=m.get("incident_id"),
                similarity=float(m.get("similarity", 0.0)),
                service_area=m.get("service_area"),
                category=m.get("category"),
            )
            for m in hist_matches
        ]
        decision.append(DecisionStep(
            step="02_llm",
            detail=f"{llm_candidate.service_area}/{llm_candidate.category}@{llm_candidate.confidence:.2f}",
        ))
        decision.append(DecisionStep(step="03_history", detail=f"matches={len(history_evidence)}"))

        # ─── step 4: weighted merge ─────────────────────────────────────────
        conf_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.classification_confidence,
            process="i2r", step="triage.classify",
        )
        try:
            llm_weight = float(conf_rule["llm_weight"])
            hist_weight = float(conf_rule["history_weight"])
            min_confidence = float(conf_rule["minimum"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticPlaneError(f"Malformed classification_confidence: {conf_rule}") from exc

        history_label = _majority_label(hist_matches)
        if history_label and (history_label[0], history_label[1]) == (llm_candidate.service_area, llm_candidate.category):
            merged_conf = llm_candidate.confidence * llm_weight + history_label[2] * hist_weight
            chosen = (llm_candidate.service_area, llm_candidate.category, min(1.0, merged_conf), "llm+history_agree")
        elif llm_candidate.confidence >= min_confidence:
            chosen = (llm_candidate.service_area, llm_candidate.category, llm_candidate.confidence, "llm_high_confidence")
        elif history_label:
            chosen = (history_label[0], history_label[1], history_label[2] * hist_weight, "history_fallback")
        else:
            chosen = (llm_candidate.service_area, llm_candidate.category, llm_candidate.confidence, "llm_only_low_conf")
        decision.append(DecisionStep(step="04_merge", detail=f"strategy={chosen[3]}"))

        # ─── step 5: taxonomy validate ───────────────────────────────────────
        validation = await self.taxonomy.validate(service_area=chosen[0], category=chosen[1])
        if not (validation["service_area_valid"] and validation["category_valid"]):
            raise AgentError(
                f"Chosen label {chosen[0]}/{chosen[1]} not in taxonomy v{validation['taxonomy_version']}",
                step=5,
            )
        decision.append(DecisionStep(step="05_taxonomy_valid", detail=validation["taxonomy_version"]))

        # ─── step 6: SBCA classification_overrides ──────────────────────────
        overrides_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.classification_overrides,
            process="i2r", step="triage.classify",
        )
        override = _detect_override(f"{incident.symptoms_verbatim} {incident.symptoms_summary}", overrides_rule)
        override_reason: str | None = None
        if override is not None:
            override_reason = override.get("reason") or "override_applied"
            forced = (override["service_area"], override["category"], 1.0, override_reason)
            # Re-validate the forced label is in taxonomy too.
            forced_val = await self.taxonomy.validate(service_area=forced[0], category=forced[1])
            if not (forced_val["service_area_valid"] and forced_val["category_valid"]):
                raise AgentError(
                    f"Override label {forced[0]}/{forced[1]} not in taxonomy — refresh rules",
                    step=6,
                )
            chosen = forced
            decision.append(DecisionStep(step="06_override_applied", detail=override_reason))
        else:
            decision.append(DecisionStep(step="06_override", detail="none"))

        # ─── step 7: taxonomy-version hard-gate ─────────────────────────────
        version_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.classification_taxonomy_version,
            process="i2r", step="triage.classify",
        )
        expected = str(version_rule.get("expected", ""))
        if expected and expected != validation["taxonomy_version"]:
            raise AgentError(
                f"Taxonomy version drift: SBCA expects {expected}, sidecar serves {validation['taxonomy_version']}",
                step=7,
            )
        decision.append(DecisionStep(step="07_version_check", detail="ok"))

        # ─── step 8: emit ───────────────────────────────────────────────────
        pst_event("classification_emit", strategy=chosen[3], confidence=chosen[2])
        if chosen[2] < min_confidence and override_reason is None:
            pst_event("low_confidence_classification", confidence=chosen[2])
        decision.append(DecisionStep(step="08_emit", detail=f"{chosen[0]}/{chosen[1]}@{chosen[2]:.2f}"))
        pst_event("classify_complete", duration_ms=(time.perf_counter() - start) * 1000.0)

        return Classification(
            incident_id=incident.incident_id,
            service_area=chosen[0],
            category=chosen[1],
            confidence=chosen[2],
            override_reason=override_reason,
            llm_candidate=llm_candidate,
            history_candidates=history_evidence,
            taxonomy_version=validation["taxonomy_version"],
            decision_chain=decision,
        )
