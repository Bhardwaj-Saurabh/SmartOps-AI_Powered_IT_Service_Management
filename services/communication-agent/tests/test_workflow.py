"""Offline tests for the Communication Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from communication.config import AgentConfig
from communication.models import (
    ClassificationSlice,
    CommunicationInput,
    DiagnosisSlice,
    FixResultSlice,
    IncidentSlice,
    PrioritySlice,
    VerificationSlice,
)
from communication.workflow import CommunicationRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "communication-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "communication-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _runner(cfg, *, semantic_stub) -> CommunicationRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps({
            "subject": "INC-X status update",
            "body": "We are aware. Mitigation in progress.",
            "cta": "No action required",
        })})()
    gateway.chat_completion.side_effect = chat

    email = AsyncMock(); email.send.return_value = {"message_id": "msg-1", "delivered": True, "queued_at_epoch": 0}
    slack = AsyncMock(); slack.post.return_value = {"ok": True, "message_id": "slk-1", "posted_at_epoch": 0}
    sms = AsyncMock(); sms.send.return_value = {"ok": True, "message_id": "sms-1", "sent_at_epoch": 0}

    return CommunicationRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        email=email, slack=slack, sms=sms,
    )


def _payload(*, priority: str = "P2", trigger: str = "state_change") -> CommunicationInput:
    return CommunicationInput(
        incident=IncidentSlice(incident_id="INC-COMM-1", affected_service="okta-sso",
                               reporter="alice@example.sales",
                               reporter_department="sales",
                               symptoms_summary="SSO failing"),
        classification=ClassificationSlice(service_area="application", category="okta-sso"),
        priority=PrioritySlice(priority=priority),
        diagnosis=DiagnosisSlice(root_cause="CA group sync lapsed"),
        fix_result=FixResultSlice(state="completed", selected_runbook_id="okta-ca-resync",
                                  what_changed="Re-synced CA group"),
        verification=VerificationSlice(fix_verified=True, confidence=0.9),
        trigger=trigger,
        current_state="resolved",
    )


@pytest.mark.asyncio
async def test_p2_dispatches_to_configured_audiences(cfg, semantic_stub):
    runner = _runner(cfg, semantic_stub=semantic_stub)
    result = await runner.run(_payload(priority="P2"))
    # P2 template covers end_user (email), affected_stakeholders (email, slack),
    # resolver_team (slack) — 4 attempted dispatches.
    assert result.deliveries_attempted == 4
    assert result.deliveries_failed == 0
    audiences = {a.audience for a in result.attempts}
    assert audiences == {"end_user", "affected_stakeholders", "resolver_team"}


@pytest.mark.asyncio
async def test_p1_escalation_pulls_in_executive(cfg, semantic_stub):
    runner = _runner(cfg, semantic_stub=semantic_stub)
    result = await runner.run(_payload(priority="P1", trigger="escalation"))
    audiences = {a.audience for a in result.attempts}
    assert "executive" in audiences


@pytest.mark.asyncio
async def test_missing_recipient_logged_as_failure(cfg, semantic_stub):
    """SMS to end_user isn't supported in Phase 1 (no phone in incident record).
    The dispatch for that cell is logged as failed."""
    # Construct a payload at P1 — end_user has email + slack channels but
    # NO sms (per the seed rules). So no missing-recipient cell. To force
    # a missing-recipient scenario, set reporter=None then P2 (end_user has
    # email only). With reporter=None the cell becomes empty.
    runner = _runner(cfg, semantic_stub=semantic_stub)
    payload = _payload(priority="P2")
    payload.incident.reporter = None
    result = await runner.run(payload)
    end_user_attempts = [a for a in result.attempts if a.audience == "end_user"]
    assert end_user_attempts and all(not a.delivered for a in end_user_attempts)


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
