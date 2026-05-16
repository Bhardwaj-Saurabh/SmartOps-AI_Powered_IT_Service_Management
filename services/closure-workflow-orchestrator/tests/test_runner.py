"""Offline tests for the Closure Workflow Orchestrator runner.

Covers:
  * the new nested-input ref syntax (``input.priority.service_tier``)
  * end-to-end chain of Communication + SLA with mocked downstream agents
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
from closure.config import OrchestratorConfig
from closure.workflow import ClosureRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "closure-workflow-orchestrator" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> OrchestratorConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, OrchestratorConfig)


def _completed(artifact_name: str, data: dict[str, Any]) -> Task:
    return Task(
        contextId="ctx",
        status=TaskStatusModel(state=TaskStatus.COMPLETED, message=None),
        artifacts=[TaskArtifact(name=artifact_name, parts=[DataPart(data=data)])],
    )


def _initial() -> dict[str, Any]:
    return {
        "incident": {"incident_id": "INC-C1", "affected_service": "okta-sso",
                     "symptoms_summary": "SSO failing", "reporter": "alice@example.sales"},
        "classification": {"service_area": "application", "category": "okta-sso"},
        "priority": {"priority": "P2", "service_tier": "silver", "blast_radius": 1},
        "diagnosis": {"root_cause": "CA group sync lapsed"},
        "fix_result": {"state": "completed", "selected_runbook_id": "okta-ca-resync",
                       "what_changed": "Re-synced CA group"},
        "verification": {"fix_verified": True, "confidence": 0.9},
        "trigger": "resolution",
        "current_state": "resolved",
        "region": "UK",
        "started_at_epoch": 1_700_000_000,
        "state_transitions": [{"state": "new", "at_epoch": 1_700_000_000}],
    }


def test_resolve_ref_dot_walks_into_input(cfg):
    runner = ClosureRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    initial = {"priority": {"priority": "P1", "service_tier": "gold"}, "x": {"y": {"z": 42}}}
    assert runner._resolve_ref("input.priority.priority", initial, []) == "P1"  # noqa: SLF001
    assert runner._resolve_ref("input.priority.service_tier", initial, []) == "gold"  # noqa: SLF001
    assert runner._resolve_ref("input.x.y.z", initial, []) == 42  # noqa: SLF001
    assert runner._resolve_ref("input.priority.does_not_exist", initial, []) is None  # noqa: SLF001


def test_resolve_ref_dot_walks_into_artifact(cfg):
    runner = ClosureRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    outputs = [{"artifacts": [{"name": "sla_status", "data": {"targets": {"resolve": 480}}}]}]
    assert runner._resolve_ref("0.sla_status.targets.resolve", {}, outputs) == 480  # noqa: SLF001
    assert runner._resolve_ref("0.sla_status.does_not_exist", {}, outputs) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_chain_dispatches_notify_then_sla(cfg, monkeypatch):
    comm = AsyncMock(); comm.message_send.return_value = _completed(
        "communications_sent", {"incident_id": "INC-C1", "attempts": [], "audiences_reached": ["end_user"]},
    )
    sla = AsyncMock(); sla.message_send.return_value = _completed(
        "sla_status", {"incident_id": "INC-C1", "response_consumed_pct": 50.0,
                       "resolve_consumed_pct": 12.5, "response_breached": False,
                       "resolve_breached": False, "response_warning": False,
                       "resolve_warning": False, "targets": {"response": 120, "resolve": 480}},
    )

    clients = {"send_status_update": comm, "calculate_sla_status": sla}
    async def fake_from_capability(name, **kwargs):
        return clients[name]
    monkeypatch.setattr("closure.workflow.A2AClient.from_capability", fake_from_capability)

    runner = ClosureRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    assert result["chain_state"] == "completed"
    assert len(result["steps"]) == 2

    # Communication step receives the rolled-up composite.
    comm_payload = comm.message_send.await_args.kwargs["parts"][0]["data"]
    assert comm_payload["incident"]["incident_id"] == "INC-C1"
    assert comm_payload["priority"]["priority"] == "P2"
    assert comm_payload["trigger"] == "resolution"

    # SLA step receives dot-walked scalars (priority string, not the whole object).
    sla_payload = sla.message_send.await_args.kwargs["parts"][0]["data"]
    assert sla_payload["priority"] == "P2"
    assert sla_payload["customer_tier"] == "silver"
    assert sla_payload["region"] == "UK"
    assert sla_payload["started_at_epoch"] == 1_700_000_000
