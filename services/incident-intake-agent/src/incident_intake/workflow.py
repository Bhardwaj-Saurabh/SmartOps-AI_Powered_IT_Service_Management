"""The 12-step Incident Intake chain.

Each step is a function; the function name + docstring quote the PRD step it
implements. Steps 5 and 8 are decision gates that read from SBCA; steps 6
and 9 are short-circuit exits.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from di_framework_core import AuditType, AgentError, SemanticPlaneError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from incident_intake.agent import extract_with_gateway
from incident_intake.config import AgentConfig
from incident_intake.models import (
    Channel,
    DuplicateCheck,
    ExtractedIncident,
    Incident,
    RawInput,
)
from incident_intake.tools import (
    EmailParser,
    FormNormaliser,
    QdrantTool,
    SlackConnector,
)


# ─── prompt cache ─────────────────────────────────────────────────────
def _read_prompt(path: str) -> str:
    return Path(path).read_text()


# ─── steps 1–2: receive + detect ──────────────────────────────────────
async def step_01_receive(raw: RawInput) -> RawInput:
    """1. Receive raw input — channel-agnostic."""
    with audit_span("intake.01_receive", audit_type=AuditType.PLATFORM):
        if raw.email_raw is None and raw.slack_event is None and raw.form is None:
            raise AgentError("No payload — one of email_raw / slack_event / form is required", step=1)
        return raw


async def step_02_detect_channel(raw: RawInput) -> Channel:
    """2. Detect input channel and format."""
    with audit_span("intake.02_detect_channel", audit_type=AuditType.PLATFORM):
        if raw.channel and raw.channel != Channel.UNKNOWN:
            return raw.channel
        if raw.email_raw is not None:
            return Channel.EMAIL
        if raw.slack_event is not None:
            return Channel.SLACK
        if raw.form is not None:
            return Channel.FORM
        return Channel.UNKNOWN


async def _to_canonical_text(
    raw: RawInput,
    channel: Channel,
    email_parser: EmailParser,
    slack_connector: SlackConnector,
    form_normaliser: FormNormaliser,
) -> tuple[str, str | None]:
    """Helper: collapse the channel-specific payload into (text, received_at)."""
    if channel == Channel.EMAIL:
        parsed = await email_parser.parse(raw.email_raw or "")
        return parsed["body"], parsed.get("received_at")
    if channel == Channel.SLACK:
        parsed = await slack_connector.parse(raw.slack_event or {})
        return parsed["text"], parsed.get("received_at")
    if channel == Channel.FORM:
        form = raw.form or {}
        parsed = await form_normaliser.normalise(form.get("schema_id", ""), form.get("payload", {}))
        return parsed["body"], parsed.get("received_at")
    raise AgentError(f"Unsupported channel {channel}", step=2)


# ─── step 3: LLM extract ───────────────────────────────────────────────
async def step_03_extract(
    text: str,
    channel: Channel,
    received_at: str | None,
    *,
    cfg: AgentConfig,
    gateway: GatewayClient,
    system_prompt: str,
    user_prompt_template: str,
) -> ExtractedIncident:
    """3. LLM extracts reporter, affected service, symptoms, timestamp."""
    user_prompt = user_prompt_template.format(channel=channel, received_at=received_at, raw_text=text)
    with audit_span(
        "intake.03_extract",
        audit_type=AuditType.PLATFORM,
        attributes={"intake.channel": channel.value},
    ):
        cat_event("llm_prompt", prompt=user_prompt)
        try:
            extracted_raw = await extract_with_gateway(
                gateway=gateway,
                cfg=cfg,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            raise AgentError(f"LLM extraction failed: {exc}", step=3, cause=exc) from exc
        cat_event("llm_response", response=str(extracted_raw))
    return ExtractedIncident.model_validate(extracted_raw)


# ─── step 4: normalise (passthrough — schema already canonical) ────────
async def step_04_normalise(extracted: ExtractedIncident) -> ExtractedIncident:
    """4. Normalise extracted fields. LLM already emits the canonical schema."""
    return extracted


# ─── step 5: duplicate check ───────────────────────────────────────────
async def step_05_duplicate_check(
    extracted: ExtractedIncident,
    *,
    cfg: AgentConfig,
    gateway: GatewayClient,
    semantic: SemanticClient,
    qdrant: QdrantTool,
) -> DuplicateCheck:
    """5. Duplicate detection via embedding similarity vs SBCA-defined threshold."""
    threshold_rule = await semantic.query_rule(
        domain=cfg.semantic_queries.duplicate_threshold,
        process="i2r",
        step="triage.intake",
    )  # raises SemanticPlaneError on failure — NO FALLBACK per §5

    try:
        similarity_min = float(threshold_rule["similarity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticPlaneError(f"Malformed duplicate_threshold from SBCA: {threshold_rule}") from exc

    with audit_span(
        "intake.05_duplicate_check",
        audit_type=AuditType.PLATFORM,
        attributes={"intake.threshold": similarity_min},
    ):
        await qdrant.ensure_collection()
        embed = await gateway.embedding(model=cfg.embedding.alias, input=extracted.symptoms_summary)
        vector = embed.vectors[0] if embed.vectors else []
        if not vector:
            raise AgentError("Gateway returned empty embedding", step=5)
        nearest = await qdrant.nearest(vector=vector, limit=1)

    if not nearest:
        return DuplicateCheck(similarity=0.0, matched_incident_id=None, matched_title=None, is_duplicate=False)

    top = nearest[0]
    similarity = float(top["score"])
    payload = top.get("payload") or {}
    return DuplicateCheck(
        similarity=similarity,
        matched_incident_id=payload.get("incident_id"),
        matched_title=payload.get("title"),
        is_duplicate=similarity >= similarity_min,
    )


# ─── step 7: enrich reporter ───────────────────────────────────────────
async def step_07_enrich_reporter(
    extracted: ExtractedIncident,
    *,
    cfg: AgentConfig,
    semantic: SemanticClient,
) -> tuple[bool, str | None]:
    """7. Enrich reporter context (VIP, department).

    Phase 1: department is heuristically derived from email domain prefix.
    VIP lookup uses the SBCA ``vip_departments`` rule.
    """
    department: str | None = None
    if extracted.reporter and "@" in extracted.reporter:
        local = extracted.reporter.split("@", 1)[0]
        if "." in local:
            department = local.split(".")[-1] or None
    vips = await semantic.query_rule(
        domain=cfg.semantic_queries.vip_departments,
        process="i2r",
        step="triage.intake",
    )
    vip_set = {str(v).lower() for v in (vips or [])}
    is_vip = (department or "").lower() in vip_set
    pst_event("reporter_enriched", reporter_vip=is_vip)
    return is_vip, department


# ─── step 8: required-fields check ─────────────────────────────────────
async def step_08_validate_completeness(
    extracted: ExtractedIncident,
    *,
    cfg: AgentConfig,
    semantic: SemanticClient,
) -> list[str]:
    """8. Required fields complete? Returns the list of missing fields ([] if complete)."""
    rule = await semantic.query_rule(
        domain=cfg.semantic_queries.required_fields,
        context={"service_area": extracted.service_area},
        process="i2r",
        step="triage.intake",
    )
    must = (rule or {}).get("must", [])
    extracted_map = extracted.model_dump()
    missing = [name for name in must if not extracted_map.get(name) and name != "error_messages"]
    pst_event("completeness", missing_count=len(missing))
    return missing


# ─── step 9: clarification questions ───────────────────────────────────
async def step_09_clarification(
    missing: list[str],
    *,
    cfg: AgentConfig,
    semantic: SemanticClient,
) -> str:
    """9. Compose the clarification message for an INPUT_REQUIRED response."""
    template = await semantic.query_rule(
        domain=cfg.semantic_queries.clarification_template,
        process="i2r",
        step="triage.intake",
    )
    bulleted = "\n".join(f"- {f}" for f in missing)
    return str(template).format(missing_fields_bulleted=bulleted)


# ─── steps 10–11: assign id + emit ─────────────────────────────────────
def step_10_assign_id() -> str:
    """10. Assign incident ID."""
    return f"INC-{uuid4().hex[:10].upper()}"


def step_11_emit(
    *,
    incident_id: str,
    channel: Channel,
    extracted: ExtractedIncident,
    reporter_vip: bool,
    reporter_department: str | None,
    correlation_id: str | None,
    state: str,
    duplicate_of: str | None,
    missing_fields: list[str],
    clarification_questions: str | None,
) -> Incident:
    """11. Emit structured incident record."""
    return Incident(
        incident_id=incident_id,
        state=state,  # type: ignore[arg-type]
        channel=channel,
        reporter=extracted.reporter,
        reporter_vip=reporter_vip,
        reporter_department=reporter_department,
        affected_service=extracted.affected_service,
        service_area=extracted.service_area,
        symptoms_verbatim=extracted.symptoms_verbatim,
        symptoms_summary=extracted.symptoms_summary,
        urgency=extracted.urgency,
        reported_at=extracted.reported_at,
        correlation_id=correlation_id,
        duplicate_of=duplicate_of,
        missing_fields=missing_fields,
        clarification_questions=clarification_questions,
    )


# ─── orchestration: the full chain ─────────────────────────────────────
class IntakeRunner:
    def __init__(
        self,
        *,
        cfg: AgentConfig,
        gateway: GatewayClient,
        semantic: SemanticClient,
        email_parser: EmailParser,
        slack_connector: SlackConnector,
        form_normaliser: FormNormaliser,
        qdrant: QdrantTool,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.email_parser = email_parser
        self.slack_connector = slack_connector
        self.form_normaliser = form_normaliser
        self.qdrant = qdrant
        self._system_prompt = _read_prompt(cfg.prompts.extract_system_path)
        self._user_prompt = _read_prompt(cfg.prompts.extract_user_path)

    async def run(self, raw: RawInput, *, correlation_id: str | None) -> Incident:
        start = time.perf_counter()
        # Step 1
        raw = await step_01_receive(raw)
        # Step 2
        channel = await step_02_detect_channel(raw)
        text, received_at = await _to_canonical_text(
            raw, channel, self.email_parser, self.slack_connector, self.form_normaliser
        )
        # Step 3
        extracted = await step_03_extract(
            text, channel, received_at,
            cfg=self.cfg, gateway=self.gateway,
            system_prompt=self._system_prompt, user_prompt_template=self._user_prompt,
        )
        # Step 4
        extracted = await step_04_normalise(extracted)
        # Step 5 — decision gate
        dup = await step_05_duplicate_check(
            extracted, cfg=self.cfg, gateway=self.gateway,
            semantic=self.semantic, qdrant=self.qdrant,
        )
        if dup.is_duplicate:
            # Step 6 — short-circuit
            incident_id = step_10_assign_id()
            cat_event("duplicate_detected", matched=dup.matched_incident_id, similarity=dup.similarity)
            pst_event("emit_state", state="duplicate")
            return step_11_emit(
                incident_id=incident_id, channel=channel, extracted=extracted,
                reporter_vip=False, reporter_department=None,
                correlation_id=correlation_id, state="duplicate",
                duplicate_of=dup.matched_incident_id, missing_fields=[],
                clarification_questions=None,
            )
        # Step 7
        is_vip, department = await step_07_enrich_reporter(extracted, cfg=self.cfg, semantic=self.semantic)
        # Step 8 — decision gate
        missing = await step_08_validate_completeness(extracted, cfg=self.cfg, semantic=self.semantic)
        if missing:
            # Step 9 — short-circuit (INPUT_REQUIRED handled by caller)
            clarification = await step_09_clarification(missing, cfg=self.cfg, semantic=self.semantic)
            incident_id = step_10_assign_id()
            pst_event("emit_state", state="needs_clarification", missing=len(missing))
            return step_11_emit(
                incident_id=incident_id, channel=channel, extracted=extracted,
                reporter_vip=is_vip, reporter_department=department,
                correlation_id=correlation_id, state="needs_clarification",
                duplicate_of=None, missing_fields=missing,
                clarification_questions=clarification,
            )
        # Step 10 + 11 — happy path
        incident_id = step_10_assign_id()
        pst_event("emit_state", state="new")
        cat_event("decision_chain", duplicate=False, completeness=True, vip=is_vip)
        incident = step_11_emit(
            incident_id=incident_id, channel=channel, extracted=extracted,
            reporter_vip=is_vip, reporter_department=department,
            correlation_id=correlation_id, state="new",
            duplicate_of=None, missing_fields=[],
            clarification_questions=None,
        )
        # Step 12 — final audit. Step-level CAT/PST events fired throughout.
        pst_event("intake_complete", duration_ms=(time.perf_counter() - start) * 1000.0)
        return incident
