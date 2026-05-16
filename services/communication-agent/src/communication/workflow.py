"""Communication workflow — Anthropic prompt chaining.

For each (audience, channel) cell in the SBCA-supplied template, the LLM
composes a tailored message and the agent dispatches it via the matching
sidecar. Cells run serially in Phase 1 (the parallel speedup belongs in
Phase 2 once we wire connection pooling and rate-limit budgets).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from di_framework_core import AgentError, AuditType, SemanticPlaneError, ToolError
from gateway_client import GatewayClient
from observability import audit_span, cat_event, pst_event
from semantic_client import SemanticClient

from communication.agent import compose_with_gateway
from communication.config import AgentConfig
from communication.models import (
    CommunicationInput,
    CommunicationResult,
    DispatchAttempt,
)
from communication.tools import EmailSender, SlackPoster, SmsGateway


# Synthetic recipient resolution. In production this would query an HR/IAM
# directory; Phase 1 keeps it config-free and predictable.
_EXEC_RECIPIENTS_EMAIL = ["executive@example.com"]
_EXEC_RECIPIENTS_SMS = ["+1-555-0100"]
_DEFAULT_STAKEHOLDER_EMAIL = ["incident-stakeholders@example.com"]
_DEFAULT_STAKEHOLDER_SLACK_CHANNEL = "C04INCIDENT-WATCH"
_DEFAULT_RESOLVER_SLACK = "C04RESOLVERS"


def _recipients_for(audience: str, channel: str, incident_reporter: str | None, resolver_team_id: str | None) -> list[str]:
    """Synthetic recipient resolution."""
    if audience == "end_user":
        if channel == "email":
            return [incident_reporter] if incident_reporter else []
        if channel == "slack":
            return ["@" + incident_reporter.split("@")[0]] if incident_reporter else []
        if channel == "sms":
            return []   # we don't have the reporter's phone — Phase 2
    if audience == "executive":
        return _EXEC_RECIPIENTS_EMAIL if channel == "email" else _EXEC_RECIPIENTS_SMS
    if audience == "affected_stakeholders":
        if channel == "email":
            return _DEFAULT_STAKEHOLDER_EMAIL
        if channel == "slack":
            return [_DEFAULT_STAKEHOLDER_SLACK_CHANNEL]
        if channel == "sms":
            return []   # only for execs by policy
    if audience == "resolver_team":
        if channel == "slack":
            return [resolver_team_id or _DEFAULT_RESOLVER_SLACK]
        if channel == "email":
            return [f"{resolver_team_id or 'resolvers'}@example.com"]
        if channel == "sms":
            return []
    return []


class CommunicationRunner:
    def __init__(
        self, *, cfg: AgentConfig, gateway: GatewayClient, semantic: SemanticClient,
        email: EmailSender, slack: SlackPoster, sms: SmsGateway,
    ) -> None:
        self.cfg = cfg
        self.gateway = gateway
        self.semantic = semantic
        self.email = email
        self.slack = slack
        self.sms = sms
        self._system = Path(cfg.prompts.compose_system_path).read_text()
        self._user = Path(cfg.prompts.compose_user_path).read_text()

    async def run(self, payload: CommunicationInput) -> CommunicationResult:
        started = time.perf_counter()
        priority = payload.priority.priority

        templates_rule = await self.semantic.query_rule(
            domain=self.cfg.semantic_queries.communication_templates,
            process="i2r", step="closure.notify",
        )
        priority_templates: dict[str, Any] = (templates_rule or {}).get(priority) or {}

        # If trigger is escalation, also pull in the escalation_audiences list.
        if payload.trigger == "escalation":
            esc_rule = await self.semantic.query_rule(
                domain=self.cfg.semantic_queries.escalation_audiences,
                process="i2r", step="closure.notify",
            )
            esc_audiences = list((esc_rule or {}).get(priority) or [])
            # Make sure every escalation audience has an entry; default to {channels:[email], tone:factual, length:medium}.
            for aud in esc_audiences:
                priority_templates.setdefault(aud, {"channels": ["email"], "tone": "factual", "length": "medium"})

        attempts: list[DispatchAttempt] = []
        audiences_reached: set[str] = set()
        channels_used: set[str] = set()
        deliveries_failed = 0

        for audience, spec in priority_templates.items():
            channels = list((spec or {}).get("channels") or [])
            tone = str((spec or {}).get("tone", "factual"))
            length = str((spec or {}).get("length", "medium"))

            for channel in channels:
                recipients = _recipients_for(
                    audience, channel, payload.incident.reporter, payload.resolver_team_id,
                )
                if not recipients:
                    # Audience configured for this channel but we don't have a recipient.
                    attempts.append(DispatchAttempt(
                        audience=audience, channel=channel, recipients=[],
                        delivered=False, error="no recipient resolved",
                    ))
                    deliveries_failed += 1
                    continue

                user_prompt = self._user.format(
                    incident_id=payload.incident.incident_id,
                    affected_service=payload.incident.affected_service or "",
                    priority=priority,
                    current_state=payload.current_state,
                    symptoms_summary=payload.incident.symptoms_summary,
                    root_cause=(payload.diagnosis.root_cause if payload.diagnosis else "") or "",
                    fix_status=(payload.fix_result.state if payload.fix_result else "") or "",
                    verification_status=(
                        "verified" if (payload.verification and payload.verification.fix_verified)
                        else ("unverified" if payload.verification else "n/a")
                    ),
                    audience=audience, tone=tone, length=length, channel=channel,
                )

                with audit_span(
                    f"comm.compose.{audience}.{channel}",
                    audit_type=AuditType.PLATFORM,
                    attributes={"comm.audience": audience, "comm.channel": channel},
                ):
                    cat_event("llm_prompt", audience=audience, channel=channel, prompt=user_prompt)
                    try:
                        msg = await compose_with_gateway(
                            gateway=self.gateway, cfg=self.cfg,
                            system_prompt=self._system, user_prompt=user_prompt,
                        )
                    except Exception as exc:
                        attempts.append(DispatchAttempt(
                            audience=audience, channel=channel, recipients=recipients,
                            delivered=False, error=f"LLM compose failed: {exc}",
                        ))
                        deliveries_failed += 1
                        continue
                    cat_event("llm_response", audience=audience, channel=channel, response=json.dumps(msg))

                subject = str(msg.get("subject", ""))
                body = str(msg.get("body", ""))
                cta = str(msg.get("cta", ""))

                attempt = DispatchAttempt(
                    audience=audience, channel=channel, recipients=recipients,
                    subject=subject, body_preview=body[:200], cta=cta,
                )
                try:
                    if channel == "email":
                        resp = await self.email.send(to=recipients, subject=subject, body=body)
                        attempt.sidecar_message_id = str(resp.get("message_id", ""))
                        attempt.delivered = bool(resp.get("delivered", False))
                    elif channel == "slack":
                        # First recipient is the channel name (or @user).
                        resp = await self.slack.post(channel=recipients[0], text=body)
                        attempt.sidecar_message_id = str(resp.get("message_id", ""))
                        attempt.delivered = bool(resp.get("ok", False))
                    elif channel == "sms":
                        resp = await self.sms.send(to=recipients, body=body)
                        attempt.sidecar_message_id = str(resp.get("message_id", ""))
                        attempt.delivered = bool(resp.get("ok", False))
                    else:
                        attempt.error = f"unknown channel {channel}"
                        attempt.delivered = False
                except ToolError as exc:
                    attempt.error = str(exc)
                    attempt.delivered = False

                if not attempt.delivered:
                    deliveries_failed += 1
                else:
                    audiences_reached.add(audience)
                    channels_used.add(channel)
                attempts.append(attempt)
                pst_event("comm_dispatch",
                          audience=audience, channel=channel, delivered=attempt.delivered)

        pst_event("comm_complete", duration_ms=(time.perf_counter() - started) * 1000.0,
                  audiences=len(audiences_reached), channels=len(channels_used),
                  failures=deliveries_failed)

        return CommunicationResult(
            incident_id=payload.incident.incident_id,
            attempts=attempts,
            audiences_reached=sorted(audiences_reached),
            channels_used=sorted(channels_used),
            deliveries_attempted=len(attempts),
            deliveries_failed=deliveries_failed,
        )
