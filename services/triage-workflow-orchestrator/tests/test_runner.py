"""Offline tests for the Triage Workflow Orchestrator runner.

The runner is the proof of the composition pattern. We stub the
``A2AClient.from_capability`` factory so we don't need a running stack —
just confirm that the chain wiring + short-circuiting + forward_field
selection do the right thing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from a2a_server.models import (
    DataPart,
    Task,
    TaskArtifact,
    TaskStatusModel,
)
from di_framework_core import TaskStatus
from triage.config import OrchestratorConfig
from triage.workflow import TriageRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "triage-workflow-orchestrator" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> OrchestratorConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, OrchestratorConfig)


def _make_completed_task(*, artifact_name: str, data: dict[str, Any]) -> Task:
    return Task(
        contextId="ctx",
        status=TaskStatusModel(state=TaskStatus.COMPLETED, message=None),
        artifacts=[TaskArtifact(name=artifact_name, parts=[DataPart(data=data)])],
    )


def _make_state_task(state: TaskStatus, *, requires_human: bool = False) -> Task:
    t = Task(contextId="ctx", status=TaskStatusModel(state=state, message=None))
    t.metadata.di.requires_human = requires_human
    return t


@pytest.mark.asyncio
async def test_chain_completes_when_all_steps_succeed(cfg, monkeypatch):
    intake_client = AsyncMock()
    intake_client.message_send.return_value = _make_completed_task(
        artifact_name="incident",
        data={"incident_id": "INC-1", "symptoms_summary": "vpn down", "symptoms_verbatim": "vpn"},
    )
    classify_client = AsyncMock()
    classify_client.message_send.return_value = _make_completed_task(
        artifact_name="classification",
        data={"incident_id": "INC-1", "service_area": "network", "category": "vpn", "confidence": 0.9},
    )

    clients_by_capability = {
        "incident_intake": intake_client,
        "incident_classification": classify_client,
    }

    async def fake_from_capability(capability_name, **kwargs):
        return clients_by_capability[capability_name]

    monkeypatch.setattr("triage.workflow.A2AClient.from_capability", fake_from_capability)

    runner = TriageRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(
        process="i2r",
        initial_payload={"email_raw": "From: x@y\nSubject: vpn down\n\nvpn keeps disconnecting"},
        correlation_id="cid-test",
    )

    assert result["chain_state"] == "completed"
    assert len(result["steps"]) == 2
    assert result["steps"][0]["capability"] == "incident_intake"
    assert result["steps"][1]["capability"] == "incident_classification"
    # forward_field=incident pulled INC-1 through
    intake_call_payload = intake_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert "email_raw" in intake_call_payload
    classify_call_payload = classify_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert classify_call_payload["incident_id"] == "INC-1"


@pytest.mark.asyncio
async def test_chain_short_circuits_on_input_required(cfg, monkeypatch):
    intake_client = AsyncMock()
    intake_client.message_send.return_value = _make_state_task(TaskStatus.INPUT_REQUIRED, requires_human=True)
    classify_client = AsyncMock()  # should never be called

    clients_by_capability = {
        "incident_intake": intake_client,
        "incident_classification": classify_client,
    }

    async def fake_from_capability(capability_name, **kwargs):
        return clients_by_capability[capability_name]

    monkeypatch.setattr("triage.workflow.A2AClient.from_capability", fake_from_capability)

    runner = TriageRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload={"email_raw": "vague"}, correlation_id="cid-test")
    assert result["chain_state"] == "input-required"
    assert len(result["steps"]) == 1
    classify_client.message_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_chain_marks_failed_step_index(cfg, monkeypatch):
    intake_client = AsyncMock()
    intake_client.message_send.return_value = _make_completed_task(artifact_name="incident", data={"incident_id": "INC-2"})
    classify_client = AsyncMock()
    classify_client.message_send.return_value = _make_state_task(TaskStatus.FAILED)

    clients_by_capability = {
        "incident_intake": intake_client,
        "incident_classification": classify_client,
    }

    async def fake_from_capability(capability_name, **kwargs):
        return clients_by_capability[capability_name]

    monkeypatch.setattr("triage.workflow.A2AClient.from_capability", fake_from_capability)

    runner = TriageRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload={}, correlation_id="cid-test")
    assert result["chain_state"] == "failed"
    assert result["failed_step_index"] == 1


@pytest.mark.asyncio
async def test_chain_composes_multi_source_inputs_for_later_steps(cfg, monkeypatch):
    """Steps with compose_inputs receive composite payloads assembled from
    earlier step artifacts — not just the immediately-previous step's output.

    This is the wiring that makes orchestrator composition over 12 tactical
    agents work: an agent like the Routing Agent needs simultaneous access
    to the incident, its classification, AND its priority — all produced by
    independent earlier steps."""
    intake_client = AsyncMock()
    intake_client.message_send.return_value = _make_completed_task(
        artifact_name="incident",
        data={"incident_id": "INC-3", "affected_service": "vpn", "symptoms_summary": "vpn drops"},
    )
    classify_client = AsyncMock()
    classify_client.message_send.return_value = _make_completed_task(
        artifact_name="classification",
        data={"service_area": "network", "category": "vpn", "confidence": 0.9},
    )
    priority_client = AsyncMock()
    priority_client.message_send.return_value = _make_completed_task(
        artifact_name="priority", data={"priority": "P2"},
    )
    routing_client = AsyncMock()
    routing_client.message_send.return_value = _make_completed_task(
        artifact_name="routing", data={"assigned_team": "network-ops"},
    )

    clients_by_capability = {
        "incident_intake": intake_client,
        "incident_classification": classify_client,
        "priority_scoring": priority_client,
        "incident_routing": routing_client,
    }

    async def fake_from_capability(capability_name, **kwargs):
        return clients_by_capability[capability_name]

    monkeypatch.setattr("triage.workflow.A2AClient.from_capability", fake_from_capability)

    runner = TriageRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(
        process="i2r",
        initial_payload={"email_raw": "vpn down"},
        correlation_id="cid",
    )

    assert result["chain_state"] == "completed"
    assert len(result["steps"]) == 4

    priority_payload = priority_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert set(priority_payload.keys()) == {"incident", "classification"}
    assert priority_payload["incident"]["incident_id"] == "INC-3"
    assert priority_payload["classification"]["service_area"] == "network"

    routing_payload = routing_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert set(routing_payload.keys()) == {"incident", "classification", "priority"}
    assert routing_payload["priority"]["priority"] == "P2"
