"""Offline tests for the Resolution Orchestrator runner.

Covers the new `input.<key>` reference resolution and the saga-stub
planning behaviour."""
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
from resolution.config import OrchestratorConfig
from resolution.workflow import ResolutionRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "resolution-workflow-orchestrator" / "configs" / "agent.yaml"


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


@pytest.mark.asyncio
async def test_chain_passes_input_refs_and_step_refs_correctly(cfg, monkeypatch):
    """Knowledge Search must receive a composite payload with:
      - incident, classification from `input.*` (caller's payload)
      - diagnosis from `0.diagnosis` (Diagnostic's artifact)
    """
    diagnostic_client = AsyncMock()
    diagnostic_client.message_send.return_value = _make_completed_task(
        artifact_name="diagnosis",
        data={"incident_id": "INC-1", "root_cause": "CA group sync lapsed", "confidence": 0.92},
    )
    knowledge_client = AsyncMock()
    knowledge_client.message_send.return_value = _make_completed_task(
        artifact_name="knowledge",
        data={"incident_id": "INC-1", "articles": []},
    )

    clients = {
        "root_cause_analysis": diagnostic_client,
        "knowledge_search": knowledge_client,
    }

    async def fake_from_capability(capability_name, **kwargs):
        return clients[capability_name]

    monkeypatch.setattr("resolution.workflow.A2AClient.from_capability", fake_from_capability)

    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(
        process="i2r",
        initial_payload={
            "incident": {"incident_id": "INC-1", "affected_service": "okta-sso", "symptoms_summary": "SSO failing"},
            "classification": {"service_area": "application", "category": "okta-sso"},
            "priority": {"priority": "P2"},
        },
        correlation_id="cid",
    )

    assert result["chain_state"] == "completed"
    assert len(result["steps"]) == 2

    # Diagnostic should have received the whole initial payload (no compose_inputs on step 0).
    diag_payload = diagnostic_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert "incident" in diag_payload and "classification" in diag_payload

    # Knowledge Search should have received compose_inputs:
    #   incident       <- input.incident
    #   classification <- input.classification
    #   diagnosis      <- 0.diagnosis
    ks_payload = knowledge_client.message_send.await_args.kwargs["parts"][0]["data"]
    assert set(ks_payload.keys()) == {"incident", "classification", "diagnosis"}
    assert ks_payload["incident"]["incident_id"] == "INC-1"
    assert ks_payload["diagnosis"]["root_cause"] == "CA group sync lapsed"


@pytest.mark.asyncio
async def test_saga_plan_present_but_unexecuted_in_stage_4a(cfg):
    """Stage 4a leaves saga.enabled=false, so the returned saga_compensations
    list is empty even when a step fails. The plan-only behaviour is
    exercised in Stage 4b's tests."""
    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    # Bypass the chain — just call the saga method directly.
    plan = await runner._run_saga(failed_step=0, chain_outputs=[], process="i2r")  # noqa: SLF001
    # Stage 4a config has no compensations, so the planner returns []
    assert plan == []


def test_resolve_ref_handles_both_kinds(cfg):
    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    initial = {"incident": {"id": "I"}, "priority": {"p": "P1"}}
    outputs = [
        {"artifacts": [{"name": "diagnosis", "data": {"cause": "x"}}]},
    ]
    assert runner._resolve_ref("input.incident", initial, outputs) == {"id": "I"}  # noqa: SLF001
    assert runner._resolve_ref("0.diagnosis", initial, outputs) == {"cause": "x"}  # noqa: SLF001
    assert runner._resolve_ref("input.does_not_exist", initial, outputs) is None  # noqa: SLF001
    assert runner._resolve_ref("99.diagnosis", initial, outputs) is None  # noqa: SLF001
    assert runner._resolve_ref("malformed", initial, outputs) is None  # noqa: SLF001
