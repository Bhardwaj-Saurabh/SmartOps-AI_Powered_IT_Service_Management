"""Saga-compensation tests for the Resolution Workflow Orchestrator.

These cover the Stage 4b behaviour: Verification reporting
``fix_verified=false`` (artifact predicate trigger) fires a rollback call
to the Automated Fix Agent's ``rollback`` skill.
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


def _make_failed_task() -> Task:
    return Task(
        contextId="ctx",
        status=TaskStatusModel(state=TaskStatus.FAILED, message=None),
    )


def _initial_payload() -> dict[str, Any]:
    return {
        "incident": {"incident_id": "INC-SAGA", "affected_service": "okta-sso",
                     "symptoms_summary": "AADSTS50105"},
        "classification": {"service_area": "application", "category": "okta-sso"},
        "priority": {"priority": "P2", "service_tier": "gold", "blast_radius": 1},
    }


@pytest.mark.asyncio
async def test_verification_fail_triggers_rollback_via_saga(cfg, monkeypatch):
    """The artifact-predicate trigger fires when Verification returns
    fix_verified=false. The runner must call automated_fix.rollback with
    the rollback_token from step 2 (the fix_result artifact)."""
    diag = AsyncMock(); diag.message_send.return_value = _make_completed_task(
        artifact_name="diagnosis",
        data={"incident_id": "INC-SAGA", "root_cause": "CA group lapsed", "confidence": 0.9},
    )
    knowledge = AsyncMock(); knowledge.message_send.return_value = _make_completed_task(
        artifact_name="knowledge", data={"incident_id": "INC-SAGA", "articles": []},
    )
    fix = AsyncMock(); fix.message_send.return_value = _make_completed_task(
        artifact_name="fix_result",
        data={"incident_id": "INC-SAGA", "state": "completed",
              "selected_runbook_id": "okta-ca-resync",
              "rollback_token": "snap-XYZ", "step_log": []},
    )
    verify = AsyncMock(); verify.message_send.return_value = _make_completed_task(
        artifact_name="verification",
        data={"incident_id": "INC-SAGA", "fix_verified": False,
              "confidence": 0.85, "reasoning": "symptoms persist",
              "residual_concerns": ["AADSTS50105 still observed"]},
    )

    rollback_invocations: list[dict[str, Any]] = []
    async def fake_rollback_send(**kwargs):
        rollback_invocations.append(kwargs)
        return _make_completed_task(
            artifact_name="rollback_result",
            data={"rollback_token": kwargs["parts"][0]["data"]["rollback_token"],
                  "restored": True, "restored_state_keys": ["pre_fix_marker"]},
        )

    rollback_client = AsyncMock()
    rollback_client.message_send.side_effect = fake_rollback_send

    clients = {
        "root_cause_analysis": diag,
        "knowledge_search": knowledge,
        "automated_fix": rollback_client,    # used by saga
        "verify_resolution": verify,
    }
    # Forward chain step 2 (apply_automated_fix → automated_fix capability)
    # uses the same `clients["automated_fix"]` — but the chain calls
    # `skill=apply_automated_fix` and saga calls `skill=rollback`.
    async def fake_fix_send(**kwargs):
        if kwargs.get("capability") == "apply_automated_fix":
            return fix.message_send.return_value
        return await fake_rollback_send(**kwargs)
    rollback_client.message_send.side_effect = fake_fix_send

    async def fake_from_capability(capability_name, **kwargs):
        return clients[capability_name]
    monkeypatch.setattr("resolution.workflow.A2AClient.from_capability", fake_from_capability)

    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload=_initial_payload(), correlation_id="cid")

    assert result["chain_state"] == "completed"        # all four chain steps ran
    assert len(result["steps"]) == 4
    assert result["saga_compensations"], "saga should have fired"
    saga = result["saga_compensations"][0]
    assert saga["capability"] == "automated_fix"
    assert saga["skill"] == "rollback"
    assert saga["executed"] is True
    assert saga["succeeded"] is True
    assert saga["params"]["rollback_token"] == "snap-XYZ"


@pytest.mark.asyncio
async def test_verification_pass_skips_saga(cfg, monkeypatch):
    diag = AsyncMock(); diag.message_send.return_value = _make_completed_task(
        artifact_name="diagnosis", data={"incident_id": "INC-OK", "root_cause": "x", "confidence": 0.9})
    knowledge = AsyncMock(); knowledge.message_send.return_value = _make_completed_task(
        artifact_name="knowledge", data={"incident_id": "INC-OK", "articles": []})
    fix_call_count = {"n": 0}

    async def fake_send(**kwargs):
        cap = kwargs.get("capability")
        if cap == "apply_automated_fix":
            fix_call_count["n"] += 1
            return _make_completed_task(
                artifact_name="fix_result",
                data={"incident_id": "INC-OK", "state": "completed",
                      "selected_runbook_id": "okta-ca-resync",
                      "rollback_token": "snap-OK"},
            )
        if cap == "rollback":
            pytest.fail("Rollback should NOT have been called on a verified fix")
        raise AssertionError(f"unexpected capability {cap}")
    automated_fix = AsyncMock(); automated_fix.message_send.side_effect = fake_send

    verify = AsyncMock(); verify.message_send.return_value = _make_completed_task(
        artifact_name="verification",
        data={"incident_id": "INC-OK", "fix_verified": True, "confidence": 0.9,
              "reasoning": "all clear", "residual_concerns": []},
    )

    clients = {
        "root_cause_analysis": diag, "knowledge_search": knowledge,
        "automated_fix": automated_fix, "verify_resolution": verify,
    }
    async def fake_from_capability(capability_name, **kwargs):
        return clients[capability_name]
    monkeypatch.setattr("resolution.workflow.A2AClient.from_capability", fake_from_capability)

    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload=_initial_payload(), correlation_id="cid")

    assert result["chain_state"] == "completed"
    assert result["saga_compensations"] == [], "saga should not fire on verified fix"
    assert fix_call_count["n"] == 1


@pytest.mark.asyncio
async def test_verification_outright_failure_also_triggers_rollback(cfg, monkeypatch):
    """Second saga trigger: on_step_failure for the verification step."""
    diag = AsyncMock(); diag.message_send.return_value = _make_completed_task(
        artifact_name="diagnosis", data={"incident_id": "INC-VFAIL", "root_cause": "x", "confidence": 0.9})
    knowledge = AsyncMock(); knowledge.message_send.return_value = _make_completed_task(
        artifact_name="knowledge", data={"incident_id": "INC-VFAIL", "articles": []})

    rollback_calls: list[dict] = []
    async def fake_send(**kwargs):
        cap = kwargs.get("capability")
        if cap == "apply_automated_fix":
            return _make_completed_task(
                artifact_name="fix_result",
                data={"incident_id": "INC-VFAIL", "state": "completed",
                      "selected_runbook_id": "okta-ca-resync", "rollback_token": "snap-VF"})
        if cap == "rollback":
            rollback_calls.append(kwargs)
            return _make_completed_task(
                artifact_name="rollback_result",
                data={"rollback_token": kwargs["parts"][0]["data"]["rollback_token"],
                      "restored": True, "restored_state_keys": []})
        raise AssertionError(f"unexpected {cap}")
    automated_fix = AsyncMock(); automated_fix.message_send.side_effect = fake_send

    verify = AsyncMock(); verify.message_send.return_value = _make_failed_task()

    clients = {
        "root_cause_analysis": diag, "knowledge_search": knowledge,
        "automated_fix": automated_fix, "verify_resolution": verify,
    }
    async def fake_from_capability(capability_name, **kwargs):
        return clients[capability_name]
    monkeypatch.setattr("resolution.workflow.A2AClient.from_capability", fake_from_capability)

    runner = ResolutionRunner(cfg=cfg, registry_url="http://sbca:8444", bearer_provider=None)
    result = await runner.run(process="i2r", initial_payload=_initial_payload(), correlation_id="cid")

    # The chain itself ends in failed (the verify step failed), but the
    # saga still fired and rolled back.
    assert result["chain_state"] == "failed"
    assert result["failed_step_index"] == 3
    assert len(rollback_calls) == 1
    assert rollback_calls[0]["parts"][0]["data"]["rollback_token"] == "snap-VF"
