"""Workflow tests with mocked external collaborators.

Asserts:
 1. Happy-path chain runs all 12 steps and emits state=new.
 2. SBCA failure on step 5 raises SemanticPlaneError — no fallback.
 3. Missing required fields produce state=needs_clarification (input-required).
 4. Duplicate match short-circuits to state=duplicate.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from di_framework_core import SemanticPlaneError
from incident_intake.config import AgentConfig
from incident_intake.models import RawInput
from incident_intake.workflow import IntakeRunner


@pytest.fixture
def runner_factory(agent_config: AgentConfig, stub_semantic):
    """Builds an IntakeRunner with mocked collaborators. The caller can
    override sub-components by passing kwargs."""

    def make(
        *,
        extracted: dict[str, Any] | None = None,
        duplicate_score: float = 0.10,
        semantic=None,
    ) -> IntakeRunner:
        gateway = AsyncMock()
        gateway.embedding.return_value.vectors = [[0.0] * agent_config.embedding.vector_size]
        # extract_with_gateway monkeypatch via gateway.chat_completion
        async def chat(*args, **kwargs):
            return type("R", (), {"text": __import__("json").dumps(extracted or {
                "reporter": "user@example.com",
                "affected_service": "vpn",
                "service_area": "network",
                "symptoms_verbatim": "vpn drops",
                "symptoms_summary": "VPN disconnects",
                "urgency": "high",
                "reported_at": "2026-05-12T09:14:32Z",
            })})()
        gateway.chat_completion.side_effect = chat

        email = AsyncMock()
        email.parse.return_value = {"body": "raw body", "received_at": "2026-05-12T09:14:32Z"}
        slack = AsyncMock()
        form = AsyncMock()
        qdrant = AsyncMock()
        qdrant.ensure_collection = AsyncMock()
        qdrant.nearest.return_value = [
            {"id": "h1", "score": duplicate_score, "payload": {"incident_id": "INC-OLD", "title": "old vpn"}}
        ] if duplicate_score >= 0 else []

        return IntakeRunner(
            cfg=agent_config,
            gateway=gateway,
            semantic=semantic or stub_semantic,
            email_parser=email,
            slack_connector=slack,
            form_normaliser=form,
            qdrant=qdrant,
        )

    return make


@pytest.mark.asyncio
async def test_happy_path_emits_new(runner_factory):
    runner = runner_factory()
    incident = await runner.run(RawInput(email_raw="anything"), correlation_id="cid-1")
    assert incident.state == "new"
    assert incident.incident_id.startswith("INC-")
    assert incident.correlation_id == "cid-1"
    assert incident.symptoms_summary == "VPN disconnects"


@pytest.mark.asyncio
async def test_duplicate_short_circuit(runner_factory):
    runner = runner_factory(duplicate_score=0.95)   # >= 0.92 threshold
    incident = await runner.run(RawInput(email_raw="anything"), correlation_id="cid-2")
    assert incident.state == "duplicate"
    assert incident.duplicate_of == "INC-OLD"


@pytest.mark.asyncio
async def test_missing_required_fields_clarification(runner_factory, stub_semantic):
    runner = runner_factory(
        extracted={
            "reporter": None, "affected_service": None, "service_area": None,
            "symptoms_verbatim": "x", "symptoms_summary": "x",
            "urgency": None, "reported_at": None,
        },
    )
    incident = await runner.run(RawInput(email_raw="anything"), correlation_id="cid-3")
    assert incident.state == "needs_clarification"
    assert set(incident.missing_fields) <= {"reporter", "affected_service", "symptoms", "reported_at"}
    assert incident.clarification_questions and "few more details" in incident.clarification_questions


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails_no_fallback(runner_factory):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("SBCA unreachable")
    runner = runner_factory(semantic=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(RawInput(email_raw="anything"), correlation_id="cid-4")
